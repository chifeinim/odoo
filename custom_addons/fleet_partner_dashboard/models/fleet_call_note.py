from odoo import models, fields, api

class FleetCallNote(models.Model):
    _name = 'x_fleet_call_note'
    _description = 'Driver Call Note'
    _order = 'created_at desc, id desc'

    driver_id = fields.Many2one(
        'x_fleet_driver',
        string='Driver',
        required=True,
        index=True,
        ondelete='cascade',
    )
    created_at = fields.Datetime(
        string='Created At',
        default=lambda self: fields.Datetime.now(),
        required=True,
        readonly=True,
    )
    note = fields.Text(string='Note', required=True)
    author_id = fields.Many2one(
        'res.users', string='Author',
        default=lambda self: self.env.user, readonly=True)
