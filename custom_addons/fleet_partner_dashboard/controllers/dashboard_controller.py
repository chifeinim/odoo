# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from datetime import date, datetime, timedelta, timezone
from dateutil.parser import isoparse
from collections import defaultdict
from typing import Optional
import calendar, json, re, os, requests, logging
_logger = logging.getLogger(__name__)

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

def _safe_div(a, b):
    return (a / b) if b else 0.0

def _last_week_range(today: date) -> tuple[date, date]:
    """Previous full Monday–Sunday week."""
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    return last_monday, last_sunday

def _this_week_range(today: date) -> tuple[date, date]:
    """Current Mon–Sun week, but end at yesterday to avoid partial 'today' metrics."""
    monday = today - timedelta(days=today.weekday())
    end = min(monday + timedelta(days=6), today - timedelta(days=1))
    if end < monday:
        end = monday  # if it's Monday, clamp to Monday
    return monday, end

def _last_7_days_excl_today(today: date) -> tuple[date, date]:
    """Last 7 calendar days, ending yesterday."""
    end = today - timedelta(days=1)
    start = end - timedelta(days=6)
    return start, end

def _last_month_range(today: date) -> tuple[date, date]:
    """Previous full calendar month."""
    first_this_month = date(today.year, today.month, 1)
    last_of_last_month = first_this_month - timedelta(days=1)
    first_of_last_month = date(last_of_last_month.year, last_of_last_month.month, 1)
    return first_of_last_month, last_of_last_month

def _last_n_months_range(today: date, n: int) -> tuple[date, date]:
    """Previous N full calendar months, ending with last month."""
    first_this_month = date(today.year, today.month, 1)
    end = first_this_month - timedelta(days=1)  # last day of last month
    # compute first day N-1 months before `end`'s month
    total = end.year * 12 + (end.month - 1) - (n - 1)
    start_year, start_month_index = divmod(total, 12)
    start_month = start_month_index + 1
    start = date(start_year, start_month, 1)
    return start, end

def _previous_period(df: date, dt_: date) -> tuple[date, date]:
    """Immediately preceding period of equal length to df..dt_."""
    length = (dt_ - df).days + 1
    prev_dt = df - timedelta(days=1)
    prev_df = prev_dt - timedelta(days=length - 1)
    return prev_df, prev_dt

class FleetDashboardController(http.Controller):

    @http.route('/fleet_partner_dashboard/data', type='json', auth='user')
    def dashboard_data(self):

        today = date.today()
        Issue = request.env['x_fleet_issue'].sudo()
        Driver = request.env['x_fleet_driver'].sudo()
        drivers = Driver.search([], order='name')

        # windows now define labels and explicit (start, end) ranges
        windows = [
            ('Last Week',      *_last_week_range(today)),
            ('Last Month',     *_last_month_range(today)),
            ('Last 3 Months',  *_last_n_months_range(today, 3)),
            ('All Time',       None, None),
        ]

        # per-driver breakdown (with human labels)
        result = []
        for drv in drivers:
            issues = Issue.search([('driver_id', '=', drv.id)])
            row = {'name': drv.name, 'phone': drv.phone or '', 'periods': []}

            # counts for sorting
            lw_df, lw_dt = _last_week_range(today)
            last7_count = len(issues.filtered(lambda i: i.date_reported and lw_df <= i.date_reported.date() <= lw_dt))
            total_count = len(issues)

            for label, start, end in windows:
                if start and end:
                    subset = issues.filtered(lambda i: i.date_reported and start <= i.date_reported.date() <= end)
                else:
                    subset = issues
                cats = [{
                    'name':  issue.main_category,
                    'label': _humanize(issue.main_category),
                    'color': issue.color,
                } for issue in subset]
                row['periods'].append({'label': label, 'cats': cats})

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

        # use (Last 3 Months, Last Month, Last Week) in that order for sorting signals
        range_map = {
            'Last 3 Months': _last_n_months_range(today, 3),
            'Last Month':    _last_month_range(today),
            'Last Week':     _last_week_range(today),
        }

        for cat in unique_cats:
            counts = []
            for lbl in ('Last 3 Months', 'Last Month', 'Last Week'):
                df_, dt_ = range_map[lbl]
                domain = [('main_category', '=', cat), ('date_reported', '>=', df_), ('date_reported', '<=', dt_)]
                counts.append(Issue.search_count(domain))
            cats_summary.append({'name': cat, 'label': _humanize(cat), 'counts': counts})

        # sort by newest window desc, then next…
        cats_summary.sort(key=lambda x: (-x['counts'][2], -x['counts'][1], -x['counts'][0]))

        return {
            'windows': [w[0] for w in windows],  # ['Last Week', 'Last Month', 'Last 3 Months', 'All Time']
            'drivers': result,
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
    def performance_data(self,
                        period: Optional[str] = None,
                        products=None, scores=None, categories=None,
                        start_date: Optional[str] = None,
                        end_date: Optional[str] = None):

        # --- A) Dates / windows (same semantics as before) ---
        today = date.today()
        want_all_time = False

        if start_date and end_date:
            # keep custom range behavior the same (respect user input)
            df = datetime.strptime(start_date, '%Y-%m-%d').date()
            dt_ = datetime.strptime(end_date,   '%Y-%m-%d').date()
            if df > dt_:
                df, dt_ = dt_, df
            prev_df, prev_dt = _previous_period(df, dt_)
        else:
            if period == 'All Time':
                df = prev_df = dt_ = prev_dt = None
                want_all_time = True
            elif period == 'Last Month':
                df, dt_ = _last_month_range(today)
                prev_df, prev_dt = _previous_period(df, dt_)
            elif period == 'Last 3 Months':
                df, dt_ = _last_n_months_range(today, 3)
                prev_df, prev_dt = _previous_period(df, dt_)
            else:
                # default to 'Last Week'
                df, dt_ = _last_week_range(today)
                prev_df, prev_dt = _previous_period(df, dt_)

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
            try:
                r = requests.get(
                    f"{SIGNER_URL}{path}",
                    params=params,
                    headers={"x-api-key": SIGNER_API_KEY},
                    timeout=30,
                )

                # Be forgiving on client errors (e.g., bad date range)
                if 400 <= r.status_code < 500:
                    _logger.warning(
                        "Signer %s returned %s: %s",
                        path, r.status_code, (r.text or "")[:200],
                    )
                    return {"rows": [], "count": 0}

                r.raise_for_status()

                # Try to parse JSON and coerce to the expected shape
                try:
                    data = r.json()
                except ValueError:
                    _logger.warning("Signer %s returned non-JSON", path)
                    return {"rows": [], "count": 0}

                if isinstance(data, dict) and "rows" in data and "count" in data:
                    return data
                if isinstance(data, list):
                    return {"rows": data, "count": len(data)}

                return {"rows": [], "count": 0}

            except requests.Timeout:
                _logger.warning("Signer request timed out: %s", path)
                return {"rows": [], "count": 0}
            except requests.RequestException as e:
                _logger.exception("Signer request failed: %s", e)
                return {"rows": [], "count": 0}

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
        cur = {'orders': 0, 'completes': 0, 'cash': 0.0, 'util_secs': 0.0, 'eff_secs': 0.0, 'accepts': 0, 'sup_secs': 0.0, 'driver_cancels': 0}
        prv = {'orders': 0, 'completes': 0, 'cash': 0.0, 'util_secs': 0.0, 'eff_secs': 0.0, 'accepts': 0, 'sup_secs': 0.0, 'driver_cancels': 0}
        cur_active_set, prv_active_set = set(), set()

        # Per-driver current window for table
        drv_cur = defaultdict(lambda: {'orders': 0, 'completes': 0, 'cash': 0.0, 'util_secs': 0.0, 'eff_secs': 0.0, 'accepts': 0, 'sup_secs': 0.0, 'driver_cancels': 0})

        # Series accumulators by bucket label (current window only)
        series_acc = {
            'orders': defaultdict(int),
            'completes': defaultdict(int),
            'cash': defaultdict(float),
            'util_secs': defaultdict(float),
            'eff_secs': defaultdict(float),
            'accepts': defaultdict(int),
            'sup_secs': defaultdict(float),
            'driver_cancels': defaultdict(int),
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
            driver_cancels = int(r.get('driver_cancellations') or 0)

            in_prev = (prev_df and prev_dt and prev_df <= day <= prev_dt)
            in_cur  = (df and dt_ and df <= day <= dt_)

            if in_prev:
                prv['orders']     += orders_total
                prv['completes']  += completes
                prv['cash']       += cash_sum
                prv['util_secs']  += util_secs
                prv['eff_secs']   += eff_secs
                prv['accepts']    += accepts
                prv['sup_secs']   += sup_secs
                prv['driver_cancels'] += driver_cancels
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
                cur['driver_cancels'] += driver_cancels
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
                x['driver_cancels'] += driver_cancels

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
                    series_acc['driver_cancels'][bk] += driver_cancels
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
        driver_cancellations_current  = cur['driver_cancels']
        driver_cancellations_previous = prv['driver_cancels']

        util_pct_current = _safe_div(cur['util_secs']/3600.0, max(supply_current, 1e-12)) * 100.0 if supply_current else 0.0
        util_pct_prev    = _safe_div(prv['util_secs']/3600.0, max(supply_previous, 1e-12)) * 100.0 if supply_previous else 0.0

        eff_pct_current  = _safe_div(cur['eff_secs']/3600.0, max(supply_current, 1e-12)) * 100.0 if supply_current else 0.0
        eff_pct_prev     = _safe_div(prv['eff_secs']/3600.0, max(supply_previous, 1e-12)) * 100.0 if supply_previous else 0.0

        accept_rate_current  = _safe_div(cur['accepts'] * 100.0, max(cur['orders'], 1e-12))
        accept_rate_previous = _safe_div(prv['accepts'] * 100.0, max(prv['orders'], 1e-12))

        completed_to_request_current  = _safe_div(trip_current * 100.0, max(cur['orders'], 1e-12))
        completed_to_request_previous = _safe_div(trip_previous * 100.0, max(prv['orders'], 1e-12))
        
        completion_rate_current  = _safe_div(trip_current * 100.0, max(cur['accepts'], 1e-12))
        completion_rate_previous = _safe_div(trip_previous * 100.0, max(prv['accepts'], 1e-12))
        
        cancelled_by_driver_pct_current  = _safe_div(cur['driver_cancels'] * 100.0, max(cur['accepts'], 1e-12)) if cur['accepts'] else 0.0
        cancelled_by_driver_pct_previous = _safe_div(prv['driver_cancels'] * 100.0, max(prv['accepts'], 1e-12)) if prv['accepts'] else 0.0

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
            'completionRate':       completion_rate_current,
            'prevCompletionRate':   completion_rate_previous,
            'cancelledByDriverPct':     cancelled_by_driver_pct_current,
            'prevCancelledByDriverPct': cancelled_by_driver_pct_previous,
            'serviceFee': cash_current * 0.1,
            'prevServiceFee': cash_previous * 0.1,
            'partnerFee': cash_current * 0.03,
            'prevPartnerFee': cash_previous * 0.03,
        }

        # --- G) Series from per-bucket aggregates (current window only) ---
        series_active, series_trips, series_supply, series_cash = [], [], [], []
        series_mph, series_trph = [], []
        series_avg_supply, series_utilisation, series_efficiency = [], [], []
        series_acceptance, series_completed, series_completion = [], [], []
        series_driverCancels, series_cancelledByDriver = [], []
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
            cmp_r_pct = _safe_div(trips   * 100.0, max(accepts, 1e-12)) if accepts else 0.0
            series_acceptance.append({'period': lbl, 'value': acc_pct})
            series_completed.append({'period': lbl, 'value': c2r_pct})
            series_completion.append({'period': lbl, 'value': cmp_r_pct})
            const_driver_cancels = series_acc['driver_cancels'].get(lbl, 0)
            series_driverCancels.append({'period': lbl, 'value': const_driver_cancels})

            const_cancelled_by_driver_pct = _safe_div(const_driver_cancels * 100.0, max(accepts, 1e-12)) if accepts else 0.0
            series_cancelledByDriver.append({'period': lbl, 'value': const_cancelled_by_driver_pct})

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
            'completionRate': series_completion,
            'serviceFee':    series_serviceFee,
            'partnerFee':    series_partnerFee,
            'driverCancellations':   series_driverCancels,
            'cancelledByDriverPct':  series_cancelledByDriver,
        }

        # --- H) Per-driver detail table (from aggregates; current window only) ---
        data = {}
        for drv in filtered_drivers:
            s = drv_cur.get(drv.id, {'orders': 0, 'completes': 0, 'cash': 0.0,
                                    'util_secs': 0.0, 'eff_secs': 0.0, 'accepts': 0, 'sup_secs': 0.0, 'driver_cancels': 0, })
            hours = s['sup_secs'] / 3600.0
            trips = s['completes']
            orders = s['orders']
            cash   = s['cash']
            driver_cancels = s['driver_cancels']

            accept_rate = _safe_div(s['accepts'] * 100.0, max(orders, 1e-12)) if orders else 0.0
            efficiency  = _safe_div((s['eff_secs']/3600.0) * 100.0, max(hours, 1e-12)) if hours else 0.0
            utilisation = _safe_div((s['util_secs']/3600.0) * 100.0, max(hours, 1e-12)) if hours else 0.0
            cancelled_by_driver = _safe_div(driver_cancels * 100.0, max(s['accepts'], 1e-12)) if s['accepts'] else 0.0

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
                'completion_rate': _safe_div(trips * 100.0, max(s['accepts'], 1e-12)) if s['accepts'] else 0.0,
                'cancelled_by_driver':  cancelled_by_driver,
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
        
    @http.route('/fleet_low_performers/filters', type='json', auth='user')
    def low_perf_filters(self):
        Product = request.env['x_fleet_product_type'].sudo()
        pts = Product.search([], order='name')
        product_types = [{'id': p.id, 'name': p.name} for p in pts]

        Driver = request.env['x_fleet_driver'].sudo()
        categories = [k for k, _ in Driver._fields['type'].selection]  # same as perf dash
        # risk filter options
        risks = [{'key': 'High', 'label': 'High'}, {'key': 'Medium', 'label': 'Medium'}]
        periods = ['This Week', 'Last Week', 'Last Month', 'Last 3 Months']

        return {
            'product_types': product_types,
            'categories':    categories,
            'risks':         risks,
            'time_periods':  periods,
        }
        
    @http.route('/fleet_low_performers/data', type='json', auth='user')
    def low_perf_data(self,
                    period: Optional[str] = None,
                    products=None, scores=None, categories=None, risks=None,
                    start_date: Optional[str] = None,
                    end_date: Optional[str] = None):
        """
        Build the Low Performers table dataset.

        - Risk is computed from the last 7 days (ending yesterday) using:
            High    if avg daily hours <= 2 OR avg daily trips <= 2
            Medium  if (2 < avg daily hours < 5) OR (2 < avg daily trips < 5)
        (High takes precedence if one metric <= 2.)
        - Table metrics (trips, hours, cash, rates, issues_flag) are computed
        for the user-selected date window below.
        """
        today = date.today()

        # --- A) Date window for the TABLE (not the risk) ---
        if start_date and end_date:
            df = datetime.strptime(start_date, '%Y-%m-%d').date()
            dt_ = datetime.strptime(end_date,   '%Y-%m-%d').date()
            if df > dt_:
                df, dt_ = dt_, df
        else:
            if period == 'This Week':
                df, dt_ = _this_week_range(today)
            elif period == 'Last Month':
                df, dt_ = _last_month_range(today)
            elif period == 'Last 3 Months':
                df, dt_ = _last_n_months_range(today, 3)
            else:
                # default Last Week (Mon–Sun)
                df, dt_ = _last_week_range(today)

        # Clamp table end to yesterday to avoid partial 'today' metrics from metrics-db
        dt_ = min(dt_, today - timedelta(days=1))

        # --- B) Config + signer fetch (mirrors performance_data) ---
        icp = request.env['ir.config_parameter'].sudo()
        SIGNER_URL     = (icp.get_param('media_signer.base_url') or os.environ.get('SIGNER_URL', '')).rstrip('/')
        SIGNER_API_KEY = icp.get_param('media_signer.api_key')   or os.environ.get('SIGNER_API_KEY')
        TENANT_CODE    = icp.get_param('metrics.tenant_code')    or os.environ.get('TENANT_CODE', 'anda')
        if not SIGNER_URL or not SIGNER_API_KEY:
            return {'error': 'Missing SIGNER_URL or SIGNER_API_KEY in system parameters'}

        def _get(path: str, params: dict) -> dict:
            try:
                r = requests.get(
                    f"{SIGNER_URL}{path}",
                    params=params,
                    headers={"x-api-key": SIGNER_API_KEY},
                    timeout=30,
                )
                if 400 <= r.status_code < 500:
                    _logger.warning("Signer %s returned %s: %s", path, r.status_code, (r.text or "")[:200])
                    return {"rows": [], "count": 0}
                r.raise_for_status()
                try:
                    data = r.json()
                except ValueError:
                    _logger.warning("Signer %s returned non-JSON", path)
                    return {"rows": [], "count": 0}
                if isinstance(data, dict) and "rows" in data and "count" in data:
                    return data
                if isinstance(data, list):
                    return {"rows": data, "count": len(data)}
                return {"rows": [], "count": 0}
            except requests.Timeout:
                _logger.warning("Signer request timed out: %s", path)
                return {"rows": [], "count": 0}
            except requests.RequestException as e:
                _logger.exception("Signer request failed: %s", e)
                return {"rows": [], "count": 0}

        # --- C) Pull driver-day rows for:
        #       (1) the table window df..dt_
        #       (2) the risk window (last 7 days ending yesterday)
        risk_df, risk_dt = _last_7_days_excl_today(today)

        def _pull_day_rows(_from: date, _to: date):
            resp = _get('/metrics/driver-day', {
                'from_date': _from.strftime('%Y-%m-%d'),
                'to_date':   _to.strftime('%Y-%m-%d'),
                'tenant':    TENANT_CODE,
            })
            return resp.get('rows', [])

        table_rows = _pull_day_rows(df, dt_)
        risk_rows  = _pull_day_rows(risk_df, risk_dt)

        # --- D) Odoo drivers + filters (same model fields you use elsewhere) ---
        Driver = request.env['x_fleet_driver'].sudo()
        all_drivers = Driver.search([])
        products   = products   or []
        scores     = scores     or []
        categories = categories or []
        risks      = risks      or []  # ['High','Medium'] or empty (treated as both)

        # DQS matrix & OTRS (copied from performance_data)
        otrs_resp = _get('/metrics/driver-otrs', {'tenant': TENANT_CODE})
        otrs_rows = otrs_resp.get('rows', [])
        otrs_by_driver = {}
        for r in otrs_rows:
            yid = (r.get('driver_id') or '').strip()
            band = (r.get('score_band') or '').strip().lower()  # strong/average/weak
            if yid:
                otrs_by_driver[yid] = band or 'average'

        dqs_matrix = {
            'strong':  {'strong': 'High Performer',    'average': 'Average Performer', 'weak': 'Low Performer'},
            'average': {'strong': 'High Performer',    'average': 'Average Performer', 'weak': 'Low Performer'},
            'weak':    {'strong': 'Average Performer', 'average': 'Low Performer',     'weak': 'Low Performer'},
        }
        dqs_map = {}
        for drv in all_drivers:
            yid = (drv.yango_driver_id or '').strip()
            training = (drv.training_rating or 'average').strip().lower()
            onroad   = otrs_by_driver.get(yid, 'average')
            dqs_map[drv.id] = dqs_matrix.get(training, {}).get(onroad, 'Average Performer')

        # --- E) Index rows by odoo driver id (metrics rows use Yango driver_id) ---
        # day row fields we expect: driver_id (yango id), day, completes, orders, accepts, sup_seconds, util_seconds, eff_seconds, cash
        def _by_odoo_id(rows):
            # map Yango ID -> Odoo driver ids
            y2o = {}
            for d in all_drivers:
                y = (d.yango_driver_id or '').strip()
                if y:
                    y2o[y] = d.id
            out = defaultdict(list)
            for r in rows:
                y = (r.get('driver_id') or '').strip()
                oid = y2o.get(y)
                if not oid:
                    continue
                out[oid].append(r)
            return out

        table_by_drv = _by_odoo_id(table_rows)
        risk_by_drv  = _by_odoo_id(risk_rows)

        # --- F) Compute risk from last 7 days ---
        def _risk_for(rows7: list) -> Optional[str]:
            trips     = sum(int(r.get('orders_completed') or 0) for r in rows7)
            sup_secs  = sum(float(r.get('supply_seconds') or 0.0) for r in rows7)
            # divide by 6 instead of 7, since drivers are only expected to work 6 days a week
            avg_trips = trips / 6.0
            avg_hours = (sup_secs / 3600.0) / 6.0
            if avg_hours <= 2.0 or avg_trips <= 2.0:
                return 'High'
            if (2.0 < avg_hours < 5.0) or (2.0 < avg_trips < 5.0):
                return 'Medium'
            return None

        # --- G) Build table data for selected window, filtered to High/Medium ---
        Issue = request.env['x_fleet_issue'].sudo()
        data = {}
        for drv in all_drivers:
            rid = _risk_for(risk_by_drv.get(drv.id, []))
            if rid not in ('High', 'Medium'):
                continue
            if risks and rid not in risks:
                continue
            if products and drv.product_type_id.id not in products:
                continue
            if scores and dqs_map.get(drv.id) not in scores:
                continue
            # Categories (archive normally unticked on frontend)
            if categories and drv.type not in categories:
                continue

            # Aggregates in the TABLE window (use same field names as performance_data)
            rows = table_by_drv.get(drv.id, [])
            orders_total  = sum(int(r.get('orders_total') or 0)      for r in rows)
            accepts       = sum(int(r.get('accepts') or 0)           for r in rows)
            completes     = sum(int(r.get('orders_completed') or 0)  for r in rows)
            cash_sum      = sum(float(r.get('cash_sum') or 0.0)      for r in rows)
            sup_seconds   = sum(float(r.get('supply_seconds') or 0.0) for r in rows)

            hours  = sup_seconds / 3600.0
            acc_pct = _safe_div(accepts * 100.0, max(orders_total, 1e-12)) if orders_total else 0.0
            cmp_pct = _safe_div(completes * 100.0, max(accepts, 1e-12))     if accepts else 0.0

            # Issues flag within the TABLE window
            issues_flag = False
            if df and dt_:
                issues_flag = bool(Issue.search_count([
                    ('driver_id', '=', drv.id),
                    ('date_reported', '>=', datetime.combine(df, datetime.min.time())),
                    ('date_reported', '<=', datetime.combine(dt_, datetime.max.time())),
                ]))

            data[drv.id] = {
                'id': drv.id,
                'name': drv.name,
                'phone': drv.phone or '',
                'risk': rid,
                'trips': completes,
                'hours': hours,
                'cash': cash_sum,
                'acceptance_rate': acc_pct,
                'completion_rate': cmp_pct,
                'issues_reported': 'Yes' if issues_flag else 'No',
                'product_type': drv.product_type_id.name or '',
                'type': drv.type or '',
                'quality_score': dqs_map.get(drv.id),
                'hire_date': drv.hire_date and drv.hire_date.strftime('%Y-%m-%d'),
            }

        # Frontend does sorting/paging; we just ship the rows
        return {
            'data': data,
            'meta': {
                'table_from': df.strftime('%Y-%m-%d'),
                'table_to':   dt_.strftime('%Y-%m-%d'),
                'risk_from':  risk_df.strftime('%Y-%m-%d'),
                'risk_to':    risk_dt.strftime('%Y-%m-%d'),
            },
        }
        
    @http.route('/fleet_low_performers/driver_detail', type='json', auth='user')
    def low_perf_driver_detail(self, driver_id: int, start_date: str, end_date: str):
        """
        Return per-driver aggregates for the selected date window, bucketed series
        using the same bucketing rule as performance dashboard, and the driver's issues
        (newest first) with "Main, Sub, Sub Sub" text.
        """
        today = date.today()
        df = datetime.strptime(start_date, '%Y-%m-%d').date()
        dt_ = datetime.strptime(end_date,   '%Y-%m-%d').date()
        # Avoid partial today from metrics-db
        dt_ = min(dt_, today - timedelta(days=1))

        icp = request.env['ir.config_parameter'].sudo()
        SIGNER_URL     = (icp.get_param('media_signer.base_url') or os.environ.get('SIGNER_URL', '')).rstrip('/')
        SIGNER_API_KEY = icp.get_param('media_signer.api_key')   or os.environ.get('SIGNER_API_KEY')
        TENANT_CODE    = icp.get_param('metrics.tenant_code')    or os.environ.get('TENANT_CODE', 'anda')
        if not SIGNER_URL or not SIGNER_API_KEY:
            return {'error': 'Missing signer config'}

        def _get(path: str, params: dict) -> dict:
            try:
                r = requests.get(f"{SIGNER_URL}{path}", params=params, headers={"x-api-key": SIGNER_API_KEY}, timeout=30)
                if 400 <= r.status_code < 500:
                    _logger.warning("Signer %s returned %s: %s", path, r.status_code, (r.text or "")[:200])
                    return {"rows": [], "count": 0}
                r.raise_for_status()
                try:
                    data = r.json()
                except ValueError:
                    return {"rows": [], "count": 0}
                if isinstance(data, dict) and "rows" in data and "count" in data:
                    return data
                if isinstance(data, list):
                    return {"rows": data, "count": len(data)}
                return {"rows": [], "count": 0}
            except requests.RequestException:
                _logger.exception("Signer request failed")
                return {"rows": [], "count": 0}

        # map this driver to its Yango id
        Driver = request.env['x_fleet_driver'].sudo()
        drv = Driver.browse(int(driver_id))
        yid = (drv.yango_driver_id or '').strip()
        if not drv or not yid:
            return {'data': {}, 'series': {}, 'issues': []}

        # Pull rows for just this driver
        day_resp = _get('/metrics/driver-day', {
            'from_date': df.strftime('%Y-%m-%d'),
            'to_date':   dt_.strftime('%Y-%m-%d'),
            'tenant':    TENANT_CODE,
            'driver_id': yid,    # if signer supports filtering by driver_id; if not, we’ll filter after
        })
        rows = day_resp.get('rows', [])
        # Filter to this yid if API ignores driver_id
        rows = [r for r in rows if (r.get('driver_id') or '').strip() == yid]

        # Bucketing rule exactly like performance dashboard
        def _bucketize_span(df_, dt__):
            span = (dt__ - df_).days + 1
            buckets = []
            if span <= 30:
                cur = df_
                while cur <= dt__:
                    buckets.append((cur, cur, cur.strftime('%Y-%m-%d'), 'day'))
                    cur += timedelta(days=1)
            elif span < 90:
                start = df_ - timedelta(days=df_.weekday())
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
        labels = [lbl for _, _, lbl, _ in buckets]

        # Prepare series accumulators
        acc = {
            'orders': defaultdict(int),
            'completes': defaultdict(int),
            'cash': defaultdict(float),
            'accepts': defaultdict(int),
            'sup_secs': defaultdict(float),
            'util_secs': defaultdict(float),
            'eff_secs': defaultdict(float),
        }

        def _bucket_label(d):
            for b0, b1, lbl, _ in buckets:
                if b0 <= d <= b1:
                    return lbl
            return None

        # Aggregate per bucket
        totals = {'orders':0,'completes':0,'cash':0.0,'accepts':0,'sup_secs':0.0,'util_secs':0.0,'eff_secs':0.0}
        for r in rows:
            try:
                d = datetime.strptime(r.get('day'), '%Y-%m-%d').date()
            except Exception:
                continue
            lbl = _bucket_label(d)
            if not lbl:
                continue

            orders_total = int(r.get('orders_total') or 0)
            completes    = int(r.get('orders_completed') or 0)
            cash_sum     = float(r.get('cash_sum') or 0.0)
            accepts      = int(r.get('accepts') or 0)
            sup_secs     = float(r.get('supply_seconds') or 0.0)
            util_secs    = float(r.get('interval_seconds') or 0.0)
            eff_secs     = float(r.get('transport_seconds') or 0.0)

            acc['orders'][lbl]    += orders_total
            acc['completes'][lbl] += completes
            acc['cash'][lbl]      += cash_sum
            acc['accepts'][lbl]   += accepts
            acc['sup_secs'][lbl]  += sup_secs
            acc['util_secs'][lbl] += util_secs
            acc['eff_secs'][lbl]  += eff_secs

            totals['orders']    += orders_total
            totals['completes'] += completes
            totals['cash']      += cash_sum
            totals['accepts']   += accepts
            totals['sup_secs']  += sup_secs
            totals['util_secs'] += util_secs
            totals['eff_secs']  += eff_secs

        # Build series (values already bucketed)
        series = {
            'trips':        [{'period': L, 'value': acc['completes'].get(L, 0)} for L in labels],
            'supplyHours':  [{'period': L, 'value': acc['sup_secs'].get(L, 0.0)/3600.0} for L in labels],
            'cashEarned':   [{'period': L, 'value': acc['cash'].get(L, 0.0)} for L in labels],
            'acceptanceRate': [
                {'period': L, 'value': _safe_div(acc['accepts'].get(L,0)*100.0, max(acc['orders'].get(L,0), 1e-12))}
                for L in labels
            ],
            'completionRate': [
                {'period': L, 'value': _safe_div(acc['completes'].get(L,0)*100.0, max(acc['accepts'].get(L,0), 1e-12))}
                for L in labels
            ],
        }

        # Totals for the cards
        hours = totals['sup_secs'] / 3600.0
        cards = {
            'cash': totals['cash'],
            'trips': totals['completes'],
            'hours': hours,
            'acceptance_rate': _safe_div(totals['accepts'] * 100.0, max(totals['orders'], 1e-12)) if totals['orders'] else 0.0,
            'completion_rate': _safe_div(totals['completes'] * 100.0, max(totals['accepts'], 1e-12)) if totals['accepts'] else 0.0,
        }

        # Issues — same pattern as dashboard_data()
        Issue = request.env['x_fleet_issue'].sudo()
        issues = Issue.search([
            ('driver_id', '=', drv.id),
            ('date_reported', '>=', datetime.combine(df, datetime.min.time())),
            ('date_reported', '<=', datetime.combine(dt_, datetime.max.time())),
        ], order='date_reported desc, id desc')

        def _cat_path(i):
            parts = [p for p in [i.main_category, i.sub_category, i.sub_sub_category] if p]
            return ', '.join(parts)

        issue_rows = [{
            'category_path': _cat_path(i),
            'date_reported': i.date_reported and i.date_reported.strftime('%Y-%m-%d %H:%M'),
            'date_resolved': i.resolved_on and i.resolved_on.strftime('%Y-%m-%d %H:%M') or '',
            'note':          (i.note or '').strip(),
        } for i in issues]

        return {'cards': cards, 'series': series, 'issues': issue_rows}