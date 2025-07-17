# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from datetime import date, datetime, timedelta

class FleetDashboardController(http.Controller):

    @http.route('/fleet_partner_dashboard/data', type='json', auth='user')
    def dashboard_data(self):
        # unchanged support‑dashboard code
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
                'name':    drv.name,
                'phone':   drv.phone or '',
                'periods': [],
            }
            for days, label in windows:
                if days is not None:
                    cutoff = today - timedelta(days=days)
                    subset = issues.filtered(lambda i: i.date_reported >= cutoff)
                else:
                    subset = issues
                tags = subset.mapped('tag_ids.name')
                row['periods'].append({'label': label, 'tags': tags})
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
        # 1) compute current & previous period windows
        if start_date and end_date:
            df = datetime.strptime(start_date, '%Y-%m-%d').date()
            dt = datetime.strptime(end_date,   '%Y-%m-%d').date()
            length = dt - df
            prev_dt = df
            prev_df = df - length
        else:
            today = date.today()
            mapping = {'Last Week':7,'Last Month':30,'All Time':None}
            days = mapping.get(period, 7)
            if days is not None:
                df      = today - timedelta(days=days)
                dt      = today
                prev_df = today - timedelta(days=2*days)
                prev_dt = today - timedelta(days=days)
            else:
                df = None; dt = None; prev_df = None; prev_dt = None

        products   = products   or []
        scores     = scores     or []
        categories = categories or []

        score_map = {'weak':'Low Performer',
                     'average':'Average Performer',
                     'strong':'High Performer'}

        # --- AGGREGATE METRICS ---
        all_drivers      = request.env['x_fleet_driver'].sudo().search([])
        active_current   = 0
        active_previous  = 0
        trips_current    = 0
        trips_previous   = 0

        for drv in all_drivers:
            if df is not None and dt is not None:
                # current period
                cur_orders = drv.order_ids.filtered(
                    lambda o: o.order_date and df <= o.order_date.date() <= dt
                )
                if cur_orders:
                    active_current += 1
                trips_current += len(cur_orders.filtered(lambda o: o.status == 'complete'))

                # previous period (if any)
                if prev_df is not None and prev_dt is not None:
                    prev_orders = drv.order_ids.filtered(
                        lambda o: o.order_date and prev_df <= o.order_date.date() < prev_dt
                    )
                    if prev_orders:
                        active_previous += 1
                    trips_previous += len(prev_orders.filtered(lambda o: o.status == 'complete'))
            else:
                # All Time: only drivers with at least one completed order ever
                completed = drv.order_ids.filtered(lambda o: o.status == 'complete')
                if completed:
                    active_current += 1
                    trips_current += len(completed)
                # we do NOT calculate a "previous" All Time window

        Supply = request.env['x_fleet_driver_supply_hours'].sudo()
        # ** updated: handle All Time **
        if df and dt:
            supply_secs_current = sum(
                Supply.search([('date','>=',df),('date','<=',dt)]).mapped('seconds')
            )
        else:
            # All Time: sum _all_ records
            supply_secs_current = sum(Supply.search([]).mapped('seconds'))
        if prev_df and prev_dt:
            supply_secs_previous = sum(
                Supply.search([('date','>=',prev_df),('date','<',prev_dt)]).mapped('seconds')
            )
        else:
            supply_secs_previous = 0.0

        metrics = {
            'activeDrivers':      active_current,
            'prevActiveDrivers':  active_previous,
            'tripsCompleted':     trips_current,
            'prevTripsCompleted': trips_previous,
            'supplyHours':        supply_secs_current / 3600.0,
            'prevSupplyHours':    supply_secs_previous / 3600.0,
        }
        # ---------------------------

        # 2) per‑driver rows (unchanged)
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
                orders = orders.filtered(lambda o: o.order_date and o.order_date.date() >= df)
            if dt:
                orders = orders.filtered(lambda o: o.order_date and o.order_date.date() <= dt)

            total    = len(orders)
            complete = orders.filtered(lambda o: o.status=='complete')
            trips    = len(complete)
            cash     = sum(o.price for o in complete)

            sh_dom = [('driver_id','=',drv.id)]
            if df: sh_dom.append(('date','>=', df))
            if dt: sh_dom.append(('date','<=', dt))
            secs = request.env['x_fleet_driver_supply_hours']\
                         .sudo().search(sh_dom).mapped('seconds')
            hours = sum(secs) / 3600.0

            ar  = (trips/total*100.0) if total else 0.0
            tph = (trips/hours)        if hours else 0.0
            mph = (cash/hours)         if hours else 0.0

            dur_secs = sum(
                (o.interval_to-o.interval_from).total_seconds()
                for o in complete if o.interval_from and o.interval_to
            )
            eff = (dur_secs/3600.0)/hours*100.0 if hours else 0.0
            svc = cash * 0.10
            prt = cash * 0.03

            data[drv.id] = {
                'id':                drv.id,
                'name':              drv.name,
                'phone':             drv.phone or '',
                'hire_date':         drv.hire_date and drv.hire_date.strftime('%Y-%m-%d'),
                'product_type':      drv.product_type_id.name or '',
                'type':              drv.type,
                'active':            total>0,
                'cash':              cash,
                'trips':             trips,
                'hours':             hours,
                'acceptance_rate':   ar,
                'trips_per_hour':    tph,
                'money_per_hour':    mph,
                'efficiency':        eff,
                'service_fee':       svc,
                'partner_fee':       prt,
            }

        return {
            'metrics': metrics,
            'data':    data,
        }
