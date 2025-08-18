# models/issue.py
from odoo import models, fields, api, _

class FleetIssue(models.Model):
    _name = 'x_fleet_issue'
    _description = 'Fleet Issue'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_reported desc'

    name = fields.Char(string="Issue ID", copy=False, default='New')
    date_reported = fields.Datetime(string="Reported On", default=fields.Datetime.now)
    driver_id = fields.Many2one('x_fleet_driver', string="Driver", required=True, ondelete='cascade')

    # free-form classification columns
    main_category    = fields.Char(string="Main Category", required=True, tracking=True)
    sub_category     = fields.Char(string="Sub-Category", tracking=True)
    sub_sub_category = fields.Char(string="Sub-Sub-Category", tracking=True)

    status   = fields.Selection([('unresolved','Unresolved'),('resolved','Resolved')],
                                default='unresolved', tracking=True)

    # NEW: boolean instead of selection
    can_work = fields.Boolean(string="Can Work", default=True, tracking=True)
    can_work_label = fields.Char(string="Can Work", compute="_compute_can_work_label")

    color = fields.Integer(compute='_compute_color', store=True)

    @api.model
    def create(self, vals):
        if vals.get('name','New') == 'New':
            vals['name'] = self.env['ir.sequence'].next_by_code('x_fleet_issue') or 'New'
        return super().create(vals)
    
    @api.model
    def _compute_can_work_label(self):
        for rec in self:
            rec.can_work_label = _("Yes") if rec.can_work else _("No")

    @api.depends('status','can_work')
    def _compute_color(self):
        for rec in self:
            if rec.status == 'resolved':
                rec.color = 3
            elif rec.can_work:
                rec.color = 1
            else:
                rec.color = 2

    _sql_constraints = [
        ('unique_issue_name', 'unique(name)', 'Each issue must have a unique Issue ID.')
    ]
