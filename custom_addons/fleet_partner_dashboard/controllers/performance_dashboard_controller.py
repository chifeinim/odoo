## `controllers/performance_dashboard_controller.py`

# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from datetime import date, datetime, timedelta
from collections import defaultdict
from typing import Optional
from .helpers import (
    humanize, safe_div,
    last_week_range, last_month_range, last_n_months_range, previous_period,
    bucketize_span, signer_get,
)
import os

class PerformanceDashboardController(http.Controller):

    @http.route('/fleet_partner_performance/filters', type='json', auth='user')
    def performance_filters(self):
        Product = request.env['x_fleet_product_type'].sudo()
        pts = Product.search([], order='name')
        product_types = [{'id': p.id, 'name': p.name} for p in pts]

        Driver = request.env['x_fleet_driver'].sudo()
        categories = [k for k, _ in Driver._fields['type'].selection]
        return {'product_types': product_types, 'categories': categories}

    @http.route('/fleet_partner_performance/data', type='json', auth='user')
    def performance_data(self,
                         period: Optional[str] = None,
                         products=None, scores=None, categories=None,
                         start_date: Optional[str] = None,
                         end_date: Optional[str] = None):
        today = date.today()
        want_all_time = False

        if start_date and end_date:
            df = datetime.strptime(start_date, '%Y-%m-%d').date()
            dt_ = datetime.strptime(end_date,   '%Y-%m-%d').date()
            if df > dt_:
                df, dt_ = dt_, df
            prev_df, prev_dt = previous_period(df, dt_)
        else:
            if period == 'All Time':
                df = prev_df = dt_ = prev_dt = None
                want_all_time = True
            elif period == 'Last Month':
                df, dt_ = last_month_range(today)
                prev_df, prev_dt = previous_period(df, dt_)
            elif period == 'Last 3 Months':
                df, dt_ = last_n_months_range(today, 3)
                prev_df, prev_dt = previous_period(df, dt_)
            else:
                df, dt_ = last_week_range(today)
                prev_df, prev_dt = previous_period(df, dt_)

        def _min_or(a, b): return min(a, b) if (a and b) else (a or b)
        def _max_or(a, b): return max(a, b) if (a and b) else (a or b)

        big_df = df
        big_dt = dt_ or today
        if prev_df and prev_dt:
            big_df = _min_or(df, prev_df)
            big_dt = _max_or(dt_ or today, prev_dt)

        if df is None and dt_ is None:
            df = today - timedelta(days=90)
            dt_ = today
            big_df = df
            big_dt = dt_

        icp = request.env['ir.config_parameter'].sudo()
        TENANT_CODE = icp.get_param('metrics.tenant_code') or os.environ.get('TENANT_CODE', 'anda')

        if want_all_time:
            fetch_from = '2000-01-01'
            fetch_to   = today.strftime('%Y-%m-%d')
        else:
            fetch_from = big_df.strftime('%Y-%m-%d')  # type: ignore
            fetch_to   = big_dt.strftime('%Y-%m-%d')

        day_resp = signer_get('/metrics/driver-day', {
            'from_date': fetch_from, 'to_date': fetch_to, 'tenant': TENANT_CODE,
        })
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

        otrs_resp = signer_get('/metrics/driver-otrs', {'tenant': TENANT_CODE})
        otrs_rows = otrs_resp.get('rows', [])

        otrs_by_driver = {}
        for r in otrs_rows:
            yid = (r.get('driver_id') or '').strip()
            band = (r.get('score_band') or '').strip().lower()
            if yid:
                otrs_by_driver[yid] = band or 'average'

        buckets = bucketize_span(df, dt_)
        bucket_labels = [lbl for _, _, lbl, _ in buckets]

        def _bucket_key(d):
            for bstart, bend, lbl, _ in buckets:
                if bstart <= d <= bend:
                    return lbl
            return None

        Driver = request.env['x_fleet_driver'].sudo()
        all_drivers = Driver.search([])
        total_drivers = len(all_drivers)

        products   = products   or []
        scores     = scores     or []
        categories = categories or []

        dqs_matrix = {
            'strong':  {'strong': 'High Performer',    'average': 'Average Performer', 'weak': 'Low Performer'},
            'average': {'strong': 'High Performer',    'average': 'Average Performer', 'weak': 'Low Performer'},
            'weak':    {'strong': 'Average Performer', 'average': 'Low Performer',     'weak': 'Low Performer'},
        }

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

        filtered_drivers = [
            d for d in all_drivers
            if (not products   or d.product_type_id.id in products)
            and (not scores    or dqs_map.get(d.id) in scores)
            and (not categories or d.type in categories)
        ]
        filtered_ids = {d.id for d in filtered_drivers}
        filtered_yids = {(d.yango_driver_id or '').strip(): d.id for d in filtered_drivers if d.yango_driver_id}

        cur = {
            'orders': 0, 'completes': 0, 'cash': 0.0,
            'util_secs': 0.0, 'eff_secs': 0.0, 'accepts': 0,
            'sup_secs': 0.0, 'driver_cancels': 0,
            'cust_cancels': 0, 'net_cancels': 0,
        }
        prv = {k: (0 if 'cash' not in k and 'secs' not in k else 0.0) for k in cur.keys()}
        cur_active_set, prv_active_set = set(), set()

        drv_cur = defaultdict(lambda: {
            'orders': 0, 'completes': 0, 'cash': 0.0,
            'util_secs': 0.0, 'eff_secs': 0.0, 'accepts': 0,
            'sup_secs': 0.0, 'driver_cancels': 0, 'mileage_meters': 0.0,
        })

        series_acc = {
            'orders': defaultdict(int),
            'completes': defaultdict(int),
            'cash': defaultdict(float),
            'util_secs': defaultdict(float),
            'eff_secs': defaultdict(float),
            'accepts': defaultdict(int),
            'sup_secs': defaultdict(float),
            'driver_cancels': defaultdict(int),
            'cust_cancels': defaultdict(int),
            'net_cancels': defaultdict(int),
            'active_sets': defaultdict(set),
        }

        for r in day_rows:
            try:
                day = datetime.strptime(r.get('day'), '%Y-%m-%d').date()
            except Exception:
                continue

            yid = (r.get('driver_id') or '').strip()
            odoo_id = filtered_yids.get(yid)
            if not odoo_id:
                continue

            orders_total        = int(r.get('orders_total') or 0)
            completes           = int(r.get('orders_completed') or 0)
            cash_sum            = float(r.get('cash_sum') or 0.0)
            accepts             = int(r.get('accepts') or 0)
            util_secs           = float(r.get('interval_seconds') or 0.0)
            eff_secs            = float(r.get('transport_seconds') or 0.0)
            sup_secs            = float(r.get('supply_seconds') or 0.0)
            driver_cancels      = int(r.get('driver_cancellations') or 0)
            customer_cancels    = int(r.get('customer_cancellations') or 0)
            network_cancels     = int(r.get('network_cancellations') or 0)
            mileage_meters      = float(r.get('mileage_meters') or 0.0)

            in_prev = (prev_df and prev_dt and prev_df <= day <= prev_dt)
            in_cur  = (df and dt_   and df    <= day <= dt_)

            if in_prev:
                prv['orders']    += orders_total
                prv['completes'] += completes
                prv['cash']      += cash_sum
                prv['util_secs'] += util_secs
                prv['eff_secs']  += eff_secs
                prv['accepts']   += accepts
                prv['sup_secs']  += sup_secs
                prv['driver_cancels'] += driver_cancels
                prv['cust_cancels']   += customer_cancels
                prv['net_cancels']    += network_cancels
                if completes > 0:
                    prv_active_set.add(odoo_id)

            if in_cur:
                cur['orders']    += orders_total
                cur['completes'] += completes
                cur['cash']      += cash_sum
                cur['util_secs'] += util_secs
                cur['eff_secs']  += eff_secs
                cur['accepts']   += accepts
                cur['sup_secs']  += sup_secs
                cur['driver_cancels'] += driver_cancels
                cur['cust_cancels']   += customer_cancels
                cur['net_cancels']    += network_cancels
                if completes > 0:
                    cur_active_set.add(odoo_id)

                x = drv_cur[odoo_id]
                x['orders']         += orders_total
                x['completes']      += completes
                x['cash']           += cash_sum
                x['util_secs']      += util_secs
                x['eff_secs']       += eff_secs
                x['accepts']        += accepts
                x['sup_secs']       += sup_secs
                x['driver_cancels'] += driver_cancels
                x['mileage_meters'] += mileage_meters

                bk = _bucket_key(day)
                if bk:
                    series_acc['orders'][bk]          += orders_total
                    series_acc['completes'][bk]       += completes
                    series_acc['cash'][bk]            += cash_sum
                    series_acc['util_secs'][bk]       += util_secs
                    series_acc['eff_secs'][bk]        += eff_secs
                    series_acc['accepts'][bk]         += accepts
                    series_acc['sup_secs'][bk]        += sup_secs
                    series_acc['driver_cancels'][bk]  += driver_cancels
                    series_acc['cust_cancels'][bk]    += customer_cancels
                    series_acc['net_cancels'][bk]     += network_cancels
                    if completes > 0:
                        series_acc['active_sets'][bk].add(odoo_id)

        active_current   = len(cur_active_set)
        active_previous  = len(prv_active_set)

        trip_current     = cur['completes']
        trip_previous    = prv['completes']

        supply_current   = cur['sup_secs'] / 3600.0
        supply_previous  = prv['sup_secs'] / 3600.0

        cash_current     = cur['cash']
        cash_previous    = prv['cash']

        util_pct_current = safe_div(cur['util_secs']/3600.0, max(supply_current, 1e-12)) * 100.0 if supply_current else 0.0
        util_pct_prev    = safe_div(prv['util_secs']/3600.0, max(supply_previous, 1e-12)) * 100.0 if supply_previous else 0.0

        eff_pct_current  = safe_div(cur['eff_secs']/3600.0, max(supply_current, 1e-12)) * 100.0 if supply_current else 0.0
        eff_pct_prev     = safe_div(prv['eff_secs']/3600.0, max(supply_previous, 1e-12)) * 100.0 if supply_previous else 0.0

        accept_rate_current  = safe_div(cur['accepts'] * 100.0, max(cur['orders'], 1e-12))
        accept_rate_previous = safe_div(prv['accepts'] * 100.0, max(prv['orders'], 1e-12))

        completed_to_request_current  = safe_div(trip_current * 100.0, max(cur['orders'], 1e-12))
        completed_to_request_previous = safe_div(trip_previous * 100.0, max(prv['orders'], 1e-12))

        completion_rate_current  = safe_div(trip_current * 100.0, max(cur['accepts'], 1e-12)) if cur['accepts'] else 0.0
        completion_rate_previous = safe_div(trip_previous * 100.0, max(prv['accepts'], 1e-12)) if prv['accepts'] else 0.0

        cancelled_by_driver_pct_current  = safe_div(cur['driver_cancels'] * 100.0, max(cur['accepts'], 1e-12)) if cur['accepts'] else 0.0
        cancelled_by_driver_pct_previous = safe_div(prv['driver_cancels'] * 100.0, max(prv['accepts'], 1e-12)) if prv['accepts'] else 0.0

        cancelled_by_customer_pct_current  = safe_div(cur['cust_cancels'] * 100.0, max(cur['accepts'], 1e-12)) if cur['accepts'] else 0.0
        cancelled_by_customer_pct_previous = safe_div(prv['cust_cancels'] * 100.0, max(prv['accepts'], 1e-12)) if prv['accepts'] else 0.0

        cancelled_due_to_network_pct_current  = safe_div(cur['net_cancels'] * 100.0, max(cur['orders'], 1e-12)) if cur['orders'] else 0.0
        cancelled_due_to_network_pct_previous = safe_div(prv['net_cancels'] * 100.0, max(prv['orders'], 1e-12)) if prv['orders'] else 0.0

        trips_per_active_driver_current  = safe_div(trip_current, max(active_current, 1e-12)) if active_current else 0.0
        trips_per_active_driver_previous = safe_div(trip_previous, max(active_previous, 1e-12)) if active_previous else 0.0

        avg_supply        = safe_div(supply_current, active_current) if active_current else 0.0
        avg_supply_prev   = safe_div(supply_previous, active_previous) if active_previous else 0.0

        current_range = {
            'start': df and df.strftime('%Y-%m-%d'),
            'end':   dt_ and dt_.strftime('%Y-%m-%d'),
        }
        previous_range = {
            'start': prev_df and prev_df.strftime('%Y-%m-%d'),
            'end':   prev_dt and prev_dt.strftime('%Y-%m-%d'),
        }

        metrics = {
            'activeDrivers':     active_current,
            'prevActiveDrivers': active_previous,
            'tripCount':         trip_current,
            'prevTripCount':     trip_previous,
            'supplyHours':       supply_current,
            'prevSupplyHours':   supply_previous,
            'cashEarned':        cash_current,
            'prevCashEarned':    cash_previous,
            'moneyPerHour':      safe_div(cash_current, max(supply_current, 1e-12)) if supply_current else 0.0,
            'prevMoneyPerHour':  safe_div(cash_previous, max(supply_previous, 1e-12)) if supply_previous else 0.0,
            'tripsPerHour':      safe_div(trip_current, max(supply_current, 1e-12)) if supply_current else 0.0,
            'prevTripsPerHour':  safe_div(trip_previous, max(supply_previous, 1e-12)) if supply_previous else 0.0,
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
            'cancelledByCustomerPct':        cancelled_by_customer_pct_current,
            'prevCancelledByCustomerPct':    cancelled_by_customer_pct_previous,
            'cancelledDueToNetworkPct':      cancelled_due_to_network_pct_current,
            'prevCancelledDueToNetworkPct':  cancelled_due_to_network_pct_previous,
            'tripsPerActiveDriver':          trips_per_active_driver_current,
            'prevTripsPerActiveDriver':      trips_per_active_driver_previous,
            'serviceFee': cash_current * 0.1,
            'prevServiceFee': cash_previous * 0.1,
            'partnerFee': cash_current * 0.03,
            'prevPartnerFee': cash_previous * 0.03,
        }

        series_active, series_trips, series_supply, series_cash = [], [], [], []
        series_mph, series_trph = [], []
        series_avg_supply, series_utilisation, series_efficiency = [], [], []
        series_acceptance, series_completed, series_completion = [], [], []
        series_driverCancels, series_cancelledByDriver = [], []
        series_serviceFee, series_partnerFee = [], []
        series_cancelledByCustomer, series_cancelledDueToNetwork, series_tripsPerActiveDriver = [], [], []

        for lbl in bucket_labels:
            trips        = series_acc['completes'].get(lbl, 0)
            orders       = series_acc['orders'].get(lbl, 0)
            cash_sum     = series_acc['cash'].get(lbl, 0.0)
            sup_hours    = series_acc['sup_secs'].get(lbl, 0.0) / 3600.0
            util_h       = series_acc['util_secs'].get(lbl, 0.0) / 3600.0
            eff_h        = series_acc['eff_secs'].get(lbl, 0.0) / 3600.0
            accepts      = series_acc['accepts'].get(lbl, 0)
            active       = len(series_acc['active_sets'].get(lbl, set()))
            drv_canc     = series_acc['driver_cancels'].get(lbl, 0)
            cust_canc    = series_acc['cust_cancels'].get(lbl, 0)
            net_canc     = series_acc['net_cancels'].get(lbl, 0)

            series_active.append({'period': lbl, 'value': active})
            series_trips.append({'period': lbl, 'value': trips})
            series_supply.append({'period': lbl, 'value': sup_hours})
            series_cash.append({'period': lbl, 'value': cash_sum})
            series_serviceFee.append({'period': lbl, 'value': cash_sum * 0.1})
            series_partnerFee.append({'period': lbl, 'value': cash_sum * 0.03})

            mph  = safe_div(cash_sum, max(sup_hours, 1e-12)) if sup_hours else 0.0
            trph = safe_div(trips,    max(sup_hours, 1e-12)) if sup_hours else 0.0
            series_mph.append({'period': lbl, 'value': mph})
            series_trph.append({'period': lbl, 'value': trph})

            avg_sup = safe_div(sup_hours, active) if active else 0.0
            series_avg_supply.append({'period': lbl, 'value': avg_sup})

            util_pct = safe_div(util_h, max(sup_hours, 1e-12)) * 100.0 if sup_hours else 0.0
            eff_pct  = safe_div(eff_h,  max(sup_hours, 1e-12)) * 100.0 if sup_hours else 0.0
            series_utilisation.append({'period': lbl, 'value': util_pct})
            series_efficiency.append({'period': lbl, 'value': eff_pct})

            acc_pct = safe_div(accepts * 100.0, max(orders, 1e-12)) if orders else 0.0
            c2r_pct = safe_div(trips   * 100.0, max(orders, 1e-12)) if orders else 0.0
            cmp_r_pct = safe_div(trips * 100.0, max(accepts, 1e-12)) if accepts else 0.0
            series_acceptance.append({'period': lbl, 'value': acc_pct})
            series_completed.append({'period': lbl, 'value': c2r_pct})
            series_completion.append({'period': lbl, 'value': cmp_r_pct})

            series_driverCancels.append({'period': lbl, 'value': drv_canc})
            series_cancelledByDriver.append({'period': lbl, 'value': safe_div(drv_canc * 100.0, max(accepts, 1e-12)) if accepts else 0.0})
            series_cancelledByCustomer.append({'period': lbl, 'value': safe_div(cust_canc * 100.0, max(accepts, 1e-12)) if accepts else 0.0})
            series_cancelledDueToNetwork.append({'period': lbl, 'value': safe_div(net_canc * 100.0, max(orders, 1e-12)) if orders else 0.0})
            series_tripsPerActiveDriver.append({'period': lbl, 'value': safe_div(trips, max(active, 1e-12)) if active else 0.0})

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
            'cancelledByCustomerPct':    series_cancelledByCustomer,
            'cancelledDueToNetworkPct':  series_cancelledDueToNetwork,
            'tripsPerActiveDriver':      series_tripsPerActiveDriver,
        }

        data = {}
        for drv in filtered_drivers:
            s = drv_cur.get(drv.id, {
                'orders': 0, 'completes': 0, 'cash': 0.0,
                'util_secs': 0.0, 'eff_secs': 0.0, 'accepts': 0,
                'sup_secs': 0.0, 'driver_cancels': 0, 'mileage_meters': 0.0,
            })
            hours = s['sup_secs'] / 3600.0
            trips = s['completes']
            orders = s['orders']
            cash   = s['cash']
            driver_cancels = s['driver_cancels']
            mileage_km = (s.get('mileage_meters', 0.0) or 0.0) / 1000.0

            accept_rate = safe_div(s['accepts'] * 100.0, max(orders, 1e-12)) if orders else 0.0
            efficiency  = safe_div((s['eff_secs']/3600.0) * 100.0, max(hours, 1e-12)) if hours else 0.0
            utilisation = safe_div((s['util_secs']/3600.0) * 100.0, max(hours, 1e-12)) if hours else 0.0
            cancelled_by_driver = safe_div(driver_cancels * 100.0, max(s['accepts'], 1e-12)) if s['accepts'] else 0.0

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
                'mileage_km':     mileage_km,
                'acceptance_rate': accept_rate,
                'trips_per_hour': safe_div(trips, max(hours, 1e-12)) if hours else 0.0,
                'money_per_hour': safe_div(cash,  max(hours, 1e-12)) if hours else 0.0,
                'utilisation':    utilisation,
                'efficiency':     efficiency,
                'completed_to_request': safe_div(trips * 100.0, max(orders, 1e-12)) if orders else 0.0,
                'completion_rate': safe_div(trips * 100.0, max(s['accepts'], 1e-12)) if s['accepts'] else 0.0,
                'cancelled_by_driver':  cancelled_by_driver,
                'service_fee':    cash * 0.10,
                'partner_fee':    cash * 0.03,
            }

        return {
            'metrics': metrics,
            'series':  series,
            'data':    data,
            'allDrivers': total_drivers,
            'range': current_range,
            'prev_range': previous_range,
            'distributions': {
                'product':  prod_dist,
                'quality':  qual_dist,
                'category': cat_dist,
            },
        }

    @http.route('/fleet_partner_performance/driver_detail', type='json', auth='user')
    def performance_driver_detail(self, driver_id: int, start_date: str, end_date: str):
        today = date.today()
        try:
            df = datetime.strptime(start_date, '%Y-%m-%d').date()
            dt_ = datetime.strptime(end_date,   '%Y-%m-%d').date()
            if df > dt_:
                df, dt_ = dt_, df
        except Exception:
            df, dt_ = last_week_range(today)

        dt_ = min(dt_, today - timedelta(days=1))

        icp = request.env['ir.config_parameter'].sudo()
        TENANT_CODE = icp.get_param('metrics.tenant_code') or os.environ.get('TENANT_CODE', 'anda')

        Driver = request.env['x_fleet_driver'].sudo().browse(int(driver_id))
        yid = (Driver.yango_driver_id or '').strip()
        if not Driver or not yid:
            return {'cards': {}, 'series': {}, 'issues': []}

        resp = signer_get('/metrics/driver-day', {
            'from_date': df.strftime('%Y-%m-%d'),
            'to_date':   dt_.strftime('%Y-%m-%d'),
            'tenant':    TENANT_CODE,
        })
        rows = [r for r in resp.get('rows', []) if (r.get('driver_id') or '').strip() == yid]

        buckets = bucketize_span(df, dt_)
        labels  = [lbl for _, _, lbl, _ in buckets]

        def _bucket_key(d):
            for bstart, bend, lbl, _ in buckets:
                if bstart <= d <= bend:
                    return lbl
            return None

        agg = {lbl: {'orders':0,'accepts':0,'completes':0,'sup_secs':0.0,'cash':0.0,'driver_cancels':0} for lbl in labels}
        for r in rows:
            try:
                day = datetime.strptime(r.get('day'), '%Y-%m-%d').date()
            except Exception:
                continue
            lbl = _bucket_key(day)
            if not lbl:
                continue
            a = agg[lbl]
            a['orders']         += int(r.get('orders_total') or 0)
            a['accepts']        += int(r.get('accepts') or 0)
            a['completes']      += int(r.get('orders_completed') or 0)
            a['sup_secs']       += float(r.get('supply_seconds') or 0.0)
            a['cash']           += float(r.get('cash_sum') or 0.0)
            a['driver_cancels'] += int(r.get('driver_cancellations') or 0)

        series = {k: [] for k in ['trips','supplyHours','cashEarned','acceptanceRate','completionRate','tripsPerHour','cancelledByDriverPct']}
        totals = {'orders':0,'accepts':0,'completes':0,'sup_secs':0.0,'cash':0.0,'driver_cancels':0}
        for lbl in labels:
            a = agg[lbl]
            hours = a['sup_secs'] / 3600.0
            acc_pct  = safe_div(a['accepts'] * 100.0, max(a['orders'], 1e-12)) if a['orders'] else 0.0
            comp_pct = safe_div(a['completes'] * 100.0, max(a['accepts'], 1e-12)) if a['accepts'] else 0.0
            trph     = safe_div(a['completes'], max(hours, 1e-12)) if hours else 0.0
            cbd_pct  = safe_div(a['driver_cancels'] * 100.0, max(a['accepts'], 1e-12)) if a['accepts'] else 0.0

            series['trips'].append({'period': lbl, 'value': a['completes']})
            series['supplyHours'].append({'period': lbl, 'value': hours})
            series['cashEarned'].append({'period': lbl, 'value': a['cash']})
            series['acceptanceRate'].append({'period': lbl, 'value': acc_pct})
            series['completionRate'].append({'period': lbl, 'value': comp_pct})
            series['tripsPerHour'].append({'period': lbl, 'value': trph})
            series['cancelledByDriverPct'].append({'period': lbl, 'value': cbd_pct})

            for k in totals.keys():
                totals[k] += a[k]

        total_hours = totals['sup_secs'] / 3600.0
        cards = {
            'cash': totals['cash'],
            'trips': totals['completes'],
            'hours': total_hours,
            'acceptance_rate': safe_div(totals['accepts'] * 100.0, max(totals['orders'], 1e-12)) if totals['orders'] else 0.0,
            'completion_rate': safe_div(totals['completes'] * 100.0, max(totals['accepts'], 1e-12)) if totals['accepts'] else 0.0,
            'trips_per_hour': safe_div(totals['completes'], max(total_hours, 1e-12)) if total_hours else 0.0,
            'cancelled_by_driver': safe_div(totals['driver_cancels'] * 100.0, max(totals['accepts'], 1e-12)) if totals['accepts'] else 0.0,
        }

        Issue = request.env['x_fleet_issue'].sudo()
        issues = Issue.search([
            ('driver_id', '=', Driver.id),
            ('date_reported', '>=', datetime.combine(df, datetime.min.time())),
            ('date_reported', '<=', datetime.combine(dt_, datetime.max.time())),
        ], order='date_reported desc, id desc')

        def _cat_path(i):
            parts = [
                i.main_category_label or humanize(i.main_category),
                i.sub_category_label or humanize(i.sub_category),
                i.sub_sub_category_label or humanize(i.sub_sub_category),
            ]
            parts = [p for p in parts if p]
            return ' › '.join(parts)

        issue_rows = [{
            'category_path': _cat_path(i),
            'date_reported': i.date_reported and i.date_reported.strftime('%Y-%m-%d %H:%M'),
            'date_resolved': i.resolved_on and i.resolved_on.strftime('%Y-%m-%d %H:%M') or '',
            'note': (i.note or '').strip(),
        } for i in issues]

        return {
            'cards': cards,
            'series': series,
            'issues': issue_rows,
            'metrics_range': { 'start': df.strftime('%Y-%m-%d'), 'end': dt_.strftime('%Y-%m-%d') },
            'issues_range':  { 'start': df.strftime('%Y-%m-%d'), 'end': dt_.strftime('%Y-%m-%d') },
        }