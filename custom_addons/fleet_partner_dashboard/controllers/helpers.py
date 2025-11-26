## `controllers/helpers.py`

# -*- coding: utf-8 -*-
from datetime import date, datetime, timedelta
from dateutil.parser import isoparse
from odoo.http import request
import calendar, json, os, re, requests, logging

_logger = logging.getLogger(__name__)

# -------- Generic utils --------

def humanize(s):
    if not s:
        return ''
    s = re.sub(r'[_\s]+', ' ', str(s)).strip()
    return s.title()


def parse_iso_with_frac(s: str) -> datetime:
    dt = isoparse(s)
    if dt.tzinfo:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)  # type: ignore[attr-defined]
    return dt


def parse_events(o):
    evs = o.events or '[]'
    if isinstance(evs, str):
        try:
            evs = json.loads(evs)
        except ValueError:
            evs = []
    return [e for e in evs if isinstance(e, dict)]


def order_statuses(o):
    return {e.get('order_status') for e in parse_events(o)}


def safe_div(a, b):
    return (a / b) if b else 0.0

# -------- Date windows --------

def last_week_range(today: date) -> tuple[date, date]:
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    return last_monday, last_sunday


def this_week_range(today: date) -> tuple[date, date]:
    monday = today - timedelta(days=today.weekday())
    end = min(monday + timedelta(days=6), today - timedelta(days=1))
    if end < monday:
        end = monday
    return monday, end


def last_7_days_excl_today(today: date) -> tuple[date, date]:
    end = today - timedelta(days=1)
    start = end - timedelta(days=6)
    return start, end


def first_monday_prev_8_weeks(today: date) -> date:
    this_mon = today - timedelta(days=today.weekday())
    return this_mon - timedelta(days=7*8)


def last_month_range(today: date) -> tuple[date, date]:
    first_this_month = date(today.year, today.month, 1)
    last_of_last_month = first_this_month - timedelta(days=1)
    first_of_last_month = date(last_of_last_month.year, last_of_last_month.month, 1)
    return first_of_last_month, last_of_last_month


def last_n_months_range(today: date, n: int) -> tuple[date, date]:
    first_this_month = date(today.year, today.month, 1)
    end = first_this_month - timedelta(days=1)
    total = end.year * 12 + (end.month - 1) - (n - 1)
    start_year, start_month_index = divmod(total, 12)
    start_month = start_month_index + 1
    start = date(start_year, start_month, 1)
    return start, end


def previous_period(df: date, dt_: date) -> tuple[date, date]:
    length = (dt_ - df).days + 1
    prev_dt = df - timedelta(days=1)
    prev_df = prev_dt - timedelta(days=length - 1)
    return prev_df, prev_dt

# -------- Bucketing (day/week/month) --------

def bucketize_span(df: date, dt_: date):
    span = (dt_ - df).days + 1
    buckets = []
    if span <= 30:
        cur = df
        while cur <= dt_:
            buckets.append((cur, cur, cur.strftime('%Y-%m-%d'), 'day'))
            cur += timedelta(days=1)
    elif span < 90:
        start = df - timedelta(days=df.weekday())  # Monday
        cur = start
        while cur <= dt_:
            nxt = cur + timedelta(days=6)
            buckets.append((cur, min(nxt, dt_), cur.strftime('%Y-%m-%d'), 'week'))
            cur += timedelta(days=7)
    else:
        y0, m0 = df.year, df.month
        y1, m1 = dt_.year, dt_.month
        start_month = y0 * 12 + (m0 - 1)
        end_month   = y1 * 12 + (m1 - 1)
        for ym in range(start_month, end_month + 1):
            y, mo = divmod(ym, 12); mo += 1
            start_day = date(y, mo, 1)
            last_day  = date(y, mo, calendar.monthrange(y, mo)[1])
            buckets.append((start_day, min(last_day, dt_), start_day.strftime('%Y-%m'), 'month'))
    return buckets

# -------- Signer client --------

def signer_get(path: str, params: dict) -> dict:
    """Centralised signer call with ICP + retries. Returns dict(rows=[], count=0)."""
    icp = request.env['ir.config_parameter'].sudo()
    base = (icp.get_param('media_signer.base_url') or os.environ.get('SIGNER_URL', '')).rstrip('/')
    key  = icp.get_param('media_signer.api_key')   or os.environ.get('SIGNER_API_KEY')
    if not base or not key:
        return {"error": "Missing SIGNER_URL or SIGNER_API_KEY", "rows": [], "count": 0}

    url = f"{base}{path}"
    headers = {"x-api-key": key}
    TIMEOUT = (5, 120)
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
            if 400 <= r.status_code < 500:
                _logger.warning("Signer %s returned %s: %s", path, r.status_code, (r.text or "")[:200])
                return {"rows": [], "count": 0}
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and "rows" in data and "count" in data:
                return data
            if isinstance(data, list):
                return {"rows": data, "count": len(data)}
            return {"rows": [], "count": 0}
        except requests.Timeout:
            _logger.warning("Signer request timed out (attempt %d/3): %s", attempt+1, path)
            import time as _t; _t.sleep(1.0 * (attempt + 1))
            continue
        except requests.RequestException as e:
            _logger.exception("Signer request failed: %s", e)
            break
    return {"rows": [], "count": 0}