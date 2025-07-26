from odoo import models, fields, api, _

class FleetIncidentIssue(models.Model):
    _name = 'x_fleet_incident_issue'
    _description = 'Incident Issue'
    _inherit = ['mail.thread', 'mail.activity.mixin']
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

    @api.model_create_multi
    def create(self, vals_list):
        issues = super().create(vals_list)
        for issue in issues:
            # Log creation on the parent Incident
            issue.incident_id.message_post(
                body=_("New issue <b>%s</b> created with status <b>%s</b>") % (
                    issue.display_name, issue.status))
        return issues

    def write(self, vals):
        # Store old values
        old = {
            issue.id: (issue.status, issue.severity)
            for issue in self
        }
        result = super().write(vals)
        for issue in self:
            old_status, old_sev = old[issue.id]
            # If status changed, log it
            if 'status' in vals and issue.status != old_status:
                issue.incident_id.message_post(
                    body=_("Issue <b>%s</b> status changed from <b>%s</b> to <b>%s</b>") % (
                        issue.display_name, old_status, issue.status))
            # If severity changed, log it
            if 'severity' in vals and issue.severity != old_sev:
                issue.incident_id.message_post(
                    body=_("Issue <b>%s</b> severity changed from <b>%s</b> to <b>%s</b>") % (
                        issue.display_name, old_sev, issue.severity))
        return result
