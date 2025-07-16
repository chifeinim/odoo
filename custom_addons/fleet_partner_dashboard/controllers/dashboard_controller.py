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

        # New: build tag summary
        Tag = request.env['x_fleet_driver_issue_tag'].sudo()
        tags = Tag.search([], order='name')
        tags_summary = []
        for tag in tags:
            counts = []
            for days, _ in windows[:3][::-1]:  # reversed: 90,30,7
                if days is not None:
                    cutoff = today - timedelta(days=days)
                    domain = [
                        ('tag_ids', 'in', tag.id),
                        ('date_reported', '>=', cutoff),
                    ]
                else:
                    domain = [('tag_ids', 'in', tag.id)]
                counts.append(request.env['x_fleet_driver_issue']
                              .sudo().search_count(domain))
            # counts = [90d_count, 30d_count, 7d_count]
            tags_summary.append({'name': tag.name, 'counts': counts})
        # sort by counts[0] (90d), then counts[1] (30d), then counts[2] (7d)
        tags_summary.sort(key=lambda x: (-x['counts'][0],
                                        -x['counts'][1],
                                        -x['counts'][2]))

        return {
            'windows': [lbl for _, lbl in windows],
            'drivers': result,
            'tags_summary': tags_summary,
        }

    @http.route('/fleet_partner_dashboard', type='http', auth='user')
    def dashboard(self, **kw):
        return request.render('fleet_partner_dashboard.dashboard_template')
    
    # 1) filters endpoint: product_types and driver.categories
    @http.route('/fleet_partner_performance/filters', type='json', auth='user')
    def performance_filters(self):
        # all product types for the “Anda Product” dropdown
        Product = request.env['x_fleet_product_type'].sudo()
        pts = Product.search([], order='name')
        product_types = [{'id': p.id, 'name': p.name} for p in pts]

        # all driver.type values for the “Driver Category” dropdown
        Driver = request.env['x_fleet_driver'].sudo()
        # pull the selection keys from the field definition:
        categories = [k for k, _ in Driver._fields['type'].selection]

        return {
            'product_types': product_types,
            'categories':    categories,
        }

    # 2) performance data endpoint: accepts period, products, scores, categories
    @http.route('/fleet_partner_performance/data', type='json', auth='user')
    def performance_data(self, period=None, products=None, scores=None, categories=None):
        # parse period → cutoff date
        today = date.today()
        mapping = {
            'Last Week':  7,
            'Last Month': 30,
            'All Time':   None,
        }
        days = mapping.get(period)
        cutoff = (today - timedelta(days=days)) if days else None

        # ensure lists
        products   = products or []
        scores     = scores   or []
        categories = categories or []

        # training_rating → quality score map
        score_map = {
            'weak':    'Low Performer',
            'average': 'Average Performer',
            'strong':  'High Performer',
        }

        drivers = request.env['x_fleet_driver'].sudo().search([], order='name')
        data = {}
        for drv in drivers:
            # apply the three multi‑select filters on the driver record
            if products and drv.product_type_id.id not in products:
                continue
            drv_score = score_map.get(drv.training_rating, False)
            if scores and drv_score not in scores:
                continue
            if categories and drv.type not in categories:
                continue

            # fetch orders in period (if cutoff)
            orders = drv.order_ids
            if cutoff:
                orders = orders.filtered(lambda o: o.order_date and o.order_date.date() >= cutoff)

            total_orders    = len(orders)
            completed_orders = orders.filtered(lambda o: o.status == 'complete')
            trips = len(completed_orders)

            # Active? at least one order in period
            active = total_orders > 0

            # Cash = sum price of completed
            cash = sum(o.price for o in completed_orders)

            # Supply hours in period
            sh = request.env['x_fleet_driver_supply_hours'].sudo().search([
                ('driver_id', '=', drv.id),
                # if cutoff, only after that
                *([('date', '>=', cutoff)] if cutoff else [])
            ]).mapped('seconds')
            hours = sum(sh) / 3600.0

            # Acceptance rate
            acceptance_rate = (trips / total_orders * 100.0) if total_orders else 0.0

            # Trips per hour & Money per hour
            trips_per_hour = (trips / hours) if hours else 0.0
            money_per_hour = (cash  / hours) if hours else 0.0

            # Efficiency: sum of (interval_to - interval_from)
            dur_secs = sum(
                (o.interval_to - o.interval_from).total_seconds()
                for o in completed_orders
                if o.interval_from and o.interval_to
            )
            # % of transport time vs online time
            efficiency = (dur_secs / 3600.0) / hours * 100.0 if hours else 0.0

            # fees
            service_fee = cash * 0.10
            partner_fee = cash * 0.03

            data[drv.id] = {
                'id':              drv.id,
                'name':            drv.name,
                'phone':           drv.phone or '',
                'hire_date':       drv.hire_date and drv.hire_date.strftime('%Y-%m-%d'),
                'product_type':    drv.product_type_id.name or '',
                'type':            drv.type,
                'active':          active,
                'cash':            cash,
                'trips':           trips,
                'hours':           hours,
                'acceptance_rate': acceptance_rate,
                'trips_per_hour':  trips_per_hour,
                'money_per_hour':  money_per_hour,
                'efficiency':      efficiency,
                'service_fee':     service_fee,
                'partner_fee':     partner_fee,
            }

        return data

