# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from datetime import date, timedelta

class FleetDashboardController(http.Controller):

    @http.route('/fleet_partner_dashboard/data', type='json', auth='user')
    def dashboard_data(self):
        # Define your time windows and labels
        windows = [
            (7,   'Last 7 Days'),
            (30,  'Last Month'),
            (90,  'Last 3 Months'),
            (None,'All Time'),
        ]
        today = date.today()
        drivers = request.env['x_fleet_driver'].sudo().search([], order='name')

        result = []
        for drv in drivers:
            issues = drv.issue_ids.filtered('date_reported')
            # add phone into the row dict
            row = {
                'name': drv.name,
                'phone': drv.phone or '',
                'periods': [],
            }
            for days, label in windows:
                if days is not None:
                    cutoff = today - timedelta(days=days)
                    subset = issues.filtered(lambda i: i.date_reported >= cutoff)
                else:
                    subset = issues
                tags = subset.mapped('tag_ids.name')
                row['periods'].append({
                    'label': label,
                    'tags': tags,
                })
            result.append(row)

        return {
            'windows': [lbl for _, lbl in windows],
            'drivers': result,
        }

    @http.route('/fleet_partner_dashboard', type='http', auth='user')
    def dashboard(self, **kw):
        return request.render('fleet_partner_dashboard.dashboard_template')
