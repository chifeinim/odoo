from odoo import models, fields


class FleetCallCenterSegmentProduct(models.Model):
    _name = "x_fleet_cc_segment_product"
    _description = "Call Center Segment per Product Type"

    segment_id = fields.Many2one(
        "x_fleet_cc_segment",
        required=True,
        ondelete="cascade",
    )
    product_type_id = fields.Many2one(
        "x_fleet_product_type",
        required=True,
        ondelete="cascade",
    )

    # Enable/disable this driver_type for this product_type
    enabled = fields.Boolean(
        string="Enabled for this Product",
        default=True,
        help="Uncheck to exclude this segment for this specific product type.",
    )

    # --------- OPTIONAL OVERRIDES (PER PRODUCT) ---------
    # If left empty / 0-ish, you can treat them as 'use segment defaults'
    # when building the payload for Supabase.

    min_days_since_hire = fields.Integer(
        string="Override: Min days since hire (NEW)",
        help="If set, overrides the segment's min_days_since_hire for this product.",
    )

    churn_inactive_days = fields.Integer(
        string="Override: No orders for last X days (CHURN)",
        help="If set, overrides the segment's churn_inactive_days for this product.",
    )

    active_hours_threshold = fields.Integer(
        string="Override: Max hours online last week (ACTIVE)",
        help="If set, overrides the segment's active_hours_threshold for this product.",
    )

    active_trips_threshold = fields.Integer(
        string="Override: Max trips completed last week (ACTIVE)",
        help="If set, overrides the segment's active_trips_threshold for this product.",
    )

    # Convenience: expose work rule to Supabase
    work_rule_external_id = fields.Char(
        related="product_type_id.work_rule_external_id",
        store=True,
        readonly=True,
    )

    _sql_constraints = [
        (
            "segment_product_unique",
            "unique(segment_id, product_type_id)",
            "Segment/Product combination must be unique.",
        )
    ]

    def get_effective_config_dict(self):
        """
        Return a dict that Supabase can consume, resolving overrides
        vs segment defaults. We assume overrides are only used when
        you want a *different* value from the default, so a value of 0
        normally won't be used as an override (defaults already cover 0).
        """
        self.ensure_one()
        s = self.segment_id

        def _or_default(override_value, default_value):
            # Simple pattern: if an override is truthy, use it,
            # otherwise fall back to the segment default.
            # This means you typically only override with non-zero values.
            return override_value or default_value

        return {
            "segment_code": s.code,  # 'new' / 'active' / 'churn'
            "segment_enabled": bool(s.enabled and self.enabled),
            "work_rule_external_id": self.work_rule_external_id,
            # Effective thresholds with fallback
            "min_days_since_hire": _or_default(
                self.min_days_since_hire, s.min_days_since_hire
            ),
            "churn_inactive_days": _or_default(
                self.churn_inactive_days, s.churn_inactive_days
            ),
            "active_hours_threshold": _or_default(
                self.active_hours_threshold, s.active_hours_threshold
            ),
            "active_trips_threshold": _or_default(
                self.active_trips_threshold, s.active_trips_threshold
            ),
        }
