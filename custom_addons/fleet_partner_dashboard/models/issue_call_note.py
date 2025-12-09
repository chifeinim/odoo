# models/issue_call_note.py
from odoo import models, fields

ISSUE_STATUS_SELECTION = [
    ('unresolved', 'Not Started'),
    ('resolved', 'Resolved'),
    ('requires_follow_up_call', 'Requires Follow-Up Call'),
    ('invited_to_office', 'Invited To Office'),
    ('invited_to_workshop', 'Invited To Workshop'),
    ('unresponsive', 'Unresponsive'),
]


class FleetIssueCallNote(models.Model):
    _name = 'x_fleet_issue_call_note'
    _description = 'Issue Call Note'
    _order = 'created_at desc, id desc'

    issue_id = fields.Many2one(
        'x_fleet_issue',
        string='Issue',
        required=True,
        index=True,
        ondelete='cascade',
    )

    # Convenience: denormalized driver for easier search / stats
    driver_id = fields.Many2one(
        'x_fleet_driver',
        string='Driver',
        related='issue_id.driver_id',
        store=True,
        index=True,
    )

    created_at = fields.Datetime(
        string='Created At',
        default=lambda self: fields.Datetime.now(),
        required=True,
        readonly=True,
    )

    author_id = fields.Many2one(
        'res.users',
        string='Author',
        default=lambda self: self.env.user,
        readonly=True,
    )

    note = fields.Text(string='Note', required=True)

    status_from = fields.Selection(
        selection=ISSUE_STATUS_SELECTION,
        string='Previous Status',
    )

    status_to = fields.Selection(
        selection=ISSUE_STATUS_SELECTION,
        string='New Status',
    )
