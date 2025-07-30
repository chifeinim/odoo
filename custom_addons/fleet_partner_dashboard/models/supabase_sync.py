from odoo import models, api, _
from odoo.exceptions import UserError
from supabase import create_client
import logging, requests, datetime, json

_logger = logging.getLogger(__name__)

def _normalize_datetime(val):
    """Convert ISO8601 with offset into naive UTC 'YYYY‑MM‑DD HH:MM:SS'."""
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
            "Accept-Profile":  "data_for_odoo",
            "Content-Profile": "data_for_odoo",
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
            "Accept-Profile":  "data_for_odoo",
            "Content-Profile": "data_for_odoo",
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

    @api.model
    def _upsert_product_types(self, rows):
        _logger.info("Upserting %d product types", len(rows))
        ProductType = self.env['x_fleet_product_type'].sudo()
        new_vals = []
        for rec in rows:
            name = rec.get('name')
            if not name:
                continue
            if not ProductType.search([('name','=',name)], limit=1):
                new_vals.append({'name': name})
        if new_vals:
            ProductType.create(new_vals)
            _logger.info("Bulk‑created %d new product types", len(new_vals))

    @api.model
    def _upsert_drivers(self, rows):
        _logger.info("Upserting %d drivers", len(rows))
        ProductType = self.env['x_fleet_product_type'].sudo()
        Driver      = self.env['x_fleet_driver'].sudo()
        new_vals    = []
        for rec in rows:
            raw = {
                'name':            rec.get('name'),
                'phone':           rec.get('phone'),
                'hire_date':       _normalize_datetime(rec['hire_date']) if rec.get('hire_date') else None,
                'training_rating': rec.get('training_rating'),
                'work_status':     rec.get('work_status'),
                'type':            rec.get('type'),
            }
            vals = {k: v for k, v in raw.items() if v is not None}
            pt_name = rec.get('product_type_id')
            if pt_name:
                pt = ProductType.search([('name','=',pt_name)], limit=1)
                if not pt:
                    pt = ProductType.create({'name': pt_name})
                vals['product_type_id'] = pt.id
            ext_id = rec.get('yango_driver_id')
            if not ext_id:
                continue
            drv = Driver.search([('yango_driver_id','=',ext_id)], limit=1)
            if drv:
                drv.write(vals)
            else:
                vals['yango_driver_id'] = ext_id
                new_vals.append(vals)
        if new_vals:
            Driver.create(new_vals)
            _logger.info("Bulk‑created %d new drivers", len(new_vals))

    @api.model
    def sync_product_types(self, last_sync):
        rows = self._fetch_table('product_types', last_sync)
        self._upsert_product_types(rows)

    @api.model
    def sync_drivers(self, last_sync):
        rows = self._fetch_table('drivers', last_sync)
        self._upsert_drivers(rows)

    @api.model
    def _upsert_orders(self, rows):
        _logger.info("Upserting %d orders", len(rows))
        Order  = self.env['x_fleet_order'].sudo()
        Driver = self.env['x_fleet_driver'].sudo()
        driver_map = {d.yango_driver_id: d.id for d in Driver.search([('yango_driver_id','!=',False)])}
        existing_orders = {o.name: o for o in Order.search([])}
        new_vals = []
        for rec in rows:
            name = rec.get('name')
            if not name:
                continue
            drv_id = driver_map.get(rec.get('yango_driver_id'))
            if not drv_id:
                continue
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
                'events':                   json.dumps(rec.get('events')) if rec.get('events') is not None else None,
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

    @api.model
    def sync_orders(self, last_sync):
        _logger.info("Streaming orders since %s…", last_sync)
        page_size = 1000
        offset    = 0
        total     = 0
        max_ts    = last_sync
        while True:
            batch = self._fetch_page('orders', last_sync, limit=page_size, offset=offset)
            if not batch:
                break
            self._upsert_orders(batch)
            total += len(batch)
            _logger.info("Upserted %d orders (total so far: %d)", len(batch), total)
            ts_list = [r['updated_at'] for r in batch if r.get('updated_at')]
            if ts_list:
                page_max = max(ts_list)
                if page_max > max_ts:
                    max_ts = page_max
            if len(batch) < page_size:
                break
            offset += page_size
        _logger.info("Finished streaming %d orders; latest updated_at %s", total, max_ts)
        return max_ts

    @api.model
    def _upsert_supply_hours(self, rows):
        """Bulk upsert supply_hours via single SQL, deduping by (driver_id,date)."""
        _logger.info("Upserting %d supply_hours rows (bulk SQL + dedupe)…", len(rows))
        Driver = self.env['x_fleet_driver'].sudo()
        cr     = self.env.cr

        # 1) Build a dict: key=(drv.id,date_str) → secs (last one wins)
        upsert_map = {}
        for rec in rows:
            yid  = rec.get('yango_driver_id')
            secs = rec.get('seconds')
            date = rec.get('date')
            if not (yid and date and secs is not None):
                _logger.warning("Skipping incomplete supply_hours: %r", rec)
                continue
            drv = Driver.search([('yango_driver_id','=',yid)], limit=1)
            if not drv:
                _logger.warning("No driver for supply_hours: %r", rec)
                continue
            date_str = _normalize_datetime(date)
            # override any previous entry so we only have one per key
            upsert_map[(drv.id, date_str)] = secs

        if not upsert_map:
            _logger.info("Nothing to upsert for supply_hours.")
            return

        # 2) Build VALUES placeholders and parameter list
        tuples = list(upsert_map.items())  # [ ((drv_id, date_str), secs), … ]
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

        # 3) Execute the one bulk upsert
        cr.execute(sql, params)
        _logger.info("Bulk-upserted %d unique supply_hours rows", len(tuples))

    @api.model
    def sync_issues(self, last_sync):
        rows = self._fetch_table('issues', last_sync)
        Issue  = self.env['x_fleet_issue'].sudo()
        Driver = self.env['x_fleet_driver'].sudo()
        new_vals = []
        for rec in rows:
            ext_id = rec.get('id')
            drv    = Driver.search([('yango_driver_id','=',rec.get('driver_id'))], limit=1)
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
            vals = {k:v for k,v in raw.items() if v is not None}
            exists = Issue.search([('name','=',ext_id)], limit=1)
            if exists:
                exists.write(vals)
            else:
                new_vals.append(vals)
        if new_vals:
            Issue.create(new_vals)
            _logger.info("Bulk‑created %d new issues", len(new_vals))

    @api.model
    def sync_all(self):
        _logger.info("Starting full Supabase → Odoo sync")
        params = self.env['ir.config_parameter'].sudo()
        last   =  '1970-01-01T00:00:00Z'
        #last   = params.get_param('fleet_partner.last_sync') or '1970-01-01T00:00:00Z'

        # support tables
        self.sync_product_types(last)
        self.sync_drivers(last)

        # orders streamed
        orders_max_ts = self.sync_orders(last)

        # supply hours & issues
        sh_rows  = self._fetch_table('supply_hours', last)
        self._upsert_supply_hours(sh_rows)
        self.sync_issues(last)

        # compute new last_sync
        pt_rows = self._fetch_table('product_types', last)
        dr_rows = self._fetch_table('drivers', last)
        is_rows = self._fetch_table('issues', last)
        all_ts = []
        all_ts.extend(r.get('updated_at') for r in pt_rows if r.get('updated_at'))
        all_ts.extend(r.get('updated_at') for r in dr_rows if r.get('updated_at'))
        all_ts.append(orders_max_ts)
        all_ts.extend(r.get('updated_at') for r in sh_rows if r.get('updated_at'))
        all_ts.extend(r.get('updated_at') for r in is_rows if r.get('updated_at'))
        new_last = max(all_ts)
        params.set_param('fleet_partner.last_sync', new_last)
        _logger.info("Recorded last_sync = %s", new_last)
        _logger.info("Completed full sync")
