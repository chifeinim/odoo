from odoo import models, fields

class FleetDriverIssueTag(models.Model):
    _name = 'x_fleet_driver_issue_tag'
    _description = 'Driver Issue Tag'

    name = fields.Char(string="Tag Name", required=True)
    color = fields.Integer(string="Color Index")
