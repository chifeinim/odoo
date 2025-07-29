# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from datetime import date, datetime, timedelta
import calendar, json

def _parse_events(o):
    evs = o.events or '[]'
    if isinstance(evs, str):
        try:
            evs = json.loads(evs)
        except ValueError:
            evs = []
    return [e for e in evs if isinstance(e, dict)]

def _order_statuses(o):
    return {e.get('order_status') for e in _parse_events(o)}

def _transport_seconds(o):
    """If there’s a transporting→(complete|cancelled) pair, return the  
       seconds between them, else 0."""
    evs = _parse_events(o)
    if 'transporting' not in _order_statuses(o):
        return 0.0
    t0 = next((e['event_at'] for e in evs if e['order_status']=='transporting'), None)
    terminal = 'complete' if any(e['order_status']=='complete' for e in evs) else 'cancelled'
    t1 = next((e['event_at'] for e in evs if e['order_status']==terminal), None)
    if not (t0 and t1):
        return 0.0
    d0 = datetime.fromisoformat(t0.replace('Z','+00:00'))
    d1 = datetime.fromisoformat(t1.replace('Z','+00:00'))
    return (d1 - d0).total_seconds()

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

        Issue   = request.env['x_fleet_issue'].sudo()
        Driver  = request.env['x_fleet_driver'].sudo()
        drivers = Driver.search([], order='name')
        all_issues = Issue.search([])

        # 1) Build a map driver_id → recordset of that driver's issues
        issues_by_driver = {}
        for drv in drivers:
            issues_by_driver[drv.id] = all_issues.filtered(lambda i: i.driver_id.id == drv.id)

        # 2) Per‑driver breakdown
        result = []
        for drv in drivers:
            drv_issues = issues_by_driver.get(drv.id, Issue.browse())  # recordset
            row = {
                'name':    drv.name,
                'phone':   drv.phone or '',
                'periods': [],
            }
            for days, label in windows:
                if days is None:
                    subset = drv_issues
                else:
                    cutoff = today - timedelta(days=days)
                    subset = drv_issues.filtered(
                        lambda i: i.date_reported and i.date_reported.date() >= cutoff
                    )
                cats = [{
                    'name':  issue.main_category,
                    'color': issue.color,
                } for issue in subset]
                row['periods'].append({'label': label, 'cats': cats})
            result.append(row)

        # 3) Global category‑summary
        # pull all distinct category names:
        unique_cats = sorted(set(all_issues.mapped('main_category') or []))
        cats_summary = []
        for cat in unique_cats:
            counts = []
            # reversed so counts[0] == 3‑months
            for days, _ in reversed(windows[:3]):
                domain = [('main_category', '=', cat)]
                if days is not None:
                    cutoff = today - timedelta(days=days)
                    domain.append(('date_reported', '>=', cutoff))
                counts.append(Issue.search_count(domain))
            cats_summary.append({'name': cat, 'counts': counts})

        # sort by newest first:
        cats_summary.sort(key=lambda x: (-x['counts'][0], -x['counts'][1], -x['counts'][2]))

        return {
            'windows':     [lbl for _, lbl in windows],
            'drivers':     result,
            'catsSummary': cats_summary,
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
        total_drivers = len(all_drivers)

        dqs_matrix = {
            'strong':  {'strong': 'High Performer',    'average': 'Average Performer', 'weak': 'Low Performer'},
            'average': {'strong': 'High Performer',    'average': 'Average Performer', 'weak': 'Low Performer'},
            'weak':    {'strong': 'Average Performer', 'average': 'Low Performer',      'weak': 'Low Performer'},
        }
        dqs_map = {}
        prod_counts = {}
        qual_counts = {}
        cat_counts = {}
        for drv in all_drivers:
            
            # Determine On‑the‑Road score using hire_date → today
            prod      = drv.product_type_id
            kpi_type  = prod.kpi_type or 'none'
            lower_kpi = prod.lower_kpi
            upper_kpi = prod.upper_kpi
            onroad    = 'Average'

            # only compute if we have both a KPI type and a hire_date
            if kpi_type != 'none' and drv.hire_date:
                first = drv.hire_date                # ← use hire_date now
                days  = max((today - first).days + 1, 1)

                if kpi_type == 'avg_hours_online':
                    secs = request.env['x_fleet_driver_supply_hours'].sudo().search([
                        ('driver_id', '=', drv.id),
                        ('date',      '>=', first),
                        ('date',      '<=', today),
                    ]).mapped('seconds')
                    value = sum(secs) / 3600.0 / days

                elif kpi_type == 'avg_trips_completed':
                    comp = drv.order_ids.filtered(lambda o:
                        o.status == 'complete'
                        and o.order_date
                        and first <= o.order_date.date() <= today
                    )
                    value = len(comp) / days

                elif kpi_type == 'avg_cash':
                    comp = drv.order_ids.filtered(lambda o:
                        o.status == 'complete'
                        and o.order_date
                        and first <= o.order_date.date() <= today
                    )
                    value = sum(o.price for o in comp) / days

                else:
                    value = None

                # compare against bounds
                if value is not None:
                    if lower_kpi is not None and value < lower_kpi:
                        onroad = 'Weak'
                    elif upper_kpi is not None and value > upper_kpi:
                        onroad = 'Strong'
                    else:
                        onroad = 'Average'

            # combine with training rating via your existing matrix
            training = (drv.training_rating or 'average').lower()
            dqs_map[drv.id] = dqs_matrix.get(training, {}) \
                                    .get(onroad.lower(), 'Average Performer')
                                    
            # Determine distribution of drivers by Product, Quality Score, Category
            key = drv.product_type_id.name or 'Unspecified'
            prod_counts[key] = prod_counts.get(key, 0) + 1
            
            qs = dqs_map.get(drv.id, 'Average Performer')
            qual_counts[qs] = qual_counts.get(qs, 0) + 1
            
            cat = drv.type or 'Unspecified'
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

        # Map distribution of drivers
        prod_dist = [{'label': k, 'value': v} for k, v in prod_counts.items()]
        qual_dist = [{'label': k, 'value': v} for k, v in qual_counts.items()]
        cat_dist = [{'label': k, 'value': v} for k, v in cat_counts.items()]

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
        util_secs_current = util_secs_previous = 0.0
        eff_secs_current = eff_secs_previous = 0.0
        accept_num_current = accept_num_previous = 0
        order_num_current  = order_num_previous  = 0

        for drv in filtered_drivers:
            # current orders
            ords = drv.order_ids
            if df:
                ords = ords.filtered(lambda o:
                    o.order_date and df <= o.order_date.date() <= dt
                )
            
            # current complete orders (trips)    
            cur_comp = ords.filtered(lambda o: o.status == 'complete')
            if cur_comp:
                active_current += 1
            trip_current += len(cur_comp)
            
            for o in ords:
                evs = o.events or '[]'
                if isinstance(evs, str):
                    try:
                        evs = json.loads(evs)
                    except ValueError:
                        evs = []
                        
                statuses = { e.get('order_status') for e in evs if isinstance(e, dict) }
                
                # 3) ACCEPTANCE: did we ever reach driving|waiting|transporting?
                if statuses & {'driving', 'waiting', 'transporting'}:
                    accept_num_current += 1
                order_num_current += 1
                
                # 4) EFFICIENCY: if we started transporting, find the terminal event
                if 'transporting' in statuses:
                    t0 = next((e['event_at'] for e in evs if e.get('order_status') == 'transporting'), None)
                    # prefer complete over cancelled
                    terminal = 'complete' if 'complete' in statuses else 'cancelled'
                    t1 = next((e['event_at'] for e in evs if e.get('order_status') == terminal), None)
                    if t0 and t1:
                        dt0 = datetime.fromisoformat(t0.replace('Z','+00:00'))
                        dt1 = datetime.fromisoformat(t1.replace('Z','+00:00'))
                        eff_secs_current += (dt1 - dt0).total_seconds()
            
            # current cash
            cash_current += sum(o.price for o in cur_comp)

            # current supply
            sh_dom = [('driver_id','=',drv.id)]
            if df: sh_dom.append(('date','>=', df))
            if dt: sh_dom.append(('date','<=', dt))
            secs = request.env['x_fleet_driver_supply_hours']\
                         .sudo().search(sh_dom).mapped('seconds')
            supply_current += sum(secs) / 3600.0
            
            # current utilisation seconds
            util_secs_current += sum(
                (o.interval_to - o.interval_from).total_seconds()
                for o in ords
                if o.interval_from and o.interval_to
            )

            # previous window (same as before) …
            if prev_df and prev_dt:
                prev_ords = drv.order_ids.filtered(lambda o:
                    o.order_date and prev_df <= o.order_date.date() < prev_dt
                )
                
                # previous complete orders
                prev_comp = prev_ords.filtered(lambda o: o.status == 'complete')
                if prev_comp:
                    active_previous += 1
                
                for o in ords:
                    # previous no. of accepted
                    if statuses & {'driving', 'waiting', 'transporting'}:
                        accept_num_previous += 1
                    order_num_previous += 1
                    
                    if 'transporting' in statuses:
                        t0 = next((e['event_at'] for e in evs if e.get('order_status') == 'transporting'), None)
                        # prefer complete over cancelled
                        terminal = 'complete' if 'complete' in statuses else 'cancelled'
                        t1 = next((e['event_at'] for e in evs if e.get('order_status') == terminal), None)
                        if t0 and t1:
                            dt0 = datetime.fromisoformat(t0.replace('Z','+00:00'))
                            dt1 = datetime.fromisoformat(t1.replace('Z','+00:00'))
                            eff_secs_previous += (dt1 - dt0).total_seconds()
                
                # previous no. of completed trips
                trip_previous += len(prev_comp)
                
                # previous cash
                cash_previous += sum(o.price for o in prev_comp)

                # previous supply hours
                sh_dom_prev = [
                    ('driver_id','=',drv.id),
                    ('date','>=', prev_df),
                    ('date','<',  prev_dt),
                ]
                secs_prev = request.env['x_fleet_driver_supply_hours']\
                                  .sudo().search(sh_dom_prev).mapped('seconds')
                supply_previous += sum(secs_prev) / 3600.0
                
                # previous utilisation
                util_secs_previous += sum(
                    (o.interval_to - o.interval_from).total_seconds()
                    for o in prev_ords
                )
                
                # previous efficiency
                eff_secs_previous += sum(
                    (o.interval_to - o.order_date).total_seconds()
                    for o in prev_ords
                )
                
        # compute supply hours per active driver
        avg_supply = (supply_current / active_current) if active_current else 0.0
        avg_supply_prev = (supply_previous / active_previous) if active_previous else 0.0
        
        # compute % utilisation
        avg_util_current = (util_secs_current / 3600.0 / supply_current * 100.0) \
                            if supply_current else 0.0
        avg_util_previous = (util_secs_previous / 3600.0 / supply_previous * 100.0) \
                             if supply_previous else 0.0
                             
        # compute % efficiency
        avg_eff_current = (eff_secs_current / 3600.0 / supply_current * 100.0) \
                            if supply_current else 0.0
        avg_eff_previous = (eff_secs_previous / 3600.0 / supply_previous * 100.0) \
                             if supply_previous else 0.0
        
        # compute acceptance rate %                     
        accept_rate_current = (
            (accept_num_current / order_num_current) * 100.0
        ) if order_num_current else 0.0
        accept_rate_previous = (
            (accept_num_previous / order_num_previous) * 100.0
        ) if order_num_previous else 0.0
        
        # compute completed to request %
        completed_to_request_current = (trip_current / order_num_current * 100.0) if order_num_current else 0.0
        completed_to_request_previous = (trip_previous / order_num_previous * 100.0) if order_num_previous else 0.0

        metrics = {
            'activeDrivers':     active_current,
            'prevActiveDrivers': active_previous,
            'tripCount':         trip_current,
            'prevTripCount':     trip_previous,
            'supplyHours':       supply_current,
            'prevSupplyHours':   supply_previous,
            'cashEarned':        cash_current,
            'prevCashEarned':    cash_previous,
            'moneyPerHour':     (cash_current / supply_current) if supply_current else 0.0,
            'prevMoneyPerHour': (cash_previous / supply_previous) if supply_previous else 0.0,
            'tripsPerHour':      (trip_current   / supply_current) if supply_current else 0.0,
            'prevTripsPerHour':  (trip_previous  / supply_previous) if supply_previous else 0.0,
            'avgSupplyHoursPerDriver':     avg_supply,
            'prevAvgSupplyHoursPerDriver': avg_supply_prev,
            'avgUtilisation':     avg_util_current,
            'prevAvgUtilisation': avg_util_previous,
            'avgEfficiency':      avg_eff_current,
            'prevAvgEfficiency':  avg_eff_previous,
            'acceptanceRate':      accept_rate_current,
            'prevAcceptanceRate':  accept_rate_previous,
            'completedToRequest': completed_to_request_current,
            'prevCompletedToRequest': completed_to_request_previous,
            'serviceFee': cash_current * 0.1,
            'prevServiceFee': cash_previous * 0.1,
            'partnerFee': cash_current * 0.03,
            'prevPartnerFee': cash_previous * 0.03,
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
        series_mph  = []
        series_trph  = []
        series_avg_supply = []
        series_utilisation = []
        series_efficiency = []
        series_acceptanceRate = []
        series_completedToRequest = []
        series_serviceFee = []
        series_partnerFee = []

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
                
                # Cash Earned / service + partner fees
                cash_sum = sum(
                    sum(o.price for o in drv.order_ids.filtered(
                        lambda o: o.status=='complete'
                                  and o.order_date
                                  and start_b <= o.order_date.date() <= end_b
                    ))
                    for drv in filtered_drivers
                )
                series_cash.append({'period': label, 'value': cash_sum})
                series_serviceFee.append({'period': label, 'value': cash_sum * 0.1})
                series_partnerFee.append({'period': label, 'value': cash_sum * 0.03})
                
                # Money per hour
                hours = sum(request.env['x_fleet_driver_supply_hours']
                              .sudo().search([
                                ('driver_id', 'in', f_ids),
                                ('date',     '>=', start_b),
                                ('date',     '<=', end_b),
                              ]).mapped('seconds')) / 3600.0
                avg_money = (cash_sum / hours) if hours else 0.0
                series_mph.append({'period': label, 'value': avg_money})
                
                # Trips per hour
                const_supply_secs = request.env['x_fleet_driver_supply_hours'].sudo().search([
                  ('driver_id', 'in', f_ids),
                  ('date','>=', start_b),
                  ('date','<=', end_b),
                ]).mapped('seconds')
                bucket_hours = sum(const_supply_secs)/3600.0
                bucket_trips = trips  # as you already summed
                avg_trph = (bucket_trips / bucket_hours) if bucket_hours else 0.0
                series_trph.append({'period': label, 'value': avg_trph})
                
                # SH per active driver
                avg_supply_bucket = (bucket_hours and bucket_hours > 0) and (
                    series_supply[-1]['value'] / cnt if cnt else 0.0
                ) or 0.0
                series_avg_supply.append({'period': label, 'value': avg_supply_bucket})
                
                # Utilisation %
                bucket_util_secs = sum(
                    (o.interval_to - o.interval_from).total_seconds()
                    for drv in filtered_drivers
                    for o in drv.order_ids.filtered(
                        lambda o:
                            o.interval_from and o.interval_to
                            and start_b <= o.interval_from.date() <= end_b
                    )
                )
                bucket_supply_h = series_supply[-1]['value']
                util_pct = (bucket_util_secs/3600.0 / bucket_supply_h * 100.0) \
                            if bucket_supply_h else 0.0
                series_utilisation.append({'period': label, 'value': util_pct})
                
                # Efficiency % via events JSON
                bucket_eff_secs = 0.0
                bucket_orders    = 0
                bucket_accepts   = 0

                for drv in filtered_drivers:
                    for o in drv.order_ids.filtered(lambda o:
                            o.order_date and start_b <= o.order_date.date() <= end_b):
                        bucket_orders += 1
                        sts = _order_statuses(o)
                        if sts & {'driving','waiting','transporting'}:
                            bucket_accepts += 1
                        bucket_eff_secs += _transport_seconds(o)

                # now compute the two rates
                series_efficiency.append({
                    'period': label,
                    'value': (bucket_eff_secs/3600.0 / series_supply[-1]['value'] * 100.0)
                            if series_supply[-1]['value'] else 0.0
                })
                series_acceptanceRate.append({
                    'period': label,
                    'value': (bucket_accepts / bucket_orders * 100.0) if bucket_orders else 0.0
                })
                
                # Completed to Request %
                completed_to_request = (trips / bucket_orders * 100) if bucket_orders else 0.0
                series_completedToRequest.append({'period': label, 'value': completed_to_request})

        series = {
            'activeDrivers': series_active,
            'trips':         series_trips,
            'supplyHours':   series_supply,
            'cashEarned':    series_cash,
            'moneyPerHour':  series_mph,
            'tripsPerHour':  series_trph,
            'avgSupplyHoursPerDriver': series_avg_supply,
            'utilisation': series_utilisation,
            'efficiency':  series_efficiency,
            'acceptanceRate': series_acceptanceRate,
            'completedToRequest': series_completedToRequest,
            'serviceFee': series_serviceFee,
            'partnerFee': series_partnerFee,
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
            
            # event‑based acceptance
            accepted = sum(1 for o in orders if _order_statuses(o) & {'driving','waiting','transporting'})
            total_orders = len(orders)
            accept_rate  = (accepted / total_orders * 100.0) if total_orders else 0.0

            # event‑based efficiency
            eff_secs     = sum(_transport_seconds(o) for o in orders)
            efficiency   = (eff_secs/3600.0 / hours * 100.0) if hours else 0.0

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
                'acceptance_rate': accept_rate,
                'trips_per_hour': (trips / hours) if hours else 0.0,
                'money_per_hour': (cash  / hours) if hours else 0.0,
                'utilisation':    (
                                  sum((o.interval_to - o.interval_from).total_seconds()
                                      for o in orders
                                      if o.interval_from and o.interval_to
                                  ) / 3600.0
                                ) / hours * 100.0 if hours else 0.0,
                'efficiency':     efficiency,
                'completed_to_request': (trips / len(orders) * 100.0) if orders else 0.0,
                'service_fee':    cash * 0.10,
                'partner_fee':    cash * 0.03,
            }

        return {
            'metrics': metrics,
            'series':  series,
            'data':    data,
            'allDrivers': total_drivers,
            'distributions': {
                'product':  prod_dist,
                'quality':  qual_dist,
                'category': cat_dist,
            },
        }
