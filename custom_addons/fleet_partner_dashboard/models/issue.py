# models/issue.py
from odoo import models, fields, api

class FleetIssue(models.Model):
    _name = 'x_fleet_issue'
    _description = 'Fleet Issue'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_reported desc'

    name = fields.Char(string="Issue ID", copy=False, default='New')
    date_reported = fields.Datetime(string="Reported On", default=fields.Datetime.now)
    driver_id = fields.Many2one('x_fleet_driver', string="Driver", required=True, ondelete='cascade')

    # free‑form classification columns
    main_category    = fields.Char(
        string="Main Category",
        required=True,
        tracking=True)
    sub_category     = fields.Char(
        string="Sub‑Category",
        tracking=True)
    sub_sub_category = fields.Char(
        string="Sub‑Sub‑Category",
        tracking=True)

    status   = fields.Selection([('unresolved','Unresolved'),('resolved','Resolved')],
                                default='unresolved', tracking=True)
    severity = fields.Selection([('can_work',"Can Work"),('cannot_work',"Cannot Work")],
                                default='can_work', tracking=True)

    color = fields.Integer(compute='_compute_color', store=True)

    @api.model
    def create(self, vals):
        if vals.get('name','New') == 'New':
            vals['name'] = self.env['ir.sequence'].next_by_code('x_fleet_issue') or 'New'
        return super().create(vals)

    @api.depends('status','severity')
    def _compute_color(self):
        for rec in self:
            if rec.status == 'resolved':
                rec.color = 3
            elif rec.severity == 'can_work':
                rec.color = 1
            else:
                rec.color = 2
