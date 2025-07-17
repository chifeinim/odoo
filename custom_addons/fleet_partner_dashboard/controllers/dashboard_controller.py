# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from datetime import date, datetime, timedelta

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

        # Build tag summary
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
            tags_summary.append({'name': tag.name, 'counts': counts})
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

    @http.route('/fleet_partner_performance/filters', type='json', auth='user')
    def performance_filters(self):
        # all product types for the Anda Product dropdown
        Product = request.env['x_fleet_product_type'].sudo()
        pts = Product.search([], order='name')
        product_types = [{'id': p.id, 'name': p.name} for p in pts]

        # all driver.type values for the Driver Category dropdown
        Driver = request.env['x_fleet_driver'].sudo()
        categories = [k for k, _ in Driver._fields['type'].selection]

        return {
            'product_types': product_types,
            'categories':    categories,
        }

    @http.route('/fleet_partner_performance/data', type='json', auth='user')
    def performance_data(self, period=None, products=None, scores=None,
                         categories=None, start_date=None, end_date=None):
        # 1) compute current & previous window based on named period or explicit dates
        if start_date and end_date:
            # explicit date range
            df = datetime.strptime(start_date, '%Y-%m-%d').date()
            dt = datetime.strptime(end_date,   '%Y-%m-%d').date()
            span = dt - df
            prev_dt = df
            prev_df = df - span
        else:
            today = date.today()
            mapping = {'Last Week':7, 'Last Month':30, 'All Time':None}
            days = mapping.get(period, 7)
            if days is None:
                # all time: no previous window
                df = dt = prev_df = prev_dt = None
            else:
                df      = today - timedelta(days=days)
                dt      = today
                prev_df = today - timedelta(days=2*days)
                prev_dt = today - timedelta(days=days)

        # --- compute aggregate active‑driver metrics ---
        all_drivers     = request.env['x_fleet_driver'].sudo().search([])
        active_current  = 0
        active_previous = 0
        for drv in all_drivers:
            # current period
            if df:
                cur_orders = drv.order_ids.filtered(
                    lambda o: o.order_date and df <= o.order_date.date() <= dt
                )
                if cur_orders:
                    active_current += 1
            else:
                # all‑time: everyone counts as “active”
                active_current = len(all_drivers)
                break
            # previous period
            if prev_df and prev_dt:
                prev_orders = drv.order_ids.filtered(
                    lambda o: o.order_date and prev_df <= o.order_date.date() < prev_dt
                )
                if prev_orders:
                    active_previous += 1

        metrics = {
            'activeDrivers':     active_current,
            'prevActiveDrivers': active_previous,
        }
        # --------------------------------------------------

        # Now build the per‑driver rows with your existing filters
        products   = products   or []
        scores     = scores     or []
        categories = categories or []

        score_map = {'weak':'Low Performer',
                     'average':'Average Performer',
                     'strong':'High Performer'}

        drivers = request.env['x_fleet_driver'].sudo().search([], order='name')
        data = {}
        for drv in drivers:
            if products and drv.product_type_id.id not in products:
                continue
            sc = score_map.get(drv.training_rating)
            if scores and sc not in scores:
                continue
            if categories and drv.type not in categories:
                continue

            orders = drv.order_ids
            if df:
                orders = orders.filtered(
                    lambda o: o.order_date and df <= o.order_date.date() <= dt
                )

            total    = len(orders)
            complete = orders.filtered(lambda o: o.status=='complete')
            trips    = len(complete)
            cash     = sum(o.price for o in complete)

            # supply‑hours
            sh_dom = [('driver_id','=',drv.id)]
            if df:   sh_dom.append(('date','>=', df))
            if dt:   sh_dom.append(('date','<=', dt))
            secs = request.env['x_fleet_driver_supply_hours']\
                         .sudo().search(sh_dom).mapped('seconds')
            hours = sum(secs) / 3600.0

            ar  = (trips/total*100.0) if total else 0.0
            tph = (trips/hours)        if hours else 0.0
            mph = (cash/hours)         if hours else 0.0

            dur = sum(
                (o.interval_to - o.interval_from).total_seconds()
                for o in complete if o.interval_from and o.interval_to
            )
            eff = (dur/3600.0)/hours*100.0 if hours else 0.0

            svc = cash * 0.10
            prt = cash * 0.03

            data[drv.id] = {
                'id':              drv.id,
                'name':            drv.name,
                'phone':           drv.phone or '',
                'hire_date':       drv.hire_date and drv.hire_date.strftime('%Y-%m-%d'),
                'product_type':    drv.product_type_id.name or '',
                'type':            drv.type,
                'active':          total > 0,
                'cash':            cash,
                'trips':           trips,
                'hours':           hours,
                'acceptance_rate': ar,
                'trips_per_hour':  tph,
                'money_per_hour':  mph,
                'efficiency':      eff,
                'service_fee':     svc,
                'partner_fee':     prt,
            }

        return {
            'metrics': metrics,
            'data':    data,
        }
