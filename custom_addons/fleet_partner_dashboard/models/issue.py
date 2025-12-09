# models/issue.py
import re
from odoo import models, fields, api, _

class FleetIssue(models.Model):
    _name = 'x_fleet_issue'
    _description = 'Fleet Issue'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_reported desc'

    name = fields.Char(string="Issue ID", copy=False, default='New')
    date_reported = fields.Datetime(string="Reported on", default=fields.Datetime.now)
    driver_id = fields.Many2one('x_fleet_driver', string="Driver", required=True, ondelete='cascade')

    # free-form classification columns
    issue_type       = fields.Char(string="Issue Type", tracking=True)
    main_category    = fields.Char(string="Main Category", required=True, tracking=True)
    sub_category     = fields.Char(string="Sub-Category", tracking=True)
    sub_sub_category = fields.Char(string="Sub-Sub-Category", tracking=True)
    issue_type_label = fields.Char(
        string="Issue Type", compute="_compute_category_labels",
        search="_search_issue_type_label")
    main_category_label = fields.Char(
        string="Main Category", compute="_compute_category_labels",
        search="_search_main_category_label")
    sub_category_label = fields.Char(
        string="Sub-Category", compute="_compute_category_labels",
        search="_search_sub_category_label")
    sub_sub_category_label = fields.Char(
        string="Sub-Sub-Category", compute="_compute_category_labels",
        search="_search_sub_sub_category_label")

    status   = fields.Selection([('unresolved','Not Started'),
                                 ('resolved','Resolved'),
                                 ('requires_follow_up_call','Requires Follow-Up Call'),
                                 ('invited_to_office','Invited To Office'),
                                 ('invited_to_workshop','Invited To Workshop'),
                                 ('unresponsive','Unresponsive')],
                                default='unresolved', tracking=True)

    # NEW: resolved timestamp
    resolved_on = fields.Datetime(string="Resolved on", readonly=True, copy=False, tracking=True)

    # NEW: boolean instead of selection
    can_work = fields.Boolean(string="Can Work", default=True, tracking=True)
    note = fields.Char(string="Reporter Note", tracking=True)
    can_work_label = fields.Char(string="Can Work", compute="_compute_can_work_label")
    
    ext_attachment_ids = fields.One2many('x_issue_attachment', 'issue_id', string='Media')

    color = fields.Integer(compute='_compute_color', store=True)

    @api.model
    def create(self, vals):
        if vals.get('name', 'New') == 'New':
            vals['name'] = self.env['ir.sequence'].next_by_code('x_fleet_issue') or 'New'
        rec = super().create(vals)
        # stamp resolution time once, if created already resolved
        if rec.status == 'resolved' and not rec.resolved_on:
            rec.resolved_on = fields.Datetime.now()

        # Push to Supabase unless we're in "import from Supabase" mode
        if not self.env.context.get('skip_supabase_issue_push'):
            rec.env['x_fleet_partner_supabase_sync']._push_issue_state(rec)

        return rec
    
    def write(self, vals):
        res = super().write(vals)

        # existing resolution timestamp logic
        if 'status' in vals:
            for rec in self:
                if rec.status == 'resolved' and not rec.resolved_on:
                    rec.resolved_on = fields.Datetime.now()
                elif rec.status == 'unresolved' and rec.resolved_on:
                    rec.resolved_on = False

        # If status / can_work / resolved_on changed, push to Supabase
        changed = {'status', 'can_work', 'resolved_on'} & set(vals.keys())
        if changed and not self.env.context.get('skip_supabase_issue_push'):
            self.env['x_fleet_partner_supabase_sync']._push_issue_state(self)

        return res
    
    def message_post(self, **kwargs):
        """
        Extend mail.thread.message_post to push chatter messages to Supabase.
        """
        message = super().message_post(**kwargs)  # this is a mail.message recordset (usually size 1)
        messages = message.sudo()

        # Filter to meaningful chatter items only
        messages = messages.filtered(
            lambda m: m.model == self._name and m.message_type in ('comment', 'notification')
        )

        if messages:
            self.env['x_fleet_partner_supabase_sync']._push_issue_messages(messages)

        return message
    
    @api.model
    def backfill_issue_messages_to_supabase(self, batch_size=500):
        """
        Backfill existing mail.message rows for x_fleet_issue into dashboard.issue_messages.

        Uses odoo_message_id unique constraint in Supabase to avoid duplicates.
        Run manually from shell or server action; safe to run multiple times.
        """
        Message = self.env['mail.message'].sudo()
        Sync = self.env['x_fleet_partner_supabase_sync'].sudo()

        last_id = 0
        while True:
            msgs = Message.search(
                [
                    ('model', '=', 'x_fleet_issue'),
                    ('id', '>', last_id),
                    ('message_type', 'in', ['comment', 'notification']),
                ],
                order='id',
                limit=batch_size,
            )
            if not msgs:
                break

            Sync._push_issue_messages(msgs)
            last_id = msgs[-1].id

            # Commit in batches so progress is saved
            self.env.cr.commit()

        return True

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
            rec.issue_type_label = self._humanize(rec.issue_type)
            rec.main_category_label = self._humanize(rec.main_category)
            rec.sub_category_label = self._humanize(rec.sub_category)
            rec.sub_sub_category_label = self._humanize(rec.sub_sub_category)
            
    def action_refresh_signed_urls(self):
        self.mapped('ext_attachment_ids').ensure_fresh_url() # type: ignore
        return True
    
    @api.model
    def _search_issue_type_label(self, operator, value):
        norm = self._slug(value)
        like = norm.replace('_', '%') if norm else norm
        return ['|', '|',
                ('issue_type', 'ilike', value),
                ('issue_type', 'ilike', norm or value),
                ('issue_type', 'ilike', like or value)]

    @api.model
    def _search_main_category_label(self, operator, value):
        norm = self._slug(value)
        like = norm.replace('_', '%') if norm else norm
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