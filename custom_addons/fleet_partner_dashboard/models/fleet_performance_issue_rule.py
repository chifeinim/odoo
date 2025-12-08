from odoo import models, fields


class FleetPerformanceIssueRule(models.Model):
    _name = "x_fleet_performance_issue_rule"
    _description = "Performance Issue Rule"

    name = fields.Char(required=True)
    code = fields.Selection(
        [
            ("new", "New"),
            ("active", "Active"),
            ("churn", "Churn"),
        ],
        required=True,
        index=True,
        help="Logical type used by Supabase / edge functions.",
    )

    # Global enable/disable (for all products)
    enabled = fields.Boolean(
        string="Enabled",
        default=True,
        help="If unchecked, this rule is completely ignored.",
    )

    # ---------- DEFAULT RULE PARAMETERS (GLOBAL) ----------

    # NEW: hired more than X days ago
    min_days_since_hire = fields.Integer(
        string="Min days since hire (NEW)",
        default=21,
        help="Drivers hired more than this number of days ago are considered NEW.",
    )

    # CHURN: no orders completed for last X days
    churn_inactive_days = fields.Integer(
        string="No orders for last X days (CHURN)",
        default=60,
        help="Drivers with no completed orders for at least this many days are considered CHURN.",
    )

    # ACTIVE: integer thresholds instead of booleans
    active_hours_threshold = fields.Integer(
        string="Max hours online last week (ACTIVE)",
        default=0,
        help=(
            "If total hours online in the previous Monday–Sunday week are "
            "less than or equal to this value, the driver is considered for the ACTIVE rule. "
            "Use 0 to mean '0 hours online'."
        ),
    )

    active_trips_threshold = fields.Integer(
        string="Max trips completed last week (ACTIVE)",
        default=0,
        help=(
            "If total trips completed in the previous Monday–Sunday week are "
            "less than or equal to this value, the driver is considered for the ACTIVE rule. "
            "Use 0 to mean '0 trips'."
        ),
    )

    # Per-product overrides (exceptions)
    product_line_ids = fields.One2many(
        "x_fleet_performance_issue_exception",
        "rule_id",
        string="Per-Product Exceptions",
    )

    _sql_constraints = [
        (
            "rule_code_uniq",
            "unique(code)",
            "Each rule code (new/active/churn) must be unique.",
        )
    ]
