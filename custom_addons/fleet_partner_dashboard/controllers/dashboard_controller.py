# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from datetime import date, datetime, timedelta
import calendar

class FleetDashboardController(http.Controller):

    @http.route('/fleet_partner_dashboard/data', type='json', auth='user')
    def dashboard_data(self):
        windows = [
            (7,   'Last 7 Days'),
            (30,  'Last Month'),
            (90,  'Last 3 Months'),
            (None,'All Time'),
        ]
        today = date.today()
        drivers = request.env['x_fleet_driver'].sudo().search([], order='name')

        # … your existing support‑dashboard logic …
        result = []
        for drv in drivers:
            issues = drv.issue_ids.filtered('date_reported')
            row = {'name': drv.name, 'phone': drv.phone or '', 'periods': []}
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
                    domain = [('tag_ids', 'in', tag.id), ('date_reported', '>=', cutoff)]
                else:
                    domain = [('tag_ids', 'in', tag.id)]
                counts.append(request.env['x_fleet_driver_issue']
                                  .sudo().search_count(domain))
            tags_summary.append({'name': tag.name, 'counts': counts})
        tags_summary.sort(key=lambda x: (-x['counts'][0], -x['counts'][1], -x['counts'][2]))

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
        # --- 1) Build df, dt, prev_df, prev_dt exactly as before ---
        today = date.today()
        if start_date and end_date:
            df = datetime.strptime(start_date, '%Y-%m-%d').date()
            dt = datetime.strptime(end_date,   '%Y-%m-%d').date()
            span = (dt - df).days + 1
            prev_dt = df - timedelta(days=1)
            prev_df = prev_dt - timedelta(days=span-1)
        else:
            mapping = {
                'Last Week':      7,
                'Last Month':    30,
                'Last 3 Months': 90,
                'All Time':     None,
            }
            days = mapping.get(period, 7)
            if days is None:
                df = prev_df = dt = prev_dt = None
            else:
                df      = today - timedelta(days=days)
                dt      = today
                prev_df = today - timedelta(days=2*days)
                prev_dt = today - timedelta(days=days)

        # normalize filters
        products   = products   or []
        scores     = scores     or []
        categories = categories or []

        # fetch all drivers & compute DQS map
        all_drivers = request.env['x_fleet_driver'].sudo().search([])

        dqs_matrix = {
            'strong':  {'strong': 'High Performer',    'average': 'Average Performer', 'weak': 'Low Performer'},
            'average': {'strong': 'High Performer',    'average': 'Average Performer', 'weak': 'Low Performer'},
            'weak':    {'strong': 'Average Performer', 'average': 'Low Performer',      'weak': 'Low Performer'},
        }
        dqs_map = {}
        for drv in all_drivers:
            # --- your On‑Road logic unchanged ---
            prod      = drv.product_type_id
            kpi_type  = prod.kpi_type or 'none'
            lower_kpi = prod.lower_kpi
            upper_kpi = prod.upper_kpi
            onroad    = 'Average'
            if kpi_type != 'none':
                dates = [o.order_date.date() for o in drv.order_ids if o.order_date]
                if dates:
                    first = min(dates)
                    days  = max((today - first).days + 1, 1)
                    if kpi_type == 'avg_hours_online':
                        secs = request.env['x_fleet_driver_supply_hours'].sudo().search([
                                   ('driver_id','=',drv.id),
                                   ('date','>=', first),
                                   ('date','<=', today),
                               ]).mapped('seconds')
                        value = sum(secs)/3600.0/days
                    elif kpi_type == 'avg_trips_completed':
                        comp = drv.order_ids.filtered(
                            lambda o: o.status=='complete'
                                      and o.order_date
                                      and first <= o.order_date.date() <= today
                        )
                        value = len(comp)/days
                    elif kpi_type == 'avg_cash':
                        comp = drv.order_ids.filtered(
                            lambda o: o.status=='complete'
                                      and o.order_date
                                      and first <= o.order_date.date() <= today
                        )
                        value = sum(o.price for o in comp)/days
                    else:
                        value = None
                    if value is not None:
                        if lower_kpi is not None and value < lower_kpi:
                            onroad = 'Weak'
                        elif upper_kpi is not None and value > upper_kpi:
                            onroad = 'Strong'
                        else:
                            onroad = 'Average'
            training = (drv.training_rating or 'average').lower()
            dqs_map[drv.id] = dqs_matrix.get(training, {}).get(onroad.lower(),
                                                               'Average Performer')

        # --- build filtered_drivers once for both metrics + series ---
        filtered_drivers = [
            drv for drv in all_drivers
            if (not products   or drv.product_type_id.id in products)
            and (not scores     or dqs_map.get(drv.id) in scores)
            and (not categories or drv.type in categories)
        ]

        # --- 2) Metrics over filtered_drivers ---
        active_current = active_previous = 0
        trip_current   = trip_previous   = 0
        supply_current = supply_previous = 0.0
        cash_current = cash_previous = 0.0

        for drv in filtered_drivers:
            # current orders
            ords = drv.order_ids
            if df:
                ords = ords.filtered(lambda o:
                    o.order_date and df <= o.order_date.date() <= dt
                )
            cur_comp = ords.filtered(lambda o: o.status == 'complete')
            if cur_comp:
                active_current += 1
            trip_current += len(cur_comp)
            
            cash_current += sum(o.price for o in cur_comp)

            # current supply
            sh_dom = [('driver_id','=',drv.id)]
            if df: sh_dom.append(('date','>=', df))
            if dt: sh_dom.append(('date','<=', dt))
            secs = request.env['x_fleet_driver_supply_hours']\
                         .sudo().search(sh_dom).mapped('seconds')
            supply_current += sum(secs) / 3600.0

            # previous window (same as before) …
            if prev_df and prev_dt:
                prev_ords = drv.order_ids.filtered(lambda o:
                    o.order_date and prev_df <= o.order_date.date() < prev_dt
                )
                prev_comp = prev_ords.filtered(lambda o: o.status == 'complete')
                if prev_comp:
                    active_previous += 1
                trip_previous += len(prev_comp)
                
                cash_previous += sum(o.price for o in prev_comp)

                sh_dom_prev = [
                    ('driver_id','=',drv.id),
                    ('date','>=', prev_df),
                    ('date','<',  prev_dt),
                ]
                secs_prev = request.env['x_fleet_driver_supply_hours']\
                                  .sudo().search(sh_dom_prev).mapped('seconds')
                supply_previous += sum(secs_prev) / 3600.0

        metrics = {
            'activeDrivers':     active_current,
            'prevActiveDrivers': active_previous,
            'tripCount':         trip_current,
            'prevTripCount':     trip_previous,
            'supplyHours':       supply_current,
            'prevSupplyHours':   supply_previous,
            'cashEarned':        cash_current,
            'prevCashEarned':    cash_previous,
        }

        # --- 3) Time‑series over the SAME filtered_drivers ---
        # (first ensure df/dt for “All Time”)
        if df is None and dt is None:
            dates = all_drivers.mapped('order_ids.order_date')
            dates = [d.date() for d in dates if d]
            if dates:
                df = min(dates)
            dt = today

        series_active = []
        series_trips  = []
        series_supply = []
        series_cash   = []

        if df is not None:
            end  = dt or today
            span = (end - df).days + 1
            buckets = []

            # DAILY / WEEKLY / MONTHLY exactly as before…
            if span <= 30:
                cur = df
                while cur <= end:
                    buckets.append((cur, cur, cur.strftime('%Y-%m-%d')))
                    cur += timedelta(days=1)

            elif span < 90:
                start = df - timedelta(days=df.weekday())
                cur = start
                while cur <= end:
                    nxt = cur + timedelta(days=6)
                    buckets.append((cur, min(nxt, end), cur.strftime('%Y-%m-%d')))
                    cur += timedelta(days=7)

            else:
                y0, m0 = df.year, df.month
                y1, m1 = end.year, end.month
                start_month = y0 * 12 + (m0 - 1)
                end_month   = y1 * 12 + (m1 - 1)
                for ym in range(start_month, end_month + 1):
                    y, mo = divmod(ym, 12)
                    mo += 1
                    start_day = date(y, mo, 1)
                    last_day  = date(y, mo, calendar.monthrange(y, mo)[1])
                    label     = start_day.strftime('%Y-%m')
                    buckets.append((start_day, min(last_day, end), label))

            # now compute each series using filtered_drivers
            f_ids = [d.id for d in filtered_drivers]
            for start_b, end_b, label in buckets:
                # Active Drivers
                cnt = sum(1 for drv in filtered_drivers
                          if drv.order_ids.filtered(
                              lambda o: o.order_date
                                        and start_b <= o.order_date.date() <= end_b
                                        and o.status == 'complete'
                          ))
                series_active.append({'period': label, 'value': cnt})

                # Trips
                trips = sum(len(drv.order_ids.filtered(
                              lambda o: o.order_date
                                        and start_b <= o.order_date.date() <= end_b
                                        and o.status == 'complete'
                            )) for drv in filtered_drivers)
                series_trips.append({'period': label, 'value': trips})

                # Supply Hours
                secs = request.env['x_fleet_driver_supply_hours'].sudo().search([
                    ('driver_id', 'in', f_ids),
                    ('date','>=', start_b),
                    ('date','<=', end_b),
                ]).mapped('seconds')
                series_supply.append({'period': label, 'value': sum(secs) / 3600.0})
                
                # Cash Earned
                cash_sum = sum(
                    sum(o.price for o in drv.order_ids.filtered(
                        lambda o: o.status=='complete'
                                  and o.order_date
                                  and start_b <= o.order_date.date() <= end_b
                    ))
                    for drv in filtered_drivers
                )
                series_cash.append({'period': label, 'value': cash_sum})

        series = {
            'activeDrivers': series_active,
            'trips':         series_trips,
            'supplyHours':   series_supply,
            'cashEarned':    series_cash,
        }

        # --- 4) Build per‑driver detail rows ---
        drivers = request.env['x_fleet_driver'].sudo().search([], order='name')
        data = {}
        for drv in drivers:
            if products and drv.product_type_id.id not in products:
                continue
            sc = dqs_map.get(drv.id)
            if scores and sc not in scores:
                continue
            if categories and drv.type not in categories:
                continue

            orders = drv.order_ids
            if df:
                orders = orders.filtered(lambda o: o.order_date and o.order_date.date() >= df)
            if end_date:
                orders = orders.filtered(lambda o: o.order_date and o.order_date.date() <= dt)

            complete = orders.filtered(lambda o: o.status == 'complete')
            trips    = len(complete)
            cash     = sum(o.price for o in complete)

            sh_dom = [('driver_id','=',drv.id)]
            if df: sh_dom.append(('date','>=', df))
            if dt: sh_dom.append(('date','<=', dt))
            secs = request.env['x_fleet_driver_supply_hours']\
                         .sudo().search(sh_dom).mapped('seconds')
            hours = sum(secs) / 3600.0

            data[drv.id] = {
                'id':             drv.id,
                'name':           drv.name,
                'phone':          drv.phone or '',
                'hire_date':      drv.hire_date and drv.hire_date.strftime('%Y-%m-%d'),
                'product_type':   drv.product_type_id.name or '',
                'type':           drv.type,
                'quality_score':  sc,
                'active':         trips > 0,
                'cash':           cash,
                'trips':          trips,
                'hours':          hours,
                'completed_to_request': (trips / len(orders) * 100.0) if orders else 0.0,
                'trips_per_hour': (trips / hours) if hours else 0.0,
                'money_per_hour': (cash  / hours) if hours else 0.0,
                'utilisation':    (
                                  sum((o.interval_to - o.interval_from).total_seconds()
                                      for o in complete
                                      if o.interval_from and o.interval_to
                                  ) / 3600.0
                                ) / hours * 100.0 if hours else 0.0,
                'efficiency':     (
                                  sum((o.interval_to - o.order_date).total_seconds()
                                      for o in complete
                                      if o.interval_from and o.interval_to
                                  ) / 3600.0
                                ) / hours * 100.0 if hours else 0.0,
                'service_fee':    cash * 0.10,
                'partner_fee':    cash * 0.03,
            }

        return {
            'metrics': metrics,
            'series':  series,
            'data':    data,
        }
