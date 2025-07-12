from odoo import models, fields
from odoo.exceptions import UserError

class FleetDriverIssueTag(models.Model):
    _name = 'x_fleet_driver_issue_tag'
    _description = 'Driver Issue Tag'

    name = fields.Char(string="Tag Name", required=True)
    color = fields.Integer(string="Color Index")
    issue_ids = fields.Many2many('x_fleet_driver_issue', string="Issues", inverse_name='tag_ids')
    
    def unlink(self):
        for tag in self:
            if tag.issue_ids:
                raise UserError("You cannot delete a tag that is linked to issues.")
        return super().unlink()
