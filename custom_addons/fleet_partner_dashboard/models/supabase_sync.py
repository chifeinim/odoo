from odoo import models, fields, api, _
from odoo.exceptions import UserError
from supabase import create_client
import logging, requests, datetime, json

def _normalize_datetime(val):
    """Convert ISO8601 with offset into naive UTC 'YYYY‑MM‑DD HH:MM:SS'."""
    # Parse the full ISO string (including fractional seconds & offset)
    dt = datetime.datetime.fromisoformat(val)
    # Convert to UTC (if it had any tzinfo) and strip tzinfo
    if dt.tzinfo:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    # Format for Odoo
    return dt.strftime('%Y-%m-%d %H:%M:%S')

_logger = logging.getLogger(__name__)

class FleetPartnerSupabaseSync(models.AbstractModel):
    _name = 'x_fleet_partner_supabase_sync'
    _description = 'Sync data from Supabase by table'

    def _get_config(self):
        """Return (base_url, api_key) from ir.config_parameter."""
        params = self.env['ir.config_parameter'].sudo()
        url = params.get_param('supabase.url')
        key = params.get_param('supabase.publishable_key')
        if not url or not key:
            raise UserError(
                "Supabase URL or publishable key not found in ir.config_parameter."
            )
        return url.rstrip('/'), key

    def _get_client(self):
        params = self.env['ir.config_parameter'].sudo()
        url = params.get_param('supabase.url')
        key = params.get_param('supabase.publishable_key')
        if not url or not key:
            raise UserError(
                "Supabase URL or publishable key not found in ir.config_parameter."
            )
        return create_client(url, key)
    
    def _fetch_table(self, table, last_sync):
        """Fetch *all* rows from data_for_odoo.<table> updated since last_sync."""
        base_url, key = self._get_config()
        endpoint = f"{base_url}/rest/v1/{table}"
        headers = {
            "apikey":        key,
            "Authorization": f"Bearer {key}",
            "Accept":        "application/json",
            "Accept-Profile":  "data_for_odoo",
            "Content-Profile": "data_for_odoo",
            "Prefer":        "count=exact",
        }
        all_rows = []
        page_size = 1000   # max Supabase will return
        offset = 0

        while True:
            params = {
                "select":     "*",
                "updated_at": f"gt.{last_sync}",
                "order":      "updated_at.asc",
                "limit":      page_size,
                "offset":     offset,
            }
            _logger.info("Fetching %s rows %d→%d…", table, offset + 1, offset + page_size)
            resp = requests.get(endpoint, headers=headers, params=params, timeout=60)
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            all_rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size

        _logger.info("Fetched %d rows from %s", len(all_rows), table)
        return all_rows
    
    @api.model
    def sync_drivers(self, last_sync):
        """Fetch and upsert driver rows updated since last_sync."""
        rows = self._fetch_table('drivers', last_sync)
        _logger.info("Syncing %d drivers", len(rows))

        ProductType = self.env['x_fleet_product_type'].sudo()
        Driver      = self.env['x_fleet_driver'].sudo()

        new_vals = []
        for i, rec in enumerate(rows, start=1):
            # progress every 100 rows
            if i % 100 == 0:
                _logger.info("Processed %d/%d drivers…", i, len(rows))

            # build the core vals, dropping None so defaults apply
            raw_vals = {
                'name':            rec.get('name'),
                'phone':           rec.get('phone'),
                'hire_date':       _normalize_datetime(rec['hire_date']) if rec.get('hire_date') else None,
                'training_rating': rec.get('training_rating'),
                'work_status':     rec.get('work_status'),
                'type':            rec.get('type'),
            }
            vals = {k: v for k, v in raw_vals.items() if v is not None}

            # ensure product_type exists
            pt_name = rec.get('product_type_id')
            if pt_name:
                existing_pt = ProductType.search([('name','=',pt_name)], limit=1)
                if not existing_pt:
                    existing_pt = ProductType.create({'name': pt_name})
                vals['product_type_id'] = existing_pt.id

            # upsert by external ID
            ext_id = rec.get('yango_driver_id')
            if not ext_id:
                _logger.warning("Skipping driver without ID: %r", rec)
                continue

            existing_drv = Driver.search([('yango_driver_id','=',ext_id)], limit=1)
            if existing_drv:
                existing_drv.write(vals)
            else:
                # queue for bulk creation
                vals['yango_driver_id'] = ext_id
                new_vals.append(vals)

        # bulk create *all* the new ones in one SQL INSERT
        if new_vals:
            Driver.create(new_vals)
            _logger.info("Bulk‑created %d new drivers", len(new_vals))

    @api.model
    def sync_product_types(self, last_sync):
        """Fetch product‑type names and ensure each exists in Odoo."""
        rows = self._fetch_table('product_types', last_sync)
        _logger.info("Syncing %d product types", len(rows))

        ProductType = self.env['x_fleet_product_type'].sudo()
        new_pts = []

        for i, rec in enumerate(rows, start=1):
            if i % 50 == 0:
                _logger.info("Processed %d/%d product types…", i, len(rows))

            name = rec.get('name')
            if not name:
                _logger.warning("Skipping unnamed product type: %r", rec)
                continue

            exists = ProductType.search([('name','=',name)], limit=1)
            if exists:
                _logger.debug("ProductType %r exists, skipping", name)
            else:
                new_pts.append({'name': name})

        if new_pts:
            ProductType.create(new_pts)
            _logger.info("Bulk‑created %d new product types", len(new_pts))

    @api.model
    def sync_orders(self, last_sync):
        """Fetch and upsert orders, linking each to its driver."""
        rows = self._fetch_table('orders', last_sync)
        _logger.info("Fetched %d orders; starting upsert…", len(rows))

        Order   = self.env['x_fleet_order'].sudo()
        Driver  = self.env['x_fleet_driver'].sudo()

        # 1) build a driver_id map: {yango_driver_id → Odoo record id}
        driver_map = {
            drv.yango_driver_id: drv.id
            for drv in Driver.search([('yango_driver_id', '!=', False)])
        }
        _logger.info("Loaded %d drivers into memory", len(driver_map))

        # 2) load existing orders into a map: {name → record}
        existing_orders = {
            o.name: o for o in Order.search([])
        }
        _logger.info("Loaded %d existing orders into memory", len(existing_orders))

        new_orders = []
        for i, rec in enumerate(rows, start=1):
            if i % 500 == 0:
                _logger.info("Processed %d/%d orders…", i, len(rows))

            name = rec.get('name')
            if not name:
                _logger.warning("Skipping order without name: %r", rec)
                continue

            yid = rec.get('yango_driver_id')
            drv_id = driver_map.get(yid)
            if not drv_id:
                _logger.warning("No driver for order %s (yango_driver_id=%s)", name, yid)
                continue
            evs = rec.get('events')
            vals = {
                'order_date':               _normalize_datetime(rec['order_date'])      if rec.get('order_date')     else None,
                'interval_from':            _normalize_datetime(rec['interval_from'])   if rec.get('interval_from')  else None,
                'interval_to':              _normalize_datetime(rec['interval_to'])     if rec.get('interval_to')    else None,
                'status':                   rec.get('status'),
                'cancellation_description': rec.get('cancellation_description'),
                'pickup_address':           rec.get('pickup_address'),
                'price':                    rec.get('price'),
                'driver_id':                drv_id,
                'yango_driver_id':          yid,
                'driver_name':              rec.get('driver_name'),
                'pick_latitude':            rec.get('pick_latitude'),
                'pick_longitude':           rec.get('pick_longitude'),
                'events': json.dumps(evs) if evs is not None else None,
            }
            vals = {k: v for k, v in vals.items() if v is not None}

            if name in existing_orders:
                existing_orders[name].write(vals)
            else:
                vals['name'] = name
                new_orders.append(vals)

        if new_orders:
            Order.create(new_orders)
            _logger.info("Bulk-created %d new orders", len(new_orders))

        _logger.info("Finished syncing orders.")

    @api.model
    def sync_supply_hours(self, last_sync):
        """Fetch and upsert supply_hours by driver & date."""
        rows = self._fetch_table('supply_hours', last_sync)
        _logger.info("Syncing %d supply_hours rows", len(rows))

        SupplyHour = self.env['x_fleet_driver_supply_hours'].sudo()
        Driver     = self.env['x_fleet_driver'].sudo()
        new_sh     = []

        for i, rec in enumerate(rows, start=1):
            if i % 100 == 0:
                _logger.info("Processed %d/%d supply_hours…", i, len(rows))

            # lookup driver
            drv = Driver.search([('yango_driver_id','=',rec.get('yango_driver_id'))], limit=1)
            if not drv:
                _logger.warning("No driver for supply_hours row: %r", rec)
                continue

            date = rec.get('date')
            secs = rec.get('seconds')
            if not date or secs is None:
                _logger.warning("Incomplete supply_hours row: %r", rec)
                continue

            vals = {
                'driver_id': drv.id,
                'date':      _normalize_datetime(date),
                'seconds':   secs,
            }

            existing = SupplyHour.search([
                ('driver_id','=',drv.id),
                ('date','=',vals['date'])
            ], limit=1)

            if existing:
                existing.write({'seconds': secs})
            else:
                new_sh.append(vals)

        if new_sh:
            SupplyHour.create(new_sh)
            _logger.info("Bulk‑created %d new supply_hours rows", len(new_sh))

    @api.model
    def sync_issues(self, last_sync):
        """Fetch and upsert issue records, linking each to its driver."""
        rows = self._fetch_table('issues', last_sync)
        _logger.info("Syncing %d issue rows", len(rows))

        Issue  = self.env['x_fleet_issue'].sudo()
        Driver = self.env['x_fleet_driver'].sudo()
        new_issues = []

        for i, rec in enumerate(rows, start=1):
            if i % 50 == 0:
                _logger.info("Processed %d/%d issues…", i, len(rows))

            ext_id = rec.get('id')
            if not ext_id:
                _logger.warning("Skipping issue without ID: %r", rec)
                continue

            drv = Driver.search([('yango_driver_id','=',rec.get('driver_id'))], limit=1)
            if not drv:
                _logger.warning("No driver for issue %s", ext_id)
                continue

            vals = {
                'name':            ext_id,
                'date_reported':   _normalize_datetime(rec['date_reported']) if rec.get('date_reported') else None,
                'main_category':   rec.get('main_category'),
                'sub_category':    rec.get('sub_category'),
                'sub_sub_category':rec.get('sub_sub_category'),
                'status':          rec.get('status'),
                'severity':        rec.get('severity'),
                'driver_id':       drv.id,
            }
            # drop None so defaults take effect
            vals = {k:v for k,v in vals.items() if v is not None}

            existing = Issue.search([('name','=',ext_id)], limit=1)
            if existing:
                existing.write(vals)
            else:
                new_issues.append(vals)

        if new_issues:
            Issue.create(new_issues)
            _logger.info("Bulk‑created %d new issues", len(new_issues))
            
    def _upsert_product_types(self, rows):
        """Upsert a list of rows (dicts) into the fleet_product_type model."""
        _logger.info("Upserting %d product types", len(rows))
        ProductType = self.env['x_fleet_product_type'].sudo()
        new_pts = []
        for i, rec in enumerate(rows, start=1):
            # (optional) progress every 50
            if not rec.get('name'):
                _logger.warning("Skipping unnamed product type: %r", rec)
                continue
            name = rec['name']
            exists = ProductType.search([('name', '=', name)], limit=1)
            if exists:
                _logger.debug("ProductType %r already exists; skipping", name)
            else:
                new_pts.append({'name': name})
        if new_pts:
            ProductType.create(new_pts)
            _logger.info("Bulk‑created %d new product types", len(new_pts))
            
    def _upsert_drivers(self, rows):
        """Upsert a list of driver rows (dicts) into x_fleet_driver."""
        _logger.info("Upserting %d drivers", len(rows))
        ProductType = self.env['x_fleet_product_type'].sudo()
        Driver      = self.env['x_fleet_driver'].sudo()
        new_vals    = []

        for i, rec in enumerate(rows, start=1):
            if i % 100 == 0:
                _logger.info("Processed %d/%d drivers…", i, len(rows))

            # core fields
            raw = {
                'name':            rec.get('name'),
                'phone':           rec.get('phone'),
                'hire_date':       _normalize_datetime(rec['hire_date']) if rec.get('hire_date') else None,
                'training_rating': rec.get('training_rating'),
                'work_status':     rec.get('work_status'),
                'type':     rec.get('type'),
            }
            vals = {k: v for k, v in raw.items() if v is not None}

            # ensure product_type
            pt_name = rec.get('product_type_id')
            if pt_name:
                existing = ProductType.search([('name', '=', pt_name)], limit=1)
                if not existing:
                    existing = ProductType.create({'name': pt_name})
                vals['product_type_id'] = existing.id

            # external ID
            ext_id = rec.get('yango_driver_id')
            if not ext_id:
                _logger.warning("Skipping driver without ID: %r", rec)
                continue

            # update vs create
            existing_drv = Driver.search([('yango_driver_id', '=', ext_id)], limit=1)
            if existing_drv:
                existing_drv.write(vals)
            else:
                vals['yango_driver_id'] = ext_id
                new_vals.append(vals)

        if new_vals:
            Driver.create(new_vals)
            _logger.info("Bulk‑created %d new drivers", len(new_vals))


    def _upsert_orders(self, rows):
        """Upsert a list of order rows (dicts) into fleet.order."""
        _logger.info("Upserting %d orders", len(rows))
        Order  = self.env['x_fleet_order'].sudo()
        Driver = self.env['x_fleet_driver'].sudo()

        # preload maps
        driver_map = {
            d.yango_driver_id: d.id
            for d in Driver.search([('yango_driver_id', '!=', False)])
        }
        existing_orders = {o.name: o for o in Order.search([])}
        new_vals = []

        for i, rec in enumerate(rows, start=1):
            if i % 500 == 0:
                _logger.info("Processed %d/%d orders…", i, len(rows))

            name = rec.get('name')
            if not name:
                _logger.warning("Skipping order without name: %r", rec)
                continue

            drv_id = driver_map.get(rec.get('yango_driver_id'))
            if not drv_id:
                _logger.warning("No driver for order %s (yango_driver_id=%s)", name, rec.get('yango_driver_id'))
                continue
            evs = rec.get('events')
            raw = {
                'order_date':               _normalize_datetime(rec['order_date'])      if rec.get('order_date')     else None,
                'interval_from':            _normalize_datetime(rec['interval_from'])   if rec.get('interval_from')  else None,
                'interval_to':              _normalize_datetime(rec['interval_to'])     if rec.get('interval_to')    else None,
                'status':                   rec.get('status'),
                'cancellation_description': rec.get('cancellation_description'),
                'pickup_address':           rec.get('pickup_address'),
                'price':                    rec.get('price'),
                'driver_id':                drv_id,
                'yango_driver_id':          rec.get('yango_driver_id'),
                'driver_name':              rec.get('driver_name'),
                'pick_latitude':            rec.get('pick_latitude'),
                'pick_longitude':           rec.get('pick_longitude'),
                'events': json.dumps(evs) if evs is not None else None,
            }
            vals = {k: v for k, v in raw.items() if v is not None}

            if name in existing_orders:
                existing_orders[name].write(vals)
            else:
                vals['name'] = name
                new_vals.append(vals)

        if new_vals:
            Order.create(new_vals)
            _logger.info("Bulk‑created %d new orders", len(new_vals))


    def _upsert_supply_hours(self, rows):
        """Upsert a list of supply_hours rows (dicts) into x_fleet_driver_supply_hours."""
        _logger.info("Upserting %d supply_hours rows", len(rows))
        SupplyHour = self.env['x_fleet_driver_supply_hours'].sudo()
        Driver     = self.env['x_fleet_driver'].sudo()
        new_vals   = []

        for i, rec in enumerate(rows, start=1):
            if i % 100 == 0:
                _logger.info("Processed %d/%d supply_hours…", i, len(rows))

            yid = rec.get('yango_driver_id')
            drv = Driver.search([('yango_driver_id', '=', yid)], limit=1)
            if not drv:
                _logger.warning("No driver for supply_hours: %r", rec)
                continue

            date = rec.get('date')
            secs = rec.get('seconds')
            if not date or secs is None:
                _logger.warning("Incomplete supply_hours row: %r", rec)
                continue

            date_str = _normalize_datetime(date)
            vals = {
                'driver_id': drv.id,
                'date':      date_str,
                'seconds':   secs,
            }

            existing = SupplyHour.search([
                ('driver_id', '=', drv.id),
                ('date',      '=', date_str),
            ], limit=1)

            if existing:
                existing.write({'seconds': secs})
            else:
                new_vals.append(vals)

        if new_vals:
            SupplyHour.create(new_vals)
            _logger.info("Bulk‑created %d new supply_hours", len(new_vals))


    def _upsert_issues(self, rows):
        """Upsert a list of issue rows (dicts) into x_fleet_issue."""
        _logger.info("Upserting %d issues", len(rows))
        Issue  = self.env['x_fleet_issue'].sudo()
        Driver = self.env['x_fleet_driver'].sudo()
        new_vals = []

        for i, rec in enumerate(rows, start=1):
            if i % 50 == 0:
                _logger.info("Processed %d/%d issues…", i, len(rows))

            ext_id = rec.get('id')
            if not ext_id:
                _logger.warning("Skipping issue without ID: %r", rec)
                continue

            drv = Driver.search([('yango_driver_id', '=', rec.get('driver_id'))], limit=1)
            if not drv:
                _logger.warning("No driver for issue %s", ext_id)
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

            existing = Issue.search([('name', '=', ext_id)], limit=1)
            if existing:
                existing.write(vals)
            else:
                new_vals.append(vals)

        if new_vals:
            Issue.create(new_vals)
            _logger.info("Bulk‑created %d new issues", len(new_vals))

    @api.model
    def sync_all(self):
        """Master method for the cron — fetch + upsert each table, then record last_sync."""
        _logger.info("Starting full Supabase → Odoo sync")
        params = self.env['ir.config_parameter'].sudo()
        last = params.get_param('fleet_partner.last_sync')
        # if it exists, convert to ISO8601, otherwise start at Unix epoch
        #last_dt = last or '1970-01-01T00:00:00Z'
        last_dt = '1970-01-01T00:00:00Z'

        # fetch once per table
        pt_rows  = self._fetch_table('product_types', last_dt)
        drv_rows = self._fetch_table('drivers',       last_dt)
        ord_rows = self._fetch_table('orders',        last_dt)
        sh_rows  = self._fetch_table('supply_hours',  last_dt)
        iss_rows = self._fetch_table('issues',        last_dt)

        # upsert into Odoo
        self._upsert_product_types(pt_rows)
        self._upsert_drivers(drv_rows)
        self._upsert_orders(ord_rows)
        self._upsert_supply_hours(sh_rows)
        self._upsert_issues(iss_rows)

        # compute the maximum updated_at across all rows we just processed
        all_ts = []
        for rows in (pt_rows, drv_rows, ord_rows, sh_rows, iss_rows):
            all_ts.extend(r.get('updated_at') for r in rows if r.get('updated_at'))
        if all_ts:
            # pick the latest timestamp string
            max_ts = max(all_ts)
            params.set_param('fleet_partner.last_sync', max_ts)
            _logger.info("Recorded last_sync = %s", max_ts)
        else:
            _logger.info("No updated_at found; last_sync unchanged")

        _logger.info("Completed full sync")
