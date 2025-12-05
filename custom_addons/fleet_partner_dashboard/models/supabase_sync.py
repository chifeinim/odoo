from odoo import models, api, fields, tools, _
from odoo.exceptions import UserError
from odoo.fields import Datetime as OdooDatetime
from requests.exceptions import HTTPError
from datetime import datetime, timezone, timedelta
from dateutil.parser import isoparse
import logging, requests, json, urllib.parse, re, traceback


_logger = logging.getLogger(__name__)

def _to_supabase_iso(dt):
    """Convert an Odoo datetime (naive UTC) to RFC3339 UTC string."""
    if not dt:
        return None
    # Odoo stores naive UTC; treat as UTC.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace('+00:00', 'Z')

def _normalize_datetime(val):
    """Convert arbitrary ISO8601 (with offset / fractional seconds) into naive UTC 'YYYY-MM-DD HH:MM:SS'."""
    dt = isoparse(val)  # handles fractional seconds, offsets, Z, etc.
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime('%Y-%m-%d %H:%M:%S')

class FleetPartnerSupabaseSync(models.AbstractModel):
    _name = 'x_fleet_partner_supabase_sync'
    _description = 'Sync data from Supabase by table'
    
    def _parse_sh_cursor(self, cursor):
        """
        Parse a supply_hours composite cursor into (ts, yango_driver_id, date_str).

        Accepted forms:
        - "" / None                                 → ('1970-01-01T00:00:00Z', '', '0001-01-01')
        - "2025-08-06T10:00:01Z"                    → (ts, '', '0001-01-01')
        - "2025-08-06T10:00:01Z||abcd1234"          → (ts, 'abcd1234', '0001-01-01')
        - "2025-08-06T10:00:01Z||abcd1234||2025-08-05" → (ts, 'abcd1234', '2025-08-05')
        """
        # defaults: ts at epoch, and the smallest ISO date (lexicographically minimal)
        ts_default = "1970-01-01T00:00:00Z"
        yid_default = ""
        date_default = "0001-01-01"

        if not cursor:
            return ts_default, yid_default, date_default

        if not isinstance(cursor, str):
            cursor = str(cursor)

        parts = [p.strip() for p in cursor.split("||")]

        if len(parts) >= 3 and parts[0]:
            ts, yid, d = parts[0], parts[1], parts[2]
        elif len(parts) == 2 and parts[0]:
            ts, yid, d = parts[0], parts[1], date_default
        elif len(parts) == 1 and parts[0]:
            ts, yid, d = parts[0], yid_default, date_default
        else:
            return ts_default, yid_default, date_default

        # Very light validation / normalization for date component
        if not (isinstance(d, str) and len(d) == 10 and d[4] == '-' and d[7] == '-'):
            d = date_default

        return ts, yid or yid_default, d
    
    def _queue_failed_row(self, table, rec, record_key, reason=None):
        Q = self.env['x_supabase_sync_queue'].sudo()
        existing = Q.search([('table','=',table), ('record_key','=',record_key)], limit=1)
        vals = {
            'table': table,
            'record_key': record_key,
            'raw_data': json.dumps(rec)[:100000],
            'error': (reason or '')[:1000],
            'state': 'queued',
            'next_attempt': fields.Datetime.now(),
        }
        return existing.write(vals) or existing or Q.create(vals)

    def _get_config(self):
        params = self.env['ir.config_parameter'].sudo()
        url = params.get_param('supabase.url')
        key = params.get_param('supabase.service_key') or params.get_param('supabase.publishable_key')
        if not url or not key:
            raise UserError("Supabase URL or key not found in ir.config_parameter.")
        return url.rstrip('/'), key
    
    def _build_headers(self, profile):
        base_url, key = self._get_config()
        params = self.env['ir.config_parameter'].sudo()
        tenant_secret = (params.get_param('supabase.tenant_secret') or '').strip()

        headers = {
            "apikey":        key,
            "Authorization": f"Bearer {key}",
            "Accept":        "application/json",
            "Accept-Profile":  profile,
            "Content-Profile": profile,
            "Prefer":        "count=exact",
        }
        if tenant_secret:
            headers["X-Tenant-Secret"] = tenant_secret
        return headers


    def _fetch_table(self, table, last_sync, page_size=1000, profile="external_data_yango"):
        base_url, key = self._get_config()
        endpoint = f"{base_url}/rest/v1/{table}"
        headers = self._build_headers(profile)
        all_rows = []
        offset = 0
        while True:
            params = {
                "select":     "*",
                "updated_at": f"gt.{last_sync}",
                "order":      "updated_at.asc",
                "limit":      page_size,
                "offset":     offset,
            }
            _logger.info("Fetching %s rows %d→%d (page_size=%d)…", table, offset+1, offset+page_size, page_size)
            resp = requests.get(endpoint, headers=headers, params=params, timeout=60)
            resp.raise_for_status()
            batch = resp.json() or []
            if not batch:
                break
            all_rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
        _logger.info("Fetched %d rows from %s", len(all_rows), table)
        return all_rows

    def _fetch_page(self, table, last_sync, limit, offset, profile="external_data_yango"):
        base_url, key = self._get_config()
        endpoint = f"{base_url}/rest/v1/{table}"
        headers = self._build_headers(profile)
        params = {
            "select":     "*",
            "updated_at": f"gte.{last_sync}",
            "order":      "updated_at.asc",
            "limit":      limit,
            "offset":     offset,
        }
        _logger.info("Fetching page %d→%d of %s…", offset+1, offset+limit, table)
        resp = requests.get(endpoint, headers=headers, params=params, timeout=60)
        resp.raise_for_status()
        return resp.json() or []

    def _fetch_orders_page(self, last_updated_at, last_id, page_size=5000, profile="external_data_yango"):
        """
        Fetch next page of orders with composite cursor (updated_at, id).
        """
        base_url, key = self._get_config()
        endpoint = f"{base_url}/rest/v1/orders"
        headers = self._build_headers(profile)

        # Build composite filter: updated_at > last_updated_at
        # OR (updated_at = last_updated_at AND id > last_id)
        or_clause = (
            f"or=(updated_at.gt.{urllib.parse.quote_plus(last_updated_at)},"
            f"and(updated_at.eq.{urllib.parse.quote_plus(last_updated_at)},id.gt.{last_id}))"
        )
        params = {
            "select": "*,updated_at,id",
            "order": "updated_at.asc,id.asc",
            "limit": page_size,
        }
        query = f"{urllib.parse.urlencode(params)}&{or_clause}"
        _logger.info("Fetching orders page after (%s, %s)… (limit=%d)", last_updated_at, last_id, page_size)
        resp = requests.get(f"{endpoint}?{query}", headers=headers, timeout=60)
        resp.raise_for_status()
        return resp.json() or []
    
    def _fetch_supply_hours_page(self, last_ts, last_yid, last_date,
                                page_size=1000, profile="external_data_yango"):
        base_url, key = self._get_config()
        endpoint = f"{base_url}/rest/v1/supply_hours"
        headers = self._build_headers(profile)
        ts_q  = urllib.parse.quote_plus(last_ts)
        yid_q = urllib.parse.quote_plus(last_yid or '')
        date_q= urllib.parse.quote_plus(last_date or '0001-01-01')

        or_clause = (
            f"or=("
            f"updated_at.gt.{ts_q},"
            f"and(updated_at.eq.{ts_q},yango_driver_id.gt.{yid_q}),"
            f"and(updated_at.eq.{ts_q},yango_driver_id.eq.{yid_q},date.gt.{date_q})"
            f")"
        )
        params = {
            "select": "*",
            "order": "updated_at.asc,yango_driver_id.asc,date.asc",
            "limit": page_size,
        }
        query = f"{urllib.parse.urlencode(params)}&{or_clause}"
        resp = requests.get(f"{endpoint}?{query}", headers=headers, timeout=60)
        resp.raise_for_status()
        return resp.json() or []
    
    def _fetch_issue_logs(self, last_sync, page_size=1000, profile="dashboard"):
        """
        Fetch issue_logs joined with issues_list (embedded), filtered by updated_at.
        Returns rows like:
        {
            "id": ...,
            "created_at": "...",
            "updated_at": "...",
            "issue_id": ...,
            "yango_driver_id": "...",
            "internal_driver_id": "...",
            "status": "...",
            "can_work": true/false,
            "issues_list": {
                "issue_type": "...",       # NEW
                "main_category": "...",
                "sub_category": "...",
                "sub_sub_category": "..."
            }
        }
        """
        base_url, key = self._get_config()
        endpoint = f"{base_url}/rest/v1/issue_logs"
        headers = self._build_headers(profile)

        # include issue_type in the embedded issues_list
        select = "*,issues_list:issue_id(issue_type,main_category,sub_category,sub_sub_category)"
        all_rows = []
        offset = 0
        while True:
            params = {
                "select": select,
                "updated_at": f"gt.{last_sync}",
                "order": "updated_at.asc,id.asc",
                "limit": page_size,
                "offset": offset,
            }
            _logger.info(
                "Fetching issue_logs %d→%d (page_size=%d)…",
                offset + 1, offset + page_size, page_size
            )
            resp = requests.get(endpoint, headers=headers, params=params, timeout=60)
            resp.raise_for_status()
            batch = resp.json() or []
            if not batch:
                break
            all_rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size

        _logger.info("Fetched %d rows from issue_logs (with issues_list)", len(all_rows))
        return all_rows
    
    def _fetch_issue_attachments(self, last_sync, page_size=1000, profile="dashboard"):
        """
        Step 1: fetch dashboard.issue_attachments (by created_at)
        Step 2: batch fetch comms.attachments for the referenced ids
        Returns rows shaped like:
        {
            "id": ...,
            "created_at": "...",
            "issue_log_id": ...,
            "attachment_id": ...,
            "attachment": { "id":..., "bucket_id":"...", "key":"...", "url":"...", "created_at":"..." }
        }
        """
        base_url, key = self._get_config()

        # ---------- Step 1: issue_attachments (dashboard profile) ----------
        ia_endpoint = f"{base_url}/rest/v1/issue_attachments"
        ia_headers = self._build_headers(profile)

        all_rows, offset = [], 0
        select_ia = "id,created_at,issue_log_id,attachment_id"

        while True:
            params = {
                "select":     select_ia,
                "created_at": f"gt.{last_sync}",
                "order":      "created_at.asc",
                "limit":      page_size,
                "offset":     offset,
            }
            _logger.info("Fetching issue_attachments %d→%d…", offset+1, offset+page_size)
            resp = requests.get(ia_endpoint, headers=ia_headers, params=params, timeout=60)
            try:
                resp.raise_for_status()
            except requests.HTTPError as e:
                # Log server message to help diagnose bad requests
                _logger.error("issue_attachments fetch failed: %s - %s", e, resp.text)
                raise
            batch = resp.json() or []
            if not batch:
                break
            all_rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size

        if not all_rows:
            _logger.info("Fetched 0 rows from issue_attachments")
            return []

        # ---------- Step 2: comms.attachments (comms profile) ----------
        att_endpoint = f"{base_url}/rest/v1/attachments"
        att_headers = self._build_headers("comms")
        # Collect unique ids
        att_ids = sorted({r["attachment_id"] for r in all_rows if r.get("attachment_id")})
        att_map = {}

        # Chunk to avoid long URLs
        CHUNK = 500
        select_att = "id,bucket_id,key,url,created_at"
        for i in range(0, len(att_ids), CHUNK):
            chunk = att_ids[i:i+CHUNK]
            # PostgREST IN syntax: id=in.(1,2,3)
            params = {
                "select": select_att,
                "id":     "in.(" + ",".join(str(x) for x in chunk) + ")",
                "limit":  CHUNK,
            }
            _logger.info("Fetching attachments ids %d..%d", i+1, i+len(chunk))
            resp = requests.get(att_endpoint, headers=att_headers, params=params, timeout=60)
            try:
                resp.raise_for_status()
            except requests.HTTPError as e:
                _logger.error("attachments fetch failed: %s - %s", e, resp.text)
                raise
            for row in (resp.json() or []):
                att_map[row["id"]] = row

        # ---------- Merge ----------
        for r in all_rows:
            aid = r.get("attachment_id")
            r["attachment"] = att_map.get(aid) if aid is not None else None

        _logger.info("Fetched %d rows from issue_attachments; joined %d attachments",
                    len(all_rows), len(att_map))
        return all_rows

    @api.model
    def _upsert_product_types(self, rows):
        """
        Upsert product types from external_data_yango.work_rules.

        Canonical key = work_rule_id (stored as work_rule_external_id).
        We keep other fields (kpi_type, description, bounds) under Odoo's control.
        """
        ProductType = self.env['x_fleet_product_type'].sudo()
        _logger.info("Upserting %d product types (via work_rule_id)", len(rows))

        new_vals = []

        for rec in rows:
            ext_id = rec.get('work_rule_id')
            if not ext_id:
                # If a work_rule row without work_rule_id ever appears, skip it
                continue

            name = rec.get('name') or ext_id

            # Backwards compat:
            # 1) Try existing by external id
            # 2) If none, try by name (old behavior), then attach ext_id to it
            pt = ProductType.search([
                '|',
                ('work_rule_external_id', '=', ext_id),
                ('name', '=', name),
            ], limit=1)

            vals_update = {
                'name': name,
                'work_rule_external_id': ext_id,
            }

            if pt:
                # Update just the "identity" fields; do not touch kpi config
                pt.write(vals_update)
            else:
                new_vals.append(vals_update)

        if new_vals:
            ProductType.create(new_vals)
            _logger.info("Bulk-created %d new product types", len(new_vals))

    @api.model
    def _upsert_drivers(self, rows, work_rule_map, from_queue=False):
        """Upsert drivers.

        - Canonical link to product type is work_rule_id.
        - product types are identified by x_fleet_product_type.work_rule_external_id.
        - work_rule_map is still accepted as a fallback (work_rule_id -> name).
        """
        _logger.info("Upserting %d drivers", len(rows))
        ProductType = self.env['x_fleet_product_type'].sudo()
        Driver = self.env['x_fleet_driver'].sudo()
        new_vals = []

        # cache by work_rule_id -> product_type_id
        pt_cache: dict[str, int] = {}

        for rec in rows:
            first = rec.get('first_name') or ''
            last = rec.get('last_name') or ''
            full_name = (first + ' ' + last).strip() or None

            raw = {
                'name':            full_name,
                'phone':           rec.get('phone_number'),
                'hire_date':       _normalize_datetime(rec['hire_date']) if rec.get('hire_date') else None,
                'training_rating': rec.get('training_rating') if rec.get('training_rating') else None,
                'work_status':     rec.get('work_status'),
                'type':            rec.get('driver_type'),
            }
            vals = {k: v for k, v in raw.items() if v is not None}

            # Map work_rule -> product_type by external key
            wr_id = rec.get('work_rule_id')
            if wr_id:
                pt_id = pt_cache.get(wr_id)
                if not pt_id:
                    # First try canonical external id
                    pt = ProductType.search(
                        [('work_rule_external_id', '=', wr_id)],
                        limit=1
                    )
                    if not pt:
                        # Fallback: try inferred name from work_rule_map, or default to wr_id
                        pt_name = work_rule_map.get(wr_id) or wr_id
                        pt = ProductType.create({
                            'name': pt_name,
                            'work_rule_external_id': wr_id,
                        })

                    pt_id = pt.id
                    pt_cache[wr_id] = pt_id

                vals['product_type_id'] = pt_id

            ext_id = rec.get('yango_driver_id')
            if not ext_id:
                msg = f"Skipping driver without yango_driver_id: {rec!r}"
                if from_queue:
                    raise ValueError(msg)
                _logger.warning(msg)
                # queue and continue on normal run
                self._queue_failed_row('drivers', rec, '<missing_yango_driver_id>', msg)
                continue

            drv = Driver.search([('yango_driver_id', '=', ext_id)], limit=1)
            try:
                if drv:
                    drv.write(vals)
                else:
                    vals['yango_driver_id'] = ext_id
                    new_vals.append(vals)
            except Exception as e:
                if from_queue:
                    raise
                self._queue_failed_row('drivers', rec, ext_id, str(e))
                _logger.exception("Failed to upsert driver %s; queued for retry", ext_id)

        if new_vals:
            try:
                Driver.create(new_vals)
                _logger.info("Bulk-created %d new drivers", len(new_vals))
            except Exception:
                _logger.exception("Bulk create for drivers failed; falling back per-row")
                for vals in new_vals:
                    ext_id = vals.get('yango_driver_id')
                    rec = next((r for r in rows if r.get('yango_driver_id') == ext_id), {})
                    try:
                        Driver.create(vals)
                    except Exception as e:
                        if from_queue:
                            raise
                        self._queue_failed_row('drivers', rec, ext_id or '<missing>', str(e))

    @api.model
    def _upsert_orders(self, rows, from_queue=False):
        """
        Upsert orders. When from_queue=True, DO NOT requeue; raise on prerequisite
        problems so the retry loop keeps/backoffs the item.
        """
        _logger.info("Upserting %d orders", len(rows))
        Order  = self.env['x_fleet_order'].sudo()
        Driver = self.env['x_fleet_driver'].sudo()

        # preload driver map once per call
        driver_map = {d.yango_driver_id: d.id for d in Driver.search([('yango_driver_id', '!=', False)])}

        # Build the list of order names in this batch
        batch_names = []
        short_id_to_rec = {}
        for rec in rows:
            short_id = rec.get('order_short_id')
            if short_id is None:
                msg = "missing order_short_id"
                if from_queue:
                    raise ValueError(msg)
                self._queue_failed_row('orders', rec, '<missing>', msg)
                continue
            name = str(short_id)
            batch_names.append(name)
            short_id_to_rec[name] = rec

        if not batch_names:
            _logger.info("No valid order_short_id in batch; skipping.")
            return

        # Fetch existing orders for just these names
        existing = Order.search([('name', 'in', batch_names)])
        existing_map = {o.name: o for o in existing}

        new_vals = []
        for name in batch_names:
            rec = short_id_to_rec.get(name)
            if not rec:
                continue

            drv_ext_id = rec.get('driver_id')
            drv_id = driver_map.get(drv_ext_id)
            if not drv_id:
                msg = f"missing driver {drv_ext_id}"
                if from_queue:
                    raise ValueError(msg)
                self._queue_failed_row('orders', rec, name, msg)
                _logger.warning("Queued order %s for later (driver %s missing)", name, drv_ext_id)
                continue

            raw = {
                'order_date':               _normalize_datetime(rec['booked_at'])      if rec.get('booked_at')      else None,
                'interval_from':            _normalize_datetime(rec['interval_from'])  if rec.get('interval_from')  else None,
                'interval_to':              _normalize_datetime(rec['interval_to'])    if rec.get('interval_to')    else None,
                'status':                   rec.get('order_status'),
                'cancellation_description': rec.get('cancellation_description'),
                'pickup_address':           rec.get('pickup_address'),
                'price':                    rec.get('price'),
                'driver_id':                drv_id,
                'yango_driver_id':          drv_ext_id,
                'driver_name':              rec.get('driver_name'),
                'pick_latitude':            rec.get('pickup_latitude'),
                'pick_longitude':           rec.get('pickup_longitude'),
                'events':                   json.dumps(rec.get('events')) if rec.get('events') is not None else None,
            }
            vals = {k: v for k, v in raw.items() if v is not None}

            try:
                if name in existing_map:
                    existing_map[name].write(vals)
                else:
                    vals['name'] = name
                    new_vals.append(vals)
            except Exception as e:
                if from_queue:
                    raise
                self._queue_failed_row('orders', rec, name, str(e))
                _logger.exception("Failed to upsert order %s; queued for retry", name)

        # Bulk-create new ones; on failure, fall back per-row and queue bad ones
        if new_vals:
            try:
                Order.create(new_vals)
                _logger.info("Bulk-created %d new orders", len(new_vals))
            except Exception:
                _logger.exception("Bulk create for orders failed; falling back per-row")
                for vals in new_vals:
                    name = vals.get('name')
                    rec  = short_id_to_rec.get(name, {})
                    try:
                        Order.create(vals)
                    except Exception as e:
                        if from_queue:
                            raise
                        self._queue_failed_row('orders', rec, name or '<missing>', str(e))

    @api.model
    def sync_orders(self, last_sync):
        """
        Stream orders using a composite cursor (updated_at, id).
        Fetch up to 5 pages of 1,000 each per invocation, committing per batch so
        partial progress survives timeouts/crashes. Returns the new composite cursor.
        """
        Order  = self.env['x_fleet_order'].sudo()
        Driver = self.env['x_fleet_driver'].sudo()
        params = self.env['ir.config_parameter'].sudo()

        # Reconstruct cursor; parse id as integer for proper comparison
        if isinstance(last_sync, str) and "||" in last_sync:
            last_updated_at, last_id = last_sync.split("||", 1)
            try:
                last_id = int(last_id)
            except ValueError:
                last_id = 0
        else:
            last_updated_at = last_sync
            last_id = 0

        _logger.info("Streaming orders since cursor (%s, %s)…", last_updated_at, last_id)

        # preload driver map once per run
        driver_map = {d.yango_driver_id: d.id for d in Driver.search([('yango_driver_id', '!=', False)])}
        total = 0
        max_updated_at = last_updated_at
        max_id = last_id  # integer

        page_size = 1000
        max_pages = 5  # up to 5 pages per sync_orders call

        for page in range(max_pages):
            try:
                batch = self._fetch_orders_page(last_updated_at, str(last_id), page_size=page_size)
            except Exception:
                _logger.exception("Failed to fetch orders page after (%s, %s); aborting stream", last_updated_at, last_id)
                break

            if not batch:
                break

            # Upsert this batch
            self._upsert_orders(batch)
            batch_count = len(batch)
            total += batch_count
            _logger.info("Upserted %d orders (total so far: %d)", batch_count, total)

            # Advance cursor to last row (ordered by updated_at,id)
            last_row = batch[-1]
            last_updated_at = last_row.get('updated_at', last_updated_at)
            try:
                last_id = int(last_row.get('id', last_id))
            except (TypeError, ValueError):
                # fallback if id missing or non-int
                last_id = last_id

            # Update max markers with proper numeric comparison
            if (last_updated_at > max_updated_at) or (
                last_updated_at == max_updated_at and last_id > max_id
            ):
                max_updated_at = last_updated_at
                max_id = last_id

            # Persist cursor & commit immediately for durability
            new_cursor = f"{max_updated_at}||{max_id}"
            params.set_param('fleet_partner_dashboard.orders_cursor', new_cursor)
            try:
                self.env.cr.commit()
            except Exception:
                _logger.exception("Failed to commit after processing orders batch; continuing")

            if batch_count < page_size:
                break  # no more pages

            # prepare for next iteration: use updated last_updated_at and last_id

        final_cursor = f"{max_updated_at}||{max_id}"
        _logger.info("Finished streaming %d orders; new cursor (%s, %s)", total, max_updated_at, max_id)
        return final_cursor
    
    @api.model
    def _upsert_supply_hours(self, rows, from_queue=False):
        """Bulk upsert supply_hours via single SQL, deduping by (driver_id,date).
        Uses ONLY `supply_duration_seconds`.
        When from_queue=True, raise on problems so the retry job backs off.
        """
        _logger.info("Upserting %d supply_hours rows (bulk SQL + dedupe)…", len(rows))
        Driver = self.env['x_fleet_driver'].sudo()
        cr = self.env.cr

        # Preload driver map
        driver_map = {d.yango_driver_id: d.id for d in Driver.search([('yango_driver_id', '!=', False)])}

        upsert_map = {}  # (driver_id, date_str) -> seconds
        for rec in rows:
            yid  = rec.get('yango_driver_id')
            secs = rec.get('supply_duration_seconds')
            date_val = rec.get('date')

            if not yid:
                msg = "supply_hours: missing yango_driver_id"
                if from_queue:
                    raise ValueError(msg)
                self._queue_failed_row('supply_hours', rec, '<missing_yid>', msg)
                continue

            drv_id = driver_map.get(yid)
            if not drv_id:
                msg = f"supply_hours: missing driver {yid}"
                if from_queue:
                    raise ValueError(msg)
                self._queue_failed_row('supply_hours', rec, f"{yid}::<missing_date>", msg)
                _logger.warning("Queued supply_hours for later (driver %s missing)", yid)
                continue

            if not date_val:
                msg = "supply_hours: missing date"
                if from_queue:
                    raise ValueError(msg)
                self._queue_failed_row('supply_hours', rec, f"{yid}::<missing_date>", msg)
                continue

            # Normalize date to 'YYYY-MM-DD'
            if hasattr(date_val, 'strftime'):
                date_str = date_val.strftime('%Y-%m-%d')
            else:
                date_str = str(date_val)

            # seconds must be int-like
            try:
                secs = int(secs)
            except Exception:
                msg = f"supply_hours: invalid seconds {secs!r}"
                if from_queue:
                    raise ValueError(msg)
                self._queue_failed_row('supply_hours', rec, f"{yid}::{date_str}", msg)
                continue

            upsert_map[(drv_id, date_str)] = secs

        if not upsert_map:
            _logger.info("Nothing to upsert for supply_hours.")
            return

        # Build single bulk upsert SQL
        tuples = list(upsert_map.items())
        placeholders = ",".join(["(%s,%s,%s)"] * len(tuples))
        sql = f"""
            INSERT INTO x_fleet_driver_supply_hours (driver_id, date, seconds)
            VALUES {placeholders}
            ON CONFLICT (driver_id, date)
            DO UPDATE SET seconds = EXCLUDED.seconds
        """
        params = []
        for (drv_id, date_str), secs in tuples:
            params.extend([drv_id, date_str, secs])

        cr.execute(sql, params)
        _logger.info("Bulk-upserted %d unique supply_hours rows", len(tuples))

    @api.model
    def sync_issues(self, last_sync, rows=None, profile="dashboard"):
        """
        Sync from dashboard.issue_logs joined with dashboard.issues_list.
        Cursor uses issue_logs.updated_at.
        """
        if rows is None:
            try:
                rows = self._fetch_issue_logs(last_sync, page_size=1000, profile=profile)
            except HTTPError as e:
                # If the profile rejects embedding, fall back to default profile once
                if e.response.status_code == 406 and profile != "external_data_yango":
                    _logger.warning("Profile '%s' rejected for issue_logs; falling back to default profile", profile)
                    rows = self._fetch_issue_logs(last_sync, page_size=1000, profile="external_data_yango")
                else:
                    raise

        Issue  = self.env['x_fleet_issue'].sudo()
        Driver = self.env['x_fleet_driver'].sudo()

        for rec in rows:
            ext_id = rec.get('id')  # issue_logs.id (unique per log row)
            if not ext_id:
                self._queue_failed_row('issues', rec, '<missing>', 'missing issue_log id')
                continue

            # 👇 NEW: support both yango_driver_id (old) and external_id (new)
            yid = rec.get('yango_driver_id') or rec.get('external_id')

            if not yid:
                self._queue_failed_row(
                    'issues',
                    rec,
                    str(ext_id),
                    'missing external driver id (no yango_driver_id/external_id)'
                )
                continue

            drv = Driver.search([('yango_driver_id', '=', yid)], limit=1)
            if not drv:
                self._queue_failed_row('issues', rec, str(ext_id), f"missing driver {yid}")
                continue

            il = rec.get('issues_list') or {}
            raw = {
                'name':              ext_id,  # keep as text; unique enforced by SQL constraint
                'date_reported':     _normalize_datetime(rec['created_at']) if rec.get('created_at') else None,
                'issue_type':        il.get('issue_type'), # "support", "performance", "training"
                'main_category':     il.get('main_category'),
                'sub_category':      il.get('sub_category'),
                'sub_sub_category':  il.get('sub_sub_category'),
                'status':            rec.get('status'),     # 'unresolved' / 'resolved' expected
                'can_work':          rec.get('can_work'),   # boolean from issue_logs
                'driver_id':         drv.id,
                'note':              rec.get('note')
            }
            vals = {k: v for k, v in raw.items() if v is not None}

            try:
                exists = Issue.search([('name', '=', str(ext_id))], limit=1)
                if exists:
                    exists.with_context(skip_supabase_issue_push=True).write(vals)
                else:
                    vals['name'] = str(ext_id)
                    Issue.with_context(skip_supabase_issue_push=True).create(vals)
            except Exception as e:
                self._queue_failed_row('issues', rec, str(ext_id), str(e))

        # return rows so caller can compute last_sync (updated_at)
        return rows
    
    def _push_issue_state(self, issues):
        """
        Push x_fleet_issue fields -> dashboard.issue_logs:

        issue_logs.id          == int(issue.name)
        issue_logs.status      <- issue.status
        issue_logs.date_resolved <- issue.resolved_on
        issue_logs.can_work    <- issue.can_work
        """
        base_url, key = self._get_config()
        endpoint = f"{base_url}/rest/v1/issue_logs"
        headers = self._build_headers("dashboard")
        headers.update({
            "Content-Type":  "application/json",
            "Prefer":        "return=minimal",
        })

        issues = issues.sudo()
        total = len(issues)
        if not total:
            return

        ok = 0
        skipped = 0
        failed = 0

        _logger.debug("Pushing Supabase issue state for %d issues", total)

        for issue in issues:
            # Map Odoo Issue -> dashboard.issue_logs.id
            try:
                issue_log_id = int(issue.name)
            except (TypeError, ValueError):
                skipped += 1
                _logger.warning(
                    "Issue %s has non-numeric name %r; cannot map to issue_logs.id, skipping",
                    issue.id, issue.name,
                )
                continue

            payload = {
                "status":       issue.status,
                "can_work":     issue.can_work,
                # if resolved_on is falsy, send null to clear date_resolved
                "date_resolved": _to_supabase_iso(issue.resolved_on) if issue.resolved_on else None,
            }

            params = {"id": f"eq.{issue_log_id}"}
            try:
                resp = requests.patch(
                    endpoint,
                    headers=headers,
                    params=params,
                    json=payload,
                    timeout=10,
                )
                if resp.ok:
                    ok += 1
                else:
                    failed += 1
                    _logger.error(
                        "Supabase issue_logs update failed for issue %s (issue_logs.id=%s): %s %s",
                        issue.id, issue_log_id, resp.status_code, resp.text
                    )
            except Exception:
                failed += 1
                _logger.exception(
                    "Exception while pushing issue %s (issue_logs.id=%s) to Supabase",
                    issue.id, issue_log_id
                )

        _logger.debug(
            "Supabase issue_logs state push finished: total=%d, ok=%d, skipped=%d, failed=%d",
            total, ok, skipped, failed,
        )

    def _push_issue_messages(self, messages):
        """
        Push mail.message rows (for x_fleet_issue) -> dashboard.issue_messages.

        Requires Supabase table:
        - issue_log_id
        - created_at
        - author_name
        - author_email
        - body
        - message_type
        - subtype
        - odoo_message_id (unique)
        """
        if not messages:
            return

        base_url, key = self._get_config()
        endpoint = f"{base_url}/rest/v1/issue_messages"

        # use the same header builder so X-Tenant-Secret is sent when configured
        headers = self._build_headers("dashboard")
        headers.update({
            "Content-Type": "application/json",
            # merge on odoo_message_id when backfilling
            "Prefer": "return=minimal,resolution=merge-duplicates",
        })

        payloads = []
        for msg in messages.sudo():
            if msg.model != 'x_fleet_issue' or not msg.res_id:
                continue

            issue = self.env['x_fleet_issue'].sudo().browse(msg.res_id)
            if not issue.exists():
                continue

            # Map Odoo issue -> issue_logs.id
            try:
                issue_log_id = int(issue.name)
            except (TypeError, ValueError):
                _logger.warning("Cannot map issue %s to issue_logs.id for message %s", issue.id, msg.id)
                continue

            # Author
            author_name = msg.author_id.name or (msg.email_from or "Unknown")
            author_email = msg.email_from

            # 1) Try normal body
            body_html = msg.body or ""
            body_plain = tools.html2plaintext(body_html).strip()

            # 2) If body is empty, try to build from tracking values (field changes)
            if not body_plain and msg.tracking_value_ids:
                lines = []
                for tv in msg.tracking_value_ids:
                    # field_desc is the human label; fall back to field name
                    field_label = getattr(tv, "field_desc", None) or getattr(tv, "field", None) or _("Field")
                    field_type = getattr(tv, "field_type", None)

                    def _as_unset_or_str(val):
                        if val in (None, ""):
                            return _("Unset")
                        return str(val)

                    old_raw = new_raw = None

                    if field_type in ("char", "text", "html", "selection", "many2one"):
                        # Most “human” values (status, categories, M2O labels, etc.)
                        old_raw = tv.old_value_char or tv.old_value_text
                        new_raw = tv.new_value_char or tv.new_value_text

                    elif field_type in ("integer",):
                        old_raw = tv.old_value_integer
                        new_raw = tv.new_value_integer

                    elif field_type in ("float", "monetary"):
                        old_raw = tv.old_value_float or tv.old_value_monetary
                        new_raw = tv.new_value_float or tv.new_value_monetary

                    elif field_type in ("boolean",):
                        # Represent booleans as Yes/No instead of 0/1
                        old_int = tv.old_value_integer
                        new_int = tv.new_value_integer

                        def _bool_label(v):
                            if v in (None, ""):
                                return _("Unset")
                            try:
                                return _("Yes") if int(v) else _("No")
                            except Exception:
                                return str(v)

                        old_str = _bool_label(old_int)
                        new_str = _bool_label(new_int)
                        lines.append(f"{field_label}: {old_str} → {new_str}")
                        continue  # done with this tv

                    elif field_type in ("datetime", "date"):
                        old_dt = getattr(tv, "old_value_datetime", None)
                        new_dt = getattr(tv, "new_value_datetime", None)

                        if old_dt:
                            if field_type == "datetime":
                                old_raw = fields.Datetime.to_string(old_dt)
                            else:
                                old_raw = fields.Date.to_string(old_dt)
                        if new_dt:
                            if field_type == "datetime":
                                new_raw = fields.Datetime.to_string(new_dt)
                            else:
                                new_raw = fields.Date.to_string(new_dt)

                    else:
                        # Fallback: try everything in a reasonable order
                        old_raw = (
                            tv.old_value_char
                            or tv.old_value_text
                            or tv.old_value_monetary
                            or tv.old_value_integer
                            or tv.old_value_float
                        )
                        new_raw = (
                            tv.new_value_char
                            or tv.new_value_text
                            or tv.new_value_monetary
                            or tv.new_value_integer
                            or tv.new_value_float
                        )

                    old_str = _as_unset_or_str(old_raw)
                    new_str = _as_unset_or_str(new_raw)
                    lines.append(f"{field_label}: {old_str} → {new_str}")

                body_plain = "\n".join(lines).strip()

            # Still nothing? then it's genuinely uninteresting, skip it
            if not body_plain:
                continue

            vals = {
                "odoo_message_id": msg.id,
                "issue_log_id": issue_log_id,
                "created_at": _to_supabase_iso(msg.date) if msg.date else None,
                "author_name": author_name,
                "author_email": author_email,
                "body": body_plain,
                "message_type": msg.message_type,
                "subtype": msg.subtype_id and msg.subtype_id.description or None,
            }
            payloads.append({k: v for k, v in vals.items() if v is not None})

        # Chunk large payloads
        CHUNK = 200
        for i in range(0, len(payloads), CHUNK):
            chunk = payloads[i:i + CHUNK]
            try:
                resp = requests.post(
                    endpoint + "?on_conflict=odoo_message_id",
                    headers=headers,
                    json=chunk,
                    timeout=20,
                )
                if not resp.ok:
                    _logger.error(
                        "Failed to push issue_messages chunk %d-%d: %s %s",
                        i + 1, i + len(chunk), resp.status_code, resp.text
                    )
            except Exception:
                _logger.exception("Exception while pushing issue_messages chunk %d-%d", i + 1, i + len(chunk))

    @api.model
    def backfill_issue_states(self, limit=None, batch_size=200):
        """
        Backfill status / can_work / resolved_on from x_fleet_issue -> dashboard.issue_logs.

        - limit: total number of issues to process (None = all)
        - batch_size: number of issues per batch
        """
        Issue = self.env['x_fleet_issue'].sudo()
        processed = 0
        last_id = 0

        while True:
            this_batch_size = batch_size
            if limit is not None:
                remaining = limit - processed
                if remaining <= 0:
                    break
                this_batch_size = min(this_batch_size, max(remaining, 0))

            issues = Issue.search(
                [('id', '>', last_id)],
                order='id',
                limit=this_batch_size,
            )
            if not issues:
                break

            _logger.info(
                "Backfill issue states: pushing %d issues (ids %s..%s)",
                len(issues), issues[0].id, issues[-1].id
            )

            self._push_issue_state(issues)
            processed += len(issues)
            last_id = issues[-1].id

            self.env.cr.commit()

        _logger.info("Backfilled %d issue states to Supabase", processed)
        return processed
    
    @api.model
    def sync_issue_attachments(self, last_sync, rows=None, profile="dashboard"):
        """
        Upsert into x_issue_attachment. Link by Issue.name == issue_log_id.
        Cursor key: issue_attachments.created_at.
        """
        if rows is None:
            try:
                rows = self._fetch_issue_attachments(last_sync, page_size=1000, profile=profile)
            except HTTPError as e:
                # Fallback once if caller passed a non-working profile
                if e.response.status_code == 406 and profile != "dashboard":
                    _logger.warning("Profile '%s' rejected for issue_attachments; falling back to 'dashboard'", profile)
                    rows = self._fetch_issue_attachments(last_sync, page_size=1000, profile="dashboard")
                else:
                    raise

        Issue = self.env["x_fleet_issue"].sudo()
        Att   = self.env["x_issue_attachment"].sudo()

        for r in rows or []:
            try:
                issue_log_id = r.get("issue_log_id")
                att          = r.get("attachment") or {}
                ext_att_id   = att.get("id")
                key_full     = att.get("key") or ""
                created_at   = r.get("created_at")

                if not (issue_log_id and ext_att_id and key_full):
                    self._queue_failed_row("issue_attachments", r, str(ext_att_id or issue_log_id or "<missing>"), "missing essentials")
                    continue

                # Map to local Issue (you store issue_logs.id as x_fleet_issue.name)
                issue = Issue.search([("name", "=", str(issue_log_id))], limit=1)
                if not issue:
                    self._queue_failed_row("issue_attachments", r, str(ext_att_id), f"missing x_fleet_issue for log {issue_log_id}")
                    continue

                # Split "anda-media/attachments/..." into bucket + object path
                parts = key_full.split("/", 1)
                if len(parts) == 2 and parts[0]:
                    bucket = parts[0]
                    obj_key = parts[1]
                else:
                    bucket = "anda-media"
                    obj_key = key_full

                vals = {
                    "issue_id":          issue.id,
                    "ext_attachment_id": str(ext_att_id),
                    "bucket":            bucket,
                    "key":               obj_key,
                    "created_at":        _normalize_datetime(created_at) if created_at else False,
                }

                existing = Att.search([("ext_attachment_id", "=", str(ext_att_id))], limit=1)
                if existing:
                    existing.write(vals)
                else:
                    Att.create(vals)

            except Exception as e:
                self._queue_failed_row("issue_attachments", r, str(r.get("attachment", {}).get("id") or r.get("issue_log_id") or "<unknown>"), str(e))

        return rows
    
    def sync_supply_hours(self, cursor):
        """
        Stream supply_hours ordered by (updated_at, yango_driver_id, date),
        queue rows with missing drivers, bulk-upsert the rest,
        and return the new composite cursor "ts||yid||date".
        """
        params = self.env['ir.config_parameter'].sudo()
        Driver = self.env['x_fleet_driver'].sudo()

        # preload map once
        driver_map = {d.yango_driver_id: d.id
                    for d in Driver.search([('yango_driver_id', '!=', False)])}

        page_size = 1000
        max_pages = 5
        last_ts, last_yid, last_date = self._parse_sh_cursor(cursor)  # your helper

        total = 0
        for _ in range(max_pages):
            rows = self._fetch_supply_hours_page(last_ts, last_yid, last_date, page_size)  # ordered by ts,yid,date
            if not rows:
                break

            # split into queue vs upsertable
            upsert_rows = []
            queued = 0
            for rec in rows:
                yid = rec.get('yango_driver_id')
                dt  = rec.get('date')
                secs = rec.get('supply_duration_seconds')
                drv_id = driver_map.get(yid)
                if not (yid and dt and secs is not None):
                    continue
                if not drv_id:
                    # queue for later retry
                    self._queue_failed_row('supply_hours', rec, f"{yid}|{dt}")
                    queued += 1
                    continue
                upsert_rows.append(rec)

            if upsert_rows:
                self._upsert_supply_hours(upsert_rows, from_queue=False)  # bulk SQL on (driver_id,date)

            total += len(rows)
            if queued:
                _logger.warning("Queued %d supply_hours rows (missing drivers)", queued)

            # advance cursor to the last row actually fetched
            last = rows[-1]
            last_ts   = last['updated_at']
            last_yid  = last['yango_driver_id']
            last_date = last['date']
            params.set_param('fleet_partner_dashboard.supply_hours_cursor',
                            f"{last_ts}||{last_yid}||{last_date}")
            try:
                self.env.cr.commit()
            except Exception:
                _logger.exception("Commit failed after supply_hours batch; continuing")

            if len(rows) < page_size:
                break

        _logger.info("Streamed supply_hours rows: %d", total)
        return f"{last_ts}||{last_yid}||{last_date}"

    @api.model
    def sync_all(self):
        params = self.env['ir.config_parameter'].sudo()

        # 1) work_rules
        wr_cur = params.get_param('fleet_partner_dashboard.work_rules_cursor') or '1970-01-01T00:00:00Z'
        wr = self._fetch_table('work_rules', wr_cur)
        self._upsert_product_types(wr)
        if wr:
            params.set_param('fleet_partner_dashboard.work_rules_cursor',
                            max(r['updated_at'] for r in wr))
            self.env.cr.commit()

        # 2) drivers
        dr_cur = params.get_param('fleet_partner_dashboard.drivers_cursor') or wr_cur
        dr = self._fetch_table('drivers', dr_cur)
        work_map = {r['work_rule_id']: r['name'] for r in wr}
        self._upsert_drivers(dr, work_map)
        if dr:
            params.set_param('fleet_partner_dashboard.drivers_cursor',
                            max(r['updated_at'] for r in dr))
            self.env.cr.commit()

        # 3) orders (composite cursor)
        ord_cur = params.get_param('fleet_partner_dashboard.orders_cursor') or dr_cur
        new_ord_cur = self.sync_orders(ord_cur)
        params.set_param('fleet_partner_dashboard.orders_cursor', new_ord_cur)

        # 4) supply_hours
        sh_cur = params.get_param('fleet_partner_dashboard.supply_hours_cursor') or dr_cur
        new_sh_cur = self.sync_supply_hours(sh_cur)
        if new_sh_cur:
            params.set_param('fleet_partner_dashboard.supply_hours_cursor', new_sh_cur)
            self.env.cr.commit()

        # 5) issues
        is_cur = params.get_param('fleet_partner_dashboard.issues_cursor') or dr_cur
        is_rows = self.sync_issues(is_cur)
        if is_rows:
            # issue_logs are filtered by updated_at
            params.set_param('fleet_partner_dashboard.issues_cursor',
                            max(r['updated_at'] for r in is_rows))
            self.env.cr.commit()
            
        # 6) issue_attachments — cursor by created_at
        ia_cur = params.get_param('fleet_partner_dashboard.issue_attachments_cursor') or is_cur
        ia_rows = self.sync_issue_attachments(ia_cur)
        if ia_rows:
            params.set_param('fleet_partner_dashboard.issue_attachments_cursor',
                            max(r['created_at'] for r in ia_rows if r.get('created_at')))
            self.env.cr.commit()

        _logger.info("Full multi-cursor sync complete")
        
    @api.model
    def _retry_queued_rows(self, limit=200):
        Q = self.env['x_supabase_sync_queue'].sudo()
        now = fields.Datetime.now()
        items = Q.search([('state','=','queued'), ('next_attempt','<=', now)],
                        order='next_attempt,id', limit=limit)

        ok = fail = 0
        for q in items:
            rec = json.loads(q.raw_data or '{}')
            try:
                if q.table == 'orders':
                    self._upsert_orders([rec], from_queue=True)
                elif q.table == 'supply_hours':
                    self._upsert_supply_hours([rec], from_queue=True)
                elif q.table == 'drivers':
                    self._upsert_drivers([rec], {}, from_queue=True)
                elif q.table == 'issues':
                    # ensure your issues path won’t requeue when from_queue=True (raise instead)
                    self.sync_issues(None, rows=[rec])  # or make a variant that raises on failure
                elif q.table == 'issue_attachments':
                    self.sync_issue_attachments(None, rows=[rec])
                else:
                    raise ValueError(f"Unknown table {q.table}")

                # success → remove from queue
                q.unlink()
                ok += 1

            except Exception as e:
                # failure → keep & back off
                q.write({'error': str(e)[:1000]})
                q.set_retry_backoff()        # exponential delay until next attempt
                # optionally cap attempts to flip to 'failed'
                # if q.retries >= 10:
                #     q.state = 'failed'
                fail += 1

        _logger.info("DLQ retry run: processed=%d, ok=%d, failed(backoff)=%d", len(items), ok, fail)



