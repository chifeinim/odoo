from odoo import models, fields


class FleetCar(models.Model):
    _name = "x_fleet_car"
    _description = "Fleet Car"

    name = fields.Char(string="Number", required=True)  # license plate / number
    car_id = fields.Char(string="Car ID", required=True, index=True)
    brand = fields.Char(string="Brand")
    model = fields.Char(string="Model")
    color = fields.Char(string="Color")
    year = fields.Integer(string="Year")
    vin = fields.Char(string="VIN")
    status = fields.Char(string="Status")
    driver_ids = fields.One2many("x_fleet_driver", "car_id", string="Drivers")

    _sql_constraints = [
        ("unique_car_id", "unique(car_id)", "Each car must have a unique Car ID."),
    ]
