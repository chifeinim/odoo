from odoo import models, fields

class FleetIncidentCategory(models.Model):
    _name = 'x_fleet_incident_category'
    _description = 'Issue Category'

    name = fields.Char(required=True)
    subcategory_ids = fields.One2many(
        'x_fleet_incident_subcategory', 'category_id', string="Sub‑Categories")
