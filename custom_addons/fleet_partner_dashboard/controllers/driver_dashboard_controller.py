## `controllers/driver_dashboard_controller.py`

# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from datetime import date
from .helpers import (
    humanize, last_week_range, last_month_range, last_n_months_range,
)

class DriverDashboardController(http.Controller):

    @http.route('/fleet_partner_dashboard/data', type='json', auth='user')
    def dashboard_data(self):
        today = date.today()
        Issue = request.env['x_fleet_issue'].sudo()
        Driver = request.env['x_fleet_driver'].sudo()
        drivers = Driver.search([], order='name')

        windows = [
            ('Last Week',     *last_week_range(today)),
            ('Last Month',    *last_month_range(today)),
            ('Last 3 Months', *last_n_months_range(today, 3)),
            ('All Time',      None, None),
        ]

        result = []
        lw_df, lw_dt = last_week_range(today)
        for drv in drivers:
            issues = Issue.search([('driver_id', '=', drv.id)])
            row = {'name': drv.name, 'phone': drv.phone or '', 'periods': []}
            last7_count = len(issues.filtered(lambda i: i.date_reported and lw_df <= i.date_reported.date() <= lw_dt))
            total_count = len(issues)

            for label, start, end in windows:
                subset = issues
                if start and end:
                    subset = issues.filtered(lambda i: i.date_reported and start <= i.date_reported.date() <= end)
                cats = [{
                    'name':  issue.main_category,
                    'label': humanize(issue.main_category),
                    'color': issue.color,
                } for issue in subset]
                row['periods'].append({'label': label, 'cats': cats})

            row['_last7'] = last7_count
            row['_total'] = total_count
            result.append(row)

        result.sort(key=lambda r: (
            -(1 if r['_last7'] > 0 else 0),
            -(1 if r['_total'] > 0 else 0),
            -r['_last7'],
            -r['_total'],
            r['name'] or ''
        ))

        unique_cats = sorted(set(Issue.search([]).mapped('main_category')))
        cats_summary = []
        range_map = {
            'Last 3 Months': last_n_months_range(today, 3),
            'Last Month':    last_month_range(today),
            'Last Week':     last_week_range(today),
        }

        for cat in unique_cats:
            counts = []
            for lbl in ('Last 3 Months', 'Last Month', 'Last Week'):
                df_, dt_ = range_map[lbl]
                domain = [('main_category', '=', cat), ('date_reported', '>=', df_), ('date_reported', '<=', dt_)]
                counts.append(Issue.search_count(domain))
            cats_summary.append({'name': cat, 'label': humanize(cat), 'counts': counts})

        cats_summary.sort(key=lambda x: (-x['counts'][2], -x['counts'][1], -x['counts'][0]))

        return {
            'windows': [w[0] for w in windows],
            'drivers': result,
            'catsSummary': cats_summary,
        }

    @http.route('/fleet_partner_dashboard', type='http', auth='user')
    def dashboard(self, **kw):
        return request.render('fleet_partner_dashboard.dashboard_template')