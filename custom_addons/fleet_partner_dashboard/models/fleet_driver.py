from odoo import models, fields

class FleetDriver(models.Model):
    _name = 'x_fleet_driver'
    _description = 'Fleet Driver'

    name = fields.Char(string="Driver Name", required=True) #first_name + last_name
    phone = fields.Char(string="Phone") #phone_number
    id = fields.Char(string="Yango Driver ID") #yango_driver_id
    hire_date = fields.Date(string="Hire Date") #hire_date
    work_status = fields.Selection([
        ('not_working', 'Not Working'),
        ('fired', 'Fired'),
        ('working', 'Working'),
        ], default='working', string="Status") #work_status
    type = fields.Selection([
        ('new', 'New'),
        ('active', 'Active'),
        ('churn', 'Churn'),
        ('archive', 'Archive'),
        ('other', 'Other')
        ], default='other', string="Type") #driver_type, after cleaning to include other
    product_type_id = fields.Many2one('x_fleet_product_type', string="Product Type") # work_rule_id, after cleaning to main products + other
    issue_ids = fields.One2many('x_fleet_driver_issue', 'driver_id', string="Issues") #Contextual link to a list of issues
    order_ids = fields.One2many('x_fleet_order', 'driver_id', string="Orders") #Contextual link to a list of orders