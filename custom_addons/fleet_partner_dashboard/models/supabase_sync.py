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
        rows = client.table('drivers') \
                     .select('*') \
                     .gt('updated_at', last_sync) \
                     .order('updated_at', asc=True) \
                     .execute().data
        _logger.info("Syncing %d drivers", len(rows))
        Driver = self.env['fleet.driver'].sudo()
        for rec in rows:
            # rec is a dict, e.g. {'name': 'John Doe', 'yango_driver_id': 42, ...}
            vals = {
                'name':             rec['name'],
                'phone':            rec['phone'],
                'hire_date':        rec['hire_date'],
                'training_rating':  rec.get('training_rating'),
                'work_status':      rec['work_status'],
                'driver_type':      rec['type'],
                'product_type_id':  rec['product_type_id'],
            }
            # search for an existing driver by its external ID
            existing = Driver.search([('yango_driver_id','=', rec['yango_driver_id'])], limit=1)
            if existing:
                existing.write(vals)
            else:
                vals['yango_driver_id'] = rec['yango_driver_id']
                Driver.create(vals)

    @api.model
    def sync_product_types(self, last_sync):
        """Fetch and upsert product-type (work_rules) rows."""
        client = self._get_client()
        rows = client.table('product_types') \
                     .select('*') \
                     .gt('updated_at', last_sync) \
                     .order('updated_at', asc=True) \
                     .execute().data
        _logger.info("Syncing %d product types", len(rows))
        PT = self.env['fleet.product.type'].sudo()
        for rec in rows:
            vals = {
                'name': rec['name'],
            }
            existing = PT.search([('work_rule_id','=', rec['work_rule_id'])], limit=1)
            if existing:
                existing.write(vals)
            else:
                vals['work_rule_id'] = rec['work_rule_id']
                PT.create(vals)

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
        Order = self.env['fleet.order'].sudo()
        Driver = self.env['fleet.driver'].sudo()
        for rec in rows:
            # find the driver record we synced earlier
            drv = Driver.search([('yango_driver_id','=', rec['yango_driver_id'])], limit=1)
            if not drv:
                _logger.warning("Skipping order %s: no driver %s", rec['order_id'], rec['yango_driver_id'])
                continue
            vals = {
                'fleet_driver_id': drv.id,
                'pickup_location': rec['pickup_location'],
                'date_order':      rec['order_date'],
                # … map additional fields …
            }
            existing = Order.search([('yango_order_id','=', rec['order_id'])], limit=1)
            if existing:
                existing.write(vals)
            else:
                vals['yango_order_id'] = rec['order_id']
                Order.create(vals)

    @api.model
    def sync_supply_hours(self, last_sync):
        """Fetch and upsert supply_hours rows."""
        client = self._get_client()
        rows = client.table('supply_hours') \
                     .select('*') \
                     .gt('updated_at', last_sync) \
                     .order('updated_at', asc=True) \
                     .execute().data
        _logger.info("Syncing %d supply_hours", len(rows))
        SH = self.env['fleet.supply.hour'].sudo()
        for rec in rows:
            vals = {
                'start_time': rec['start_time'],
                'end_time':   rec['end_time'],
                # … map other fields …
            }
            existing = SH.search([('supply_hour_id','=', rec['supply_hour_id'])], limit=1)
            if existing:
                existing.write(vals)
            else:
                vals['supply_hour_id'] = rec['supply_hour_id']
                SH.create(vals)

    @api.model
    def sync_issues(self, last_sync):
        """Fetch and upsert issues rows."""
        client = self._get_client()
        rows = client.table('issues') \
                     .select('*') \
                     .gt('updated_at', last_sync) \
                     .order('updated_at', asc=True) \
                     .execute().data
        _logger.info("Syncing %d issues", len(rows))
        Issue = self.env['fleet.issue'].sudo()
        for rec in rows:
            vals = {
                'description': rec['description'],
                'severity':    rec['severity'],
                # … map other fields …
            }
            existing = Issue.search([('issue_id','=', rec['issue_id'])], limit=1)
            if existing:
                existing.write(vals)
            else:
                vals['issue_id'] = rec['issue_id']
                Issue.create(vals)

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
