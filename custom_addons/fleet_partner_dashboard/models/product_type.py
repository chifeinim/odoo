from odoo import models, fields

class FleetProductType(models.Model):
    _name = 'x_fleet_product_type'
    _description = 'Fleet Product Type'

    # NEW: external work rule id from Supabase
    work_rule_external_id = fields.Char(
        string="Work Rule ID",
        index=True,
        help="External work_rule_id from Supabase (external_data_yango.work_rules).",
    )

    name = fields.Char(string="Product Name", required=True)  # Anda Piloto Normal - 22k, etc
    description = fields.Text(string="Description")
    kpi_type = fields.Selection([
        ('avg_hours_online', 'Avg. Hours Online per day'),
        ('avg_trips_completed', 'Avg. Trips Completed per day'),
        ('avg_cash', 'Avg. Cash per day'),
        ('none', 'None'),
    ], default='none', string="KPI Type")
    lower_kpi = fields.Float(string="Lower KPI bound")
    upper_kpi = fields.Float(string="Upper KPI bound")
    active = fields.Boolean(default=True)
    driver_ids = fields.One2many(
        'x_fleet_driver', 'product_type_id', string="Drivers"
    )  # Contextual link to drivers

    _sql_constraints = [
        # Allow NULLs, but when set, work_rule_external_id must be unique
        ('work_rule_external_id_uniq',
         'unique(work_rule_external_id)',
         'Each Work Rule ID must be unique on Product Types.'),
    ]
