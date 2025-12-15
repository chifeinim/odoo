# models/issue_call_note.py
from odoo import models, fields, api

ISSUE_STATUS_SELECTION = [
    ('unresolved', 'Not Started'),
    ('requires_follow_up_call', 'Requires Follow-Up Call'),
    ('invited_to_office', 'Invited To Office'),
    ('invited_to_workshop', 'Invited To Workshop'),
    ('resolved', 'Resolved'),
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

    @api.model
    def create(self, vals):
        rec = super().create(vals)
        if not self.env.context.get('skip_supabase_issue_call_note_push'):
            try:
                self.env['x_fleet_partner_supabase_sync']._push_issue_call_notes(rec)
            except Exception:
                _logger.exception("Failed to push issue_call_notes create to Supabase") # type: ignore
        return rec

    @api.model
    def backfill_issue_call_notes_to_supabase(self, batch_size=500):
        """
        Backfill existing x_fleet_issue_call_note rows into dashboard.issue_call_notes.

        Uses odoo_call_note_id unique constraint in Supabase to avoid duplicates.
        Safe to run multiple times.
        """
        CallNote = self.env['x_fleet_issue_call_note'].sudo()
        Sync = self.env['x_fleet_partner_supabase_sync'].sudo()

        last_id = 0
        while True:
            notes = CallNote.search(
                [('id', '>', last_id)],
                order='id',
                limit=batch_size,
            )
            if not notes:
                break

            Sync._push_issue_call_notes(notes)
            last_id = notes[-1].id

            # commit each batch
            self.env.cr.commit()

        return True
