"""
app.py  —  HR Late-Comer Warning Tool  (v4)
==========================================
New in v4:
  - Department column populated (fetched from /employee/v2/departments)
  - Department filter moved to TOP of main page
  - Date-wise filter in sidebar
  - Active employees only (leftorg=False) — much faster
  - Half-day excluded from late count
  - Late Details page link (pages/late_details.py)
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
    "gmail_service": None,
    "hr_email":      None,
    "hr_name":       None,
    "result":        None,
    "send_log":      [],
    "subjects":      dict(emailer.SUBJECTS),
    "bodies":        dict(emailer.BODIES),
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ──────────────────────────────────────────────────────────
# OAUTH HELPERS
# ──────────────────────────────────────────────────────────

def _make_flow():
    return Flow.from_client_config(
        {"web": {
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uris": [REDIRECT_URI],
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
        }},
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
        autogenerate_code_verifier=False,
    )

def _get_user_info(creds):
    svc  = gbuild("oauth2", "v2", credentials=creds)
    info = svc.userinfo().get().execute()
    return info.get("email", ""), info.get("name", "HR User")


# ──────────────────────────────────────────────────────────
# OAUTH CALLBACK
# ──────────────────────────────────────────────────────────

params = st.query_params.to_dict()

if "error" in params:
    st.error(f"**Google sign-in error:** `{params.get('error')}`")
    st.warning(params.get("error_description", ""))
    st.info("Fix: GCP → OAuth Consent → Test Users → add your email")
    if st.button("Clear and try again"):
        st.query_params.clear(); st.rerun()
    st.stop()

if "code" in params:
    with st.spinner("Completing sign-in..."):
        try:
            import urllib.parse
            callback_url = REDIRECT_URI + "?" + urllib.parse.urlencode(params)
            flow = _make_flow()
            if st.session_state.get("_oauth_state"):
                flow.state = st.session_state._oauth_state
            flow.fetch_token(authorization_response=callback_url)
            creds = flow.credentials
            email, name = _get_user_info(creds)
            st.session_state.gmail_service = gbuild("gmail", "v1", credentials=creds)
            st.session_state.hr_email      = email
            st.session_state.hr_name       = name
            st.query_params.clear()
            st.rerun()
        except Exception as e:
            st.error(f"**Token exchange failed:** {e}")
            if st.button("Clear and try again"):
                st.query_params.clear(); st.rerun()
            st.stop()


# ──────────────────────────────────────────────────────────
# LOGIN GATE
# ──────────────────────────────────────────────────────────

if not st.session_state.gmail_service:
    with st.expander("🔍 Diagnostics"):
        st.json({
            "GT_USERNAME":          GT_USERNAME or "❌ MISSING",
            "GT_DOMAIN":            GT_DOMAIN   or "❌ MISSING",
            "GOOGLE_CLIENT_ID":     (CLIENT_ID[:12] + "...") if CLIENT_ID else "❌ MISSING",
            "GOOGLE_CLIENT_SECRET": "✅ set" if CLIENT_SECRET else "❌ MISSING",
            "REDIRECT_URI":         REDIRECT_URI or "❌ MISSING",
        })

    _, mid, _ = st.columns([1, 1.4, 1])
    with mid:
        st.markdown("<div class='login-card'><div class='login-title'>⏰ HR Warning Tool</div>"
                    "<div class='login-sub'>Sign in with your Google account to access<br>"
                    "the Late-Comer Dashboard and send warning emails.</div></div>",
                    unsafe_allow_html=True)

        if not CLIENT_ID or not CLIENT_SECRET:
            st.error("Google OAuth credentials missing in Streamlit secrets.")
            st.stop()

        try:
            flow = _make_flow()
            auth_url, state = flow.authorization_url(
                prompt="select_account", access_type="offline", include_granted_scopes="true")
            st.session_state._oauth_state = state
        except Exception as e:
            st.error(f"Could not build sign-in URL: {e}"); st.stop()

        st.markdown("<div style='text-align:center; margin-top:8px;'>", unsafe_allow_html=True)
        st.link_button("🔑  Sign in with Google", url=auth_url, type="primary")
        st.markdown("</div>", unsafe_allow_html=True)
        st.markdown("<p style='text-align:center;color:#94a3b8;font-size:12px;margin-top:20px;'>"
                    "Only authorised HR accounts can access this tool.</p>", unsafe_allow_html=True)
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

with st.sidebar.expander("1. greytHR connection", expanded=True):
    if GT_USERNAME and GT_DOMAIN:
        st.success(f"Connected: `{GT_DOMAIN}`")
        st.caption(f"API user: `{GT_USERNAME}`")
    else:
        st.error("greytHR secrets missing.")

with st.sidebar.expander("2. Month & late-arrival rule", expanded=True):
    today = dt.date.today()
    col_y, col_m = st.columns(2)
    year  = col_y.number_input("Year", min_value=2020, max_value=2100, value=today.year, step=1)
    month = col_m.selectbox("Month", options=list(range(1, 13)), index=today.month - 1,
                            format_func=lambda m: calendar.month_name[m])

    use_fixed = st.toggle("Fixed cutoff for everyone", value=True,
                          help="ON = anyone after cutoff is late. OFF = vs their shift start.")
    if use_fixed:
        cutoff_time  = st.slider("Late if clock-in after", min_value=dt.time(6, 0),
                                 max_value=dt.time(14, 0), value=dt.time(10, 0),
                                 step=dt.timedelta(minutes=15), format="HH:mm")
        fixed_cutoff  = cutoff_time.strftime("%H:%M")
        grace_minutes = 0
        st.caption(f"⏰ Cutoff: **{fixed_cutoff}**")
    else:
        fixed_cutoff  = None
        grace_minutes = st.slider("Grace period (minutes)", 0, 60, 10)
        st.caption(f"⏰ Late if in-time > shift start + **{grace_minutes} min**")

    workers = st.slider("Parallel threads", 1, 20, 10)

# ── Date-wise filter in sidebar ────────────────────────────
st.sidebar.divider()
with st.sidebar.expander("3. Date filter (optional)", expanded=False):
    st.caption("Filter the results to a specific date after fetching.")
    date_filter = st.date_input(
        "Show only late-comers on this date",
        value=None,
        min_value=dt.date(2020, 1, 1),
        max_value=dt.date(2100, 12, 31),
        help="Leave blank to show all late days across the month.",
    )

fetch_clicked = st.sidebar.button(
    "🔄 Fetch late-comers", type="primary", use_container_width=True,
    disabled=not (GT_USERNAME and GT_PASSWORD and GT_DOMAIN),
)


# ──────────────────────────────────────────────────────────
# FETCH
# ──────────────────────────────────────────────────────────

if fetch_clicked:
    prog = st.sidebar.progress(0.0, text="Starting...")
    def _cb(done, total):
        prog.progress(done / total, text=f"Fetched {done}/{total} employees")
    try:
        with st.spinner("Fetching attendance data from greytHR..."):
            result = api.get_late_comers_for_month(
                username=GT_USERNAME, password=GT_PASSWORD, domain=GT_DOMAIN,
                year=int(year), month=int(month),
                grace_minutes=int(grace_minutes), fixed_cutoff=fixed_cutoff,
                max_workers=int(workers), progress_cb=_cb,
            )
        st.session_state.result   = result
        st.session_state.send_log = []
        prog.progress(1.0, text="Done ✅")
        st.sidebar.success(
            f"Found **{len(result['employees'])}** late-comer(s) "
            f"out of {result['all_employees_count']} active employees "
            f"in {result['elapsed']:.1f}s"
        )
    except Exception as e:
        prog.empty()
        st.sidebar.error(f"Fetch failed: {e}")


# ──────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────

st.title("⏰ Late-Comer Warning Dashboard")
st.caption("Review late arrivals, filter by department or date, pick who gets a warning, and send emails.")

result = st.session_state.result

if result is None:
    st.info("👈 Pick a month in the sidebar and click **Fetch late-comers** to begin.")
    with st.expander("ℹ️ How lateness & tiers work"):
        st.markdown("""
**Fixed cutoff:** Anyone clocking in after the cutoff time is late. Holidays, WeekOffs,
leave days, and **half-days** are all excluded automatically.

**Shift-relative:** Each person is compared to their own shift start + grace period.

| Late days | Tier | Email tone |
|---|---|---|
| 1 | 🟢 Normal | Gentle reminder |
| 2 | 🟡 Moderate | Formal warning |
| 3+ | 🔴 Strict | Final warning |
        """)
    st.stop()

month_label   = f"{calendar.month_name[int(month)]} {int(year)}"
all_employees = result["employees"]

if not all_employees:
    st.success(f"🎉 No late-comers in {month_label}. Everyone was on time!")
    st.stop()


# ── Build full dataframe ───────────────────────────────────
def _make_df(emp_list, date_filter=None):
    rows = []
    for e in emp_list:
        # If date filter active — only include employees late on that date
        if date_filter:
            date_str = str(date_filter)
            matching_days = [d for d in e["late_days"] if d["date"] == date_str]
            if not matching_days:
                continue
            late_count_display = len(matching_days)
        else:
            late_count_display = e["late_count"]

        tier = emailer.tier_for(e["late_count"])  # tier always based on total month count
        rows.append({
            "Employee ID": e["employee_id"],
            "Emp No":      e["employee_no"],
            "Name":        e["employee_name"],
            "Email":       e["employee_email"],
            "Department":  e.get("department") or "",
            "Late Days":   late_count_display,
            "Tier":        emailer.TIER_META[tier]["label"],
            "_tier":       tier,
            "_orig_idx":   all_employees.index(e),
        })
    return pd.DataFrame(rows)


# ── ① DEPARTMENT FILTER (top of page) ─────────────────────
st.subheader("🏢 Filters")

all_depts = result.get("departments") or []

fc1, fc2, fc3 = st.columns([3, 1, 2])
with fc1:
    selected_depts = st.multiselect(
        "Filter by department",
        options=all_depts,
        default=[],
        placeholder="All departments",
    )
with fc2:
    exclude_mode = st.checkbox("Exclude", value=False, help="Remove selected departments instead")
with fc3:
    if date_filter:
        st.info(f"📅 Showing late-comers on **{date_filter.strftime('%d %b %Y')}** only")
    else:
        st.caption("No date filter — showing full month")

df_all = _make_df(all_employees, date_filter=date_filter)

if selected_depts:
    mask = ~df_all["Department"].isin(selected_depts) if exclude_mode else df_all["Department"].isin(selected_depts)
    df   = df_all[mask].reset_index(drop=True)
else:
    df = df_all.copy()

filtered_employees = [all_employees[i] for i in df["_orig_idx"].tolist()]

if df.empty:
    st.warning("No late-comers match the current filters.")
    st.stop()

st.divider()


# ── ② INSIGHTS ────────────────────────────────────────────
st.subheader("📊 Insights")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Late-comers",     len(df))
c2.metric("Total employees", result["all_employees_count"])
c3.metric("🟢 Normal (1)",   int((df["_tier"] == "normal").sum()))
c4.metric("🟡 Moderate (2)", int((df["_tier"] == "moderate").sum()))
c5.metric("🔴 Strict (3+)",  int((df["_tier"] == "strict").sum()))

ch1, ch2, ch3 = st.columns(3)
with ch1:
    st.markdown("**By tier**")
    st.bar_chart(
        df["Tier"].value_counts().reindex(["Normal Warning","Moderate Warning","Strict Warning"]).fillna(0).astype(int),
        color="#4361ee")
with ch2:
    st.markdown("**By department**")
    dc = df["Department"].value_counts().head(10)
    if not dc.empty: st.bar_chart(dc, color="#7209b7")
    else: st.caption("No department data")
with ch3:
    st.markdown("**Top late-comers**")
    st.dataframe(df.sort_values("Late Days", ascending=False).head(8)
                   [["Name","Department","Late Days","Tier"]], hide_index=True, use_container_width=True)

# ── Link to detail page ────────────────────────────────────
st.page_link("pages/late_details.py", label="🔍 View detailed late-time breakdown per employee →", icon="📋")

st.divider()


# ── ③ SELECTION TABLE ──────────────────────────────────────
st.subheader("✅ Select who receives a warning email")

sel_df = df.copy()
sel_df.insert(0, "Send", False)
sel_df["Has Email"] = sel_df["Email"].str.len() > 0

qb1, qb2, qb3, qb4, qb5 = st.columns(5)
if qb1.button("Select all"):    st.session_state._preselect = "all"
if qb2.button("🟢 Normal"):     st.session_state._preselect = "normal"
if qb3.button("🟡 Moderate"):   st.session_state._preselect = "moderate"
if qb4.button("🔴 Strict"):     st.session_state._preselect = "strict"
if qb5.button("Clear"):         st.session_state._preselect = "none"

pre = st.session_state.get("_preselect")
if pre == "all":
    sel_df["Send"] = sel_df["Has Email"]
elif pre in ("normal", "moderate", "strict"):
    sel_df["Send"] = (sel_df["_tier"] == pre) & sel_df["Has Email"]
elif pre == "none":
    sel_df["Send"] = False

edited = st.data_editor(
    sel_df[["Send","Emp No","Name","Email","Department","Late Days","Tier","Has Email"]],
    hide_index=True, use_container_width=True,
    column_config={
        "Send":      st.column_config.CheckboxColumn("Send"),
        "Has Email": st.column_config.CheckboxColumn("Has Email", disabled=True),
    },
    disabled=["Emp No","Name","Email","Department","Late Days","Tier","Has Email"],
    key="sel_editor",
)

sel_local_idx      = edited.index[edited["Send"] & edited["Has Email"]].tolist()
selected_employees = [all_employees[df.iloc[i]["_orig_idx"]] for i in sel_local_idx]

missing = edited[edited["Send"] & ~edited["Has Email"]]
if not missing.empty:
    st.warning("No email — will be skipped: " + ", ".join(missing["Name"].tolist()))

st.info(f"**{len(selected_employees)}** employee(s) selected.")
st.divider()


# ── ④ EMAIL TEMPLATES ──────────────────────────────────────
company = "Growify"; signatory = hr_name; include_table = True
with st.expander("✏️ Edit email templates"):
    st.caption("Placeholders: {name} {late_count} {month} {company} {hr_name} {late_days_table}")
    company       = st.text_input("Company name", value="Growify")
    signatory     = st.text_input("HR signatory", value=hr_name)
    include_table = st.checkbox("Include late-days table in email", value=True)
    for tier_key in ("normal", "moderate", "strict"):
        meta = emailer.TIER_META[tier_key]
        st.markdown(f"<span class='tier-pill' style='background:{meta['color']};'>{meta['label']}</span>",
                    unsafe_allow_html=True)
        st.session_state.subjects[tier_key] = st.text_input(
            f"Subject ({tier_key})", value=st.session_state.subjects[tier_key], key=f"subj_{tier_key}")
        st.session_state.bodies[tier_key] = st.text_area(
            f"Body HTML ({tier_key})", value=st.session_state.bodies[tier_key], height=170, key=f"body_{tier_key}")
        st.markdown("---")


# ── ⑤ EMAIL PREVIEW ────────────────────────────────────────
if selected_employees:
    with st.expander("👀 Preview an email"):
        names = [e["employee_name"] for e in selected_employees]
        pick  = st.selectbox("Preview for", range(len(names)), format_func=lambda i: names[i])
        emp   = selected_employees[pick]
        tier, subject, html = emailer.build_email(
            emp, month_label, company=company, hr_name=signatory,
            subjects=st.session_state.subjects, bodies=st.session_state.bodies,
            include_table=include_table)
        meta = emailer.TIER_META[tier]
        st.markdown(
            f"**From:** {hr_email}  →  **To:** {emp['employee_email']}  |  "
            f"<span class='tier-pill' style='background:{meta['color']};'>{meta['label']}</span>",
            unsafe_allow_html=True)
        st.markdown(f"**Subject:** {subject}")
        st.components.v1.html(html, height=380, scrolling=True)


# ── ⑥ SEND ─────────────────────────────────────────────────
st.subheader("🚀 Send warning emails")
st.markdown(f"<div class='info-box'>Emails sent <b>from: {hr_email}</b> via Gmail API.</div>",
            unsafe_allow_html=True)

dry_run = st.checkbox("Dry run — build but DON'T send", value=True)
confirm = st.checkbox(f"I have reviewed the {len(selected_employees)} selected employee(s).")
send_clicked = st.button("Send selected warning emails", type="primary",
                         disabled=(len(selected_employees) == 0 or not confirm))

if send_clicked:
    send_log = []; prog = st.progress(0.0)
    for i, emp in enumerate(selected_employees, 1):
        tier, subject, html = emailer.build_email(
            emp, month_label, company=company, hr_name=signatory,
            subjects=st.session_state.subjects, bodies=st.session_state.bodies,
            include_table=include_table)
        row = {"Name": emp["employee_name"], "Email": emp["employee_email"],
               "Department": emp.get("department",""), "Tier": emailer.TIER_META[tier]["label"]}
        try:
            if dry_run:
                row["Status"] = "DRY RUN — not sent"
            else:
                emailer.send_via_gmail_api(
                    st.session_state.gmail_service,
                    to_email=emp["employee_email"], subject=subject,
                    html_body=html, from_name=signatory)
                row["Status"] = f"✅ Sent from {hr_email}"
        except Exception as e:
            row["Status"] = f"❌ Failed: {e}"
        send_log.append(row)
        prog.progress(i / len(selected_employees))
    st.session_state.send_log = send_log
    if dry_run:
        st.info("Dry run complete. Uncheck 'Dry run' to send for real.")
    else:
        ok = sum(1 for r in send_log if r["Status"].startswith("✅"))
        bad = len(send_log) - ok
        if bad: st.warning(f"Sent {ok}, failed {bad}.")
        else:   st.success(f"✅ All {ok} emails sent from {hr_email}!")

if st.session_state.send_log:
    st.markdown("#### Send log")
    log_df = pd.DataFrame(st.session_state.send_log)
    st.dataframe(log_df, hide_index=True, use_container_width=True)
    st.download_button("⬇️ Download send log", log_df.to_csv(index=False).encode(),
                       file_name=f"warning_log_{result['period']}.csv", mime="text/csv")


# ── ⑦ FULL EXPORT ──────────────────────────────────────────
with st.expander("📄 Full data & export"):
    st.dataframe(df.drop(columns=["_tier","_orig_idx"]), hide_index=True, use_container_width=True)
    detail = []
    for e in filtered_employees:
        for d in e["late_days"]:
            if date_filter and d["date"] != str(date_filter):
                continue
            detail.append({
                "Emp No": e["employee_no"], "Name": e["employee_name"],
                "Department": e.get("department",""), "Email": e["employee_email"],
                "Date": d["date"], "Day": d["day_of_week"],
                "Shift Start": d["shift_start"], "In Time": d["in_time"],
                "Late By (min)": d["late_by_minutes"],
            })
    detail_df = pd.DataFrame(detail)
    st.download_button("⬇️ Download detailed late-day log",
                       detail_df.to_csv(index=False).encode(),
                       file_name=f"late_days_{result['period']}.csv", mime="text/csv")
