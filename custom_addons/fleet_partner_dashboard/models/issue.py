from odoo import models, fields, api

class FleetIssue(models.Model):
    _name = 'x_fleet_issue'
    _description = 'Fleet Issue'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_reported desc'

    name = fields.Char(
        string="Issue ID",
        copy=False,
        readonly=True,
        default='New')
    date_reported = fields.Datetime(
        string="Reported On",
        default=fields.Datetime.now,
        readonly=True)
    driver_id = fields.Many2one(
        'x_fleet_driver',
        string="Driver",
        required=True,
        ondelete='cascade')

    category_id = fields.Many2one(
        'x_fleet_issue_category', string="Category", required=True,
        domain="[('parent_id','=', False)]",
        help="Top‑level category")
    subcategory_id = fields.Many2one(
        'x_fleet_issue_category', string="Sub‑Category",
        domain="[('parent_id','=', category_id)]",
        help="Child of the selected Category")
    subsub_id = fields.Many2one(
        'x_fleet_issue_category', string="Sub‑Sub‑Category",
        domain="[('parent_id','=', subcategory_id)]",
        help="Child of the selected Sub‑Category")

    status = fields.Selection([
        ('unresolved', 'Unresolved'),
        ('resolved',   'Resolved'),
    ], default='unresolved', tracking=True)
    severity = fields.Selection([
        ('can_work', "Can Work"),
        ('cant_work',"Can't Work"),
    ], default='can_work', tracking=True)

    # color: 1=yellow, 2=red, 3=green
    color = fields.Integer(compute='_compute_color', store=True)

    @api.model
    def create(self, vals):
        if vals.get('name', 'New') == 'New':
            vals['name'] = self.env['ir.sequence'].next_by_code('x_fleet_issue') or 'New'
        return super().create(vals)

    @api.depends('status', 'severity')
    def _compute_color(self):
        for rec in self:
            if rec.status == 'resolved':
                rec.color = 3
            elif rec.severity == 'can_work':
                rec.color = 1
            else:
                rec.color = 2
