from odoo import models, fields, api, _
import logging

_logger = logging.getLogger(__name__)


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
    enabled = fields.Boolean(default=True)

    min_days_since_hire = fields.Integer(default=21)
    churn_inactive_days = fields.Integer(default=60)
    active_hours_threshold = fields.Integer(default=0)
    active_trips_threshold = fields.Integer(default=0)

    product_line_ids = fields.One2many(
        "x_fleet_performance_issue_exception",
        "rule_id",
        string="Per-Product Exceptions",
    )

    _sql_constraints = [
        ("rule_code_uniq", "unique(code)", "Each rule code (new/active/churn) must be unique.")
    ]

    # -------- Supabase sync hooks --------

    @api.model
    def create(self, vals):
        rec = super().create(vals)
        if not self.env.context.get("skip_supabase_rule_push"):
            try:
                self.env["x_fleet_partner_supabase_sync"]._push_performance_issue_rules(rec)
            except Exception:
                _logger.exception("Failed to push performance_issue_rules create to Supabase")
        return rec

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get("skip_supabase_rule_push"):
            try:
                self.env["x_fleet_partner_supabase_sync"]._push_performance_issue_rules(self)
            except Exception:
                _logger.exception("Failed to push performance_issue_rules update to Supabase")
        return res

    def unlink(self):
        # push delete BEFORE actually removing local records, so we still have IDs
        if not self.env.context.get("skip_supabase_rule_push"):
            try:
                self.env["x_fleet_partner_supabase_sync"]._delete_performance_issue_rules(self)
            except Exception:
                _logger.exception("Failed to push performance_issue_rules delete to Supabase")
        return super().unlink()
