"""
greythr_api.py
==============
Backend module for the HR Late-Comer Warning tool.

Refactored from the original greytHR Daily Attendance Extractor into a
reusable library that the Streamlit app (app.py) imports.

Responsibilities:
  - Authenticate against greytHR
  - Fetch all employees (paginated)
  - Fetch muster (attendance) records per employee, in parallel
  - Parse each daily record into a flat dict
  - Decide whether a given day counts as "late"
  - Aggregate late counts per employee for a given month

Nothing here touches Streamlit, so it can also be unit-tested / run standalone.
"""

import base64
import logging
import time
from datetime import datetime, timedelta, date
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

log = logging.getLogger("greythr_api")
if not log.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

BASE_URL = "https://api.greythr.com"


# ══════════════════════════════════════════════════════════
# AUTH + SESSION
# ══════════════════════════════════════════════════════════

def get_token(username, password, domain, timeout=15):
    """Obtain an OAuth client-credentials access token from greytHR."""
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    r = requests.post(
        f"https://{domain}/uas/v1/oauth2/client-token",
        headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials"},
        timeout=timeout,
    )
    if r.status_code == 200:
        log.info("Token obtained")
        return r.json().get("access_token")
    raise RuntimeError(f"Auth failed [{r.status_code}]: {r.text}")


def make_session(token, domain):
    """Build a requests.Session with auth headers and retry behaviour."""
    s = requests.Session()
    s.headers.update({
        "ACCESS-TOKEN": token,
        "x-greythr-domain": domain,
        "Content-Type": "application/json",
    })
    retry = requests.adapters.Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    s.mount("https://", requests.adapters.HTTPAdapter(max_retries=retry))
    return s


# ══════════════════════════════════════════════════════════
# EMPLOYEES (pagination: data[], pages.totalPages)
# ══════════════════════════════════════════════════════════

def fetch_all_employees(session):
    """Return a list of all employee dicts."""
    log.info("Fetching all employees...")
    employees, page = [], 0

    while True:
        r = session.get(
            f"{BASE_URL}/employee/v2/employees",
            params={"page": page, "size": 200},
            timeout=20,
        )
        if r.status_code != 200:
            log.error(f"Employee fetch failed [{r.status_code}]: {r.text[:200]}")
            break

        data = r.json()
        records = data.get("data") or []
        employees.extend(records)

        pages = data.get("pages") or {}
        total_pages = int(pages.get("totalPages") or 1)
        has_next = pages.get("hasNext", False)

        log.info(f"  Page {page + 1}/{total_pages} - {len(records)} employees")

        if not has_next:
            break
        page += 1

    log.info(f"  Total employees: {len(employees)}")
    return employees


# ══════════════════════════════════════════════════════════
# MUSTER (records[].employee + records[].summary)
# ══════════════════════════════════════════════════════════

def fetch_muster(session, emp_id, start, end, timeout=20):
    """Fetch the muster (attendance) records for one employee in a date range."""
    r = session.get(
        f"{BASE_URL}/attendance/v2/employee/{emp_id}/muster",
        params={"start": start, "end": end},
        timeout=timeout,
    )
    if r.status_code == 200:
        return r.json()
    log.debug(f"  Muster [{r.status_code}] emp={emp_id}: {r.text[:100]}")
    return None


# ══════════════════════════════════════════════════════════
# TIME HELPERS
# ══════════════════════════════════════════════════════════

def _clean_time(t):
    """Normalise a time/datetime field to HH:MM string, or '' if empty."""
    if not t:
        return ""
    t = str(t)
    # full ISO datetime  ->  take the HH:MM portion
    if len(t) > 10:
        return t[11:16]
    return t[:5]


def _to_minutes(hhmm):
    """Convert 'HH:MM' to integer minutes since midnight, or None if invalid."""
    if not hhmm:
        return None
    try:
        parts = hhmm.split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return h * 60 + m
    except (ValueError, IndexError):
        return None


# ══════════════════════════════════════════════════════════
# PARSE ONE MUSTER RECORD -> flat dict
# ══════════════════════════════════════════════════════════

def parse_record(rec, emp_email=""):
    emp = rec.get("employee") or {}
    summ = rec.get("summary") or {}
    excs = rec.get("exceptions") or []

    att_date = summ.get("attendanceDate") or ""
    try:
        dow = datetime.strptime(att_date, "%Y-%m-%d").strftime("%A")
    except ValueError:
        dow = ""

    shift = summ.get("shift") or {}
    shift_start_raw = shift.get("startTime") or ""
    shift_end_raw = shift.get("endTime") or ""
    shift_start = _clean_time(shift_start_raw)
    shift_end = _clean_time(shift_end_raw)

    leave = summ.get("leave") or {}
    leave_type = leave.get("leaveTypeName") or leave.get("type") or ""

    return {
        "employee_id": emp.get("id") or "",
        "employee_no": emp.get("employeeNo") or "",
        "employee_name": emp.get("name") or "",
        "employee_email": emp_email,
        "date": att_date,
        "day_of_week": dow,
        "day_type": summ.get("dayType") or "",
        "shift_name": shift.get("name") or "",
        "shift_start": shift_start,
        "shift_end": shift_end,
        "scheme_name": summ.get("schemeName") or "",
        "in_time": _clean_time(summ.get("firstInTime")),
        "out_time": _clean_time(summ.get("lastOutTime")),
        "total_work_hrs": summ.get("totalWorkHrs") or "00:00",
        "actual_work_hrs": summ.get("actualWorkHrs") or "00:00",
        "shortfall_hrs": summ.get("shortFallHrs") or "00:00",
        "excess_work_hrs": summ.get("excessWorkHrs") or "00:00",
        "session1_label": summ.get("session1Label") or "",
        "session2_label": summ.get("session2Label") or "",
        "on_leave": bool(summ.get("onLeave") or False),
        "leave_type": leave_type,
        "absent_reason": summ.get("absentReason") or "",
        "exceptions": " | ".join(excs),
    }


# ══════════════════════════════════════════════════════════
# LATENESS DECISION
# ══════════════════════════════════════════════════════════

def is_late(row, grace_minutes=0):
    """
    Decide whether a single parsed daily record counts as a 'late arrival'.

    A day counts as late only when ALL of the following hold:
      - It is a working day (day_type Regular / blank, not Holiday / WeekOff)
      - The employee was not on leave
      - Both a shift_start and an in_time are present
      - in_time is later than shift_start + grace period

    Returns (is_late: bool, late_by_minutes: int)
    """
    day_type = (row.get("day_type") or "").strip().lower()
    if day_type in ("holiday", "weekoff", "week off", "weekly off"):
        return False, 0

    if row.get("on_leave"):
        return False, 0

    shift_start_min = _to_minutes(row.get("shift_start"))
    in_time_min = _to_minutes(row.get("in_time"))

    if shift_start_min is None or in_time_min is None:
        return False, 0

    late_by = in_time_min - (shift_start_min + grace_minutes)
    if late_by > 0:
        return True, late_by
    return False, 0


# ══════════════════════════════════════════════════════════
# MONTH HELPERS
# ══════════════════════════════════════════════════════════

def month_bounds(year, month):
    """Return (start_date_str, end_date_str) for the given year/month."""
    start = date(year, month, 1)
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    end = nxt - timedelta(days=1)
    return str(start), str(end)


# ══════════════════════════════════════════════════════════
# PARALLEL FETCH FOR A MONTH
# ══════════════════════════════════════════════════════════

def _process_employee(token, domain, emp, start, end, grace_minutes):
    """Worker: fetch + parse one employee's muster, return (summary_dict, late_days_list)."""
    session = make_session(token, domain)
    emp_id = emp.get("employeeId")
    emp_name = emp.get("name") or ""
    emp_email = emp.get("email") or ""

    if not emp_id:
        return None

    data = fetch_muster(session, emp_id, start, end)
    if not data:
        # Still return a zero-row so the employee shows up (optional).
        return {
            "employee_id": emp_id,
            "employee_no": emp.get("employeeNo") or "",
            "employee_name": emp_name,
            "employee_email": emp_email,
            "late_count": 0,
            "late_days": [],
        }

    records = data.get("records") or []
    late_days = []

    for rec in records:
        row = parse_record(rec, emp_email)
        late, late_by = is_late(row, grace_minutes=grace_minutes)
        if late:
            late_days.append({
                "date": row["date"],
                "day_of_week": row["day_of_week"],
                "shift_start": row["shift_start"],
                "in_time": row["in_time"],
                "late_by_minutes": late_by,
            })

    return {
        "employee_id": emp_id,
        "employee_no": emp.get("employeeNo") or "",
        "employee_name": emp_name,
        "employee_email": emp_email,
        "late_count": len(late_days),
        "late_days": late_days,
    }


def get_late_comers_for_month(
    username, password, domain,
    year, month,
    grace_minutes=0,
    max_workers=10,
    progress_cb=None,
):
    """
    Top-level entry point used by the Streamlit app.

    Returns a dict:
      {
        "period": "YYYY-MM",
        "start": "...", "end": "...",
        "employees": [ <summary dict per employee with late_count > 0>, ... ],
        "all_employees_count": int,
        "elapsed": float,
      }

    progress_cb, if given, is called as progress_cb(done, total).
    """
    t0 = time.time()
    token = get_token(username, password, domain)
    session = make_session(token, domain)

    employees = fetch_all_employees(session)
    if not employees:
        return {
            "period": f"{year:04d}-{month:02d}",
            "start": "", "end": "",
            "employees": [],
            "all_employees_count": 0,
            "elapsed": time.time() - t0,
        }

    start, end = month_bounds(year, month)
    results = []
    total = len(employees)
    done = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _process_employee,
                token, domain, emp, start, end, grace_minutes
            ): emp
            for emp in employees
        }
        for fut in as_completed(futures):
            try:
                res = fut.result()
                if res:
                    results.append(res)
            except Exception as e:
                emp = futures[fut]
                log.warning(f"Error emp={emp.get('employeeId')}: {e}")
            done += 1
            if progress_cb:
                progress_cb(done, total)

    # keep only those who were actually late, sort worst-first
    late_comers = [r for r in results if r["late_count"] > 0]
    late_comers.sort(key=lambda r: r["late_count"], reverse=True)

    return {
        "period": f"{year:04d}-{month:02d}",
        "start": start,
        "end": end,
        "employees": late_comers,
        "all_employees_count": total,
        "elapsed": time.time() - t0,
    }
