# models/driver_supply_hours.py
from odoo import models, fields

class DriverSupplyHours(models.Model):
    _name = 'x_fleet_driver_supply_hours'
    _description = 'Fleet Driver Supply Hours'

    driver_id = fields.Many2one('x_fleet_driver',string='Driver',required=True,ondelete='cascade')
    date = fields.Date(string='Date',required=True,default=fields.Date.context_today)
    seconds = fields.Integer(string='Online Seconds',required=True,default=0)

    _sql_constraints = [
        ('unique_driver_date',
         'unique(driver_id, date)',
         'Each driver can have only one record per date.'),
    ]
