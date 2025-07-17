# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from datetime import date, datetime, timedelta

class FleetDashboardController(http.Controller):

    @http.route('/fleet_partner_dashboard/data', type='json', auth='user')
    def dashboard_data(self):
        # (unchanged support‑dashboard code)
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

        Tag = request.env['x_fleet_driver_issue_tag'].sudo()
        tags = Tag.search([], order='name')
        tags_summary = []
        for tag in tags:
            counts = []
            for days, _ in windows[:3][::-1]:
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
            'windows':      [lbl for _, lbl in windows],
            'drivers':      result,
            'tags_summary': tags_summary,
        }

    @http.route('/fleet_partner_dashboard', type='http', auth='user')
    def dashboard(self, **kw):
        return request.render('fleet_partner_dashboard.dashboard_template')

    @http.route('/fleet_partner_performance/filters', type='json', auth='user')
    def performance_filters(self):
        Product = request.env['x_fleet_product_type'].sudo()
        pts = Product.search([], order='name')
        product_types = [{'id': p.id, 'name': p.name} for p in pts]

        Driver = request.env['x_fleet_driver'].sudo()
        categories = [k for k, _ in Driver._fields['type'].selection]

        return {
            'product_types': product_types,
            'categories':    categories,
        }

    @http.route('/fleet_partner_performance/data', type='json', auth='user')
    def performance_data(self, period=None, products=None, scores=None,
                         categories=None, start_date=None, end_date=None):
        # 1) determine current & previous windows
        today = date.today()
        if start_date and end_date:
            df      = datetime.strptime(start_date, '%Y-%m-%d').date()
            dt      = datetime.strptime(end_date,   '%Y-%m-%d').date()
            prev_df = prev_dt = None
        else:
            mapping = {'Last Week':7, 'Last Month':30, 'All Time':None}
            days = mapping.get(period, 7)
            if days is None:
                df = dt = prev_df = prev_dt = None
            else:
                df      = today - timedelta(days=days)
                dt      = None
                prev_df = today - timedelta(days=2*days)
                prev_dt = today - timedelta(days=days)

        # 2) fast KPI: count of active drivers this period vs last period
        #    (active = at least one order in window)
        Order = request.env['x_fleet_order'].sudo()
        domain_cur = []
        if df:
            domain_cur.append(('order_date', '>=', df))
        groups_cur = Order.read_group(domain_cur, ['driver_id'], ['driver_id'])
        active_current = len(groups_cur)

        active_previous = 0
        if prev_df and prev_dt:
            domain_prev = [
                ('order_date', '>=', prev_df),
                ('order_date', '<',  prev_dt),
            ]
            groups_prev = Order.read_group(domain_prev, ['driver_id'], ['driver_id'])
            active_previous = len(groups_prev)

        metrics = {
            'activeDrivers':     active_current,
            'prevActiveDrivers': active_previous,
        }

        # 3) per‑driver rows (unchanged)
        products   = products   or []
        scores     = scores     or []
        categories = categories or []

        score_map = {
            'weak':    'Low Performer',
            'average': 'Average Performer',
            'strong':  'High Performer',
        }

        drivers = request.env['x_fleet_driver'].sudo().search([], order='name')
        data = {}
        for drv in drivers:
            if products   and drv.product_type_id.id not in products: continue
            sc = score_map.get(drv.training_rating)
            if scores     and sc not in scores:                       continue
            if categories and drv.type not in categories:             continue

            orders = drv.order_ids
            if df:
                orders = orders.filtered(
                    lambda o: o.order_date and o.order_date.date() >= df
                )
            if dt:
                orders = orders.filtered(
                    lambda o: o.order_date and o.order_date.date() <= dt
                )

            total    = len(orders)
            complete = orders.filtered(lambda o: o.status == 'complete')
            trips    = len(complete)
            cash     = sum(o.price for o in complete)

            sh_dom = [('driver_id','=',drv.id)]
            if df: sh_dom.append(('date','>=', df))
            if dt: sh_dom.append(('date','<=', dt))
            secs = request.env['x_fleet_driver_supply_hours']\
                         .sudo()\
                         .search(sh_dom)\
                         .mapped('seconds')
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
