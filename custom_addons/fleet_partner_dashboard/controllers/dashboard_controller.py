# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from datetime import date, datetime, timedelta, timezone
from dateutil.parser import isoparse
from collections import defaultdict
from typing import Optional
import calendar, json, re

def _humanize(s):
    if not s:
        return ''
    # turn "bike_breakdown" -> "Bike Breakdown"
    s = re.sub(r'[_\s]+', ' ', str(s)).strip()
    return s.title()

def parse_iso_with_frac(s: str) -> datetime:
    dt = isoparse(s)
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt

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
    d0 = parse_iso_with_frac(t0)
    d1 = parse_iso_with_frac(t1)
    return (d1 - d0).total_seconds()

def _safe_parse_events(evs):
    if not evs:
        return []
    if isinstance(evs, str):
        try:
            return json.loads(evs) or []
        except Exception:
            return []
    return evs if isinstance(evs, list) else []

def _accepted_statuses(statuses):
    # acceptance: reached any of driving|waiting|transporting
    return bool(statuses & {'driving', 'waiting', 'transporting'})

def _transport_secs_from_events(evs_list):
    # transporting -> (complete|cancelled)
    if not evs_list:
        return 0.0
    statuses = {e.get('order_status') for e in evs_list if isinstance(e, dict)}
    if 'transporting' not in statuses:
        return 0.0
    t0 = next((e.get('event_at') for e in evs_list if e.get('order_status') == 'transporting'), None)
    terminal = 'complete' if 'complete' in statuses else ('cancelled' if 'cancelled' in statuses else None)
    t1 = next((e.get('event_at') for e in evs_list if e.get('order_status') == terminal), None) if terminal else None
    if not (t0 and t1):
        return 0.0
    d0 = parse_iso_with_frac(t0)
    d1 = parse_iso_with_frac(t1)
    return max(0.0, (d1 - d0).total_seconds())

def _bucketize_span(df, dt):
    """Return [(start, end, label, kind)] as day/week/month buckets."""
    span = (dt - df).days + 1
    buckets = []
    if span <= 30:
        cur = df
        while cur <= dt:
            buckets.append((cur, cur, cur.strftime('%Y-%m-%d'), 'day'))
            cur += timedelta(days=1)
    elif span < 90:
        start = df - timedelta(days=df.weekday())  # Monday-start week
        cur = start
        while cur <= dt:
            nxt = cur + timedelta(days=6)
            buckets.append((cur, min(nxt, dt), cur.strftime('%Y-%m-%d'), 'week'))
            cur += timedelta(days=7)
    else:
        y0, m0 = df.year, df.month
        y1, m1 = dt.year, dt.month
        start_month = y0 * 12 + (m0 - 1)
        end_month   = y1 * 12 + (m1 - 1)
        for ym in range(start_month, end_month + 1):
            y, mo = divmod(ym, 12)
            mo += 1
            start_day = date(y, mo, 1)
            last_day  = date(y, mo, calendar.monthrange(y, mo)[1])
            buckets.append((start_day, min(last_day, dt), start_day.strftime('%Y-%m'), 'month'))
    return buckets

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
        Issue = request.env['x_fleet_issue'].sudo()
        Driver = request.env['x_fleet_driver'].sudo()
        drivers = Driver.search([], order='name')

        # per-driver breakdown (with human labels)
        result = []
        for drv in drivers:
            issues = Issue.search([('driver_id', '=', drv.id)])
            row = {'name': drv.name, 'phone': drv.phone or '', 'periods': []}

            # counts for sorting
            total_count = len(issues)
            cutoff_7 = today - timedelta(days=7)
            last7_count = len(issues.filtered(lambda i: i.date_reported and i.date_reported.date() >= cutoff_7))

            for days, label in windows:
                if days is None:
                    subset = issues
                else:
                    cutoff = today - timedelta(days=days)
                    subset = issues.filtered(lambda i: i.date_reported and i.date_reported.date() >= cutoff)
                cats = [{
                    'name':  issue.main_category,         # raw (snake_case) – kept if you need it later
                    'label': _humanize(issue.main_category),  # human-friendly for display
                    'color': issue.color,
                } for issue in subset]
                row['periods'].append({'label': label, 'cats': cats})

            # store sort helpers (not rendered by the UI)
            row['_last7'] = last7_count
            row['_total'] = total_count
            result.append(row)

        # Prioritize: drivers with any issues in last 7 days, then any issues at all, then counts, then name
        result.sort(key=lambda r: (
            -(1 if r['_last7'] > 0 else 0),
            -(1 if r['_total'] > 0 else 0),
            -r['_last7'],
            -r['_total'],
            r['name'] or ''
        ))

        # global category summary (with human labels)
        unique_cats = sorted(set(Issue.search([]).mapped('main_category')))
        cats_summary = []
        for cat in unique_cats:
            counts = []
            for days, _ in reversed(windows[:3]):  # 90, 30, 7
                if days is None:
                    domain = [('main_category', '=', cat)]
                else:
                    cutoff = today - timedelta(days=days)
                    domain = [('main_category', '=', cat), ('date_reported', '>=', cutoff)]
                counts.append(Issue.search_count(domain))
            cats_summary.append({'name': cat, 'label': _humanize(cat), 'counts': counts})
        # sort by newest window desc, then next desc…
        cats_summary.sort(key=lambda x: (-x['counts'][0], -x['counts'][1], -x['counts'][2]))

        return {
            'windows':      [lbl for _, lbl in windows],
            'drivers':      result,
            'catsSummary':  cats_summary,
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
    def performance_data(self,
                        period: Optional[str] = None,
                        products=None, scores=None, categories=None,
                        start_date: Optional[str] = None,
                        end_date: Optional[str] = None):

        today = date.today()

        mapping: dict[str, int | None] = {
            'Last Week':       7,
            'Last Month':     30,
            'Last 3 Months':  90,
            'All Time':     None,
        }

        if start_date and end_date:
            df = datetime.strptime(start_date, '%Y-%m-%d').date()
            dt = datetime.strptime(end_date,   '%Y-%m-%d').date()
            span = (dt - df).days + 1
            prev_dt = df - timedelta(days=1)
            prev_df = prev_dt - timedelta(days=span - 1)
        else:
            days = mapping.get(period, 7) if period else 7
            if days is None:
                df = prev_df = dt = prev_dt = None
            else:
                df      = today - timedelta(days=days)
                dt      = today
                prev_df = today - timedelta(days=2 * days)
                prev_dt = today - timedelta(days=days)

        # ---- B) Normalize filters ----
        products   = products   or []
        scores     = scores     or []
        categories = categories or []

        # ---- C) Drivers, DQS, and distributions (no per-driver queries) ----
        Driver = request.env['x_fleet_driver'].sudo()
        all_drivers = Driver.search([])
        total_drivers = len(all_drivers)

        dqs_matrix = {
            'strong':  {'strong': 'High Performer',    'average': 'Average Performer', 'weak': 'Low Performer'},
            'average': {'strong': 'High Performer',    'average': 'Average Performer', 'weak': 'Low Performer'},
            'weak':    {'strong': 'Average Performer', 'average': 'Low Performer',     'weak': 'Low Performer'},
        }

        dqs_map = {}
        prod_counts = defaultdict(int)
        qual_counts = defaultdict(int)
        cat_counts  = defaultdict(int)

        # Keep "training" part; default on-road to Average (avoids heavy per-driver queries).
        for drv in all_drivers:
            training = (drv.training_rating or 'average').lower()
            onroad = 'average'  # simplified to avoid per-driver scans; can be reintroduced later via aggregates
            dqs = dqs_matrix.get(training, {}).get(onroad, 'Average Performer')
            dqs_map[drv.id] = dqs

            prod_counts[drv.product_type_id.name or 'Unspecified'] += 1
            qual_counts[dqs] += 1
            cat_counts[drv.type or 'Unspecified'] += 1

        prod_dist = [{'label': k, 'value': v} for k, v in prod_counts.items()]
        qual_dist = [{'label': k, 'value': v} for k, v in qual_counts.items()]
        cat_dist  = [{'label': k, 'value': v} for k, v in cat_counts.items()]

        # Apply UI filters to drivers ONCE
        filtered_drivers = [
            d for d in all_drivers
            if (not products   or d.product_type_id.id in products)
            and (not scores    or dqs_map.get(d.id) in scores)
            and (not categories or d.type in categories)
        ]
        filtered_ids = {d.id for d in filtered_drivers}

        # ---- D) Build ONE big date window covering prev + current ----
        def _min_or(a, b):
            if a and b: return min(a, b)
            return a or b
        def _max_or(a, b):
            if a and b: return max(a, b)
            return a or b

        big_df = df
        big_dt = dt or today
        if prev_df and prev_dt:
            big_df = _min_or(df, prev_df)
            big_dt = _max_or(dt or today, prev_dt)

        # If All Time, find earliest among orders/supply via tiny min queries
        OrderModelName = Driver._fields['order_ids'].comodel_name
        Order = request.env[OrderModelName].sudo()
        Supply = request.env['x_fleet_driver_supply_hours'].sudo()

        if df is None and dt is None:
            ord_oldest = Order.search_read(
                [('order_date', '!=', False)],
                fields=['order_date'],
                order='order_date asc',
                limit=1
            )
            sup_oldest = Supply.search_read(
                [('date', '!=', False)],
                fields=['date'],
                order='date asc',
                limit=1
            )
            d1 = ord_oldest and ord_oldest[0]['order_date'].date()
            d2 = sup_oldest and sup_oldest[0]['date']
            if d1 and d2:
                big_df = min(d1, d2)
            else:
                big_df = (d1 or d2 or today)
            big_dt = today
            df = big_df
            dt = big_dt
            # prev window becomes None for All Time (as in your original behavior)

        # ---- E) Build buckets ONCE for series ----
        buckets = _bucketize_span(df, dt)
        bucket_labels = [lbl for _, _, lbl, _ in buckets]

        def _bucket_key(d):
            for bstart, bend, lbl, _ in buckets:
                if bstart <= d <= bend:
                    return lbl
            return None

        # ---- F) SUPPLY HOURS: one bulk scan over [big_df..big_dt] ----
        sup_domain = [('date', '>=', big_df), ('date', '<=', big_dt)]
        sup_rows = Supply.with_context(prefetch_fields=False).search_read(
            sup_domain,
            fields=['driver_id', 'date', 'seconds'],
        )

        sup_cur_by_drv   = defaultdict(float)     # seconds (current window)
        sup_prev_by_drv  = defaultdict(float)     # seconds (previous window)
        sup_series_sec   = defaultdict(float)     # seconds by bucket
        sup_tot_cur_sec  = 0.0
        sup_tot_prev_sec = 0.0

        for r in sup_rows:
            drv = r.get('driver_id')
            drv_id = drv and drv[0]
            if not drv_id or drv_id not in filtered_ids:
                continue
            d = r['date']
            secs = float(r.get('seconds') or 0.0)

            # series bucket
            bk = _bucket_key(d)
            if bk:
                sup_series_sec[bk] += secs

            # window splits
            if prev_df and prev_dt and (prev_df <= d < prev_dt):
                sup_prev_by_drv[drv_id] += secs
                sup_tot_prev_sec += secs
            if df and dt and (df <= d <= dt):
                sup_cur_by_drv[drv_id]  += secs
                sup_tot_cur_sec  += secs

        # ---- G) ORDERS: one bulk scan over [big_df..big_dt] ----
        ord_domain = [('order_date', '>=', big_df), ('order_date', '<=', big_dt)]
        ord_rows = Order.with_context(prefetch_fields=False).search_read(
            ord_domain,
            fields=['driver_id', 'order_date', 'status', 'price', 'interval_from', 'interval_to', 'events'],
        )

        cur = {'orders': 0, 'completes': 0, 'cash': 0.0, 'util_secs': 0.0, 'eff_secs': 0.0, 'accepts': 0}
        prev = {'orders': 0, 'completes': 0, 'cash': 0.0, 'util_secs': 0.0, 'eff_secs': 0.0, 'accepts': 0}

        drv_cur = defaultdict(lambda: {'orders': 0, 'completes': 0, 'cash': 0.0,
                                    'util_secs': 0.0, 'eff_secs': 0.0, 'accepts': 0})

        series_acc = {
            'orders': defaultdict(int),
            'completes': defaultdict(int),
            'cash': defaultdict(float),
            'util_secs': defaultdict(float),
            'eff_secs': defaultdict(float),
            'accepts': defaultdict(int),
            'active_sets': defaultdict(set),  # set of driver_ids with at least one complete in bucket
        }

        cur_active_set  = set()
        prev_active_set = set()

        for r in ord_rows:
            drv = r.get('driver_id')
            drv_id = drv and drv[0]
            if not drv_id or drv_id not in filtered_ids:
                continue

            odt = r.get('order_date')
            if not odt:
                continue
            d = odt.date()
            bk = _bucket_key(d)

            status = r.get('status')
            price  = float(r.get('price') or 0.0)

            # utilisation seconds from intervals
            iv_from = r.get('interval_from')
            iv_to   = r.get('interval_to')
            util_s  = 0.0
            if iv_from and iv_to:
                util_s = max(0.0, (iv_to - iv_from).total_seconds())

            # events → acceptance + transport seconds
            evs = _safe_parse_events(r.get('events'))
            statuses = {e.get('order_status') for e in evs if isinstance(e, dict)}
            accepted = 1 if _accepted_statuses(statuses) else 0
            eff_s    = _transport_secs_from_events(evs)

            # series aggregates
            if bk:
                series_acc['orders'][bk]    += 1
                series_acc['cash'][bk]      += price
                series_acc['util_secs'][bk] += util_s
                series_acc['eff_secs'][bk]  += eff_s
                series_acc['accepts'][bk]   += accepted
                if status == 'complete':
                    series_acc['completes'][bk] += 1
                    series_acc['active_sets'][bk].add(drv_id)

            # window splits
            in_prev = (prev_df and prev_dt and prev_df <= d < prev_dt)
            in_cur  = (df and dt and df <= d <= dt)

            if in_prev:
                prev['orders']    += 1
                prev['cash']      += price
                prev['util_secs'] += util_s
                prev['eff_secs']  += eff_s
                prev['accepts']   += accepted
                if status == 'complete':
                    prev['completes'] += 1
                    prev_active_set.add(drv_id)

            if in_cur:
                cur['orders']    += 1
                cur['cash']      += price
                cur['util_secs'] += util_s
                cur['eff_secs']  += eff_s
                cur['accepts']   += accepted
                if status == 'complete':
                    cur['completes'] += 1
                    cur_active_set.add(drv_id)

                # per-driver detail for current window
                x = drv_cur[drv_id]
                x['orders']    += 1
                x['cash']      += price
                x['util_secs'] += util_s
                x['eff_secs']  += eff_s
                x['accepts']   += accepted
                if status == 'complete':
                    x['completes'] += 1

        # ---- H) Metrics (derived from aggregates) ----
        active_current   = len(cur_active_set)
        active_previous  = len(prev_active_set)
        trip_current     = cur['completes']
        trip_previous    = prev['completes']
        supply_current   = sup_tot_cur_sec  / 3600.0
        supply_previous  = sup_tot_prev_sec / 3600.0
        cash_current     = cur['cash']
        cash_previous    = prev['cash']
        util_secs_current  = cur['util_secs']
        util_secs_previous = prev['util_secs']
        eff_secs_current   = cur['eff_secs']
        eff_secs_previous  = prev['eff_secs']
        accept_num_current  = cur['accepts']
        accept_num_previous = prev['accepts']
        order_num_current   = cur['orders']
        order_num_previous  = prev['orders']

        avg_supply        = (supply_current / active_current) if active_current else 0.0
        avg_supply_prev   = (supply_previous / active_previous) if active_previous else 0.0

        avg_util_current  = (util_secs_current/3600.0 / supply_current * 100.0) if supply_current else 0.0
        avg_util_previous = (util_secs_previous/3600.0 / supply_previous * 100.0) if supply_previous else 0.0

        avg_eff_current   = (eff_secs_current/3600.0 / supply_current * 100.0) if supply_current else 0.0
        avg_eff_previous  = (eff_secs_previous/3600.0 / supply_previous * 100.0) if supply_previous else 0.0

        accept_rate_current  = (accept_num_current / order_num_current * 100.0) if order_num_current else 0.0
        accept_rate_previous = (accept_num_previous / order_num_previous * 100.0) if order_num_previous else 0.0

        completed_to_request_current  = (trip_current / order_num_current * 100.0) if order_num_current else 0.0
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
            'completedToRequest':  completed_to_request_current,
            'prevCompletedToRequest': completed_to_request_previous,
            'serviceFee': cash_current * 0.1,
            'prevServiceFee': cash_previous * 0.1,
            'partnerFee': cash_current * 0.03,
            'prevPartnerFee': cash_previous * 0.03,
        }

        # ---- I) Series (assembled from the per-bucket dicts) ----
        series_active = []
        series_trips  = []
        series_supply = []
        series_cash   = []
        series_mph    = []
        series_trph   = []
        series_avg_supply = []
        series_utilisation = []
        series_efficiency  = []
        series_acceptance  = []
        series_completed   = []
        series_serviceFee  = []
        series_partnerFee  = []

        for lbl in bucket_labels:
            trips     = series_acc['completes'].get(lbl, 0)
            orders    = series_acc['orders'].get(lbl, 0)
            cash_sum  = series_acc['cash'].get(lbl, 0.0)
            sup_hours = (sup_series_sec.get(lbl, 0.0) / 3600.0)
            util_h    = (series_acc['util_secs'].get(lbl, 0.0) / 3600.0)
            eff_h     = (series_acc['eff_secs'].get(lbl, 0.0) / 3600.0)
            accepts   = series_acc['accepts'].get(lbl, 0)
            active    = len(series_acc['active_sets'].get(lbl, set()))

            series_active.append({'period': lbl, 'value': active})
            series_trips.append({'period': lbl, 'value': trips})
            series_supply.append({'period': lbl, 'value': sup_hours})
            series_cash.append({'period': lbl, 'value': cash_sum})
            series_serviceFee.append({'period': lbl, 'value': cash_sum * 0.1})
            series_partnerFee.append({'period': lbl, 'value': cash_sum * 0.03})

            mph  = (cash_sum / sup_hours) if sup_hours else 0.0
            trph = (trips    / sup_hours) if sup_hours else 0.0
            series_mph.append({'period': lbl, 'value': mph})
            series_trph.append({'period': lbl, 'value': trph})

            avg_sup = (sup_hours / active) if active else 0.0
            series_avg_supply.append({'period': lbl, 'value': avg_sup})

            util_pct = (util_h / sup_hours * 100.0) if sup_hours else 0.0
            eff_pct  = (eff_h  / sup_hours * 100.0) if sup_hours else 0.0
            series_utilisation.append({'period': lbl, 'value': util_pct})
            series_efficiency.append({'period': lbl, 'value': eff_pct})
            acc_pct = (accepts / orders * 100.0) if orders else 0.0
            series_acceptance.append({'period': lbl, 'value': acc_pct})
            c2r_pct = (trips   / orders * 100.0) if orders else 0.0
            series_completed.append({'period': lbl, 'value': c2r_pct})

        series = {
            'activeDrivers': series_active,
            'trips':         series_trips,
            'supplyHours':   series_supply,
            'cashEarned':    series_cash,
            'moneyPerHour':  series_mph,
            'tripsPerHour':  series_trph,
            'avgSupplyHoursPerDriver': series_avg_supply,
            'utilisation':   series_utilisation,
            'efficiency':    series_efficiency,
            'acceptanceRate': series_acceptance,
            'completedToRequest': series_completed,
            'serviceFee':    series_serviceFee,
            'partnerFee':    series_partnerFee,
        }

        # ---- J) Per-driver detail table (from aggregates; no re-filtering) ----
        data = {}
        for drv in filtered_drivers:
            sums = drv_cur.get(drv.id, {'orders': 0, 'completes': 0, 'cash': 0.0,
                                        'util_secs': 0.0, 'eff_secs': 0.0, 'accepts': 0})
            hours = (sup_cur_by_drv.get(drv.id, 0.0) / 3600.0)
            trips = sums['completes']
            orders = sums['orders']
            cash   = sums['cash']

            accept_rate = (sums['accepts'] / orders * 100.0) if orders else 0.0
            efficiency  = ((sums['eff_secs']/3600.0)/hours*100.0) if hours else 0.0
            utilisation = ((sums['util_secs']/3600.0)/hours*100.0) if hours else 0.0

            data[drv.id] = {
                'id':             drv.id,
                'name':           drv.name,
                'phone':          drv.phone or '',
                'hire_date':      drv.hire_date and drv.hire_date.strftime('%Y-%m-%d'),
                'product_type':   drv.product_type_id.name or '',
                'type':           drv.type,
                'quality_score':  dqs_map.get(drv.id),
                'active':         trips > 0,
                'cash':           cash,
                'trips':          trips,
                'hours':          hours,
                'acceptance_rate': accept_rate,
                'trips_per_hour': (trips / hours) if hours else 0.0,
                'money_per_hour': (cash  / hours) if hours else 0.0,
                'utilisation':    utilisation,
                'efficiency':     efficiency,
                'completed_to_request': (trips / orders * 100.0) if orders else 0.0,
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
