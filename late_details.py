"""
pages/late_details.py  —  Late Time Details Page
=================================================
Shows every employee's day-by-day attendance with filters:
  - Employee selector
  - Date range picker
  - Department filter
  - Status filter (late only / all days / absent)
"""

import datetime as dt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Late Details", page_icon="📋", layout="wide")

st.title("📋 Late Time Details")
st.caption("Day-by-day attendance breakdown per employee. Use filters to drill down.")

# ── Guard: must have fetched data first ───────────────────
result = st.session_state.get("result")
if result is None:
    st.warning("No data loaded yet. Go back to the main page and click **Fetch late-comers** first.")
    st.page_link("app.py", label="← Back to Dashboard")
    st.stop()

all_employees_data = result.get("all_employees") or []
if not all_employees_data:
    st.warning("No employee data available.")
    st.page_link("app.py", label="← Back to Dashboard")
    st.stop()

# ── Build flat dataframe of ALL days for ALL employees ────
@st.cache_data(show_spinner=False)
def build_all_days_df(result_period):
    # We pass result_period as cache key so cache busts when month changes
    rows = []
    for emp in all_employees_data:
        for day in emp.get("all_days") or []:
            rows.append({
                "employee_id":    emp["employee_id"],
                "employee_no":    emp["employee_no"],
                "employee_name":  emp["employee_name"],
                "department":     emp.get("department") or "",
                "date":           day.get("date") or "",
                "day_of_week":    day.get("day_of_week") or "",
                "day_type":       day.get("day_type") or "",
                "shift_name":     day.get("shift_name") or "",
                "shift_start":    day.get("shift_start") or "",
                "in_time":        day.get("in_time") or "",
                "out_time":       day.get("out_time") or "",
                "total_work_hrs": day.get("total_work_hrs") or "",
                "shortfall_hrs":  day.get("shortfall_hrs") or "",
                "session1_label": day.get("session1_label") or "",
                "session2_label": day.get("session2_label") or "",
                "on_leave":       day.get("on_leave") or False,
                "leave_type":     day.get("leave_type") or "",
                "absent_reason":  day.get("absent_reason") or "",
                "exceptions":     day.get("exceptions") or "",
            })
    df = pd.DataFrame(rows)
    if not df.empty and "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df

df_all = build_all_days_df(result.get("period",""))

if df_all.empty:
    st.warning("No day-level data available. The API may not have returned all_days.")
    st.page_link("app.py", label="← Back to Dashboard")
    st.stop()

# ── SIDEBAR FILTERS ───────────────────────────────────────
st.sidebar.title("🔍 Filters")
st.sidebar.page_link("app.py", label="← Back to Dashboard")
st.sidebar.divider()

# Department filter
all_depts = sorted(df_all["department"].dropna().unique().tolist())
all_depts = [d for d in all_depts if d]
sel_depts = st.sidebar.multiselect("Department", options=all_depts, default=[], placeholder="All")

# Employee filter (after dept filter applied)
if sel_depts:
    emp_pool = df_all[df_all["department"].isin(sel_depts)]
else:
    emp_pool = df_all

emp_names = sorted(emp_pool["employee_name"].dropna().unique().tolist())
sel_emps  = st.sidebar.multiselect("Employee", options=emp_names, default=[], placeholder="All employees")

# Date range filter
min_date = df_all["date"].min().date() if not df_all["date"].isna().all() else dt.date(2020,1,1)
max_date = df_all["date"].max().date() if not df_all["date"].isna().all() else dt.date.today()

date_from, date_to = st.sidebar.date_input(
    "Date range",
    value=(min_date, max_date),
    min_value=min_date,
    max_value=max_date,
)

# Status filter
status_opts = ["All days", "Late only", "Absent", "On Leave", "Present"]
sel_status  = st.sidebar.selectbox("Show", options=status_opts, index=1)

st.sidebar.divider()
st.sidebar.caption(f"Period: **{result.get('period','')}**  |  "
                   f"Total active employees: **{result.get('all_employees_count',0)}**")


# ── APPLY FILTERS ─────────────────────────────────────────
df = df_all.copy()

# Dept
if sel_depts:
    df = df[df["department"].isin(sel_depts)]

# Employee
if sel_emps:
    df = df[df["employee_name"].isin(sel_emps)]

# Date range
df = df[(df["date"] >= pd.Timestamp(date_from)) & (df["date"] <= pd.Timestamp(date_to))]

# Status
if sel_status == "Late only":
    # Late = has in_time, day_type is regular, not on leave, not absent
    df = df[
        df["in_time"].str.len() > 0
    ]
    # We need to recompute late flag — import api for this
    try:
        import greythr_api as api
        fixed_cutoff  = st.session_state.get("_fixed_cutoff")
        grace_minutes = 0
        mask = df.apply(
            lambda row: api.is_late(row.to_dict(), grace_minutes=grace_minutes,
                                     fixed_cutoff=fixed_cutoff)[0],
            axis=1
        )
        df = df[mask]
    except Exception:
        pass  # fallback: show all with in_time

elif sel_status == "Absent":
    df = df[df["absent_reason"].str.len() > 0]
elif sel_status == "On Leave":
    df = df[df["on_leave"] == True]
elif sel_status == "Present":
    df = df[df["in_time"].str.len() > 0]


# ── METRICS ROW ───────────────────────────────────────────
m1, m2, m3, m4 = st.columns(4)
m1.metric("Rows shown",        len(df))
m2.metric("Unique employees",  df["employee_name"].nunique())
m3.metric("Days with in-time", int((df["in_time"].str.len() > 0).sum()))
m4.metric("Days absent",       int((df["absent_reason"].str.len() > 0).sum()))


# ── LATE-BY MINUTES column ────────────────────────────────
# Compute late_by for display
try:
    import greythr_api as api
    _fc = st.session_state.get("_fixed_cutoff")
    def _late_by(row):
        _, late_by = api.is_late(row.to_dict(), fixed_cutoff=_fc)
        return late_by if late_by > 0 else ""
    df = df.copy()
    df["late_by_min"] = df.apply(_late_by, axis=1)
except Exception:
    df["late_by_min"] = ""


# ── TABLE ─────────────────────────────────────────────────
st.subheader("📅 Day-by-day attendance")

display_cols = [
    "employee_no", "employee_name", "department",
    "date", "day_of_week", "day_type",
    "shift_start", "in_time", "out_time",
    "late_by_min", "total_work_hrs", "shortfall_hrs",
    "session1_label", "session2_label",
    "on_leave", "leave_type", "absent_reason",
    "exceptions",
]
display_cols = [c for c in display_cols if c in df.columns]

st.dataframe(
    df[display_cols].sort_values(["employee_name","date"]).reset_index(drop=True),
    hide_index=True,
    use_container_width=True,
    column_config={
        "date":           st.column_config.DateColumn("Date", format="DD MMM YYYY"),
        "late_by_min":    st.column_config.NumberColumn("Late By (min)", format="%d min"),
        "on_leave":       st.column_config.CheckboxColumn("On Leave", disabled=True),
        "employee_no":    st.column_config.TextColumn("Emp No"),
        "employee_name":  st.column_config.TextColumn("Name"),
        "department":     st.column_config.TextColumn("Department"),
        "day_of_week":    st.column_config.TextColumn("Day"),
        "day_type":       st.column_config.TextColumn("Day Type"),
        "shift_start":    st.column_config.TextColumn("Shift Start"),
        "in_time":        st.column_config.TextColumn("In Time"),
        "out_time":       st.column_config.TextColumn("Out Time"),
        "total_work_hrs": st.column_config.TextColumn("Work Hrs"),
        "shortfall_hrs":  st.column_config.TextColumn("Shortfall"),
        "session1_label": st.column_config.TextColumn("S1"),
        "session2_label": st.column_config.TextColumn("S2"),
        "leave_type":     st.column_config.TextColumn("Leave Type"),
        "absent_reason":  st.column_config.TextColumn("Absent Reason"),
        "exceptions":     st.column_config.TextColumn("Exceptions"),
    }
)

# ── PER-EMPLOYEE SUMMARY ──────────────────────────────────
if sel_emps and len(sel_emps) == 1:
    emp_name = sel_emps[0]
    st.divider()
    st.subheader(f"📊 Summary — {emp_name}")
    emp_df = df[df["employee_name"] == emp_name].copy()

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Total days", len(emp_df))
    s2.metric("Days present", int((emp_df["in_time"].str.len() > 0).sum()))
    s3.metric("Days absent",  int((emp_df["absent_reason"].str.len() > 0).sum()))
    s4.metric("Late days",    int((emp_df["late_by_min"] > 0).sum()) if emp_df["late_by_min"].dtype != object else 0)

    # Late by timeline chart
    late_rows = emp_df[emp_df["late_by_min"] != ""].copy()
    if not late_rows.empty:
        st.markdown("**Late-by minutes timeline:**")
        chart_df = late_rows[["date","late_by_min"]].set_index("date")
        st.bar_chart(chart_df, color="#c92a2a")


# ── DOWNLOAD ──────────────────────────────────────────────
st.download_button(
    "⬇️ Download this view as CSV",
    df[display_cols].sort_values(["employee_name","date"]).to_csv(index=False).encode(),
    file_name=f"late_details_{result.get('period','')}.csv",
    mime="text/csv",
)
