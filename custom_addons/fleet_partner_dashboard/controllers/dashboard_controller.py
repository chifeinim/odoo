# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from datetime import date, datetime, timedelta, timezone
from dateutil.parser import isoparse
from collections import defaultdict
from typing import Optional
import calendar, json, re, os, requests

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

        # --- A) Dates / windows (same semantics as before) ---
        today = date.today()
        mapping: dict[str, int | None] = {
            'Last Week':       7,
            'Last Month':     30,
            'Last 3 Months':  90,
            'All Time':     None,
        }
        want_all_time = False
        if start_date and end_date:
            df = datetime.strptime(start_date, '%Y-%m-%d').date()
            dt_ = datetime.strptime(end_date,   '%Y-%m-%d').date()
            span = (dt_ - df).days + 1
            prev_dt = df - timedelta(days=1)
            prev_df = prev_dt - timedelta(days=span - 1)
        else:
            days = mapping.get(period, 7) if period else 7
            if days is None:
                df = prev_df = dt_ = prev_dt = None
                want_all_time = True
            else:
                df      = today - timedelta(days=days)
                dt_     = today
                prev_df = today - timedelta(days=2 * days)
                prev_dt = today - timedelta(days=days)

        # help funcs for the combined window
        def _min_or(a, b): return min(a, b) if (a and b) else (a or b)
        def _max_or(a, b): return max(a, b) if (a and b) else (a or b)

        big_df = df
        big_dt = dt_ or today
        if prev_df and prev_dt:
            big_df = _min_or(df, prev_df)
            big_dt = _max_or(dt_ or today, prev_dt)

        # All Time → ask metrics-db for everything it has (we still need df/dt for bucketing)
        if df is None and dt_ is None:
            # Just pick a very wide window; metrics-db will return what it has.
            # We’ll set df/dt to today for bucket labels.
            df = today - timedelta(days=90)
            dt_ = today
            big_df = df
            big_dt = dt_

        # --- B) Config / signer endpoints ---
        icp = request.env['ir.config_parameter'].sudo()
        SIGNER_URL     = (icp.get_param('media_signer.base_url') or os.environ.get('SIGNER_URL', '')).rstrip('/')
        SIGNER_API_KEY = icp.get_param('media_signer.api_key')   or os.environ.get('SIGNER_API_KEY')
        TENANT_CODE    = icp.get_param('metrics.tenant_code')    or os.environ.get('TENANT_CODE', 'anda')

        if not SIGNER_URL or not SIGNER_API_KEY:
            return {'error': 'Missing SIGNER_URL or SIGNER_API_KEY in system parameters'}

        def _get(path: str, params: dict) -> dict:
            r = requests.get(
                f"{SIGNER_URL}{path}",
                params=params,
                headers={'x-api-key': SIGNER_API_KEY},
                timeout=30,
            )
            r.raise_for_status()
            return r.json() if r.content else {}

        # Pull a single combined set of driver-day rows covering prev+current
        # Decide the fetch window we send to metrics-db
        if want_all_time:
            fetch_from = "2000-01-01"                       # safely early
            fetch_to   = today.strftime("%Y-%m-%d")         # today
        else:
            # Use the combined prev+current window you already computed
            fetch_from = big_df.strftime('%Y-%m-%d') # type: ignore
            fetch_to   = big_dt.strftime('%Y-%m-%d')

        day_resp = _get(
            '/metrics/driver-day',
            {
                'from_date': fetch_from,
                'to_date':   fetch_to,
                'tenant':    TENANT_CODE,
            }
        )
        day_rows = day_resp.get('rows', [])
        
        if want_all_time:
            if day_rows:
                all_days = []
                for r in day_rows:
                    dstr = r.get('day')
                    if dstr:
                        try:
                            all_days.append(datetime.strptime(dstr, '%Y-%m-%d').date())
                        except Exception:
                            pass
                if all_days:
                    df  = min(all_days)
                    dt_ = max(all_days)
                else:
                    df = dt_ = today
            else:
                df = dt_ = today
            prev_df = prev_dt = None
            big_df, big_dt = df, dt_

        # Pull OTRS snapshot for this tenant
        otrs_resp = _get('/metrics/driver-otrs', {'tenant': TENANT_CODE})
        otrs_rows = otrs_resp.get('rows', [])

        # --- C) Build maps: OTRS by Yango ID, and bucket labels for current window ---
        # OTRS score_band is one of: "Strong" | "Average" | "Weak"
        otrs_by_driver = {}
        for r in otrs_rows:
            yid = r.get('driver_id')
            band = (r.get('score_band') or '').strip().lower()  # -> strong/average/weak
            if yid:
                otrs_by_driver[yid] = band or 'average'

        # Buckets only for current window df..dt_
        def _bucketize_span(df_, dt__):
            span = (dt__ - df_).days + 1
            buckets = []
            if span <= 30:
                cur = df_
                while cur <= dt__:
                    buckets.append((cur, cur, cur.strftime('%Y-%m-%d'), 'day'))
                    cur += timedelta(days=1)
            elif span < 90:
                start = df_ - timedelta(days=df_.weekday())  # Monday
                cur = start
                while cur <= dt__:
                    nxt = cur + timedelta(days=6)
                    buckets.append((cur, min(nxt, dt__), cur.strftime('%Y-%m-%d'), 'week'))
                    cur += timedelta(days=7)
            else:
                y0, m0 = df_.year, df_.month
                y1, m1 = dt__.year, dt__.month
                start_month = y0 * 12 + (m0 - 1)
                end_month   = y1 * 12 + (m1 - 1)
                for ym in range(start_month, end_month + 1):
                    y, mo = divmod(ym, 12); mo += 1
                    start_day = date(y, mo, 1)
                    last_day  = date(y, mo, calendar.monthrange(y, mo)[1])
                    buckets.append((start_day, min(last_day, dt__), start_day.strftime('%Y-%m'), 'month'))
            return buckets

        buckets = _bucketize_span(df, dt_)
        bucket_labels = [lbl for _, _, lbl, _ in buckets]

        def _bucket_key(d):
            for bstart, bend, lbl, _ in buckets:
                if bstart <= d <= bend:
                    return lbl
            return None

        # --- D) Drivers from Odoo + normalize filters ---
        Driver = request.env['x_fleet_driver'].sudo()
        all_drivers = Driver.search([])
        total_drivers = len(all_drivers)

        products   = products   or []
        scores     = scores     or []
        categories = categories or []

        # DQS matrix
        dqs_matrix = {
            'strong':  {'strong': 'High Performer',    'average': 'Average Performer', 'weak': 'Low Performer'},
            'average': {'strong': 'High Performer',    'average': 'Average Performer', 'weak': 'Low Performer'},
            'weak':    {'strong': 'Average Performer', 'average': 'Low Performer',     'weak': 'Low Performer'},
        }

        # Build DQS map keyed by Odoo driver id, using Yango id to look up OTRS
        dqs_map = {}
        prod_counts = defaultdict(int)
        qual_counts = defaultdict(int)
        cat_counts  = defaultdict(int)

        for drv in all_drivers:
            yid = (drv.yango_driver_id or '').strip()
            training = (drv.training_rating or 'average').strip().lower()
            onroad   = otrs_by_driver.get(yid, 'average')
            dqs      = dqs_matrix.get(training, {}).get(onroad, 'Average Performer')
            dqs_map[drv.id] = dqs

            prod_counts[drv.product_type_id.name or 'Unspecified'] += 1
            qual_counts[dqs] += 1
            cat_counts[drv.type or 'Unspecified'] += 1

        prod_dist = [{'label': k, 'value': v} for k, v in prod_counts.items()]
        qual_dist = [{'label': k, 'value': v} for k, v in qual_counts.items()]
        cat_dist  = [{'label': k, 'value': v} for k, v in cat_counts.items()]

        # Apply UI filters to Odoo driver list
        filtered_drivers = [
            d for d in all_drivers
            if (not products   or d.product_type_id.id in products)
            and (not scores    or dqs_map.get(d.id) in scores)
            and (not categories or d.type in categories)
        ]
        filtered_ids = {d.id for d in filtered_drivers}
        filtered_yids = { (d.yango_driver_id or '').strip(): d.id for d in filtered_drivers if d.yango_driver_id }

        # --- E) Aggregate metrics from driver_day rows ---
        # Accumulators (current & previous windows)
        cur = {'orders': 0, 'completes': 0, 'cash': 0.0, 'util_secs': 0.0, 'eff_secs': 0.0, 'accepts': 0, 'sup_secs': 0.0}
        prv = {'orders': 0, 'completes': 0, 'cash': 0.0, 'util_secs': 0.0, 'eff_secs': 0.0, 'accepts': 0, 'sup_secs': 0.0}
        cur_active_set, prv_active_set = set(), set()

        # Per-driver current window for table
        drv_cur = defaultdict(lambda: {'orders': 0, 'completes': 0, 'cash': 0.0, 'util_secs': 0.0, 'eff_secs': 0.0, 'accepts': 0, 'sup_secs': 0.0})

        # Series accumulators by bucket label (current window only)
        series_acc = {
            'orders': defaultdict(int),
            'completes': defaultdict(int),
            'cash': defaultdict(float),
            'util_secs': defaultdict(float),
            'eff_secs': defaultdict(float),
            'accepts': defaultdict(int),
            'sup_secs': defaultdict(float),
            'active_sets': defaultdict(set),
        }

        for r in day_rows:
            try:
                day = datetime.strptime(r.get('day'), '%Y-%m-%d').date()
            except Exception:
                continue

            # restrict to drivers present in Odoo + filtered set
            yid = (r.get('driver_id') or '').strip()
            odoo_id = filtered_yids.get(yid)
            if not odoo_id:
                continue

            orders_total      = int(r.get('orders_total') or 0)
            completes         = int(r.get('orders_completed') or 0)
            cash_sum          = float(r.get('cash_sum') or 0.0)
            accepts           = int(r.get('accepts') or 0)
            util_secs         = float(r.get('interval_seconds') or 0.0)
            eff_secs          = float(r.get('transport_seconds') or 0.0)
            sup_secs          = float(r.get('supply_seconds') or 0.0)

            in_prev = (prev_df and prev_dt and prev_df <= day < prev_dt)
            in_cur  = (df and dt_ and df <= day <= dt_)

            if in_prev:
                prv['orders']     += orders_total
                prv['completes']  += completes
                prv['cash']       += cash_sum
                prv['util_secs']  += util_secs
                prv['eff_secs']   += eff_secs
                prv['accepts']    += accepts
                prv['sup_secs']   += sup_secs
                if completes > 0:
                    prv_active_set.add(odoo_id)

            if in_cur:
                cur['orders']     += orders_total
                cur['completes']  += completes
                cur['cash']       += cash_sum
                cur['util_secs']  += util_secs
                cur['eff_secs']   += eff_secs
                cur['accepts']    += accepts
                cur['sup_secs']   += sup_secs
                if completes > 0:
                    cur_active_set.add(odoo_id)

                # per-driver (current)
                x = drv_cur[odoo_id]
                x['orders']     += orders_total
                x['completes']  += completes
                x['cash']       += cash_sum
                x['util_secs']  += util_secs
                x['eff_secs']   += eff_secs
                x['accepts']    += accepts
                x['sup_secs']   += sup_secs

                # series by bucket
                bk = _bucket_key(day)
                if bk:
                    series_acc['orders'][bk]     += orders_total
                    series_acc['completes'][bk]  += completes
                    series_acc['cash'][bk]       += cash_sum
                    series_acc['util_secs'][bk]  += util_secs
                    series_acc['eff_secs'][bk]   += eff_secs
                    series_acc['accepts'][bk]    += accepts
                    series_acc['sup_secs'][bk]   += sup_secs
                    if completes > 0:
                        series_acc['active_sets'][bk].add(odoo_id)

        # --- F) Derived metrics (same names as before) ---
        def _safe_div(a, b): 
            return (a / b) if b else 0.0

        active_current   = len(cur_active_set)
        active_previous  = len(prv_active_set)
        trip_current     = cur['completes']
        trip_previous    = prv['completes']
        supply_current   = cur['sup_secs'] / 3600.0
        supply_previous  = prv['sup_secs'] / 3600.0
        cash_current     = cur['cash']
        cash_previous    = prv['cash']

        util_pct_current = _safe_div(cur['util_secs']/3600.0, max(supply_current, 1e-12)) * 100.0 if supply_current else 0.0
        util_pct_prev    = _safe_div(prv['util_secs']/3600.0, max(supply_previous, 1e-12)) * 100.0 if supply_previous else 0.0

        eff_pct_current  = _safe_div(cur['eff_secs']/3600.0, max(supply_current, 1e-12)) * 100.0 if supply_current else 0.0
        eff_pct_prev     = _safe_div(prv['eff_secs']/3600.0, max(supply_previous, 1e-12)) * 100.0 if supply_previous else 0.0

        accept_rate_current  = _safe_div(cur['accepts'] * 100.0, max(cur['orders'], 1e-12))
        accept_rate_previous = _safe_div(prv['accepts'] * 100.0, max(prv['orders'], 1e-12))

        completed_to_request_current  = _safe_div(trip_current * 100.0, max(cur['orders'], 1e-12))
        completed_to_request_previous = _safe_div(trip_previous * 100.0, max(prv['orders'], 1e-12))

        avg_supply        = _safe_div(supply_current, active_current) if active_current else 0.0
        avg_supply_prev   = _safe_div(supply_previous, active_previous) if active_previous else 0.0

        metrics = {
            'activeDrivers':     active_current,
            'prevActiveDrivers': active_previous,
            'tripCount':         trip_current,
            'prevTripCount':     trip_previous,
            'supplyHours':       supply_current,
            'prevSupplyHours':   supply_previous,
            'cashEarned':        cash_current,
            'prevCashEarned':    cash_previous,
            'moneyPerHour':      _safe_div(cash_current, max(supply_current, 1e-12)) if supply_current else 0.0,
            'prevMoneyPerHour':  _safe_div(cash_previous, max(supply_previous, 1e-12)) if supply_previous else 0.0,
            'tripsPerHour':      _safe_div(trip_current, max(supply_current, 1e-12)) if supply_current else 0.0,
            'prevTripsPerHour':  _safe_div(trip_previous, max(supply_previous, 1e-12)) if supply_previous else 0.0,
            'avgSupplyHoursPerDriver':     avg_supply,
            'prevAvgSupplyHoursPerDriver': avg_supply_prev,
            'avgUtilisation':     util_pct_current,
            'prevAvgUtilisation': util_pct_prev,
            'avgEfficiency':      eff_pct_current,
            'prevAvgEfficiency':  eff_pct_prev,
            'acceptanceRate':      accept_rate_current,
            'prevAcceptanceRate':  accept_rate_previous,
            'completedToRequest':  completed_to_request_current,
            'prevCompletedToRequest': completed_to_request_previous,
            'serviceFee': cash_current * 0.1,
            'prevServiceFee': cash_previous * 0.1,
            'partnerFee': cash_current * 0.03,
            'prevPartnerFee': cash_previous * 0.03,
        }

        # --- G) Series from per-bucket aggregates (current window only) ---
        series_active, series_trips, series_supply, series_cash = [], [], [], []
        series_mph, series_trph = [], []
        series_avg_supply, series_utilisation, series_efficiency = [], [], []
        series_acceptance, series_completed = [], []
        series_serviceFee, series_partnerFee = [], []

        for lbl in bucket_labels:
            trips     = series_acc['completes'].get(lbl, 0)
            orders    = series_acc['orders'].get(lbl, 0)
            cash_sum  = series_acc['cash'].get(lbl, 0.0)
            sup_hours = series_acc['sup_secs'].get(lbl, 0.0) / 3600.0
            util_h    = series_acc['util_secs'].get(lbl, 0.0) / 3600.0
            eff_h     = series_acc['eff_secs'].get(lbl, 0.0) / 3600.0
            accepts   = series_acc['accepts'].get(lbl, 0)
            active    = len(series_acc['active_sets'].get(lbl, set()))

            series_active.append({'period': lbl, 'value': active})
            series_trips.append({'period': lbl, 'value': trips})
            series_supply.append({'period': lbl, 'value': sup_hours})
            series_cash.append({'period': lbl, 'value': cash_sum})
            series_serviceFee.append({'period': lbl, 'value': cash_sum * 0.1})
            series_partnerFee.append({'period': lbl, 'value': cash_sum * 0.03})

            mph  = _safe_div(cash_sum, max(sup_hours, 1e-12)) if sup_hours else 0.0
            trph = _safe_div(trips,    max(sup_hours, 1e-12)) if sup_hours else 0.0
            series_mph.append({'period': lbl, 'value': mph})
            series_trph.append({'period': lbl, 'value': trph})

            avg_sup = _safe_div(sup_hours, active) if active else 0.0
            series_avg_supply.append({'period': lbl, 'value': avg_sup})

            util_pct = _safe_div(util_h, max(sup_hours, 1e-12)) * 100.0 if sup_hours else 0.0
            eff_pct  = _safe_div(eff_h,  max(sup_hours, 1e-12)) * 100.0 if sup_hours else 0.0
            series_utilisation.append({'period': lbl, 'value': util_pct})
            series_efficiency.append({'period': lbl, 'value': eff_pct})
            acc_pct = _safe_div(accepts * 100.0, max(orders, 1e-12)) if orders else 0.0
            c2r_pct = _safe_div(trips   * 100.0, max(orders, 1e-12)) if orders else 0.0
            series_acceptance.append({'period': lbl, 'value': acc_pct})
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

        # --- H) Per-driver detail table (from aggregates; current window only) ---
        data = {}
        for drv in filtered_drivers:
            s = drv_cur.get(drv.id, {'orders': 0, 'completes': 0, 'cash': 0.0,
                                    'util_secs': 0.0, 'eff_secs': 0.0, 'accepts': 0, 'sup_secs': 0.0})
            hours = s['sup_secs'] / 3600.0
            trips = s['completes']
            orders = s['orders']
            cash   = s['cash']

            accept_rate = _safe_div(s['accepts'] * 100.0, max(orders, 1e-12)) if orders else 0.0
            efficiency  = _safe_div((s['eff_secs']/3600.0) * 100.0, max(hours, 1e-12)) if hours else 0.0
            utilisation = _safe_div((s['util_secs']/3600.0) * 100.0, max(hours, 1e-12)) if hours else 0.0

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
                'trips_per_hour': _safe_div(trips, max(hours, 1e-12)) if hours else 0.0,
                'money_per_hour': _safe_div(cash,  max(hours, 1e-12)) if hours else 0.0,
                'utilisation':    utilisation,
                'efficiency':     efficiency,
                'completed_to_request': _safe_div(trips * 100.0, max(orders, 1e-12)) if orders else 0.0,
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
