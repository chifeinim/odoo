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
        # ── normalize filters ─────────────────────────────────────────
        products   = products   or []
        scores     = scores     or []
        categories = categories or []

        # ── A) Build current & previous date windows ───────────────────
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
                df = dt = prev_df = prev_dt = None
            else:
                df      = today - timedelta(days=days)
                dt      = today
                prev_dt = df - timedelta(days=1)
                prev_df = prev_dt - timedelta(days=days-1)

        Order       = request.env['x_fleet_order'].sudo()
        SupplyHours = request.env['x_fleet_driver_supply_hours'].sudo()

        # ── B) Precompute your four domains ───────────────────────────
        # current-window orders (any status)
        order_date_dom = []
        if df: order_date_dom.append(('order_date', '>=', df))
        if dt: order_date_dom.append(('order_date', '<=', dt))
        # same + only completed
        complete_dom = order_date_dom + [('status', '=', 'complete')]

        # previous-window orders
        prev_order_dom = []
        if prev_df is not None and prev_dt is not None:
            prev_order_dom = [
                ('order_date', '>=', prev_df),
                ('order_date', '<=', prev_dt),
            ]
        prev_complete_dom = prev_order_dom + [('status', '=', 'complete')]

        # supply‑hours domains use “date” not “order_date”
        supply_date_dom = []
        if df: supply_date_dom.append(('date', '>=', df))
        if dt: supply_date_dom.append(('date', '<=', dt))

        # ── C) Time‑series “trips” & “cashEarned” & total orders ─────
        # 1) completed trips + cash per day
        rg_tc = Order.read_group(
            complete_dom, ['__count','price'], ['order_date:day'], lazy=False
        )
        series_trips      = [{'period':r['order_date:day'], 'value':r['__count']} for r in rg_tc]
        series_cashEarned = [{'period':r['order_date:day'], 'value':r['price']}     for r in rg_tc]

        # 2) total orders per day (all statuses)
        rg_all = Order.read_group(
            order_date_dom, ['__count'], ['order_date:day'], lazy=False
        )
        total_orders_by_day = {r['order_date:day']: r['__count'] for r in rg_all}

        # ── C2) Active drivers per day ───────────────────────────────
        rg_ad = Order.read_group(
            complete_dom, ['driver_id'], ['order_date:day','driver_id'], lazy=False
        )
        day_driver_map = {}
        for r in rg_ad:
            d   = r['order_date:day']
            drv = r['driver_id'][0]
            day_driver_map.setdefault(d, set()).add(drv)
        series_activeDrivers = [
            {'period': d, 'value': len(drvs)}
            for d, drvs in sorted(day_driver_map.items())
        ]

        # ── D) Supply hours per day ──────────────────────────────────
        rg_sh = SupplyHours.read_group(
            [('seconds', '>', 0)] + supply_date_dom,
            ['seconds'],
            ['date:day'],
            lazy=False
        )
        series_supplyHours = [
            {'period': r['date:day'], 'value': r['seconds'] / 3600.0}
            for r in rg_sh
        ]

        # ── E) Single‐pass JSON “acceptance” & “efficiency” per day ──
        orders_all = Order.search(order_date_dom, order='order_date asc')
        accept_by_day  = {}
        eff_secs_by_day = {}
        for o in orders_all:
            day = o.order_date.date()
            sts = _order_statuses(o)
            if sts & {'driving','waiting','transporting'}:
                accept_by_day[day] = accept_by_day.get(day, 0) + 1
            if 'transporting' in sts:
                eff_secs_by_day[day] = eff_secs_by_day.get(day, 0.0) + _transport_seconds(o)

        # ── F) Build your DQS map & distributions ────────────────────
        all_drivers   = request.env['x_fleet_driver'].sudo().search([], order='name')
        total_drivers = len(all_drivers)
        dqs_matrix = {
            'strong':  {'strong':'High Performer','average':'Average Performer','weak':'Low Performer'},
            'average': {'strong':'High Performer','average':'Average Performer','weak':'Low Performer'},
            'weak':    {'strong':'Average Performer','average':'Low Performer',     'weak':'Low Performer'},
        }
        dqs_map, prod_counts, qual_counts, cat_counts = {}, {}, {}, {}
        for drv in all_drivers:
            # On‑the‑Road KPI
            prod     = drv.product_type_id
            kt       = prod.kpi_type or 'none'
            lo, hi   = prod.lower_kpi, prod.upper_kpi
            onroad   = 'Average'
            if kt!='none' and drv.hire_date:
                days_since = max((today - drv.hire_date).days + 1, 1)
                if kt=='avg_hours_online':
                    secs = SupplyHours.search([
                        ('driver_id','=',drv.id),
                        ('date','>=',drv.hire_date),
                        ('date','<=',today),
                    ]).mapped('seconds')
                    val = sum(secs)/3600.0/days_since
                else:
                    comp = drv.order_ids.filtered(
                        lambda o: o.status=='complete'
                                  and o.order_date
                                  and drv.hire_date <= o.order_date.date() <= today
                    )
                    val = (len(comp) if kt=='avg_trips_completed' else sum(o.price for o in comp)) / days_since
                if val is not None:
                    if lo is not None and val<lo: onroad='Weak'
                    elif hi is not None and val>hi: onroad='Strong'
            training = (drv.training_rating or 'average').lower()
            dqs_map[drv.id] = dqs_matrix[training].get(onroad.lower(), 'Average Performer')
            # distributions
            pk = drv.product_type_id.name or 'Unspecified'
            prod_counts[pk] = prod_counts.get(pk, 0) + 1
            qs = dqs_map[drv.id]
            qual_counts[qs] = qual_counts.get(qs, 0) + 1
            ct = drv.type or 'Unspecified'
            cat_counts[ct] = cat_counts.get(ct, 0) + 1

        prod_dist = [{'label':k,'value':v} for k,v in prod_counts.items()]
        qual_dist = [{'label':k,'value':v} for k,v in qual_counts.items()]
        cat_dist  = [{'label':k,'value':v} for k,v in cat_counts.items()]

        # ── G) High‑level metrics ─────────────────────────────────────
        # total trips + cash
        grp = Order.read_group(complete_dom, ['__count','price'], [], lazy=False)
        trip_current = grp and grp[0]['__count'] or 0
        cash_current = grp and grp[0]['price']     or 0.0
        if prev_complete_dom:
            grp = Order.read_group(prev_complete_dom, ['__count','price'], [], lazy=False)
            trip_previous = grp and grp[0]['__count'] or 0
            cash_previous = grp and grp[0]['price']     or 0.0
        else:
            trip_previous = 0
            cash_previous = 0.0

        # active drivers
        active_current  = len(Order.read_group(complete_dom, ['driver_id'], ['driver_id']))
        active_previous = len(Order.read_group(prev_complete_dom, ['driver_id'], ['driver_id'])) \
                            if prev_complete_dom else 0

        # supply hours
        grp = SupplyHours.read_group([('seconds','>',0)] + supply_date_dom,
                                     ['seconds'], [], lazy=False)
        sec = grp and grp[0]['seconds'] or 0.0
        supply_current = sec/3600.0
        if prev_order_dom:
            grp = SupplyHours.read_group(
                [('seconds','>',0)] + [('date','>=',prev_df),('date','<=',prev_dt)],
                ['seconds'], [], lazy=False
            )
            supply_previous = (grp and grp[0]['seconds'] or 0.0)/3600.0
        else:
            supply_previous = 0.0

        # JSON events total acceptance & efficiency
        cur_ids  = orders_all.ids
        prev_ids = Order.search(prev_order_dom).ids if prev_order_dom else []
        accept_current = sum(1 for i in accept_by_day.values())
        eff_current    = sum(eff_secs_by_day.values())
        accept_previous, eff_previous = 0, 0.0
        if prev_order_dom:
            # same single‐pass logic over previous window:
            prev_orders = Order.search(prev_order_dom)
            for o in prev_orders:
                sts = _order_statuses(o)
                if sts & {'driving','waiting','transporting'}:
                    accept_previous += 1
                if 'transporting' in sts:
                    eff_previous += _transport_seconds(o)
        order_num_current  = len(cur_ids)
        order_num_previous = len(prev_ids)

        metrics = {
            'activeDrivers':           active_current,
            'prevActiveDrivers':       active_previous,
            'tripCount':               trip_current,
            'prevTripCount':           trip_previous,
            'supplyHours':             supply_current,
            'prevSupplyHours':         supply_previous,
            'cashEarned':              cash_current,
            'prevCashEarned':          cash_previous,
            'moneyPerHour':            (cash_current / supply_current) if supply_current else 0.0,
            'prevMoneyPerHour':        (cash_previous / supply_previous) if supply_previous else 0.0,
            'tripsPerHour':            (trip_current   / supply_current) if supply_current else 0.0,
            'prevTripsPerHour':        (trip_previous  / supply_previous) if supply_previous else 0.0,
            'avgSupplyHoursPerDriver': (supply_current  / active_current) if active_current else 0.0,
            'prevAvgSupplyHoursPerDriver': (supply_previous / active_previous) if active_previous else 0.0,
            'avgUtilisation':          (eff_current/3600.0 / supply_current * 100.0) if supply_current else 0.0,
            'prevAvgUtilisation':      (eff_previous/3600.0 / supply_previous * 100.0) if supply_previous else 0.0,
            'avgEfficiency':           (eff_current/3600.0 / supply_current * 100.0) if supply_current else 0.0,
            'prevAvgEfficiency':       (eff_previous/3600.0 / supply_previous * 100.0) if supply_previous else 0.0,
            'acceptanceRate':          (accept_current / order_num_current * 100.0) if order_num_current else 0.0,
            'prevAcceptanceRate':      (accept_previous / order_num_previous * 100.0) if order_num_previous else 0.0,
            'completedToRequest':      (trip_current   / order_num_current * 100.0) if order_num_current else 0.0,
            'prevCompletedToRequest':  (trip_previous  / order_num_previous * 100.0) if order_num_previous else 0.0,
            'serviceFee':              cash_current * 0.1,
            'prevServiceFee':          cash_previous * 0.1,
            'partnerFee':              cash_current * 0.03,
            'prevPartnerFee':          cash_previous * 0.03,
        }

        # ── H) Build the rest of your time‑series ─────────────────────
        # 1) Build dicts from the raw read_group results:
        tr_map     = { r['order_date:day']: r['__count'] for r in rg_tc }
        cash_map   = { r['order_date:day']: r['price']     for r in rg_tc }
        active_map = { day: len(drvs) for day, drvs in day_driver_map.items() }
        supply_map = { r['date:day']: r['seconds']/3600.0 for r in rg_sh }

        # total_orders_by_day, accept_by_day, eff_secs_by_day were built earlier,
        # but they key by date‐object → we need ISO‐strings here:
        total_map  = { d: total_orders_by_day.get(d,0) for d in tr_map.keys() }
        accept_map = { d.isoformat(): cnt for d, cnt in accept_by_day .items() }
        eff_map    = { d.isoformat(): secs for d, secs in eff_secs_by_day.items() }

        # 2) Unified, sorted list of all days:
        buckets = sorted(set(tr_map)  # days with trips
                         | set(cash_map)
                         | set(active_map)
                         | set(supply_map))

        # 3) Build your four base series off that:
        series = {
            'trips'        : [{'period': d, 'value': tr_map.get(d,0)}        for d in buckets],
            'cashEarned'   : [{'period': d, 'value': cash_map.get(d,0.0)}    for d in buckets],
            'activeDrivers': [{'period': d, 'value': active_map.get(d,0)}    for d in buckets],
            'supplyHours'  : [{'period': d, 'value': supply_map.get(d,0.0)}  for d in buckets],
        }

        # 4) Prepare empty slots for the derived ones:
        for name in [
            'moneyPerHour','tripsPerHour','avgSupplyHoursPerDriver',
            'acceptanceRate','efficiency','completedToRequest',
            'serviceFee','partnerFee'
        ]:
            series[name] = []

        # 5) Now walk the unified bucket and pull everything via dict.get():
        for d in buckets:
            t  = tr_map.get(d,0)
            c  = cash_map.get(d,0.0)
            a  = active_map.get(d,0)
            s  = supply_map.get(d,0.0)
            to = total_map.get(d,0)
            ac = accept_map.get(d,0)
            ef = eff_map.get(d,0.0)

            # percentage of completed to requested
            comp_pct = (t / to * 100.0) if to else 0.0

            series['moneyPerHour']           .append({'period':d,'value': (c/s)    if s else 0.0})
            series['tripsPerHour']           .append({'period':d,'value': (t/s)    if s else 0.0})
            series['avgSupplyHoursPerDriver'].append({'period':d,'value': (s/a)    if a else 0.0})
            series['acceptanceRate']         .append({'period':d,'value': (ac/to*100) if to else 0.0})
            series['efficiency']             .append({'period':d,'value': ((ef/3600.0)/s*100) if s else 0.0})
            series['completedToRequest']     .append({'period':d,'value': comp_pct})
            series['serviceFee']             .append({'period':d,'value': c*0.1})
            series['partnerFee']             .append({'period':d,'value': c*0.03})

        # ── I) Per‑driver detail via two read_group + one pass ───────
        # restrict to your “filtered_drivers”
        filtered_drivers = [
            d for d in all_drivers
            if (not products or d.product_type_id.id in products)
            and (not scores   or dqs_map[d.id] in scores)
            and (not categories or d.type in categories)
        ]
        fd_ids = [d.id for d in filtered_drivers]

        # a) trips + cash per driver
        rg_drv = Order.read_group(
            complete_dom + [('driver_id','in',fd_ids)],
            ['__count','price'],
            ['driver_id'],
            lazy=False
        )
        # b) hours per driver
        rg_shd = SupplyHours.read_group(
            [('seconds','>',0),('driver_id','in',fd_ids)] + supply_date_dom,
            ['seconds'],
            ['driver_id'],
            lazy=False
        )
        # c) JSON acceptance/efficiency per driver
        orders_fd = Order.search(order_date_dom + [('driver_id','in',fd_ids)])
        acc_drv, eff_drv = {}, {}
        for o in orders_fd:
            did = o.driver_id.id
            sts = _order_statuses(o)
            if sts & {'driving','waiting','transporting'}:
                acc_drv[did] = acc_drv.get(did,0) + 1
            if 'transporting' in sts:
                eff_drv[did] = eff_drv.get(did,0.0) + _transport_seconds(o)

        drivers_data = {}
        # merge them
        for d in filtered_drivers:
            did = d.id
            grp = next((r for r in rg_drv  if r['driver_id'][0]==did), {})
            sh  = next((r for r in rg_shd if r['driver_id'][0]==did), {})
            trips = grp.get('__count',0)
            cash  = grp.get('price',   0.0)
            hours = sh.get('seconds',0.0)/3600.0
            ac    = acc_drv.get(did,0)
            ef    = eff_drv.get(did,0.0)
            drivers_data[did] = {
                'id':             did,
                'name':           d.name,
                'phone':          d.phone or '',
                'hire_date':      d.hire_date and d.hire_date.strftime('%Y-%m-%d') or '',
                'product_type':   d.product_type_id.name or '',
                'type':           d.type,
                'quality_score':  dqs_map[did],
                'active':         trips>0,
                'cash':           cash,
                'trips':          trips,
                'hours':          hours,
                'acceptance_rate': (ac/trips*100)      if trips else 0.0,
                'trips_per_hour':  (trips/hours)       if hours else 0.0,
                'money_per_hour':  (cash/hours)        if hours else 0.0,
                'efficiency':      ((ef/3600.0)/hours*100) if hours else 0.0,
                'completed_to_request': (trips/trips*100)   if trips else 0.0,
                'service_fee':     cash * 0.10,
                'partner_fee':     cash * 0.03,
            }

        return {
            'metrics':       metrics,
            'series':        series,
            'data':          drivers_data,
            'allDrivers':    total_drivers,
            'distributions': {
                'product': prod_dist,
                'quality': qual_dist,
                'category': cat_dist,
            },
        }

