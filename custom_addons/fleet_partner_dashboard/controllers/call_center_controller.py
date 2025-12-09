# controllers/call_center_controller.py
# -*- coding: utf-8 -*-
from datetime import date, datetime, timedelta
from collections import defaultdict
from typing import Optional

from odoo import http, fields, _
from odoo.http import request

from .helpers import (
    humanize,
    safe_div,
    bucketize_span,
    this_week_range,
    last_week_range,
    signer_get,
)
import os


class CallCenterController(http.Controller):

    # ------------------------------------------------------------------
    #  Filters / board data
    # ------------------------------------------------------------------

    @http.route('/fleet_call_center/filters', type='json', auth='user')
    def call_center_filters(self):
        """Basic filters for the call center board."""
        Product = request.env['x_fleet_product_type'].sudo()
        Driver = request.env['x_fleet_driver'].sudo()

        pts = Product.search([], order='name')
        product_types = [{'id': p.id, 'name': p.name} for p in pts]

        categories = [k for k, _ in Driver._fields['type'].selection]

        # Issue types as free-form char values (normalized to lower)
        Issue = request.env['x_fleet_issue'].sudo()
        all_types = Issue.search([]).mapped('issue_type')
        norm_types = sorted({(t or '').strip().lower() for t in all_types if t})
        issue_types = [{'key': t, 'label': humanize(t)} for t in norm_types]

        # Status choice list
        status_sel = request.env['x_fleet_issue']._fields['status'].selection
        statuses = [{'key': k, 'label': v} for (k, v) in status_sel]

        periods = ['This Week', 'Last Week', 'Last 4 Weeks']

        return {
            'product_types': product_types,
            'categories': categories,
            'issue_types': issue_types,
            'statuses': statuses,
            'time_periods': periods,
        }

    @http.route('/fleet_call_center/data', type='json', auth='user')
    def call_center_data(
        self,
        period: Optional[str] = None,
        products=None,
        categories=None,
        issue_types=None,
        statuses=None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ):
        """
        Main board: drivers with counts of issues per issue_type.

        Very similar to low_performers/data, but focused on issues rather than
        performance ranking. You can extend filtering later.
        """
        products = products or []
        categories = categories or []
        issue_types = issue_types or []
        statuses = statuses or []

        today = date.today()
        if start_date and end_date:
            df = datetime.strptime(start_date, '%Y-%m-%d').date()
            dt_ = datetime.strptime(end_date, '%Y-%m-%d').date()
            if df > dt_:
                df, dt_ = dt_, df
        else:
            if period == 'This Week':
                df, dt_ = this_week_range(today)
            elif period == 'Last Week':
                df, dt_ = last_week_range(today)
            else:
                # Last 4 full weeks (Mon–Sun)
                mon, sun = last_week_range(today)
                df = mon - timedelta(days=7 * 3)
                dt_ = sun

        # For “board” we care about issues overlapping this range
        dt_ = min(dt_, today)

        Driver = request.env['x_fleet_driver'].sudo()
        Issue = request.env['x_fleet_issue'].sudo()

        drv_domain = []
        if products:
            drv_domain.append(('product_type_id', 'in', products))
        if categories:
            drv_domain.append(('type', 'in', categories))
        drivers = Driver.search(drv_domain)

        # map driver id -> driver info
        data = {
            d.id: {
                'id': d.id,
                'name': d.name,
                'phone': d.phone or '',
                'product_type': d.product_type_id.name or '',
                'category': d.type or '',
                'issues_total': 0,
                'issues_by_type': defaultdict(int),
            }
            for d in drivers
        }

        # Issues overlapping the chosen range
        issue_domain = [
            ('driver_id', 'in', list(data.keys())),
            ('date_reported', '>=', datetime.combine(df, datetime.min.time())),
            ('date_reported', '<=', datetime.combine(dt_, datetime.max.time())),
        ]
        if issue_types:
            issue_domain.append(('issue_type', 'in', issue_types))
        if statuses:
            issue_domain.append(('status', 'in', statuses))

        issues = Issue.search(issue_domain)
        for i in issues:
            row = data.get(i.driver_id.id)
            if not row:
                continue
            row['issues_total'] += 1
            key = (i.issue_type or '').strip().lower() or 'unknown'
            row['issues_by_type'][key] += 1

        # Pack into JSON-friendly shape
        out = []
        for drv_id, row in data.items():
            issues_by_type = [
                {'issue_type': k, 'label': humanize(k), 'count': v}
                for k, v in row['issues_by_type'].items()
            ]
            out.append({**row, 'issues_by_type': issues_by_type})

        return {
            'drivers': out,
            'range': {'start': df.strftime('%Y-%m-%d'), 'end': dt_.strftime('%Y-%m-%d')},
        }

    # ------------------------------------------------------------------
    #  Driver detail (metrics + issues table)
    # ------------------------------------------------------------------

    @http.route('/fleet_call_center/driver_detail', type='json', auth='user')
    def call_center_driver_detail(self, driver_id: int, start_date: str, end_date: str):
        """
        Driver detail panel for the call-center:

        * Metrics cards + series (same logic as performance dashboard)
        * Issues table:
          - all non-resolved training + support issues (any date)
          - performance issues from *this* week only
        """
        today = date.today()
        try:
            df = datetime.strptime(start_date, '%Y-%m-%d').date()
            dt_ = datetime.strptime(end_date, '%Y-%m-%d').date()
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

        # ---- Metrics (copy of performance_driver_detail) ----------------
        resp = signer_get('/metrics/driver-day', {
            'from_date': df.strftime('%Y-%m-%d'),
            'to_date': dt_.strftime('%Y-%m-%d'),
            'tenant': TENANT_CODE,
        })
        rows = [r for r in resp.get('rows', []) if (r.get('driver_id') or '').strip() == yid]

        buckets = bucketize_span(df, dt_)
        labels = [lbl for _, _, lbl, _ in buckets]

        def _bucket_key(d):
            for bstart, bend, lbl, _ in buckets:
                if bstart <= d <= bend:
                    return lbl
            return None

        agg = {lbl: {'orders': 0, 'accepts': 0, 'completes': 0, 'sup_secs': 0.0,
                     'cash': 0.0, 'driver_cancels': 0} for lbl in labels}
        for r in rows:
            try:
                day = datetime.strptime(r.get('day'), '%Y-%m-%d').date()
            except Exception:
                continue
            lbl = _bucket_key(day)
            if not lbl:
                continue
            a = agg[lbl]
            a['orders'] += int(r.get('orders_total') or 0)
            a['accepts'] += int(r.get('accepts') or 0)
            a['completes'] += int(r.get('orders_completed') or 0)
            a['sup_secs'] += float(r.get('supply_seconds') or 0.0)
            a['cash'] += float(r.get('cash_sum') or 0.0)
            a['driver_cancels'] += int(r.get('driver_cancellations') or 0)

        series = {k: [] for k in [
            'trips', 'supplyHours', 'cashEarned',
            'acceptanceRate', 'completionRate',
            'tripsPerHour', 'cancelledByDriverPct',
        ]}
        totals = {'orders': 0, 'accepts': 0, 'completes': 0,
                  'sup_secs': 0.0, 'cash': 0.0, 'driver_cancels': 0}
        for lbl in labels:
            a = agg[lbl]
            hours = a['sup_secs'] / 3600.0
            acc_pct = safe_div(a['accepts'] * 100.0, max(a['orders'], 1e-12)) if a['orders'] else 0.0
            comp_pct = safe_div(a['completes'] * 100.0, max(a['accepts'], 1e-12)) if a['accepts'] else 0.0
            trph = safe_div(a['completes'], max(hours, 1e-12)) if hours else 0.0
            cbd_pct = safe_div(a['driver_cancels'] * 100.0, max(a['accepts'], 1e-12)) if a['accepts'] else 0.0

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
            'acceptance_rate': safe_div(
                totals['accepts'] * 100.0, max(totals['orders'], 1e-12)
            ) if totals['orders'] else 0.0,
            'completion_rate': safe_div(
                totals['completes'] * 100.0, max(totals['accepts'], 1e-12)
            ) if totals['accepts'] else 0.0,
            'trips_per_hour': safe_div(
                totals['completes'], max(total_hours, 1e-12)
            ) if total_hours else 0.0,
            'cancelled_by_driver': safe_div(
                totals['driver_cancels'] * 100.0, max(totals['accepts'], 1e-12)
            ) if totals['accepts'] else 0.0,
        }

        # ---- Issues for this driver ------------------------------------
        Issue = request.env['x_fleet_issue'].sudo()

        # 1) Performance issues from this week only
        this_mon, this_sun = this_week_range(today)
        perf_issues = Issue.search([
            ('driver_id', '=', Driver.id),
            ('issue_type', '=', 'performance'),
            ('date_reported', '>=', datetime.combine(this_mon, datetime.min.time())),
            ('date_reported', '<=', datetime.combine(this_sun, datetime.max.time())),
        ])

        # 2) All non-resolved Training/Support issues (any date)
        non_perf_issues = Issue.search([
            ('driver_id', '=', Driver.id),
            ('issue_type', 'in', ['support', 'training']),
            ('status', '!=', 'resolved'),
        ])

        issues = (perf_issues | non_perf_issues).sorted(
            key=lambda i: i.date_reported or fields.Datetime.from_string('1970-01-01 00:00:00'),
            reverse=True,
        )

        status_sel = dict(Issue._fields['status'].selection)

        def _cat_path(i):
            parts = [
                i.main_category_label or humanize(i.main_category),
                i.sub_category_label or humanize(i.sub_category),
                i.sub_sub_category_label or humanize(i.sub_sub_category),
            ]
            parts = [p for p in parts if p]
            return ' › '.join(parts)

        issue_rows = [{
            'id': i.id,
            'name': i.name,
            'issue_type': (i.issue_type_label or humanize(i.issue_type) or '').strip(),
            'category_path': _cat_path(i),
            'date_reported': i.date_reported and i.date_reported.strftime('%Y-%m-%d %H:%M'),
            'date_resolved': i.resolved_on and i.resolved_on.strftime('%Y-%m-%d %H:%M') or '',
            'status': i.status,
            'status_label': status_sel.get(i.status, i.status or ''),
            'reporter_note': (i.note or '').strip(),
        } for i in issues]

        return {
            'driver': {
                'id': Driver.id,
                'name': Driver.name,
                'phone': Driver.phone or '',
                'product_type': Driver.product_type_id.name or '',
                'category': Driver.type or '',
            },
            'cards': cards,
            'series': series,
            'issues': issue_rows,
            'metrics_range': {'start': df.strftime('%Y-%m-%d'), 'end': dt_.strftime('%Y-%m-%d')},
        }

    # ------------------------------------------------------------------
    #  Issue detail + status update + call notes
    # ------------------------------------------------------------------

    @http.route('/fleet_call_center/issue_detail', type='json', auth='user')
    def call_center_issue_detail(self, issue_id: int):
        Issue = request.env['x_fleet_issue'].sudo()
        CallNote = request.env['x_fleet_issue_call_note'].sudo()

        issue = Issue.browse(int(issue_id))
        if not issue.exists():
            return {'ok': False, 'error': 'Issue not found.'}

        status_sel = dict(Issue._fields['status'].selection)

        issue_info = {
            'id': issue.id,
            'name': issue.name,
            'issue_type': (issue.issue_type_label or humanize(issue.issue_type) or '').strip(),
            'main_category': issue.main_category_label or humanize(issue.main_category),
            'sub_category': issue.sub_category_label or humanize(issue.sub_category),
            'sub_sub_category': issue.sub_sub_category_label or humanize(issue.sub_sub_category),
            'status': issue.status,
            'status_label': status_sel.get(issue.status, issue.status or ''),
            'can_work': issue.can_work,
            'note': issue.note or '',
            'date_reported': issue.date_reported and issue.date_reported.strftime('%Y-%m-%d %H:%M'),
            'date_resolved': issue.resolved_on and issue.resolved_on.strftime('%Y-%m-%d %H:%M') or '',
            'driver': {
                'id': issue.driver_id.id,
                'name': issue.driver_id.name,
                'phone': issue.driver_id.phone or '',
            },
        }

        notes = CallNote.search([('issue_id', '=', issue.id)], order='created_at desc, id desc')

        def _fmt_note(n):
            return {
                'id': n.id,
                'created_at': n.created_at and n.created_at.strftime('%Y-%m-%d %H:%M'),
                'author': n.author_id and n.author_id.name or '',
                'author_id': n.author_id.id if n.author_id else False,
                'note': n.note or '',
                'status_from': n.status_from,
                'status_to': n.status_to,
                'status_from_label': status_sel.get(n.status_from, n.status_from or ''),
                'status_to_label': status_sel.get(n.status_to, n.status_to or ''),
            }

        return {
            'ok': True,
            'issue': issue_info,
            'call_notes': [_fmt_note(n) for n in notes],
        }

    @http.route('/fleet_call_center/update_issue_status', type='json', auth='user')
    def call_center_update_issue_status(self, issue_id: int, new_status: str, note: str):
        """
        Called from the dashboard when an agent changes status.

        Enforces: note must be non-empty; creates x_fleet_issue_call_note row
        in the same transaction as the status change.
        """
        note = (note or '').strip()
        if not note:
            return {'ok': False, 'error': _('Note cannot be empty.')}

        Issue = request.env['x_fleet_issue'].sudo()
        CallNote = request.env['x_fleet_issue_call_note'].sudo()

        issue = Issue.browse(int(issue_id))
        if not issue.exists():
            return {'ok': False, 'error': _('Issue not found.')}

        status_field = Issue._fields['status']
        valid_keys = {k for (k, _) in status_field.selection}
        if new_status not in valid_keys:
            return {'ok': False, 'error': _('Invalid status.')}

        old_status = issue.status

        # Write will handle resolved_on + Supabase push (existing logic)
        issue.write({'status': new_status})

        # Create structured call note
        cn = CallNote.create({
            'issue_id': issue.id,
            'note': note,
            'status_from': old_status,
            'status_to': new_status,
        })

        status_sel = dict(status_field.selection)

        return {
            'ok': True,
            'issue': {
                'id': issue.id,
                'name': issue.name,
                'status': issue.status,
                'status_label': status_sel.get(issue.status, issue.status or ''),
                'date_resolved': issue.resolved_on and issue.resolved_on.strftime('%Y-%m-%d %H:%M') or '',
            },
            'call_note': {
                'id': cn.id,
                'created_at': cn.created_at and cn.created_at.strftime('%Y-%m-%d %H:%M'),
                'author': cn.author_id and cn.author_id.name or '',
                'author_id': cn.author_id.id if cn.author_id else False,
                'note': cn.note or '',
                'status_from': cn.status_from,
                'status_to': cn.status_to,
                'status_from_label': status_sel.get(cn.status_from, cn.status_from or ''),
                'status_to_label': status_sel.get(cn.status_to, cn.status_to or ''),
            },
        }
