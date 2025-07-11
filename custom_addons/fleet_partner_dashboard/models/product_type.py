from odoo import models, fields

class FleetProductType(models.Model):
    _name = 'x_fleet_product_type'
    _description = 'Fleet Product Type'

    name = fields.Char(string="Product Name", required=True) #Anda Piloto Normal - 22k, etc
    description = fields.Text(string="Description")
    active = fields.Boolean(default=True)
    driver_ids = fields.One2many('x_fleet_driver', 'product_type_id', string="Drivers") #Contextual link to drivers
