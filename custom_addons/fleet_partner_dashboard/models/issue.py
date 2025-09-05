# models/issue.py
import re
from odoo import models, fields, api, _

class FleetIssue(models.Model):
    _name = 'x_fleet_issue'
    _description = 'Fleet Issue'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_reported desc'

    name = fields.Char(string="Issue ID", copy=False, default='New')
    date_reported = fields.Datetime(string="Reported On", default=fields.Datetime.now)
    driver_id = fields.Many2one('x_fleet_driver', string="Driver", required=True, ondelete='cascade')

    # free-form classification columns
    main_category    = fields.Char(string="Main Category", required=True, tracking=True)
    sub_category     = fields.Char(string="Sub-Category", tracking=True)
    sub_sub_category = fields.Char(string="Sub-Sub-Category", tracking=True)
    main_category_label = fields.Char(
        string="Main Category", compute="_compute_category_labels",
        search="_search_main_category_label")
    sub_category_label = fields.Char(
        string="Sub-Category", compute="_compute_category_labels",
        search="_search_sub_category_label")
    sub_sub_category_label = fields.Char(
        string="Sub-Sub-Category", compute="_compute_category_labels",
        search="_search_sub_sub_category_label")

    status   = fields.Selection([('unresolved','Unresolved'),('resolved','Resolved')],
                                default='unresolved', tracking=True)

    # NEW: boolean instead of selection
    can_work = fields.Boolean(string="Can Work", default=True, tracking=True)
    can_work_label = fields.Char(string="Can Work", compute="_compute_can_work_label")
    
    ext_attachment_ids = fields.One2many('x_issue_attachment', 'issue_id', string='Media')

    color = fields.Integer(compute='_compute_color', store=True)

    @api.model
    def create(self, vals):
        if vals.get('name','New') == 'New':
            vals['name'] = self.env['ir.sequence'].next_by_code('x_fleet_issue') or 'New'
        return super().create(vals)
    
    @api.model
    def _compute_can_work_label(self):
        for rec in self:
            rec.can_work_label = _("Yes") if rec.can_work else _("No")

    @api.depends('status','can_work')
    def _compute_color(self):
        for rec in self:
            if rec.status == 'resolved':
                rec.color = 3
            elif rec.can_work:
                rec.color = 1
            else:
                rec.color = 2
                
    def _humanize(self, s):
        return s.replace('_', ' ').strip().title() if s else False

    def _slug(self, v):
        if not v:
            return v
        v = v.lower().strip()
        v = re.sub(r'\s+', '_', v)           # spaces -> underscores
        v = re.sub(r'[^0-9a-z_]+', '', v)    # drop punctuation
        return v

    def _compute_category_labels(self):
        for rec in self:
            rec.main_category_label = self._humanize(rec.main_category)
            rec.sub_category_label = self._humanize(rec.sub_category)
            rec.sub_sub_category_label = self._humanize(rec.sub_sub_category)
            
    def action_refresh_signed_urls(self):
        # refresh all linked attachments in one go
        self.mapped('ext_attachment_ids').ensure_fresh_url() # type: ignore
        return True  # optional, nice for object buttons

    # Make searches on the *_label fields hit the stored snake_case fields
    @api.model
    def _search_main_category_label(self, operator, value):
        norm = self._slug(value)
        like = norm.replace('_', '%') if norm else norm
        # OR: raw ilike, normalized ilike, and wildcarded normalized
        return ['|', '|',
                ('main_category', 'ilike', value),
                ('main_category', 'ilike', norm or value),
                ('main_category', 'ilike', like or value)]

    @api.model
    def _search_sub_category_label(self, operator, value):
        norm = self._slug(value); like = norm.replace('_', '%') if norm else norm
        return ['|', '|',
                ('sub_category', 'ilike', value),
                ('sub_category', 'ilike', norm or value),
                ('sub_category', 'ilike', like or value)]

    @api.model
    def _search_sub_sub_category_label(self, operator, value):
        norm = self._slug(value); like = norm.replace('_', '%') if norm else norm
        return ['|', '|',
                ('sub_sub_category', 'ilike', value),
                ('sub_sub_category', 'ilike', norm or value),
                ('sub_sub_category', 'ilike', like or value)]

    _sql_constraints = [
        ('unique_issue_name', 'unique(name)', 'Each issue must have a unique Issue ID.')
    ]

