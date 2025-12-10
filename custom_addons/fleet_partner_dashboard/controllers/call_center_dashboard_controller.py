# controllers/call_center_dashboard_controller.py
# -*- coding: utf-8 -*-

from datetime import date, datetime, timedelta
import os

from odoo import http, fields
from odoo.http import request

from .helpers import (
    humanize,
    safe_div,
    bucketize_span,
    last_week_range,
    this_week_range,
    signer_get,
)


class CallCenterDashboardController(http.Controller):

    # -------------------------------------------------------------------------
    # Filters (product types, issue types)
    # -------------------------------------------------------------------------
    @http.route('/fleet_call_center/filters', type='json', auth='user')
    def call_center_filters(self):
        Product = request.env['x_fleet_product_type'].sudo()
        pts = Product.search([], order='name')
        product_types = [{'id': p.id, 'name': p.name} for p in pts]

        # For now we keep issue types simple + hard-coded
        issue_types = [
            {'key': 'support', 'label': 'Support'},
            {'key': 'performance', 'label': 'Performance'},
            {'key': 'training', 'label': 'Training'},
        ]
        return {
            'product_types': product_types,
            'issue_types': issue_types,
        }

    # -------------------------------------------------------------------------
    # Kanban board data
    # -------------------------------------------------------------------------
    @http.route('/fleet_call_center/board_data', type='json', auth='user')
    def call_center_board_data(self, products=None, issue_types=None):
        """
        Returns columns -> cards (drivers) for the Kanban-style call center board.

        - Columns are based on issue.status:
          unresolved, requires_follow_up_call, invited_to_office,
          invited_to_workshop, resolved.

        - Only issues with these statuses (plus "unresponsive") are included.
        - "unresponsive" issues are *treated as Not Started* for now and the
          corresponding driver cards are shown at the bottom of the Not Started
          column, under an "Unresponsive" section.

        - Filters:
            products    -> list of x_fleet_product_type ids
            issue_types -> list of strings (support, performance, training)
        """
        products = products or []
        issue_types = issue_types or []

        Issue = request.env['x_fleet_issue'].sudo()

        # 5 main columns on the board
        status_defs = [
            ('unresolved', 'Not Started'),
            ('requires_follow_up_call', 'Requires Follow-Up Call'),
            ('invited_to_office', 'Invited To Office'),
            ('invited_to_workshop', 'Invited To Workshop'),
            ('resolved', 'Resolved'),
        ]
        status_keys = [k for k, _ in status_defs]

        # We'll also look at "unresponsive" as a special-case flag
        domain = [
            ('status', 'in', status_keys + ['unresponsive']),
        ]
        if issue_types:
            domain.append(('issue_type', 'in', issue_types))

        issues = Issue.search(domain)

        # Filter by product (via driver.product_type_id) if requested
        if products:
            issues = issues.filtered(
                lambda i: i.driver_id
                and i.driver_id.product_type_id.id in products
            )

        # Build columns: key -> {'key', 'label', 'cards_dict'}
        columns_map = {
            key: {
                'key': key,
                'label': label,
                'cards_dict': {},  # driver_id -> card dict
            }
            for key, label in status_defs
        }

        # Aggregate issues per driver per column
        for issue in issues:
            drv = issue.driver_id
            if not drv:
                continue

            # For now, treat unresponsive as "Not Started" but mark card as unresponsive
            status = issue.status or 'unresolved'
            is_unresponsive = (status == 'unresponsive')
            if is_unresponsive:
                col_key = 'unresolved'
            else:
                col_key = status

            if col_key not in columns_map:
                continue

            col = columns_map[col_key]
            cards_dict = col['cards_dict']

            card = cards_dict.get(drv.id)
            if not card:
                card = {
                    'driver_id': drv.id,
                    'name': drv.name,
                    'phone': drv.phone or '',
                    'product': drv.product_type_id.name or '',
                    'type_counts': {
                        'support': 0,
                        'performance': 0,
                        'training': 0,
                        'other': 0,
                    },
                    'total_issues': 0,
                    'is_unresponsive': False,
                }
                cards_dict[drv.id] = card

            # Classify issue type
            t = (issue.issue_type or '').strip().lower()
            t_key = t if t in ('support', 'performance', 'training') else 'other'
            card['type_counts'][t_key] = card['type_counts'].get(t_key, 0) + 1
            card['total_issues'] += 1

            if is_unresponsive:
                card['is_unresponsive'] = True

        # Convert card dicts -> arrays and sort:
        #   - responsive cards first (is_unresponsive = False)
        #   - then unresponsive
        #   - within each group, sort by driver name
        columns = []
        for key, label in status_defs:
            col = columns_map[key]
            cards = list(col['cards_dict'].values())

            cards.sort(
                key=lambda c: (
                    1 if c.get('is_unresponsive') else 0,
                    (c.get('name') or '').lower(),
                )
            )
            columns.append({
                'key': key,
                'label': label,
                'cards': cards,
            })

        return {'columns': columns}

    # -------------------------------------------------------------------------
    # Driver detail (modal)
    # -------------------------------------------------------------------------
    @http.route('/fleet_call_center/driver_detail', type='json', auth='user')
    def call_center_driver_detail(self, driver_id: int, start_date: str = None, end_date: str = None):
        """
        Driver detail used by the Call Center modal.

        - Metrics are computed like the Performance dashboard, using driver-day
          data over a chosen date window (defaults to last week).
        - Issues table:
            * Support + training issues:
                  all NON-resolved, any date
            * Performance issues:
                  only ones from the CURRENT week
        """
        today = date.today()

        # ---- Metrics window (same as performance dashboard style) -----------
        if start_date and end_date:
            try:
                df = datetime.strptime(start_date, '%Y-%m-%d').date()
                dt_ = datetime.strptime(end_date, '%Y-%m-%d').date()
                if df > dt_:
                    df, dt_ = dt_, df
            except Exception:
                df, dt_ = last_week_range(today)
        else:
            df, dt_ = last_week_range(today)

        # Never go beyond "yesterday" for metrics
        dt_ = min(dt_, today - timedelta(days=1))

        icp = request.env['ir.config_parameter'].sudo()
        TENANT_CODE = icp.get_param('metrics.tenant_code') or os.environ.get(
            'TENANT_CODE', 'anda'
        )

        Driver = request.env['x_fleet_driver'].sudo().browse(int(driver_id))
        yid = (Driver.yango_driver_id or '').strip()
        if not Driver or not yid:
            return {'cards': {}, 'series': {}, 'issues': []}

        # Pull driver-day rows from Supabase-backed metrics
        resp = signer_get('/metrics/driver-day', {
            'from_date': df.strftime('%Y-%m-%d'),
            'to_date': dt_.strftime('%Y-%m-%d'),
            'tenant': TENANT_CODE,
        })
        rows = [
            r for r in resp.get('rows', [])
            if (r.get('driver_id') or '').strip() == yid
        ]

        buckets = bucketize_span(df, dt_)
        labels = [lbl for _, _, lbl, _ in buckets]

        def _bucket_key(d):
            for bstart, bend, lbl, _ in buckets:
                if bstart <= d <= bend:
                    return lbl
            return None

        agg = {
            lbl: {
                'orders': 0,
                'accepts': 0,
                'completes': 0,
                'sup_secs': 0.0,
                'cash': 0.0,
                'driver_cancels': 0,
            }
            for lbl in labels
        }

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

        series = {
            k: [] for k in [
                'trips',
                'supplyHours',
                'cashEarned',
                'acceptanceRate',
                'completionRate',
                'tripsPerHour',
                'cancelledByDriverPct',
            ]
        }
        totals = {
            'orders': 0,
            'accepts': 0,
            'completes': 0,
            'sup_secs': 0.0,
            'cash': 0.0,
            'driver_cancels': 0,
        }

        for lbl in labels:
            a = agg[lbl]
            hours = a['sup_secs'] / 3600.0
            acc_pct = safe_div(
                a['accepts'] * 100.0,
                max(a['orders'], 1e-12),
            ) if a['orders'] else 0.0
            comp_pct = safe_div(
                a['completes'] * 100.0,
                max(a['accepts'], 1e-12),
            ) if a['accepts'] else 0.0
            trph = safe_div(
                a['completes'],
                max(hours, 1e-12),
            ) if hours else 0.0
            cbd_pct = safe_div(
                a['driver_cancels'] * 100.0,
                max(a['accepts'], 1e-12),
            ) if a['accepts'] else 0.0

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
                totals['accepts'] * 100.0,
                max(totals['orders'], 1e-12),
            ) if totals['orders'] else 0.0,
            'completion_rate': safe_div(
                totals['completes'] * 100.0,
                max(totals['accepts'], 1e-12),
            ) if totals['accepts'] else 0.0,
            'trips_per_hour': safe_div(
                totals['completes'],
                max(total_hours, 1e-12),
            ) if total_hours else 0.0,
            'cancelled_by_driver': safe_div(
                totals['driver_cancels'] * 100.0,
                max(totals['accepts'], 1e-12),
            ) if totals['accepts'] else 0.0,
        }

        # ---- Issues window logic -------------------------------------------
        week_from, week_to = this_week_range(today)

        Issue = request.env['x_fleet_issue'].sudo()

        # 1) Support + training, not resolved, any date
        domain_st = [
            ('driver_id', '=', Driver.id),
            ('issue_type', 'in', ['support', 'training']),
            ('status', '!=', 'resolved'),
        ]
        issues_st = Issue.search(domain_st)

        # 2) Performance, current week only
        domain_perf = [
            ('driver_id', '=', Driver.id),
            ('issue_type', '=', 'performance'),
            ('date_reported', '>=', datetime.combine(week_from, datetime.min.time())),
            ('date_reported', '<=', datetime.combine(week_to, datetime.max.time())),
        ]
        issues_perf = Issue.search(domain_perf)

        issues = issues_st | issues_perf

        # Sort newest first
        issues = issues.sorted(
            key=lambda i: (
                i.date_reported or fields.Datetime.from_string('1970-01-01 00:00:00'),
                i.id,
            )
        )
        issues = issues[::-1]  # reverse to get desc

        def _cat_path(i):
            parts = [
                i.main_category_label or humanize(i.main_category),
                i.sub_category_label or humanize(i.sub_category),
                i.sub_sub_category_label or humanize(i.sub_sub_category),
            ]
            parts = [p for p in parts if p]
            return ' › '.join(parts)

        def _human_status(s):
            if not s:
                return ''
            return humanize(s)

        issue_rows = []
        for i in issues:
            issue_rows.append({
                'id': i.id,
                'issue_name': i.name or '',
                'issue_type': i.issue_type_label or humanize(i.issue_type),
                'category_path': _cat_path(i),
                'date_reported': i.date_reported and i.date_reported.strftime('%Y-%m-%d %H:%M') or '',
                'date_resolved': i.resolved_on and i.resolved_on.strftime('%Y-%m-%d %H:%M') or '',
                'status': _human_status(i.status),
                'status_raw': i.status,
                'reporter_note': (i.note or '').strip(),
            })

        return {
            'cards': cards,
            'series': series,
            'issues': issue_rows,
            'metrics_range': {
                'start': df.strftime('%Y-%m-%d'),
                'end': dt_.strftime('%Y-%m-%d'),
            },
            'issues_range': {
                'start': week_from.strftime('%Y-%m-%d'),
                'end': week_to.strftime('%Y-%m-%d'),
            },
        }
        
    # -------------------------------------------------------------------------
    # Single issue detail (for Issue ID modal)
    # -------------------------------------------------------------------------
    
    @http.route('/fleet_call_center/issue_detail', type='json', auth='user')
    def call_center_issue_detail(self, issue_id: int):
        Issue = request.env['x_fleet_issue'].sudo()
        issue = Issue.browse(int(issue_id or 0))
        if not issue.exists():
            return {'ok': False, 'error': 'Issue not found.'}

        # Map status code -> label from the selection on the model
        status_labels = dict(Issue._fields['status'].selection)

        def _label(code, label_field_name):
            rec_label = getattr(issue, label_field_name, False)
            if rec_label:
                return rec_label
            return humanize(getattr(issue, code, '') or '')

        issue_data = {
            'id': issue.id,
            'name': issue.name or '',
            'issue_type': issue.issue_type_label or humanize(issue.issue_type),
            'main_category': _label('main_category', 'main_category_label'),
            'sub_category': _label('sub_category', 'sub_category_label'),
            'sub_sub_category': _label('sub_sub_category', 'sub_sub_category_label'),
            'status': issue.status or '',
            'status_label': status_labels.get(issue.status) or humanize(issue.status),
            'note': (issue.note or '').strip(),
            'driver': {
                'id': issue.driver_id.id,
                'name': issue.driver_id.name or '',
                'phone': issue.driver_id.phone or '',
            },
        }

        CallNote = request.env['x_fleet_issue_call_note'].sudo()
        notes = CallNote.search(
            [('issue_id', '=', issue.id)],
            order='created_at desc, id desc',
        )

        call_notes = []
        for n in notes:
            call_notes.append({
                'id': n.id,
                'created_at': n.created_at and n.created_at.strftime('%Y-%m-%d %H:%M') or '',
                'author': n.author_id and n.author_id.name or '',
                'status_from': n.status_from or '',
                'status_to': n.status_to or '',
                'status_from_label': status_labels.get(n.status_from) or humanize(n.status_from),
                'status_to_label': status_labels.get(n.status_to) or humanize(n.status_to),
                'note': n.note or '',
            })

        return {
            'ok': True,
            'issue': issue_data,
            'call_notes': call_notes,
        }
        
    # -------------------------------------------------------------------------
    # Update issue status + create issue call note
    # -------------------------------------------------------------------------
    
    @http.route('/fleet_call_center/update_issue_status', type='json', auth='user')
    def call_center_update_issue_status(self, issue_id: int, new_status: str, note: str = ''):
        Issue = request.env['x_fleet_issue'].sudo()
        issue = Issue.browse(int(issue_id or 0))
        if not issue.exists():
            return {'ok': False, 'error': 'Issue not found.'}

        new_status = (new_status or '').strip()
        note = (note or '').strip()

        # Require a note – the UI is already enforcing this, but double-check
        if not note:
            return {'ok': False, 'error': 'Call note is required.'}

        status_labels = dict(Issue._fields['status'].selection)
        valid_statuses = set(status_labels.keys())

        if new_status not in valid_statuses:
            return {'ok': False, 'error': 'Invalid status.'}

        old_status = issue.status

        vals = {'status': new_status}
        # If marking resolved, set resolved_on if not already set
        if new_status == 'resolved' and not issue.resolved_on:
            vals['resolved_on'] = fields.Datetime.now()

        issue.write(vals)

        # Create the issue call note
        CallNote = request.env['x_fleet_issue_call_note'].sudo()
        CallNote.create({
            'issue_id': issue.id,
            'note': note,
            'status_from': old_status,
            'status_to': new_status,
        })

        return {
            'ok': True,
            'issue': {
                'id': issue.id,
                'status': issue.status,
                'status_label': status_labels.get(issue.status) or humanize(issue.status),
                'date_resolved': issue.resolved_on and issue.resolved_on.strftime('%Y-%m-%d %H:%M') or '',
            },
        }
