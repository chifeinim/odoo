from odoo import models, fields, api

class FleetIncidentIssue(models.Model):
    _name = 'x_fleet_incident_issue'
    _description = 'Incident Issue'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    # Use the category as the “name” of each Issue record
    _rec_name = 'display_name'
    _order = 'id desc'

    display_name = fields.Char(
        string="Issue",
        compute='_compute_display_name',
        store=True)

    incident_id = fields.Many2one(
        'x_fleet_incident', string="Incident", required=True, ondelete='cascade')
    category_id = fields.Many2one(
        'x_fleet_incident_category', string="Category", required=True)
    subcategory_id = fields.Many2one(
        'x_fleet_incident_subcategory', string="Sub‑Category",
        domain="[('category_id','=', category_id)]")
    subsub_id = fields.Many2one(
        'x_fleet_incident_subsubcategory', string="Sub‑Sub‑Category",
        domain="[('subcategory_id','=', subcategory_id)]")

    description = fields.Text(string="Details")
    status = fields.Selection([
        ('unresolved', 'Unresolved'),
        ('resolved',   'Resolved'),
    ], default='unresolved', tracking=True)
    severity = fields.Selection([
        ('can_work', "Can Work"),
        ('cant_work',"Can't Work"),
    ], default='can_work', tracking=True)

    color = fields.Integer(
        compute='_compute_color', store=True)

    @api.depends('category_id','subcategory_id','subsub_id')
    def _compute_display_name(self):
        for rec in self:
            parts = filter(None, [
                rec.category_id.name,
                rec.subcategory_id.name,
                rec.subsub_id.name
            ])
            rec.display_name = " / ".join(parts)

    @api.depends('severity')
    def _compute_color(self):
        for rec in self:
            rec.color = 1 if rec.severity == 'can_work' else 2
