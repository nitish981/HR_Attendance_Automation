"""
greythr_api.py  —  FINAL
=========================
Department is fetched from /employee/v2/employees/{id}/categories
(the only endpoint that exposes it for this tenant).

Each employee's categories are fetched inside the parallel worker,
so no extra sequential slowdown.
"""

import base64
import logging
import time
from datetime import datetime, timedelta, date
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

log = logging.getLogger("greythr_api")
if not log.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BASE_URL = "https://api.greythr.com"


# ══════════════════════════════════════════════════════════
# AUTH + SESSION
# ══════════════════════════════════════════════════════════

def get_token(username, password, domain, timeout=15):
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    r = requests.post(
        f"https://{domain}/uas/v1/oauth2/client-token",
        headers={"Authorization": f"Basic {creds}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials"}, timeout=timeout,
    )
    if r.status_code == 200:
        return r.json().get("access_token")
    raise RuntimeError(f"Auth failed [{r.status_code}]: {r.text}")


def make_session(token, domain):
    s = requests.Session()
    s.headers.update({
        "ACCESS-TOKEN": token,
        "x-greythr-domain": domain,
        "Content-Type": "application/json",
    })
    retry = requests.adapters.Retry(
        total=3, backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    s.mount("https://", requests.adapters.HTTPAdapter(max_retries=retry))
    return s


# ══════════════════════════════════════════════════════════
# EMPLOYEES — active only
# ══════════════════════════════════════════════════════════

def fetch_active_employees(session):
    log.info("Fetching employees (active only)...")
    employees, page = [], 0
    while True:
        r = session.get(
            f"{BASE_URL}/employee/v2/employees",
            params={"page": page, "size": 200}, timeout=20,
        )
        if r.status_code != 200:
            log.error(f"Employee fetch failed [{r.status_code}]: {r.text[:200]}")
            break
        data = r.json()
        records = data.get("data") or []
        for emp in records:
            if emp.get("leftorg") is True:
                continue
            employees.append(emp)
        pages = data.get("pages") or {}
        if not pages.get("hasNext", False):
            break
        page += 1
    log.info(f"  Active employees: {len(employees)}")
    return employees


# ══════════════════════════════════════════════════════════
# CATEGORIES  — department, designation, location
# GET /employee/v2/employees/{id}/categories
# Returns items with categoryDesc + valueDesc
# ══════════════════════════════════════════════════════════

def fetch_employee_categories(session, emp_id):
    """
    Fetch categories for one employee.
    Returns dict: {"department": "...", "designation": "...", "location": "..."}
    """
    result = {"department": "", "designation": "", "location": ""}
    try:
        r = session.get(
            f"{BASE_URL}/employee/v2/employees/{emp_id}/categories",
            timeout=15,
        )
        if r.status_code != 200:
            return result

        cat_data = r.json()

        # Flatten: could be list, or dict with data/items key
        items = []
        if isinstance(cat_data, list):
            items = cat_data
        elif isinstance(cat_data, dict):
            items = (cat_data.get("data")
                     or cat_data.get("items")
                     or [cat_data])
            if not isinstance(items, list):
                items = [items]

        for item in items:
            if not isinstance(item, dict):
                continue
            cat_desc = str(item.get("categoryDesc") or "").strip().lower()
            val_desc = str(item.get("valueDesc") or "").strip()
            if not val_desc or val_desc == "None":
                continue
            if "department" in cat_desc:
                result["department"] = val_desc
            elif "designation" in cat_desc:
                result["designation"] = val_desc
            elif "location" in cat_desc:
                result["location"] = val_desc

    except Exception as e:
        log.debug(f"  Categories fetch failed for {emp_id}: {e}")

    return result


# ══════════════════════════════════════════════════════════
# MUSTER
# ══════════════════════════════════════════════════════════

def fetch_muster(session, emp_id, start, end, timeout=20):
    r = session.get(
        f"{BASE_URL}/attendance/v2/employee/{emp_id}/muster",
        params={"start": start, "end": end}, timeout=timeout,
    )
    return r.json() if r.status_code == 200 else None


# ══════════════════════════════════════════════════════════
# TIME HELPERS
# ══════════════════════════════════════════════════════════

def _clean_time(t):
    if not t:
        return ""
    t = str(t)
    return t[11:16] if len(t) > 10 else t[:5]


def _to_minutes(hhmm):
    if not hhmm:
        return None
    try:
        parts = hhmm.split(":")
        return int(parts[0]) * 60 + (int(parts[1]) if len(parts) > 1 else 0)
    except (ValueError, IndexError):
        return None


# ══════════════════════════════════════════════════════════
# APPROVED HALF-DAY LEAVE CHECK
# ══════════════════════════════════════════════════════════

def _is_approved_half_day_leave(row):
    """
    Returns True ONLY if the employee has an approved half-day leave.

    Approved half-day leave patterns:
      session1 = leave code (CFL, L, SL, CL, etc.)  +  session2 = P
      session1 = P  +  session2 = leave code

    NOT excluded:
      session1=P + session2=A  → came first half, absent second half (still count late)
      session1=A + session2=P  → absent first half, came second half (still count late)
    """
    s1 = (row.get("session1_label") or "").strip().upper()
    s2 = (row.get("session2_label") or "").strip().upper()

    if not s1 or not s2:
        return False

    # P and A are attendance markers, everything else is a leave code
    attendance_codes = {"P", "A", ""}

    # One session is P and the other is a leave code → approved half-day leave
    if s1 == "P" and s2 not in attendance_codes:
        return True
    if s2 == "P" and s1 not in attendance_codes:
        return True

    return False


# ══════════════════════════════════════════════════════════
# PARSE MUSTER RECORD
# ══════════════════════════════════════════════════════════

def parse_record(rec, emp_email=""):
    emp  = rec.get("employee") or {}
    summ = rec.get("summary")  or {}
    att_date = summ.get("attendanceDate") or ""
    try:
        dow = datetime.strptime(att_date, "%Y-%m-%d").strftime("%A")
    except Exception:
        dow = ""
    shift = summ.get("shift") or {}
    leave = summ.get("leave") or {}
    return {
        "employee_id":     emp.get("id") or "",
        "employee_no":     emp.get("employeeNo") or "",
        "employee_name":   emp.get("name") or "",
        "employee_email":  emp_email,
        "date":            att_date,
        "day_of_week":     dow,
        "day_type":        summ.get("dayType") or "",
        "shift_name":      shift.get("name") or "",
        "shift_start":     _clean_time(shift.get("startTime") or ""),
        "shift_end":       _clean_time(shift.get("endTime") or ""),
        "in_time":         _clean_time(summ.get("firstInTime")),
        "out_time":        _clean_time(summ.get("lastOutTime")),
        "total_work_hrs":  summ.get("totalWorkHrs") or "00:00",
        "shortfall_hrs":   summ.get("shortFallHrs") or "00:00",
        "session1_label":  summ.get("session1Label") or "",
        "session2_label":  summ.get("session2Label") or "",
        "session1h_label": summ.get("session1hLabel") or "",
        "session2h_label": summ.get("session2hLabel") or "",
        "on_leave":        bool(summ.get("onLeave") or False),
        "leave_type":      leave.get("leaveTypeName") or leave.get("type") or "",
        "absent_reason":   summ.get("absentReason") or "",
    }


# ══════════════════════════════════════════════════════════
# LATENESS
# ══════════════════════════════════════════════════════════

def is_late(row, grace_minutes=0, fixed_cutoff=None):
    day_type = (row.get("day_type") or "").strip().lower()
    if day_type in ("holiday", "weekoff", "week off", "weekly off"):
        return False, 0
    if row.get("on_leave"):
        return False, 0
    if _is_approved_half_day_leave(row):
        return False, 0

    in_time_min = _to_minutes(row.get("in_time"))
    if in_time_min is None:
        return False, 0

    if fixed_cutoff:
        cutoff_min = _to_minutes(fixed_cutoff)
        if cutoff_min is None:
            return False, 0
        late_by = in_time_min - cutoff_min
    else:
        shift_start_min = _to_minutes(row.get("shift_start"))
        if shift_start_min is None:
            return False, 0
        late_by = in_time_min - (shift_start_min + grace_minutes)

    return (late_by > 0, max(late_by, 0))


# ══════════════════════════════════════════════════════════
# PARALLEL WORKER  — fetches categories + muster per employee
# ══════════════════════════════════════════════════════════

def _process_employee(token, domain, emp, start, end, grace_minutes, fixed_cutoff):
    session   = make_session(token, domain)
    emp_id    = emp.get("employeeId")
    emp_name  = emp.get("name") or ""
    emp_email = emp.get("email") or ""

    if not emp_id:
        return None

    # ── Fetch department from /categories endpoint ──
    cats = fetch_employee_categories(session, emp_id)
    department  = cats["department"]
    designation = cats["designation"]
    location    = cats["location"]

    # ── Fetch attendance (muster) ──
    data = fetch_muster(session, emp_id, start, end)
    if not data:
        return {
            "employee_id": emp_id, "employee_no": emp.get("employeeNo") or "",
            "employee_name": emp_name, "employee_email": emp_email,
            "department": department, "designation": designation, "location": location,
            "late_count": 0, "late_days": [],
        }

    late_days = []
    for rec in (data.get("records") or []):
        row = parse_record(rec, emp_email)
        late, late_by = is_late(row, grace_minutes=grace_minutes, fixed_cutoff=fixed_cutoff)
        if late:
            late_days.append({
                "date":            row["date"],
                "day_of_week":     row["day_of_week"],
                "shift_start":     row["shift_start"],
                "in_time":         row["in_time"],
                "late_by_minutes": late_by,
            })

    return {
        "employee_id":    emp_id,
        "employee_no":    emp.get("employeeNo") or "",
        "employee_name":  emp_name,
        "employee_email": emp_email,
        "department":     department,
        "designation":    designation,
        "location":       location,
        "late_count":     len(late_days),
        "late_days":      late_days,
    }


# ══════════════════════════════════════════════════════════
# MONTH HELPERS
# ══════════════════════════════════════════════════════════

def month_bounds(year, month):
    start = date(year, month, 1)
    nxt   = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return str(start), str(nxt - timedelta(days=1))


# ══════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════

def get_late_comers_for_range(
    username, password, domain,
    start_date, end_date,
    grace_minutes=0,
    fixed_cutoff=None,
    max_workers=10,
    progress_cb=None,
):
    t0      = time.time()
    token   = get_token(username, password, domain)
    session = make_session(token, domain)

    employees = fetch_active_employees(session)

    if not employees:
        return {
            "period": f"{start_date}__{end_date}", "start": start_date, "end": end_date,
            "employees": [], "all_employees_count": 0,
            "departments": [], "designations": [], "locations": [],
            "elapsed": time.time() - t0,
        }

    results, done, total = [], 0, len(employees)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _process_employee, token, domain, emp,
                start_date, end_date, grace_minutes, fixed_cutoff,
            ): emp
            for emp in employees
        }
        for fut in as_completed(futures):
            try:
                res = fut.result()
                if res:
                    results.append(res)
            except Exception as e:
                log.warning(f"Error: {e}")
            done += 1
            if progress_cb:
                progress_cb(done, total)

    late_comers   = sorted([r for r in results if r["late_count"] > 0],
                           key=lambda r: r["late_count"], reverse=True)
    all_depts     = sorted({r["department"]  for r in results if r.get("department")})
    all_desigs    = sorted({r["designation"] for r in results if r.get("designation")})
    all_locations = sorted({r["location"]    for r in results if r.get("location")})

    return {
        "period":              f"{start_date}__{end_date}",
        "start":               start_date,
        "end":                 end_date,
        "employees":           late_comers,
        "all_employees_count": total,
        "departments":         all_depts,
        "designations":        all_desigs,
        "locations":           all_locations,
        "elapsed":             time.time() - t0,
    }


def get_late_comers_for_month(username, password, domain, year, month, **kwargs):
    start, end = month_bounds(year, month)
    return get_late_comers_for_range(username, password, domain, start, end, **kwargs)
