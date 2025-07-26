from odoo import models, fields

class FleetIncidentSubsubcategory(models.Model):
    _name = 'x_fleet_incident_subsubcategory'
    _description = 'Issue Sub‑Sub‑Category'

    name = fields.Char(required=True)
    subcategory_id = fields.Many2one(
        'x_fleet_incident_subcategory', required=True, ondelete='cascade')
