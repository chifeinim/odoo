from odoo import models, fields, api

class FleetDriverIssue(models.Model):
    _name = 'x_fleet_driver_issue'
    _description = 'Fleet Driver Issue'

    name = fields.Char(string="Issue ID", required=True, readonly=True, copy=False, default='New Issue')
    description = fields.Text(string="Description")
    date_reported = fields.Date(string="Date Reported", default=fields.Date.today)
    status = fields.Selection([
        ('open', 'Open'),
        ('resolved', 'Resolved')
    ], default='open', string="Status")
    driver_id = fields.Many2one('x_fleet_driver', string="Driver", ondelete='cascade') #Contextual link to driver
    tag_ids = fields.Many2many('x_fleet_driver_issue_tag', string="Tags") #Contextual link to a list of tags
    
    
    # Override create to assign sequence number
    @api.model
    def create(self, vals):
        if vals.get('name', 'New Issue') == 'New Issue':
            vals['name'] = self.env['ir.sequence'].next_by_code('fleet.driver.issue') or 'New Issue'
        return super().create(vals)
