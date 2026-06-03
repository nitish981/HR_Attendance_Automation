"""
app.py  —  HR Late-Comer Warning Tool  (FINAL)
===============================================
- Google OAuth login gate
- Department auto-fetched from /employee/v2/employees/{id}/categories
- Date range picker
- Department + Designation + Location filters
- Fixed cutoff time slider
- Inline late details
- No manual CSV needed — fully automated
"""

import calendar
import datetime as dt

import pandas as pd
import streamlit as st

import greythr_api as api
import emailer

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build as gbuild

# ──────────────────────────────────────────────────────────
# PAGE CONFIG
# ──────────────────────────────────────────────────────────

st.set_page_config(page_title="HR Late-Comer Warnings", page_icon="⏰", layout="wide")

st.markdown("""
<style>
  .tier-pill {
    display:inline-block; padding:2px 10px; border-radius:12px;
    color:#fff; font-size:12px; font-weight:600; margin-left:6px;
  }
  .user-bar {
    position:fixed; top:0; right:0; z-index:9999;
    background:#1e293b; color:#e2e8f0;
    padding:6px 18px; font-size:13px; border-radius:0 0 0 8px;
  }
  .login-card {
    max-width:420px; margin:80px auto; padding:40px 36px;
    background:#fff; border-radius:16px;
    box-shadow:0 4px 32px rgba(0,0,0,0.10); text-align:center;
  }
  .login-title { font-size:26px; font-weight:700; color:#1e293b; margin-bottom:6px; }
  .login-sub   { color:#64748b; font-size:14px; margin-bottom:28px; }
  .info-box {
    background:#f0f4ff; border-left:4px solid #4361ee;
    padding:10px 14px; border-radius:4px; margin:8px 0; font-size:13px;
  }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────
# SECRETS
# ──────────────────────────────────────────────────────────

def _secret(key, fallback=""):
    try:    return st.secrets[key]
    except: return fallback

GT_USERNAME   = _secret("GT_USERNAME")
GT_PASSWORD   = _secret("GT_PASSWORD")
GT_DOMAIN     = _secret("GT_DOMAIN")
CLIENT_ID     = _secret("GOOGLE_CLIENT_ID")
CLIENT_SECRET = _secret("GOOGLE_CLIENT_SECRET")
REDIRECT_URI  = _secret("REDIRECT_URI")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
]


# ──────────────────────────────────────────────────────────
# SESSION STATE
# ──────────────────────────────────────────────────────────

DEFAULTS = {
    "gmail_service": None, "hr_email": None, "hr_name": None,
    "result": None, "send_log": [],
    "subjects": dict(emailer.SUBJECTS), "bodies": dict(emailer.BODIES),
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ──────────────────────────────────────────────────────────
# OAUTH
# ──────────────────────────────────────────────────────────

def _make_flow():
    return Flow.from_client_config(
        {"web": {
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "redirect_uris": [REDIRECT_URI],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }},
        scopes=SCOPES, redirect_uri=REDIRECT_URI,
        autogenerate_code_verifier=False,
    )

def _get_user_info(creds):
    svc = gbuild("oauth2", "v2", credentials=creds)
    info = svc.userinfo().get().execute()
    return info.get("email", ""), info.get("name", "HR User")

params = st.query_params.to_dict()

if "error" in params:
    st.error(f"**Google sign-in error:** `{params.get('error')}`")
    st.warning(params.get("error_description", ""))
    if st.button("Clear and retry"): st.query_params.clear(); st.rerun()
    st.stop()

if "code" in params:
    with st.spinner("Completing sign-in..."):
        try:
            import urllib.parse
            flow = _make_flow()
            flow.fetch_token(authorization_response=REDIRECT_URI + "?" + urllib.parse.urlencode(params))
            creds = flow.credentials
            email, name = _get_user_info(creds)
            st.session_state.gmail_service = gbuild("gmail", "v1", credentials=creds)
            st.session_state.hr_email = email
            st.session_state.hr_name  = name
            st.query_params.clear(); st.rerun()
        except Exception as e:
            st.error(f"**Token exchange failed:** {e}")
            if st.button("Clear and retry"): st.query_params.clear(); st.rerun()
            st.stop()

# ── Login gate ──
if not st.session_state.gmail_service:
    with st.expander("🔍 Diagnostics"):
        st.json({
            "GT_DOMAIN": GT_DOMAIN or "❌ MISSING",
            "GOOGLE_CLIENT_ID": (CLIENT_ID[:12] + "...") if CLIENT_ID else "❌ MISSING",
            "GOOGLE_CLIENT_SECRET": "✅ set" if CLIENT_SECRET else "❌ MISSING",
            "REDIRECT_URI": REDIRECT_URI or "❌ MISSING",
        })
    _, mid, _ = st.columns([1, 1.4, 1])
    with mid:
        st.markdown("<div class='login-card'><div class='login-title'>⏰ HR Warning Tool</div>"
                    "<div class='login-sub'>Sign in with Google to access the dashboard.</div></div>",
                    unsafe_allow_html=True)
        if not CLIENT_ID or not CLIENT_SECRET:
            st.error("OAuth credentials missing."); st.stop()
        try:
            flow = _make_flow()
            auth_url, _ = flow.authorization_url(prompt="select_account", access_type="offline",
                                                  include_granted_scopes="true")
        except Exception as e:
            st.error(str(e)); st.stop()
        with st.expander("🔗 Auth URL (debug)"):
            st.code(auth_url, language=None)
        st.link_button("🔑  Sign in with Google", url=auth_url, type="primary")
    st.stop()


# ──────────────────────────────────────────────────────────
# SIGNED IN
# ──────────────────────────────────────────────────────────

hr_email = st.session_state.hr_email
hr_name  = st.session_state.hr_name

st.markdown(f"<div class='user-bar'>👤 {hr_name} &nbsp;|&nbsp; {hr_email}</div>", unsafe_allow_html=True)
st.sidebar.markdown(f"**Signed in as:**\n\n{hr_name}\n\n`{hr_email}`")
if st.sidebar.button("🚪 Sign out"):
    for k in DEFAULTS: st.session_state[k] = DEFAULTS[k]
    st.rerun()
st.sidebar.divider()


# ──────────────────────────────────────────────────────────
# SIDEBAR — SETUP
# ──────────────────────────────────────────────────────────

st.sidebar.title("⚙️ Setup")

with st.sidebar.expander("1. greytHR", expanded=True):
    if GT_USERNAME and GT_DOMAIN:
        st.success(f"Connected: `{GT_DOMAIN}`")
    else:
        st.error("greytHR secrets missing.")

with st.sidebar.expander("2. Period & late rule", expanded=True):
    today = dt.date.today()
    col_y, col_m = st.columns(2)
    year  = col_y.number_input("Year", min_value=2020, max_value=2100, value=today.year, step=1)
    month = col_m.selectbox("Month", list(range(1, 13)), index=today.month - 1,
                            format_func=lambda m: calendar.month_name[m])

    month_start = dt.date(int(year), int(month), 1)
    month_end = (dt.date(int(year), int(month) + 1, 1) - dt.timedelta(days=1)
                 if int(month) < 12 else dt.date(int(year), 12, 31))
    default_end = min(month_end, today)

    st.markdown("**Date range:**")
    date_range = st.date_input("From → To", value=(month_start, default_end),
                               min_value=month_start, max_value=month_end,
                               label_visibility="collapsed")
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        date_from, date_to = date_range
    else:
        date_from = date_range if not isinstance(date_range, (list, tuple)) else date_range[0]
        date_to = default_end
    st.caption(f"📅 {date_from.strftime('%d %b')} → {date_to.strftime('%d %b %Y')}")

    st.markdown("---")
    st.markdown("**Late-arrival rule:**")
    use_fixed = st.toggle("Fixed cutoff for everyone", value=True)
    if use_fixed:
        cutoff_time = st.slider("Late if clock-in after", min_value=dt.time(6, 0),
                                max_value=dt.time(14, 0), value=dt.time(10, 0),
                                step=dt.timedelta(minutes=15), format="HH:mm")
        fixed_cutoff  = cutoff_time.strftime("%H:%M")
        grace_minutes = 0
        st.caption(f"⏰ Cutoff: **{fixed_cutoff}**")
    else:
        fixed_cutoff  = None
        grace_minutes = st.slider("Grace period (minutes)", 0, 60, 10)

    workers = st.slider("Parallel threads", 1, 20, 10)

    st.markdown("---")
    st.markdown("**Minimum daily work hours:**")
    req_hours = st.slider("Required hours", min_value=dt.time(4, 0),
                          max_value=dt.time(12, 0), value=dt.time(8, 30),
                          step=dt.timedelta(minutes=15), format="HH:mm")
    required_minutes = req_hours.hour * 60 + req_hours.minute
    st.caption(f"📐 Under-hours if worked < **{req_hours.strftime('%H:%M')}**")

fetch_clicked = st.sidebar.button("🔄 Fetch late-comers", type="primary",
                                  use_container_width=True,
                                  disabled=not (GT_USERNAME and GT_PASSWORD and GT_DOMAIN))


# ──────────────────────────────────────────────────────────
# FETCH
# ──────────────────────────────────────────────────────────

if fetch_clicked:
    prog = st.sidebar.progress(0.0, text="Starting...")
    def _cb(done, total):
        prog.progress(done / total, text=f"Fetched {done}/{total} employees")
    try:
        with st.spinner("Fetching attendance + department data from greytHR..."):
            result = api.get_late_comers_for_range(
                username=GT_USERNAME, password=GT_PASSWORD, domain=GT_DOMAIN,
                start_date=str(date_from), end_date=str(date_to),
                grace_minutes=int(grace_minutes), fixed_cutoff=fixed_cutoff,
                required_minutes=int(required_minutes),
                max_workers=int(workers), progress_cb=_cb,
            )
        st.session_state.result   = result
        st.session_state.send_log = []
        prog.progress(1.0, text="Done ✅")
        st.sidebar.success(
            f"**{len(result['employees'])}** late-comer(s) / "
            f"{result['all_employees_count']} employees / "
            f"{result['elapsed']:.1f}s"
        )
    except Exception as e:
        prog.empty(); st.sidebar.error(f"Fetch failed: {e}")



# ──────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────

st.title("⏰ HR Attendance Dashboard")

result = st.session_state.result
if result is None:
    st.info("👈 Pick a month and date range, then click **Fetch late-comers**.")
    st.stop()

month_label     = f"{result['start']} to {result['end']}"
all_late        = result["employees"]
all_under_hours = result.get("under_hours_employees") or []

tab_late, tab_hours = st.tabs([
    f"⏰ Late-Comers ({len(all_late)})",
    f"📉 Under-Hours ({len(all_under_hours)})",
])


# ══════════════════════════════════════════════════════════
# TAB 1: LATE-COMERS
# ══════════════════════════════════════════════════════════

with tab_late:
    if not all_late:
        st.info("No late-comers found — everyone was on time!")
    else:
        def _make_late_df(emp_list):
            rows = []
            for e in emp_list:
                tier = emailer.tier_for(e["late_count"])
                rows.append({
                    "Employee ID": e["employee_id"], "Emp No": e["employee_no"],
                    "Name": e["employee_name"], "Email": e["employee_email"],
                    "Department": e.get("department") or "",
                    "Designation": e.get("designation") or "",
                    "Late Days": e["late_count"],
                    "Tier": emailer.TIER_META[tier]["label"], "_tier": tier,
                    "_idx": all_late.index(e),
                })
            return pd.DataFrame(rows)

        df_all = _make_late_df(all_late)

        # Filters
        st.subheader("🔍 Filters")
        fc1, fc2, fc3 = st.columns(3)
        sel_depts = fc1.multiselect("Department", result.get("departments") or [], [], placeholder="All", key="lt_d")
        sel_desig = fc2.multiselect("Designation", result.get("designations") or [], [], placeholder="All", key="lt_g")
        exclude   = fc3.checkbox("Exclude selected", key="lt_x")

        df = df_all.copy()
        for col, sel in [("Department", sel_depts), ("Designation", sel_desig)]:
            if sel:
                mask = ~df[col].isin(sel) if exclude else df[col].isin(sel)
                df = df[mask]
        df = df.reset_index(drop=True)
        filtered_employees = [all_late[i] for i in df["_idx"].tolist()]

        if df.empty:
            st.warning("No late-comers match filters.")
        else:
            # Insights
            st.divider()
            st.subheader("📊 Insights")
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Late-comers", len(df))
            c2.metric("Total employees", result["all_employees_count"])
            c3.metric("🟢 Normal (1)", int((df["_tier"] == "normal").sum()))
            c4.metric("🟡 Moderate (2)", int((df["_tier"] == "moderate").sum()))
            c5.metric("🔴 Strict (3+)", int((df["_tier"] == "strict").sum()))

            ch1, ch2, ch3 = st.columns(3)
            with ch1:
                st.markdown("**By tier**")
                st.bar_chart(df["Tier"].value_counts()
                             .reindex(["Normal Warning","Moderate Warning","Strict Warning"])
                             .fillna(0).astype(int), color="#4361ee")
            with ch2:
                st.markdown("**By department**")
                dc = df["Department"].value_counts().head(10)
                if not dc.empty and not (len(dc)==1 and dc.index[0]==""):
                    st.bar_chart(dc, color="#7209b7")
            with ch3:
                st.markdown("**Top late-comers**")
                st.dataframe(df.sort_values("Late Days", ascending=False).head(8)
                             [["Name","Department","Late Days","Tier"]], hide_index=True, use_container_width=True)

            # Late details
            st.divider()
            with st.expander("📋 Detailed late-time breakdown"):
                detail_rows = []
                for e in filtered_employees:
                    for d in e["late_days"]:
                        detail_rows.append({
                            "Emp No": e["employee_no"], "Name": e["employee_name"],
                            "Department": e.get("department",""),
                            "Date": d["date"], "Day": d["day_of_week"],
                            "Shift Start": d["shift_start"], "In Time": d["in_time"],
                            "Late By (min)": d["late_by_minutes"],
                        })
                if detail_rows:
                    ddf = pd.DataFrame(detail_rows)
                    dc1, dc2 = st.columns(2)
                    emp_f = dc1.multiselect("Employee", sorted(ddf["Name"].unique()), [], placeholder="All", key="det_e")
                    dept_f = dc2.multiselect("Dept", sorted(d for d in ddf["Department"].unique() if d), [], placeholder="All", key="det_d")
                    if emp_f:  ddf = ddf[ddf["Name"].isin(emp_f)]
                    if dept_f: ddf = ddf[ddf["Department"].isin(dept_f)]
                    st.dataframe(ddf.sort_values(["Name","Date"]), hide_index=True, use_container_width=True)
                    st.download_button("⬇️ Download", ddf.to_csv(index=False).encode(),
                                       file_name=f"late_details_{result['period']}.csv", mime="text/csv")

            # Selection + Email
            st.divider()
            st.subheader("✅ Select who receives a warning email")
            sel_df = df.copy(); sel_df.insert(0, "Send", False)
            sel_df["Has Email"] = sel_df["Email"].str.len() > 0

            qb1, qb2, qb3, qb4, qb5 = st.columns(5)
            if qb1.button("Select all"):  st.session_state._pre = "all"
            if qb2.button("🟢 Normal"):   st.session_state._pre = "normal"
            if qb3.button("🟡 Moderate"): st.session_state._pre = "moderate"
            if qb4.button("🔴 Strict"):   st.session_state._pre = "strict"
            if qb5.button("Clear"):       st.session_state._pre = "none"

            pre = st.session_state.get("_pre")
            if pre == "all":    sel_df["Send"] = sel_df["Has Email"]
            elif pre in ("normal","moderate","strict"):
                sel_df["Send"] = (sel_df["_tier"] == pre) & sel_df["Has Email"]
            elif pre == "none": sel_df["Send"] = False

            edited = st.data_editor(
                sel_df[["Send","Emp No","Name","Email","Department","Late Days","Tier","Has Email"]],
                hide_index=True, use_container_width=True,
                column_config={"Send": st.column_config.CheckboxColumn("Send"),
                               "Has Email": st.column_config.CheckboxColumn("Has Email", disabled=True)},
                disabled=["Emp No","Name","Email","Department","Late Days","Tier","Has Email"],
                key="sel_editor")
            sel_idx = edited.index[edited["Send"] & edited["Has Email"]].tolist()
            selected_employees = [all_late[df.iloc[i]["_idx"]] for i in sel_idx]
            st.info(f"**{len(selected_employees)}** selected.")

            # Templates
            company = "Growify"; signatory = hr_name; include_table = True
            with st.expander("✏️ Edit email templates"):
                company   = st.text_input("Company name", value="Growify")
                signatory = st.text_input("HR signatory", value=hr_name)
                include_table = st.checkbox("Include late-days table", value=True)
                for tk in ("normal","moderate","strict"):
                    m = emailer.TIER_META[tk]
                    st.markdown(f"<span class='tier-pill' style='background:{m['color']};'>{m['label']}</span>", unsafe_allow_html=True)
                    st.session_state.subjects[tk] = st.text_input(f"Subject ({tk})", st.session_state.subjects[tk], key=f"s_{tk}")
                    st.session_state.bodies[tk]   = st.text_area(f"Body ({tk})", st.session_state.bodies[tk], height=170, key=f"b_{tk}")
                    st.markdown("---")

            # Preview
            if selected_employees:
                with st.expander("👀 Preview"):
                    names = [e["employee_name"] for e in selected_employees]
                    pick = st.selectbox("Preview for", range(len(names)), format_func=lambda i: names[i])
                    emp = selected_employees[pick]
                    tier, subject, html = emailer.build_email(
                        emp, month_label, company=company, hr_name=signatory,
                        subjects=st.session_state.subjects, bodies=st.session_state.bodies, include_table=include_table)
                    meta = emailer.TIER_META[tier]
                    st.markdown(f"**From:** {hr_email} → **To:** {emp['employee_email']}  |  "
                                f"<span class='tier-pill' style='background:{meta['color']};'>{meta['label']}</span>", unsafe_allow_html=True)
                    st.markdown(f"**Subject:** {subject}")
                    st.components.v1.html(html, height=380, scrolling=True)

            # Send
            st.divider()
            st.subheader("🚀 Send warning emails")
            st.markdown(f"<div class='info-box'>Emails from <b>{hr_email}</b> via Gmail API.</div>", unsafe_allow_html=True)
            dry_run = st.checkbox("Dry run", value=True)
            confirm = st.checkbox(f"I reviewed the {len(selected_employees)} selected.")
            if st.button("Send", type="primary", disabled=(not selected_employees or not confirm)):
                send_log = []; prog = st.progress(0.0)
                for i, emp in enumerate(selected_employees, 1):
                    tier, subject, html = emailer.build_email(
                        emp, month_label, company=company, hr_name=signatory,
                        subjects=st.session_state.subjects, bodies=st.session_state.bodies, include_table=include_table)
                    row = {"Name": emp["employee_name"], "Email": emp["employee_email"],
                           "Department": emp.get("department",""), "Tier": emailer.TIER_META[tier]["label"]}
                    try:
                        if dry_run: row["Status"] = "DRY RUN"
                        else:
                            emailer.send_via_gmail_api(st.session_state.gmail_service,
                                to_email=emp["employee_email"], subject=subject, html_body=html, from_name=signatory)
                            row["Status"] = f"✅ Sent"
                    except Exception as e: row["Status"] = f"❌ {e}"
                    send_log.append(row); prog.progress(i / len(selected_employees))
                st.session_state.send_log = send_log

            if st.session_state.send_log:
                log_df = pd.DataFrame(st.session_state.send_log)
                st.dataframe(log_df, hide_index=True, use_container_width=True)
                st.download_button("⬇️ Log", log_df.to_csv(index=False).encode(),
                                   file_name=f"send_log_{result['period']}.csv", mime="text/csv")


# ══════════════════════════════════════════════════════════
# TAB 2: UNDER-HOURS
# ══════════════════════════════════════════════════════════

with tab_hours:
    if not all_under_hours:
        st.info("Everyone completed their required hours!")
    else:
        st.subheader("📉 Employees who didn't complete required daily hours")
        st.caption(f"Threshold: **{req_hours.strftime('%H:%M')}** per day. "
                   "Holidays and approved leaves are excluded.")

        # Build dataframe
        uh_rows = []
        for e in all_under_hours:
            uh_rows.append({
                "Emp No": e["employee_no"], "Name": e["employee_name"],
                "Email": e["employee_email"],
                "Department": e.get("department") or "",
                "Designation": e.get("designation") or "",
                "Short Days": e["under_hours_count"],
                "_idx": all_under_hours.index(e),
            })
        uh_df_all = pd.DataFrame(uh_rows)

        # Filters
        uf1, uf2, uf3 = st.columns(3)
        uh_depts = uf1.multiselect("Department", result.get("departments") or [], [], placeholder="All", key="uh_d")
        uh_desig = uf2.multiselect("Designation", result.get("designations") or [], [], placeholder="All", key="uh_g")
        uh_excl  = uf3.checkbox("Exclude selected", key="uh_x")

        uh_df = uh_df_all.copy()
        for col, sel in [("Department", uh_depts), ("Designation", uh_desig)]:
            if sel:
                mask = ~uh_df[col].isin(sel) if uh_excl else uh_df[col].isin(sel)
                uh_df = uh_df[mask]
        uh_df = uh_df.reset_index(drop=True)

        if uh_df.empty:
            st.warning("No under-hours employees match filters.")
        else:
            # Metrics
            st.divider()
            um1, um2, um3 = st.columns(3)
            um1.metric("Employees with short days", len(uh_df))
            um2.metric("Total short days", int(uh_df["Short Days"].sum()))
            um3.metric("Avg short days / person", f"{uh_df['Short Days'].mean():.1f}")

            # Charts
            uc1, uc2 = st.columns(2)
            with uc1:
                st.markdown("**By department**")
                ud = uh_df["Department"].value_counts().head(10)
                if not ud.empty and not (len(ud)==1 and ud.index[0]==""):
                    st.bar_chart(ud, color="#e8590c")
            with uc2:
                st.markdown("**Most under-hours days**")
                st.dataframe(uh_df.sort_values("Short Days", ascending=False).head(10)
                             [["Name","Department","Short Days"]], hide_index=True, use_container_width=True)

            # Summary table
            st.divider()
            st.subheader("📋 Employee summary")
            st.dataframe(uh_df[["Emp No","Name","Email","Department","Designation","Short Days"]]
                         .sort_values("Short Days", ascending=False),
                         hide_index=True, use_container_width=True)

            # Day-by-day detail
            st.divider()
            with st.expander("📅 Day-by-day under-hours breakdown"):
                uh_detail = []
                uh_filtered = [all_under_hours[i] for i in uh_df["_idx"].tolist()]
                for e in uh_filtered:
                    for d in e.get("under_hours_days") or []:
                        sm = d["short_by_minutes"]
                        uh_detail.append({
                            "Emp No": e["employee_no"], "Name": e["employee_name"],
                            "Department": e.get("department",""),
                            "Date": d["date"], "Day": d["day_of_week"],
                            "In": d["in_time"], "Out": d["out_time"],
                            "Worked": d["total_work_hrs"],
                            "Short By": f"{sm//60}:{sm%60:02d}",
                            "Short (min)": sm,
                        })
                if uh_detail:
                    uhd = pd.DataFrame(uh_detail)
                    uf1, uf2 = st.columns(2)
                    uh_ef = uf1.multiselect("Employee", sorted(uhd["Name"].unique()), [], placeholder="All", key="uhd_e")
                    uh_df2 = uf2.multiselect("Dept", sorted(d for d in uhd["Department"].unique() if d), [], placeholder="All", key="uhd_d")
                    if uh_ef:  uhd = uhd[uhd["Name"].isin(uh_ef)]
                    if uh_df2: uhd = uhd[uhd["Department"].isin(uh_df2)]
                    st.dataframe(uhd.sort_values(["Name","Date"]), hide_index=True, use_container_width=True)
                    st.download_button("⬇️ Download", uhd.to_csv(index=False).encode(),
                                       file_name=f"under_hours_{result['period']}.csv", mime="text/csv")

            # Export
            st.download_button("⬇️ Download summary CSV", uh_df.to_csv(index=False).encode(),
                               file_name=f"under_hours_summary_{result['period']}.csv", mime="text/csv")
