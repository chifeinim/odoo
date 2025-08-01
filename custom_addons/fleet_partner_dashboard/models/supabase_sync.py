from odoo import models, api, _
from odoo.exceptions import UserError
import logging, requests, datetime, json, urllib.parse

_logger = logging.getLogger(__name__)

def _normalize_datetime(val):
    """Convert ISO8601 with offset into naive UTC 'YYYY-MM-DD HH:MM:SS'."""
    dt = datetime.datetime.fromisoformat(val)
    if dt.tzinfo:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt.strftime('%Y-%m-%d %H:%M:%S')


class FleetPartnerSupabaseSync(models.AbstractModel):
    _name = 'x_fleet_partner_supabase_sync'
    _description = 'Sync data from Supabase by table'

    def _get_config(self):
        params = self.env['ir.config_parameter'].sudo()
        url = params.get_param('supabase.url')
        key = params.get_param('supabase.publishable_key')
        if not url or not key:
            raise UserError("Supabase URL or publishable key not found in ir.config_parameter.")
        return url.rstrip('/'), key

    def _fetch_table(self, table, last_sync, page_size=1000):
        base_url, key = self._get_config()
        endpoint = f"{base_url}/rest/v1/{table}"
        headers = {
            "apikey":        key,
            "Authorization": f"Bearer {key}",
            "Accept":        "application/json",
            "Accept-Profile":  "external_data_yango",
            "Content-Profile": "external_data_yango",
            "Prefer":        "count=exact",
        }
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

    def _fetch_page(self, table, last_sync, limit, offset):
        base_url, key = self._get_config()
        endpoint = f"{base_url}/rest/v1/{table}"
        headers = {
            "apikey":        key,
            "Authorization": f"Bearer {key}",
            "Accept":        "application/json",
            "Accept-Profile":  "external_data_yango",
            "Content-Profile": "external_data_yango",
            "Prefer":        "count=exact",
        }
        params = {
            "select":     "*",
            "updated_at": f"gt.{last_sync}",
            "order":      "updated_at.asc",
            "limit":      limit,
            "offset":     offset,
        }
        _logger.info("Fetching page %d→%d of %s…", offset+1, offset+limit, table)
        resp = requests.get(endpoint, headers=headers, params=params, timeout=60)
        resp.raise_for_status()
        return resp.json() or []

    def _fetch_orders_page(self, last_updated_at, last_id, page_size=5000):
        """
        Fetch next page of orders with composite cursor (updated_at, id).
        """
        base_url, key = self._get_config()
        endpoint = f"{base_url}/rest/v1/orders"
        headers = {
            "apikey":        key,
            "Authorization": f"Bearer {key}",
            "Accept":        "application/json",
            "Accept-Profile":  "external_data_yango",
            "Content-Profile": "external_data_yango",
            "Prefer":        "count=exact",
        }

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

    @api.model
    def _upsert_product_types(self, rows):
        _logger.info("Upserting %d product types", len(rows))
        ProductType = self.env['x_fleet_product_type'].sudo()
        new_vals = []
        for rec in rows:
            name = rec.get('name')
            if not name:
                continue
            if not ProductType.search([('name', '=', name)], limit=1):
                new_vals.append({'name': name})
        if new_vals:
            ProductType.create(new_vals)
            _logger.info("Bulk-created %d new product types", len(new_vals))

    @api.model
    def _upsert_drivers(self, rows, work_rule_map):
        _logger.info("Upserting %d drivers", len(rows))
        ProductType = self.env['x_fleet_product_type'].sudo()
        Driver = self.env['x_fleet_driver'].sudo()
        new_vals = []
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

            wr_id = rec.get('work_rule_id')
            if wr_id:
                pt_name = work_rule_map.get(wr_id)
                if pt_name:
                    if pt_name in pt_cache:
                        vals['product_type_id'] = pt_cache[pt_name]
                    else:
                        pt = ProductType.search([('name', '=', pt_name)], limit=1)
                        if not pt:
                            pt = ProductType.create({'name': pt_name})
                        vals['product_type_id'] = pt.id
                        pt_cache[pt_name] = pt.id

            ext_id = rec.get('yango_driver_id')
            if not ext_id:
                _logger.warning("Skipping driver without yango_driver_id: %r", rec)
                continue

            drv = Driver.search([('yango_driver_id', '=', ext_id)], limit=1)
            if drv:
                drv.write(vals)
            else:
                vals['yango_driver_id'] = ext_id
                new_vals.append(vals)

        if new_vals:
            Driver.create(new_vals)
            _logger.info("Bulk-created %d new drivers", len(new_vals))

    @api.model
    def sync_product_types(self, last_sync):
        rows = self._fetch_table('work_rules', last_sync)
        self._upsert_product_types(rows)

    @api.model
    def sync_drivers(self, last_sync):
        drv_rows = self._fetch_table('drivers', last_sync)
        wr_rows = self._fetch_table('work_rules', last_sync)
        work_rule_map = {
            wr.get('work_rule_id'): wr.get('name')
            for wr in wr_rows
            if wr.get('work_rule_id') and wr.get('name')
        }
        self._upsert_drivers(drv_rows, work_rule_map)

    @api.model
    def _upsert_orders(self, rows):
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
                continue  # should not happen
            drv_ext_id = rec.get('driver_id')
            drv_id = driver_map.get(drv_ext_id)
            if not drv_id:
                _logger.warning("No driver for order %s (driver_id=%s)", name, drv_ext_id)
                continue

            raw = {
                'order_date':               _normalize_datetime(rec['booked_at'])           if rec.get('booked_at')        else None,
                'interval_from':            _normalize_datetime(rec['interval_from'])      if rec.get('interval_from')     else None,
                'interval_to':              _normalize_datetime(rec['interval_to'])        if rec.get('interval_to')       else None,
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

            if name in existing_map:
                existing_map[name].write(vals)
            else:
                vals['name'] = name
                new_vals.append(vals)

        if new_vals:
            Order.create(new_vals)
            _logger.info("Bulk-created %d new orders", len(new_vals))


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

        # Reconstruct cursor
        if isinstance(last_sync, str) and "||" in last_sync:
            last_updated_at, last_id = last_sync.split("||", 1)
        else:
            last_updated_at = last_sync
            last_id = "0"

        _logger.info("Streaming orders since cursor (%s, %s)…", last_updated_at, last_id)

        # preload driver map once per run
        driver_map = {d.yango_driver_id: d.id for d in Driver.search([('yango_driver_id', '!=', False)])}
        total = 0
        max_updated_at = last_updated_at
        max_id = last_id

        page_size = 1000
        max_pages = 5  # up to 5 pages per sync_orders call

        for page in range(max_pages):
            try:
                batch = self._fetch_orders_page(last_updated_at, last_id, page_size=page_size)
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
            last_id = str(last_row.get('id', last_id))

            # Update max markers
            if last_updated_at > max_updated_at or (last_updated_at == max_updated_at and last_id > max_id):
                max_updated_at = last_updated_at
                max_id = last_id

            # Persist cursor & commit immediately for durability
            new_cursor = f"{max_updated_at}||{max_id}"
            params.set_param('fleet_partner.orders_cursor', new_cursor)
            try:
                self.env.cr.commit()
            except Exception:
                _logger.exception("Failed to commit after processing orders batch; continuing")

            if batch_count < page_size:
                break  # no more pages

            # loop continues with updated last_updated_at/last_id

        final_cursor = f"{max_updated_at}||{max_id}"
        _logger.info("Finished streaming %d orders; new cursor (%s, %s)", total, max_updated_at, max_id)
        return final_cursor

    @api.model
    def _upsert_supply_hours(self, rows):
        """Bulk upsert supply_hours via single SQL, deduping by (driver_id,date). Assumes chunked input."""
        _logger.info("Upserting %d supply_hours rows (bulk SQL + dedupe)…", len(rows))
        Driver = self.env['x_fleet_driver'].sudo()
        cr = self.env.cr

        upsert_map = {}
        for rec in rows:
            yid = rec.get('yango_driver_id')
            secs = rec.get('supply_duration_seconds')
            date = rec.get('date')
            if not (yid and date is not None and secs is not None):
                _logger.warning("Skipping incomplete supply_hours: %r", rec)
                continue
            drv = Driver.search([('yango_driver_id', '=', yid)], limit=1)
            if not drv:
                _logger.warning("No driver for supply_hours: %r", rec)
                continue
            if isinstance(date, str):
                date_str = date
            else:
                date_str = date.strftime('%Y-%m-%d')
            upsert_map[(drv.id, date_str)] = secs

        if not upsert_map:
            _logger.info("Nothing to upsert for supply_hours.")
            return

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
    def sync_issues(self, last_sync):
        rows = self._fetch_table('issues', last_sync)
        Issue = self.env['x_fleet_issue'].sudo()
        Driver = self.env['x_fleet_driver'].sudo()
        new_vals = []
        for rec in rows:
            ext_id = rec.get('id')
            drv = Driver.search([('yango_driver_id', '=', rec.get('yango_driver_id'))], limit=1)
            if not (ext_id and drv):
                continue
            raw = {
                'name':            ext_id,
                'date_reported':   _normalize_datetime(rec['date_reported']) if rec.get('date_reported') else None,
                'main_category':   rec.get('main_category'),
                'sub_category':    rec.get('sub_category'),
                'sub_sub_category':rec.get('sub_sub_category'),
                'status':          rec.get('status'),
                'severity':        rec.get('severity'),
                'driver_id':       drv.id,
            }
            vals = {k: v for k, v in raw.items() if v is not None}
            exists = Issue.search([('name', '=', ext_id)], limit=1)
            if exists:
                exists.write(vals)
            else:
                new_vals.append(vals)
        if new_vals:
            Issue.create(new_vals)
            _logger.info("Bulk-created %d new issues", len(new_vals))

    @api.model
    def sync_all(self):
        _logger.info("Starting full Supabase → Odoo sync")
        params = self.env['ir.config_parameter'].sudo()

        last_support = params.get_param('fleet_partner.last_sync') or '1970-01-01T00:00:00Z'
        orders_cursor = params.get_param('fleet_partner.orders_cursor') or last_support

        # 1) support tables: work_rules then drivers
        wr_rows = self._fetch_table('work_rules', last_support)
        dr_rows = self._fetch_table('drivers', last_support)
        self._upsert_product_types(wr_rows)
        work_rule_map = {
            wr.get('work_rule_id'): wr.get('name')
            for wr in wr_rows
            if wr.get('work_rule_id') and wr.get('name')
        }
        self._upsert_drivers(dr_rows, work_rule_map)

        # commit support tables so they’re durable before streaming orders
        try:
            self.env.cr.commit()
        except Exception:
            _logger.exception("Failed to commit after support table upserts; continuing")

        # 2) orders streamed with composite cursor
        new_orders_cursor = self.sync_orders(orders_cursor)
        if isinstance(new_orders_cursor, str) and "||" in new_orders_cursor:
            orders_updated_at, _ = new_orders_cursor.split("||", 1)
        else:
            orders_updated_at = new_orders_cursor

        # 3) supply_hours & issues (same support window)
        sh_rows = self._fetch_table('supply_hours', last_support)
        self._upsert_supply_hours(sh_rows)
        try:
            self.env.cr.commit()  # commit supply_hours separately
        except Exception:
            _logger.exception("Failed to commit after supply_hours upsert; continuing")

        is_rows = self._fetch_table('issues', last_support)
        self.sync_issues(last_support)
        try:
            self.env.cr.commit()  # commit issues separately
        except Exception:
            _logger.exception("Failed to commit after issues upsert; continuing")

        # 4) compute new last_support as the max updated_at seen among support tables and orders
        all_ts = []
        all_ts.extend(r.get('updated_at') for r in wr_rows if r.get('updated_at'))
        all_ts.extend(r.get('updated_at') for r in dr_rows if r.get('updated_at'))
        all_ts.extend(r.get('updated_at') for r in sh_rows if r.get('updated_at'))
        all_ts.extend(r.get('updated_at') for r in is_rows if r.get('updated_at'))
        if orders_updated_at:
            all_ts.append(orders_updated_at)
        if not all_ts:
            _logger.info("No timestamps found; nothing to update for last_sync.")
            return

        new_last_support = max(all_ts)

        # 5) persist support cursor (orders_cursor already written inside sync_orders)
        params.set_param('fleet_partner.last_sync', new_last_support)
        params.set_param('fleet_partner.orders_cursor', new_orders_cursor)
        _logger.info("Recorded last_sync = %s and orders_cursor = %s", new_last_support, new_orders_cursor)
        _logger.info("Completed full sync")


