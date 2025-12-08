from odoo import models, fields, api, _
import logging

_logger = logging.getLogger(__name__)


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

    enabled = fields.Boolean(
        string="Enabled for this Product",
        default=True,
    )

    min_days_since_hire = fields.Integer(
        string="Override: Min days since hire (NEW)",
    )
    churn_inactive_days = fields.Integer(
        string="Override: No orders for last X days (CHURN)",
    )
    active_hours_threshold = fields.Integer(
        string="Override: Max hours online last week (ACTIVE)",
    )
    active_trips_threshold = fields.Integer(
        string="Override: Max trips completed last week (ACTIVE)",
    )

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
        """Same as before, but named for 'rules' concept; still used by sync."""
        self.ensure_one()
        r = self.rule_id

        def _or_default(override_value, default_value):
            return override_value or default_value

        return {
            "segment_code": r.code,
            "segment_enabled": bool(r.enabled and self.enabled),
            "work_rule_external_id": self.work_rule_external_id,
            "min_days_since_hire": _or_default(self.min_days_since_hire, r.min_days_since_hire),
            "churn_inactive_days": _or_default(self.churn_inactive_days, r.churn_inactive_days),
            "active_hours_threshold": _or_default(
                self.active_hours_threshold, r.active_hours_threshold
            ),
            "active_trips_threshold": _or_default(
                self.active_trips_threshold, r.active_trips_threshold
            ),
        }

    # -------- Supabase sync hooks --------

    @api.model
    def create(self, vals):
        rec = super().create(vals)
        if not self.env.context.get("skip_supabase_rule_exception_push"):
            try:
                self.env["x_fleet_partner_supabase_sync"]._push_performance_issue_exceptions(
                    rec, insert=True
                )
            except Exception:
                _logger.exception("Failed to push performance_issue_exceptions create to Supabase")
        return rec

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get("skip_supabase_rule_exception_push"):
            try:
                self.env["x_fleet_partner_supabase_sync"]._push_performance_issue_exceptions(
                    self, insert=False
                )
            except Exception:
                _logger.exception("Failed to push performance_issue_exceptions update to Supabase")
        return res

    def unlink(self):
        if not self.env.context.get("skip_supabase_rule_exception_push"):
            try:
                self.env["x_fleet_partner_supabase_sync"]._delete_performance_issue_exceptions(self)
            except Exception:
                _logger.exception("Failed to push performance_issue_exceptions delete to Supabase")
        return super().unlink()
