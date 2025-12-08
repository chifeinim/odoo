from odoo import models, fields


class FleetPerformanceIssueException(models.Model):
    _name = "x_fleet_performance_issue_exception"
    _description = "Performance Issue Exception per Product Type"

    rule_id = fields.Many2one(
        "x_fleet_performance_issue_rule",
        required=True,
        ondelete="cascade",
        string="Rule",
    )
    product_type_id = fields.Many2one(
        "x_fleet_product_type",
        required=True,
        ondelete="cascade",
        string="Product Type",
    )

    # Enable/disable this rule for this product_type
    enabled = fields.Boolean(
        string="Enabled for this Product",
        default=True,
        help="Uncheck to exclude this rule for this specific product type.",
    )

    # --------- OPTIONAL OVERRIDES (PER PRODUCT) ---------

    min_days_since_hire = fields.Integer(
        string="Override: Min days since hire (NEW)",
        help="If set, overrides the rule's min_days_since_hire for this product.",
    )

    churn_inactive_days = fields.Integer(
        string="Override: No orders for last X days (CHURN)",
        help="If set, overrides the rule's churn_inactive_days for this product.",
    )

    active_hours_threshold = fields.Integer(
        string="Override: Max hours online last week (ACTIVE)",
        help="If set, overrides the rule's active_hours_threshold for this product.",
    )

    active_trips_threshold = fields.Integer(
        string="Override: Max trips completed last week (ACTIVE)",
        help="If set, overrides the rule's active_trips_threshold for this product.",
    )

    # Convenience: expose work rule to Supabase
    work_rule_external_id = fields.Char(
        related="product_type_id.work_rule_external_id",
        store=True,
        readonly=True,
    )

    _sql_constraints = [
        (
            "rule_product_unique",
            "unique(rule_id, product_type_id)",
            "Rule/Product combination must be unique.",
        )
    ]

    def get_effective_config_dict(self):
        """
        Return a dict that Supabase can consume, resolving overrides
        vs rule defaults.
        """

        self.ensure_one()
        r = self.rule_id

        def _or_default(override_value, default_value):
            # Only treat non-zero / truthy overrides as real overrides.
            # Defaults already cover 0-based configs.
            return override_value or default_value

        return {
            "segment_code": r.code,  # still 'new' / 'active' / 'churn'
            "segment_enabled": bool(r.enabled and self.enabled),
            "work_rule_external_id": self.work_rule_external_id,
            "min_days_since_hire": _or_default(
                self.min_days_since_hire, r.min_days_since_hire
            ),
            "churn_inactive_days": _or_default(
                self.churn_inactive_days, r.churn_inactive_days
            ),
            "active_hours_threshold": _or_default(
                self.active_hours_threshold, r.active_hours_threshold
            ),
            "active_trips_threshold": _or_default(
                self.active_trips_threshold, r.active_trips_threshold
            ),
        }
