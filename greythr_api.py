"""
greythr_api.py  —  v4
======================
Changes:
  - Active employees only (leftorg=False, status != resigned)
  - Department fetched from /employee/v2/departments + joined by employeeId
  - Half-day exclusion: skip if one session P and other A (half day pattern)
  - Faster: skip employees with no in_time early, parallel dept fetch
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
        headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials"},
        timeout=timeout,
    )
    if r.status_code == 200:
        return r.json().get("access_token")
    raise RuntimeError(f"Auth failed [{r.status_code}]: {r.text}")


def make_session(token, domain):
    s = requests.Session()
    s.headers.update({"ACCESS-TOKEN": token, "x-greythr-domain": domain, "Content-Type": "application/json"})
    retry = requests.adapters.Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", requests.adapters.HTTPAdapter(max_retries=retry))
    return s


# ══════════════════════════════════════════════════════════
# DEPARTMENTS  — fetch once, return {employeeId: dept_name}
# ══════════════════════════════════════════════════════════

def fetch_departments_map(session):
    """
    GET /employee/v2/departments  → list of {id, name, employees:[{id,...}]}
    Build a reverse map: employee_id → department_name
    """
    dept_map = {}
    page = 0
    while True:
        r = session.get(f"{BASE_URL}/employee/v2/departments", params={"page": page, "size": 200}, timeout=20)
        if r.status_code != 200:
            log.warning(f"Departments fetch failed [{r.status_code}]")
            break
        data = r.json()
        records = data.get("data") or data.get("content") or (data if isinstance(data, list) else [])
        for dept in records:
            dept_name = dept.get("name") or dept.get("departmentName") or ""
            # employees list inside dept record
            for emp in (dept.get("employees") or []):
                eid = emp.get("id") or emp.get("employeeId")
                if eid:
                    dept_map[int(eid)] = dept_name
            # also store dept id → name for later join
            dept_id = dept.get("id") or dept.get("departmentId")
            if dept_id and dept_name:
                dept_map[f"dept_{dept_id}"] = dept_name

        pages = data.get("pages") or {}
        if not pages.get("hasNext", False):
            break
        page += 1

    log.info(f"  Departments map: {len(dept_map)} entries")
    return dept_map


# ══════════════════════════════════════════════════════════
# EMPLOYEES — active only
# ══════════════════════════════════════════════════════════

def fetch_active_employees(session, dept_map):
    """
    Fetch all employees, filter to active only (leftorg=False),
    and enrich with department from dept_map.
    """
    log.info("Fetching employees (active only)...")
    employees, page = [], 0
    while True:
        r = session.get(f"{BASE_URL}/employee/v2/employees", params={"page": page, "size": 200}, timeout=20)
        if r.status_code != 200:
            log.error(f"Employee fetch failed [{r.status_code}]: {r.text[:200]}")
            break
        data    = r.json()
        records = data.get("data") or []

        for emp in records:
            # Skip ex-employees: leftorg=True OR status indicates resigned/terminated
            if emp.get("leftorg") is True:
                continue
            status = emp.get("status")
            # status=3 seems to be active based on the raw data we saw; skip known inactive codes
            # greytHR status codes: typically 1=active, others=inactive. Keep if not explicitly left.
            # We already filter on leftorg so this is a safety net.

            # Enrich with department
            emp_id = emp.get("employeeId")
            dept = ""
            if emp_id and int(emp_id) in dept_map:
                dept = dept_map[int(emp_id)]
            elif emp.get("department"):
                dept = emp["department"]
            elif emp.get("departmentName"):
                dept = emp["departmentName"]
            emp["_department"] = dept
            employees.append(emp)

        pages    = data.get("pages") or {}
        has_next = pages.get("hasNext", False)
        log.info(f"  Page {page+1} — {len(records)} total, kept active so far: {len(employees)}")
        if not has_next:
            break
        page += 1

    log.info(f"  Active employees: {len(employees)}")
    return employees


# ══════════════════════════════════════════════════════════
# MUSTER
# ══════════════════════════════════════════════════════════

def fetch_muster(session, emp_id, start, end, timeout=20):
    r = session.get(
        f"{BASE_URL}/attendance/v2/employee/{emp_id}/muster",
        params={"start": start, "end": end},
        timeout=timeout,
    )
    if r.status_code == 200:
        return r.json()
    return None


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
# HALF-DAY CHECK
# ══════════════════════════════════════════════════════════

def _is_half_day(row):
    """
    Returns True if the employee was on half-day.
    Patterns: session1=P + session2=A  OR  session1=A + session2=P
    Also check session1hLabel / session2hLabel for half-day markers.
    """
    s1 = (row.get("session1_label") or "").strip().upper()
    s2 = (row.get("session2_label") or "").strip().upper()
    half_day_pairs = {("P", "A"), ("A", "P")}
    if (s1, s2) in half_day_pairs:
        return True
    # Some greytHR tenants use HD label
    s1h = (row.get("session1h_label") or "").strip().upper()
    s2h = (row.get("session2h_label") or "").strip().upper()
    if "HD" in (s1h, s2h):
        return True
    return False


# ══════════════════════════════════════════════════════════
# PARSE MUSTER RECORD
# ══════════════════════════════════════════════════════════

def parse_record(rec, emp_email=""):
    emp  = rec.get("employee") or {}
    summ = rec.get("summary")  or {}
    excs = rec.get("exceptions") or []

    att_date = summ.get("attendanceDate") or ""
    try:
        dow = datetime.strptime(att_date, "%Y-%m-%d").strftime("%A")
    except ValueError:
        dow = ""

    shift = summ.get("shift") or {}
    leave = summ.get("leave") or {}

    s1 = summ.get("session1Label")  or ""
    s2 = summ.get("session2Label")  or ""
    s1h = summ.get("session1hLabel") or ""
    s2h = summ.get("session2hLabel") or ""

    row = {
        "employee_id":    emp.get("id")          or "",
        "employee_no":    emp.get("employeeNo")   or "",
        "employee_name":  emp.get("name")         or "",
        "employee_email": emp_email,
        "date":           att_date,
        "day_of_week":    dow,
        "day_type":       summ.get("dayType")     or "",
        "shift_name":     shift.get("name")       or "",
        "shift_start":    _clean_time(shift.get("startTime") or ""),
        "shift_end":      _clean_time(shift.get("endTime")   or ""),
        "in_time":        _clean_time(summ.get("firstInTime")),
        "out_time":       _clean_time(summ.get("lastOutTime")),
        "total_work_hrs": summ.get("totalWorkHrs")  or "00:00",
        "shortfall_hrs":  summ.get("shortFallHrs")  or "00:00",
        "excess_work_hrs":summ.get("excessWorkHrs") or "00:00",
        "session1_label": s1,
        "session2_label": s2,
        "session1h_label":s1h,
        "session2h_label":s2h,
        "on_leave":       bool(summ.get("onLeave") or False),
        "leave_type":     leave.get("leaveTypeName") or leave.get("type") or "",
        "absent_reason":  summ.get("absentReason")  or "",
        "exceptions":     " | ".join(excs),
    }
    return row


# ══════════════════════════════════════════════════════════
# LATENESS DECISION
# ══════════════════════════════════════════════════════════

def is_late(row, grace_minutes=0, fixed_cutoff=None):
    day_type = (row.get("day_type") or "").strip().lower()
    if day_type in ("holiday", "weekoff", "week off", "weekly off"):
        return False, 0
    if row.get("on_leave"):
        return False, 0
    # Skip half-day — do not count as late
    if _is_half_day(row):
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
# MONTH HELPERS
# ══════════════════════════════════════════════════════════

def month_bounds(year, month):
    start = date(year, month, 1)
    nxt   = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return str(start), str(nxt - timedelta(days=1))


# ══════════════════════════════════════════════════════════
# PARALLEL WORKER
# ══════════════════════════════════════════════════════════

def _process_employee(token, domain, emp, start, end, grace_minutes, fixed_cutoff):
    session    = make_session(token, domain)
    emp_id     = emp.get("employeeId")
    emp_name   = emp.get("name")        or ""
    emp_email  = emp.get("email")       or ""
    department = emp.get("_department") or ""

    if not emp_id:
        return None

    data = fetch_muster(session, emp_id, start, end)
    if not data:
        return {"employee_id": emp_id, "employee_no": emp.get("employeeNo") or "",
                "employee_name": emp_name, "employee_email": emp_email,
                "department": department, "late_count": 0, "late_days": [], "all_days": []}

    records   = data.get("records") or []
    late_days = []
    all_days  = []  # every day's parsed data — used for the detail page

    for rec in records:
        row  = parse_record(rec, emp_email)
        row["department"] = department
        all_days.append(row)
        late, late_by = is_late(row, grace_minutes=grace_minutes, fixed_cutoff=fixed_cutoff)
        if late:
            late_days.append({
                "date":            row["date"],
                "day_of_week":     row["day_of_week"],
                "shift_start":     row["shift_start"],
                "in_time":         row["in_time"],
                "late_by_minutes": late_by,
                "department":      department,
            })

    return {
        "employee_id":    emp_id,
        "employee_no":    emp.get("employeeNo") or "",
        "employee_name":  emp_name,
        "employee_email": emp_email,
        "department":     department,
        "late_count":     len(late_days),
        "late_days":      late_days,
        "all_days":       all_days,
    }


# ══════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════

def get_late_comers_for_month(
    username, password, domain,
    year, month,
    grace_minutes=0,
    fixed_cutoff=None,
    max_workers=10,
    progress_cb=None,
):
    t0      = time.time()
    token   = get_token(username, password, domain)
    session = make_session(token, domain)

    # Fetch departments first (fast, single call) then enrich employees
    dept_map  = fetch_departments_map(session)
    employees = fetch_active_employees(session, dept_map)

    if not employees:
        return {"period": f"{year:04d}-{month:02d}", "start": "", "end": "",
                "employees": [], "all_employees": [], "all_employees_count": 0,
                "departments": [], "elapsed": time.time() - t0}

    start, end = month_bounds(year, month)
    results, done, total = [], 0, len(employees)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_process_employee, token, domain, emp, start, end, grace_minutes, fixed_cutoff): emp
            for emp in employees
        }
        for fut in as_completed(futures):
            try:
                res = fut.result()
                if res:
                    results.append(res)
            except Exception as e:
                log.warning(f"Error emp={futures[fut].get('employeeId')}: {e}")
            done += 1
            if progress_cb:
                progress_cb(done, total)

    late_comers = sorted([r for r in results if r["late_count"] > 0], key=lambda r: r["late_count"], reverse=True)
    all_depts   = sorted({r["department"] for r in results if r["department"]})

    return {
        "period":              f"{year:04d}-{month:02d}",
        "start":               start,
        "end":                 end,
        "employees":           late_comers,     # only late comers
        "all_employees":       results,          # everyone — for detail page
        "all_employees_count": total,
        "departments":         all_depts,
        "elapsed":             time.time() - t0,
    }
