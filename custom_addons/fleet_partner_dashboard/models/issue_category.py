# models/issue_category.py
from odoo import models, fields

class FleetIssueCategory(models.Model):
    _name = 'x_fleet_issue_category'
    _description = 'Issue Category'
    _parent_name = 'parent_id'
    _parent_store = True
    _order = 'parent_left, name'

    name = fields.Char('Name', required=True)
    parent_id = fields.Many2one(
        'x_fleet_issue_category', 'Parent Category',
        ondelete='cascade', index=True)
    child_ids = fields.One2many(
        'x_fleet_issue_category', 'parent_id', 'Sub‐Categories')
    parent_left  = fields.Integer('Left',   index=True)
    parent_right = fields.Integer('Right',  index=True)
    parent_path  = fields.Char(   'Path',   index=True)
