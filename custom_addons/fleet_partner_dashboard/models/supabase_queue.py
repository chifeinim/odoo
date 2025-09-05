from odoo import models, fields, api
from odoo.exceptions import UserError
from dateutil.relativedelta import relativedelta
from datetime import timedelta
import json, traceback

class FleetSyncQueue(models.Model):
    _name = 'x_supabase_sync_queue'
    _description = 'Supabase → Odoo Dead-Letter Queue'
    _order = 'next_attempt asc, id asc'

    table = fields.Selection([
        ('orders', 'orders'),
        ('supply_hours', 'supply_hours'),
        ('issues', 'issues'),
        ('issue_attachments', 'issue_attachments'),
    ], required=True, index=True)
    record_key    = fields.Char(required=True, index=True)
    raw_data      = fields.Text(required=True, help="JSON-serialized source row")
    error         = fields.Text()
    retries       = fields.Integer(default=0)
    next_attempt  = fields.Datetime(default=lambda self: fields.Datetime.now(), index=True)
    state = fields.Selection([
        ('queued', 'Queued'),
        ('processing', 'Processing'),
        ('failed', 'Failed'),
    ], default='queued', index=True)

    _sql_constraints = [
        ('uniq_table_key', 'unique(table, record_key)', 'This record is already queued.'),
    ]

    def set_retry_backoff(self, max_retries=10):
        """Exponential backoff: 2^retries minutes, capped at 60."""
        self.ensure_one()
        retries_val = int(self.retries) if isinstance(self.retries, int) else 0
        retries = retries_val + 1
        delay_min = min(60, 2 ** retries)
        self.write({
            'retries': retries,
            'next_attempt': fields.Datetime.now() + relativedelta(minutes=delay_min),
            'state': 'failed' if retries >= max_retries else 'queued',
        })
        
    @api.model
    def _retry_queued_rows(self, limit=200):
        Queue = self.env['x_supabase_sync_queue'].sudo()
        todo = Queue.search([
            ('state', '=', 'queued'),
            ('next_attempt', '<=', fields.Datetime.now()),
        ], limit=limit)
        for q in todo:
            q.action_retry_now()
        return True

    def action_retry_now(self):
        self.ensure_one()
        # Reuse the sync model’s logic for a single row
        self.env['x_fleet_partner_supabase_sync'].sudo()._retry_queued_rows(limit=1)
        # (Optionally, you could implement a dedicated _retry_one(self.id) on the sync model instead)
        return True
