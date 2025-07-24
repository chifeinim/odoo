from odoo import models, fields

class FleetOrder(models.Model):
    _name = 'x_fleet_order'
    _description = 'Fleet Order'

    name = fields.Char(string="Order ID", required=True) #order_id
    order_date = fields.Datetime(string="Order Date", default=fields.Datetime.now) #booked_at
    interval_from = fields.Datetime(string="Interval From") #interval_from
    interval_to   = fields.Datetime(string="Interval To") #interval_to
    status = fields.Selection([
        ('none', 'None'),
        ('driving', 'Driving'),
        ('waiting', 'Waiting'),
        ('transporting', 'Transporting'),
        ('complete', 'Complete'),
        ('cancelled', 'Cancelled'),
        ('calling', 'Calling'),
        ('expired', 'Expired'),
        ('failed', 'Failed')
    ], default='none', string="Status") #order_status
    cancellation_description = fields.Char(string="Cancellation Description") #cancellation_description
    pickup_address = fields.Char(string="Pickup Address") #pickup_address
    price = fields.Float(string="Price") #price, in Angolan Kwanzas (AOA)
    driver_id = fields.Many2one('x_fleet_driver', string="Driver", ondelete='cascade') #Contextual link to a driver
    yango_driver_id = fields.Char(string="Yango Driver ID") #driver_id
    driver_name = fields.Char(string="Driver Name") #driver_name
    pick_latitude = fields.Float(string="Pickup Latitude") #pickup_latitude
    pick_longitude = fields.Float(string="Pickup Longitude") #pickup_longitude
    events = fields.Text(
        string="Events",
        default='[]',
        help="JSON array of {event_at, order_status} objects"
    )
