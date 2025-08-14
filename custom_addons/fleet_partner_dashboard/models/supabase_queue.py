from odoo import models, fields, api
import json
from datetime import timedelta

class FleetSyncQueue(models.Model):
    _name = 'x_supabase_sync_queue'
    _description = 'Supabase → Odoo Dead-Letter Queue'
    _order = 'next_attempt asc, id asc'

    table = fields.Selection([
        ('orders', 'orders'),
        ('supply_hours', 'supply_hours'),
        ('drivers', 'drivers'),
        ('issues', 'issues'),
        ('work_rules', 'work_rules'),
    ], required=True, index=True)

    # a stable key so we don't enqueue duplicates (e.g. order_short_id, (yango_driver_id,date), etc.)
    record_key = fields.Char(required=True, index=True)
    raw_data   = fields.Text(required=True, help="JSON-serialized source row")
    error      = fields.Text()
    retries    = fields.Integer(default=0)
    next_attempt = fields.Datetime(default=lambda self: fields.Datetime.now(), index=True)
    state      = fields.Selection([('queued','Queued'), ('failed','Failed')], default='queued', index=True)

    _sql_constraints = [
        ('uniq_table_key', 'unique(table, record_key)', 'This record is already queued.'),
    ]

    # Helpers (optional but handy)
    def set_retry_backoff(self):
        """Exponential backoff up to 60 minutes."""
        self.ensure_one()
        self.retries += 1
        minutes = min(60, 2 ** self.retries)
        self.next_attempt = fields.Datetime.now() + timedelta(minutes=minutes)
