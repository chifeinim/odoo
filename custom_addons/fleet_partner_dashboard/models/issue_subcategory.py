from odoo import models, fields

class FleetIncidentSubcategory(models.Model):
    _name = 'x_fleet_incident_subcategory'
    _description = 'Issue Sub‑Category'

    name = fields.Char(required=True)
    category_id = fields.Many2one(
        'x_fleet_incident_category', required=True, ondelete='cascade')
    subsub_ids = fields.One2many(
        'x_fleet_incident_subsubcategory', 'subcategory_id', string="Sub‑Sub‑Categories")
