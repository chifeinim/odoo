# fleet_partner_dashboard/models/supabase_sync.py
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from supabase import create_client
import logging

_logger = logging.getLogger(__name__)

class FleetPartnerSupabaseSync(models.AbstractModel):
    _name = 'fleet.partner.supabase_sync'
    _description = 'Sync data from Supabase by table'

    def _get_client(self):
        """Instantiate and return the Supabase client."""
        params = self.env['ir.config_parameter'].sudo()
        url = params.get_param('supabase.url')
        key = params.get_param('supabase.key')
        if not url or not key:
            raise UserError(_("Supabase URL/key not configured"))
        return create_client(url, key)

    @api.model
    def sync_drivers(self, last_sync):
        """Fetch and upsert driver rows updated since last_sync."""
        client = self._get_client()
        rows = (
            client.table('drivers')
                  .select('*')
                  .gt('updated_at', last_sync)
                  .order('updated_at', asc=True)
                  .execute()
                  .data
        )
        _logger.info("Syncing %d drivers", len(rows))

        ProductType = self.env['x_fleet_product_type'].sudo()
        Driver = self.env['x_fleet_driver'].sudo()

        for rec in rows:
            # build the core vals, dropping None so defaults apply
            raw_vals = {
                'name':            rec.get('name'),
                'phone':           rec.get('phone'),
                'hire_date':       rec.get('hire_date'),
                'training_rating': rec.get('training_rating'),
                'work_status':     rec.get('work_status'),
                'driver_type':     rec.get('type'),
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
        client = self._get_client()
        rows = (
            client.table('product_types')
                  .select('name')
                  .gt('updated_at', last_sync)
                  .order('updated_at', asc=True)
                  .execute()
                  .data
        )
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
        client = self._get_client()
        rows = client.table('orders') \
                     .select('*') \
                     .gt('updated_at', last_sync) \
                     .order('updated_at', asc=True) \
                     .execute().data
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
                'order_date':               rec.get('order_date'),
                'interval_from':            rec.get('interval_from'),
                'interval_to':              rec.get('interval_to'),
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
        client = self._get_client()
        rows = (
            client.table('supply_hours')
                  .select('*')
                  .gt('updated_at', last_sync)
                  .order('updated_at', asc=True)
                  .execute()
                  .data
        )
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
                'date':      date,
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
        client = self._get_client()
        rows = (
            client.table('issues')
                  .select('*')
                  .gt('updated_at', last_sync)
                  .order('updated_at', asc=True)
                  .execute()
                  .data
        )
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
                'date_reported':   rec.get('date_reported'),
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
