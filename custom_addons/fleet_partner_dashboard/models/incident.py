from odoo import models, fields, api

class FleetIncident(models.Model):
    _name = 'x_fleet_incident'
    _description = 'Fleet Incident'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(
        string="Incident ID", copy=False, readonly=True, default='New')
    date_reported = fields.Datetime(
        string="Incident Date", default=fields.Datetime.now, readonly=True)

    issue_ids = fields.One2many(
        'x_fleet_incident_issue', 'incident_id',
        string="Issues", copy=True, auto_join=True)

    status = fields.Selection([
        ('unresolved', 'Unresolved'),
        ('resolved', 'Resolved'),
    ], string="Status", compute='_compute_status', store=True, tracking=True)

    severity = fields.Selection([
        ('can_work', "Can Work"),
        ('cant_work', "Can't Work"),
    ], string="Severity", compute='_compute_severity', store=True, tracking=True)

    color = fields.Integer(
        compute='_compute_color', store=True,
        help="Record color based on severity")

    @api.model
    def create(self, vals):
        if vals.get('name', 'New') == 'New':
            vals['name'] = self.env['ir.sequence'].next_by_code('x_fleet_incident') or 'New'
        return super().create(vals)

    @api.depends('issue_ids.status')
    def _compute_status(self):
        for rec in self:
            rec.status = 'unresolved' if any(i.status == 'unresolved' for i in rec.issue_ids) else 'resolved'

    @api.depends('issue_ids.severity')
    def _compute_severity(self):
        for rec in self:
            rec.severity = 'cant_work' if any(i.severity == 'cant_work' for i in rec.issue_ids) else 'can_work'

    @api.depends('severity')
    def _compute_color(self):
        for rec in self:
            rec.color = 1 if rec.severity == 'can_work' else 2
