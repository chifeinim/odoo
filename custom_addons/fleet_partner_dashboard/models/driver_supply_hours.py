# models/driver_supply_hours.py
from odoo import models, fields, api
from typing import Optional, cast

class DriverSupplyHours(models.Model):
    _name = 'x_fleet_driver_supply_hours'
    _description = 'Fleet Driver Supply Hours'

    driver_id = fields.Many2one('x_fleet_driver',string='Driver',required=True,ondelete='cascade')
    date = fields.Date(string='Date',required=True,default=fields.Date.context_today)
    seconds = fields.Integer(string='Online Seconds',required=True,default=0)
    hours = fields.Float(string='Hours',compute='_compute_hours',readonly=True)
    
    @api.depends('seconds')
    def _compute_hours(self):
        for rec in self:
            secs_opt = cast(Optional[int], getattr(rec, 'seconds', None))
            secs = secs_opt if isinstance(secs_opt, int) else 0
            rec.hours = secs / 3600.0

    _sql_constraints = [
        ('unique_driver_date',
         'unique(driver_id, date)',
         'Each driver can have only one record per date.'),
    ]
