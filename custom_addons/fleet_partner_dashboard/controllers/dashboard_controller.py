from odoo import http
from odoo.http import request
from datetime import datetime, timedelta

class FleetDashboardController(http.Controller):

    @http.route('/fleet_dashboard/data', type='json', auth='user')
    def get_driver_issues_data(self):
        Issue = request.env['x_fleet_driver_issue'].sudo()

        today = datetime.today().date()
        periods = {
            '7_days': today - timedelta(days=7),
            '30_days': today - timedelta(days=30),
            '90_days': today - timedelta(days=90),
        }

        drivers_data = {}

        all_issues = Issue.search([])

        for issue in all_issues:
            driver = issue.driver_id.name
            date = issue.date_reported
            tag_names = [tag.name for tag in issue.tag_ids]

            if not drivers_data.get(driver):
                drivers_data[driver] = {
                    '7_days': [],
                    '30_days': [],
                    '90_days': [],
                    'all_time': set()
                }

            if date >= periods['7_days']:
                drivers_data[driver]['7_days'].extend(tag_names)
            if date >= periods['30_days']:
                drivers_data[driver]['30_days'].extend(tag_names)
            if date >= periods['90_days']:
                drivers_data[driver]['90_days'].extend(tag_names)

            drivers_data[driver]['all_time'].update(tag_names)

        # Remove duplicates and convert sets to lists
        for d in drivers_data:
            for key in ['7_days', '30_days', '90_days']:
                drivers_data[d][key] = list(set(drivers_data[d][key]))
            drivers_data[d]['all_time'] = list(drivers_data[d]['all_time'])

        return drivers_data

    @http.route('/fleet_partner_dashboard', type='http', auth='user', website=True)
    def dashboard(self, **kw):
        return request.render('fleet_partner_dashboard.dashboard_template')

