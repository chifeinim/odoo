from odoo import models, fields, api, _
from odoo.exceptions import UserError
from supabase import create_client
import logging, requests, datetime

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
        """Fetch all rows from data_for_odoo.<table> updated since last_sync."""
        base_url, key = self._get_config()
        endpoint = f"{base_url}/rest/v1/{table}" 
        headers = {
            "apikey":        key,
            "Authorization": f"Bearer {key}",
            "Content-Type":  "application/json",
            "Accept":          "application/json",
            "Accept-Profile":  "data_for_odoo",
            "Content-Profile": "data_for_odoo",
        }
        params = {
            "select": "*",
            "updated_at": f"gt.{ last_sync }",
            "order":      "updated_at.asc",
        }
        response = requests.get(endpoint, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        return response.json()  # a list of dicts
    
    @api.model
    def sync_drivers(self, last_sync):
        """Fetch and upsert driver rows updated since last_sync."""
        rows = self._fetch_table('drivers', last_sync)
        _logger.info("Syncing %d drivers", len(rows))

        ProductType = self.env['x_fleet_product_type'].sudo()
        Driver = self.env['x_fleet_driver'].sudo()

        for rec in rows:
            # build the core vals, dropping None so defaults apply
            raw_vals = {
                'name':            rec.get('name'),
                'phone':           rec.get('phone'),
                'hire_date':       _normalize_datetime(rec['hire_date'])      if rec.get('hire_date')     else None,
                'training_rating': rec.get('training_rating'),
                'work_status':     rec.get('work_status'),
                'type':            rec.get('type'),
            }
            vals = {k: v for k, v in raw_vals.items() if v is not None}

            # 1) ensure product_type exists (or create it)
            pt_name = rec.get('product_type_id')
            if pt_name:
                existing_pt = ProductType.search([('name', '=', pt_name)], limit=1)
                if existing_pt:
                    vals['product_type_id'] = existing_pt.id
                else:
                    new_pt = ProductType.create({'name': pt_name})
                    vals['product_type_id'] = new_pt.id

            # 2) upsert driver by its external ID
            ext_id = rec.get('yango_driver_id')
            if not ext_id:
                _logger.warning("Skipping driver without ID: %r", rec)
                continue

            existing_drv = Driver.search(
                [('yango_driver_id', '=', ext_id)], limit=1
            )
            if existing_drv:
                existing_drv.write(vals)
            else:
                vals['yango_driver_id'] = ext_id
                Driver.create(vals)

    @api.model
    def sync_product_types(self, last_sync):
        """Fetch product-type names and ensure each exists in Odoo."""
        rows = self._fetch_table('product_types', last_sync)
        _logger.info("Syncing %d product types", len(rows))
        ProductType = self.env['x_fleet_product_type'].sudo()

        for rec in rows:
            name = rec.get('name')
            if not name:
                _logger.warning("Skipping product type with no name: %r", rec)
                continue
            # If it already exists by name, skip
            existing_pt = ProductType.search([('name', '=', name)], limit=1)
            if existing_pt:
                _logger.debug("ProductType %r already exists, skipping", name)
                continue
            # Otherwise create it
            ProductType.create({'name': name})
            _logger.info("Created new ProductType %r", name)

    @api.model
    def sync_orders(self, last_sync):
        """Fetch and upsert orders, linking each to its driver by yango_driver_id."""
        rows = self._fetch_table('orders', last_sync)
        _logger.info("Syncing %d orders", len(rows))
        Order = self.env['x_fleet_order'].sudo()
        Driver = self.env['x_fleet_driver'].sudo()
        for rec in rows:
            # guard against empty order id
            name = rec.get('name')
            if not name:
                _logger.warning("Skipping order with no name: %r", rec)
                continue
            # find the driver record we synced earlier
            yango_driver_id = rec.get('yango_driver_id')
            existing_drv = Driver.search([('yango_driver_id','=', yango_driver_id)], limit=1)
            if not existing_drv:
                _logger.warning("Skipping order %s: no driver %s", rec.get('name'), yango_driver_id)
                continue
            raw_vals = {
                'order_date':               _normalize_datetime(rec['order_date'])      if rec.get('order_date')     else None,
                'interval_from':            _normalize_datetime(rec['interval_from'])   if rec.get('interval_from')  else None,
                'interval_to':              _normalize_datetime(rec['interval_to'])     if rec.get('interval_to')    else None,
                'status':                   rec.get('status'),
                'cancellation_description': rec.get('cancellation_description'),
                'pickup_address':           rec.get('pickup_address'),
                'price':                    rec.get('price'),
                'driver_id':                existing_drv.id,
                'yango_driver_id':          rec.get('yango_driver_id'),
                'driver_name':              rec.get('driver_name'),
                'pick_latitude':            rec.get('pick_latitude'),
                'pick_longitude':           rec.get('pick_longitude'),
                'events':                   rec.get('events'),
            }
            vals = {k: v for k, v in raw_vals.items() if v is not None}
            existing_ord = Order.search([('name','=', name)], limit=1)
            if existing_ord:
                existing_ord.write(vals)
                _logger.debug("Updated order %s", name)
            else:
                vals['name'] = name
                Order.create(vals)
                _logger.info("Created order %s", name)

    @api.model
    def sync_supply_hours(self, last_sync):
        """Fetch and upsert supply_hours by driver & date."""
        rows = self._fetch_table('supply_hours', last_sync)
        _logger.info("Syncing %d supply_hours rows", len(rows))

        SupplyHours = self.env['x_fleet_driver_supply_hours'].sudo()
        Driver     = self.env['x_fleet_driver'].sudo()

        for rec in rows:
            # 1) find the driver
            yango_driver_id = rec.get('yango_driver_id')
            driver = Driver.search([('yango_driver_id', '=', yango_driver_id)], limit=1)
            if not driver:
                _logger.warning("Skipping supply_hours: no driver for yango_driver_id %r", yango_driver_id)
                continue
            # 2) pull date & seconds
            date = rec.get('date')
            seconds  = rec.get('seconds')
            if not date or seconds is None:
                _logger.warning("Skipping supply_hours for driver %s: missing date or seconds: %r", yango_driver_id, rec)
                continue
            # 3) build vals, drop None so defaults can apply
            raw_vals = {
                'driver_id': driver.id,
                'date':      _normalize_datetime(rec['date'])     if rec.get('date')    else None,
                'seconds':   seconds,
            }
            vals = {k: v for k, v in raw_vals.items() if v is not None}
            # 4) upsert by driver + date
            existing = SupplyHours.search(
                [('driver_id', '=', driver.id),
                 ('date',      '=', date)],
                limit=1
            )
            if existing:
                existing.write(vals)
                _logger.debug(
                    "Updated supply_hours for driver %s on %s", 
                    yango_driver_id, date
                )
            else:
                SupplyHours.create(vals)
                _logger.info(
                    "Created supply_hours for driver %s on %s", 
                    yango_driver_id, date)

    @api.model
    def sync_issues(self, last_sync):
        """Fetch and upsert issue records, linking each to its driver by yango_driver_id."""
        rows = self._fetch_table('issues', last_sync)
        _logger.info("Syncing %d issue rows", len(rows))
        Issue  = self.env['x_fleet_issue'].sudo()
        Driver = self.env['x_fleet_driver'].sudo()
        for rec in rows:
            # 1) find the driver
            driver_ext_id = rec.get('driver_id')
            driver = Driver.search([('yango_driver_id', '=', driver_ext_id)], limit=1)
            if not driver:
                _logger.warning(
                    "Skipping issue %r: no driver for yango_driver_id %s",
                    rec.get('id'), driver_ext_id
                )
                continue
            # 2) build values, dropping None so defaults apply
            raw_vals = {
                'date_reported':   _normalize_datetime(rec['date_reported'])     if rec.get('date_reported')    else None,
                'main_category':   rec.get('main_category'),
                'sub_category':    rec.get('sub_category'),
                'sub_sub_category':rec.get('sub_sub_category'),
                'status':          rec.get('status'),
                'severity':        rec.get('severity'),
                'driver_id':       driver.id,
            }
            vals = {k: v for k, v in raw_vals.items() if v is not None}
            # 3) upsert by the external issue ID
            ext_issue_id = rec.get('id')
            if not ext_issue_id:
                _logger.warning("Skipping issue with no ID: %r", rec)
                continue

            existing_issue = Issue.search([('name', '=', ext_issue_id)], limit=1)
            if existing_issue:
                existing_issue.write(vals)
                _logger.debug("Updated issue %s", ext_issue_id)
            else:
                vals['name'] = ext_issue_id
                Issue.create(vals)
                _logger.info("Created issue %s", ext_issue_id)

    @api.model
    def sync_all(self):
        """Master method for the cron — calls each table’s sync in turn."""
        _logger.info("Starting full Supabase → Odoo sync")
        last = self.env['ir.config_parameter'].sudo().get_param('fleet_partner.last_sync')
        last_dt = fields.Datetime.to_datetime(last) if last else '1970-01-01T00:00:00Z'

        # call each sync, passing the timestamp of the last run
        self.sync_product_types(last_dt)
        self.sync_drivers(last_dt)
        self.sync_orders(last_dt)
        self.sync_supply_hours(last_dt)
        self.sync_issues(last_dt)

        # store the new “last_sync” timestamp
        now = fields.Datetime.now()
        self.env['ir.config_parameter'].sudo().set_param('fleet_partner.last_sync', now)
        _logger.info("Completed full sync at %s", now)
