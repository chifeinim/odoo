## `controllers/low_performers_dashboard_controller.py`

# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from datetime import date, datetime, timedelta
from collections import defaultdict
from typing import Optional
from .helpers import (
    humanize, safe_div,
    this_week_range, last_week_range, last_month_range, last_n_months_range,
    first_monday_prev_8_weeks, bucketize_span, signer_get,
)
import os

class LowPerformersDashboardController(http.Controller):

    @http.route('/fleet_low_performers/filters', type='json', auth='user')
    def low_perf_filters(self):
        Product = request.env['x_fleet_product_type'].sudo()
        pts = Product.search([], order='name')
        product_types = [{'id': p.id, 'name': p.name} for p in pts]

        Driver = request.env['x_fleet_driver'].sudo()
        categories = [k for k, _ in Driver._fields['type'].selection]
        risks = [ {'key': 1, 'label': '1'}, {'key': 2, 'label': '2'}, {'key': 3, 'label': '3'} ]
        periods = ['This Week', 'Last Week', 'Last Month', 'Last 3 Months']
        return {'product_types': product_types, 'categories': categories, 'risks': risks, 'time_periods': periods}

    @http.route('/fleet_low_performers/data', type='json', auth='user')
    def low_perf_data(self,
                      period: Optional[str] = None,
                      products=None, scores=None, categories=None, risks=None,
                      start_date: Optional[str] = None,
                      end_date: Optional[str] = None):
        today = date.today()

        this_mon = today - timedelta(days=today.weekday())
        this_sun = this_mon + timedelta(days=6)

        CallNote = request.env['x_fleet_call_note'].sudo()
        notes = CallNote.search([
            ('created_at', '>=', datetime.combine(this_mon, datetime.min.time())),
            ('created_at', '<=', datetime.combine(this_sun, datetime.max.time())),
        ])
        called_recently_ids = set(notes.mapped('driver_id').ids)

        if start_date and end_date:
            df = datetime.strptime(start_date, '%Y-%m-%d').date()
            dt_ = datetime.strptime(end_date,   '%Y-%m-%d').date()
            if df > dt_:
                df, dt_ = dt_, df
        else:
            if period == 'This Week':
                df, dt_ = this_week_range(today)
            elif period == 'Last Month':
                df, dt_ = last_month_range(today)
            elif period == 'Last 3 Months':
                df, dt_ = last_n_months_range(today, 3)
            else:
                df, dt_ = last_week_range(today)

        dt_ = min(dt_, today - timedelta(days=1))

        icp = request.env['ir.config_parameter'].sudo()
        TENANT_CODE = icp.get_param('metrics.tenant_code') or os.environ.get('TENANT_CODE', 'anda')

        risk_df, risk_dt = last_week_range(today)

        def _pull_day_rows(_from: date, _to: date):
            resp = signer_get('/metrics/driver-day', {
                'from_date': _from.strftime('%Y-%m-%d'),
                'to_date':   _to.strftime('%Y-%m-%d'),
                'tenant':    TENANT_CODE,
            })
            return resp.get('rows', [])

        table_rows = _pull_day_rows(df, dt_)
        risk_rows  = _pull_day_rows(risk_df, risk_dt)

        Driver = request.env['x_fleet_driver'].sudo()
        all_drivers = Driver.search([])
        products   = products   or []
        scores     = scores     or []
        categories = categories or []
        risks      = risks      or []

        otrs_rows = signer_get('/metrics/driver-otrs', {'tenant': TENANT_CODE}).get('rows', [])
        otrs_by_driver = {}
        for r in otrs_rows:
            yid = (r.get('driver_id') or '').strip()
            band = (r.get('score_band') or '').strip().lower()
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

        def _by_odoo_id(rows):
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

        def _priority_for(rows_week: list) -> Optional[int]:
            trips    = sum(int(r.get('orders_completed') or 0) for r in rows_week)
            sup_secs = sum(float(r.get('supply_seconds') or 0.0) for r in rows_week)
            days = 6.0
            avg_trips = trips / days
            avg_hours = (sup_secs / 3600.0) / days
            if avg_hours <= 1.0 or avg_trips <= 1.0:
                return 1
            if avg_hours <= 2.0 or avg_trips <= 2.0:
                return 2
            if (2.0 < avg_hours < 5.0) or (2.0 < avg_trips < 5.0):
                return 3
            return None

        Issue = request.env['x_fleet_issue'].sudo()
        data = {}
        for drv in all_drivers:
            rid = _priority_for(risk_by_drv.get(drv.id, []))
            if rid not in (1, 2, 3):
                continue
            if risks and rid not in risks:
                continue
            if products and drv.product_type_id.id not in products:
                continue
            if scores and dqs_map.get(drv.id) not in scores:
                continue
            if categories and drv.type not in categories:
                continue

            rows = table_by_drv.get(drv.id, [])
            orders_total  = sum(int(r.get('orders_total') or 0)      for r in rows)
            accepts       = sum(int(r.get('accepts') or 0)           for r in rows)
            completes     = sum(int(r.get('orders_completed') or 0)  for r in rows)
            cash_sum      = sum(float(r.get('cash_sum') or 0.0)      for r in rows)
            sup_seconds   = sum(float(r.get('supply_seconds') or 0.0) for r in rows)

            hours  = sup_seconds / 3600.0
            acc_pct = safe_div(accepts * 100.0, max(orders_total, 1e-12)) if orders_total else 0.0
            cmp_pct = safe_div(completes * 100.0, max(accepts, 1e-12))     if accepts else 0.0

            issues_flag = False
            if df and dt_:
                issues_flag = bool(Issue.search_count([
                    ('driver_id', '=', drv.id),
                    ('date_reported', '>=', datetime.combine(df, datetime.min.time())),
                    ('date_reported', '<=', datetime.combine(dt_, datetime.max.time())),
                ]))

            called_recently = 'Yes' if drv.id in called_recently_ids else 'No'

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
                'called_recently': called_recently,
            }

        return {
            'data': data,
            'meta': {
                'table_from': df.strftime('%Y-%m-%d'),
                'table_to':   dt_.strftime('%Y-%m-%d'),
                'risk_from':  risk_df.strftime('%Y-%m-%d'),
                'risk_to':    risk_dt.strftime('%Y-%m-%d'),
            },
            'range': {'start': df.strftime('%Y-%m-%d'), 'end': dt_.strftime('%Y-%m-%d')},
        }

    @http.route('/fleet_low_performers/driver_detail', type='json', auth='user')
    def low_perf_driver_detail(self, driver_id: int, start_date: str, end_date: str):
        today = date.today()
        # Issues window (kept aligned to 8-week notes window below)
        # Incoming start/end are ignored for metrics; we keep them for FE compatibility if needed
        last_sunday  = today - timedelta(days=(today.weekday() + 1))
        last_monday  = last_sunday - timedelta(days=6)
        metrics_df   = last_monday - timedelta(days=7 * 7)
        metrics_dt   = last_sunday

        icp = request.env['ir.config_parameter'].sudo()
        TENANT_CODE = icp.get_param('metrics.tenant_code') or os.environ.get('TENANT_CODE', 'anda')

        drv = request.env['x_fleet_driver'].sudo().browse(int(driver_id))
        yid = (drv.yango_driver_id or '').strip()
        if not drv or not yid:
            return {'cards': {}, 'series': {}, 'issues': []}

        week_resp = signer_get('/metrics/driver-week', {
            'from_date': metrics_df.strftime('%Y-%m-%d'),
            'to_date':   metrics_dt.strftime('%Y-%m-%d'),
            'tenant':    TENANT_CODE,
        })
        rows = [r for r in week_resp.get('rows', []) if (r.get('driver_id') or '').strip() == yid]

        def _bucketize_8_weeks(mon0: date):
            buckets = []
            cur = mon0
            for _ in range(8):
                end = cur + timedelta(days=6)
                buckets.append((cur, end, cur.strftime('%Y-%m-%d'), 'week'))
                cur += timedelta(days=7)
            return buckets

        buckets = _bucketize_8_weeks(metrics_df)
        labels  = [lbl for _, _, lbl, _ in buckets]

        by_week = {}
        for r in rows:
            w = r.get('week_monday')
            try:
                wdt = datetime.strptime(w, '%Y-%m-%d').date()
            except Exception:
                continue
            by_week[wdt.strftime('%Y-%m-%d')] = r

        def _f(r, k, cast=float):
            try:
                return cast(r.get(k) or 0)
            except Exception:
                return 0

        series = {k: [] for k in ['trips','supplyHours','cashEarned','acceptanceRate','completionRate','tripsPerHour','cancelledByDriverPct']}
        for L in labels:
            r = by_week.get(L, {})
            trips      = _f(r, 'orders_completed', int)
            sup_hours  = _f(r, 'supply_seconds') / 3600.0
            cash_sum   = _f(r, 'cash_sum')
            acc_pct    = _f(r, 'acceptance_rate_pct')
            cmp_pct    = _f(r, 'completion_rate_pct')
            trph       = r.get('trips_per_hour')
            if trph is None:
                trph = (trips / sup_hours) if sup_hours else 0.0
            cbd_pct    = _f(r, 'cancelled_by_driver_pct')

            series['trips'].append({'period': L, 'value': trips})
            series['supplyHours'].append({'period': L, 'value': sup_hours})
            series['cashEarned'].append({'period': L, 'value': cash_sum})
            series['acceptanceRate'].append({'period': L, 'value': acc_pct})
            series['completionRate'].append({'period': L, 'value': cmp_pct})
            series['tripsPerHour'].append({'period': L, 'value': trph})
            series['cancelledByDriverPct'].append({'period': L, 'value': cbd_pct})

        totals = {
            'orders':    sum(int(r.get('orders_total') or 0) for r in rows),
            'completes': sum(int(r.get('orders_completed') or 0) for r in rows),
            'cash':      sum(float(r.get('cash_sum') or 0.0) for r in rows),
            'accepts':   sum(int(r.get('accepts') or 0) for r in rows),
            'sup_secs':  sum(float(r.get('supply_seconds') or 0.0) for r in rows),
        }
        hours = totals['sup_secs'] / 3600.0
        cards = {
            'cash': totals['cash'],
            'trips': totals['completes'],
            'hours': hours,
            'acceptance_rate': safe_div(totals['accepts'] * 100.0, max(totals['orders'], 1e-12)) if totals['orders'] else 0.0,
            'completion_rate': safe_div(totals['completes'] * 100.0, max(totals['accepts'], 1e-12)) if totals['accepts'] else 0.0,
            'trips_per_hour':  safe_div(totals['completes'], max(hours, 1e-12)) if hours else 0.0,
            'cancelled_by_driver': safe_div(sum(int(r.get('driver_cancellations') or 0) for r in rows) * 100.0, max(totals['accepts'], 1e-12)) if totals['accepts'] else 0.0,
        }

        # Issues + call notes window (previous 8 full weeks to today)
        notes_df = first_monday_prev_8_weeks(today)
        notes_dt = today

        Issue = request.env['x_fleet_issue'].sudo()
        issues = Issue.search([
            ('driver_id', '=', drv.id),
            ('date_reported', '>=', datetime.combine(notes_df, datetime.min.time())),
            ('date_reported', '<=', datetime.combine(notes_dt, datetime.max.time())),
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
            'note':          (i.note or '').strip(),
        } for i in issues]

        CallNote = request.env['x_fleet_call_note'].sudo()
        note_recs = CallNote.search([
            ('driver_id', '=', drv.id),
            ('created_at',  '>=', datetime.combine(notes_df, datetime.min.time())),
            ('created_at',  '<=', datetime.combine(notes_dt, datetime.max.time())),
        ], order='created_at desc, id desc')

        call_notes = [{
            'created_at': r.created_at and r.created_at.strftime('%Y-%m-%d %H:%M'),
            'note': (r.note or '').strip(),
            'author': r.author_id and r.author_id.name or '',
        } for r in note_recs]

        return {
            'cards': cards,
            'series': series,
            'issues': issue_rows,
            'call_notes': call_notes,
            'metrics_range': {
                'start': metrics_df.strftime('%Y-%m-%d'),
                'end':   metrics_dt.strftime('%Y-%m-%d'),
            },
            'notes_range': {
                'start': notes_df.strftime('%Y-%m-%d'),
                'end':   notes_dt.strftime('%Y-%m-%d'),
            },
            'issues_range': {
                'start': notes_df.strftime('%Y-%m-%d'),
                'end':   notes_dt.strftime('%Y-%m-%d'),
            },
        }

    # ------- Call notes endpoints (kept with LP dashboard) -------

    @http.route('/fleet_call_notes/create', type='json', auth='user')
    def call_notes_create(self, driver_id: int, note: str):
        note = (note or '').strip()
        if not note:
            return {'ok': False, 'error': 'Note cannot be empty.'}
        Driver = request.env['x_fleet_driver'].sudo().browse(int(driver_id))
        if not Driver.exists():
            return {'ok': False, 'error': 'Driver not found.'}
        rec = request.env['x_fleet_call_note'].sudo().create({
            'driver_id': Driver.id,
            'note': note,
        })
        return {
            'ok': True,
            'row': {
                'id': rec.id,
                'driver_id': Driver.id,
                'created_at': rec.created_at and rec.created_at.strftime('%Y-%m-%d %H:%M'),
                'note': (rec.note or '').strip(),
                'author': rec.author_id and rec.author_id.name or '',
            }
        }

    @http.route('/fleet_call_notes/list', type='json', auth='user')
    def call_notes_list(self, driver_id: int, start_date: str = None, end_date: str = None):
        Driver = request.env['x_fleet_driver'].sudo().browse(int(driver_id))
        if not Driver:
            return {'call_notes': []}

        df = datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else None
        dt = datetime.strptime(end_date,   '%Y-%m-%d').date() if end_date   else None
        Note = request.env['x_fleet_call_note'].sudo()
        domain = [('driver_id', '=', Driver.id)]
        if df:
            domain.append(('created_at',  '>=', datetime.combine(df, datetime.min.time())))
        if dt:
            domain.append(('created_at',  '<=', datetime.combine(dt, datetime.max.time())))
        notes = Note.search(domain, order='created_at desc, id desc')

        def _fmt(n):
            return {
                'id': n.id,
                'driver_id': n.driver_id.id,
                'note': n.note or '',
                'author': n.author_id and n.author_id.name or '',
                'created_at': (n.created_at and n.created_at.strftime('%Y-%m-%d %H:%M')) or '',
            }

        return {'call_notes': [_fmt(n) for n in notes]}