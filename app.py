"""
app.py  —  HR Late-Comer Warning Tool  (v3)
==========================================
What changed in v3:
  - Secrets (greytHR creds + Google OAuth) are read from st.secrets automatically.
    HR never types credentials — they're pre-loaded from Streamlit Cloud secrets.
  - Google "Sign in with Google" is now the FIRST screen / gate.
    The full dashboard is only shown AFTER the HR user is logged in.
  - Emails are sent from the HR user's own Google account (whoever signed in).
  - Signed-in user's name + email are shown in the top-right corner.
  - Sign-out clears the session and returns to the login screen.

Streamlit secrets required (Settings → Secrets in Streamlit Cloud):
  GT_USERNAME          = "greythr_api_username"
  GT_PASSWORD          = "greythr_api_password"
  GT_DOMAIN            = "yourcompany.greythr.com"
  GOOGLE_CLIENT_ID     = "....apps.googleusercontent.com"
  GOOGLE_CLIENT_SECRET = "GOCSPX-..."
  REDIRECT_URI         = "https://yourapp.streamlit.app"   # or http://localhost:8501
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

st.set_page_config(
    page_title="HR Late-Comer Warnings",
    page_icon="⏰",
    layout="wide",
)

st.markdown("""
<style>
  /* tier badge */
  .tier-pill {
    display:inline-block; padding:2px 10px; border-radius:12px;
    color:#fff; font-size:12px; font-weight:600; margin-left:6px;
  }
  /* top-right user bar */
  .user-bar {
    position:fixed; top:0; right:0; z-index:9999;
    background:#1e293b; color:#e2e8f0;
    padding:6px 18px; font-size:13px; border-radius:0 0 0 8px;
  }
  /* login card */
  .login-card {
    max-width:420px; margin:80px auto; padding:40px 36px;
    background:#fff; border-radius:16px;
    box-shadow:0 4px 32px rgba(0,0,0,0.10);
    text-align:center;
  }
  .login-title  { font-size:26px; font-weight:700; color:#1e293b; margin-bottom:6px; }
  .login-sub    { color:#64748b; font-size:14px; margin-bottom:28px; }
  .info-box {
    background:#f0f4ff; border-left:4px solid #4361ee;
    padding:10px 14px; border-radius:4px; margin:8px 0; font-size:13px;
  }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────
# READ SECRETS  (no hardcoding — all from st.secrets)
# ──────────────────────────────────────────────────────────

def _secret(key, fallback=""):
    try:
        return st.secrets[key]
    except Exception:
        return fallback

GT_USERNAME    = _secret("GT_USERNAME")
GT_PASSWORD    = _secret("GT_PASSWORD")
GT_DOMAIN      = _secret("GT_DOMAIN")
CLIENT_ID      = _secret("GOOGLE_CLIENT_ID")
CLIENT_SECRET  = _secret("GOOGLE_CLIENT_SECRET")
REDIRECT_URI   = _secret("REDIRECT_URI", "http://localhost:8501")

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
    "gmail_service":  None,   # Gmail API service (set after OAuth)
    "hr_email":       None,   # signed-in HR user's email
    "hr_name":        None,   # signed-in HR user's display name
    "result":         None,   # greytHR fetch result
    "send_log":       [],
    "subjects":       dict(emailer.SUBJECTS),
    "bodies":         dict(emailer.BODIES),
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ──────────────────────────────────────────────────────────
# OAUTH HELPERS
# ──────────────────────────────────────────────────────────

def _make_flow():
    return Flow.from_client_config(
        {
            "web": {
                "client_id":     CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "redirect_uris": [REDIRECT_URI],
                "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
                "token_uri":     "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )


def _get_user_info(creds):
    """Return (email, name) for the signed-in Google user."""
    svc  = gbuild("oauth2", "v2", credentials=creds)
    info = svc.userinfo().get().execute()
    return info.get("email", ""), info.get("name", "HR User")


def _handle_oauth_callback():
    """If Google redirected back with ?code=..., exchange it for credentials."""
    code = st.query_params.get("code")
    if not code:
        return False
    try:
        flow = _make_flow()
        flow.fetch_token(code=code)
        creds = flow.credentials

        email, name = _get_user_info(creds)
        st.session_state.gmail_service = gbuild("gmail", "v1", credentials=creds)
        st.session_state.hr_email      = email
        st.session_state.hr_name       = name

        st.query_params.clear()
        return True
    except Exception as e:
        st.error(f"Sign-in failed: {e}")
        return False


# ──────────────────────────────────────────────────────────
# HANDLE OAUTH CALLBACK FIRST (before any UI)
# ──────────────────────────────────────────────────────────

if "code" in st.query_params:
    _handle_oauth_callback()
    st.rerun()


# ──────────────────────────────────────────────────────────
# GATE: show login screen if not signed in
# ──────────────────────────────────────────────────────────

if not st.session_state.gmail_service:

    # centre the login card
    _, mid, _ = st.columns([1, 1.4, 1])
    with mid:
        st.markdown("""
        <div class='login-card'>
          <div class='login-title'>⏰ HR Warning Tool</div>
          <div class='login-sub'>
            Sign in with your Google account to access<br>
            the Late-Comer Dashboard and send warning emails.
          </div>
        </div>
        """, unsafe_allow_html=True)

        if not CLIENT_ID or not CLIENT_SECRET:
            st.error(
                "Google OAuth credentials are missing. "
                "Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to Streamlit secrets."
            )
            st.stop()

        # Build auth URL and show the button
        try:
            flow     = _make_flow()
            auth_url, _ = flow.authorization_url(
                prompt="select_account",   # always show account picker
                access_type="offline",
            )
        except Exception as e:
            st.error(f"Could not build sign-in URL: {e}")
            st.stop()

        # Google-style sign-in button via markdown link
        st.markdown(
            f"""
            <div style='text-align:center; margin-top:8px;'>
              <a href="{auth_url}" target="_self"
                 style="display:inline-flex;align-items:center;gap:10px;
                        background:#fff;color:#3c4043;border:1px solid #dadce0;
                        border-radius:4px;padding:10px 24px;font-size:14px;
                        font-weight:500;text-decoration:none;
                        box-shadow:0 1px 3px rgba(0,0,0,0.1);">
                <img src="https://www.gstatic.com/firebasejs/ui/2.0.0/images/auth/google.svg"
                     width="20" height="20"/>
                Sign in with Google
              </a>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            "<p style='text-align:center;color:#94a3b8;font-size:12px;margin-top:20px;'>"
            "Only authorised HR accounts can access this tool.</p>",
            unsafe_allow_html=True,
        )

    st.stop()   # nothing below runs until signed in


# ──────────────────────────────────────────────────────────
# SIGNED IN — show user bar + sign-out
# ──────────────────────────────────────────────────────────

hr_email = st.session_state.hr_email
hr_name  = st.session_state.hr_name

# Top-right user chip
st.markdown(
    f"<div class='user-bar'>👤 {hr_name} &nbsp;|&nbsp; {hr_email}</div>",
    unsafe_allow_html=True,
)

# Sign-out in sidebar
st.sidebar.markdown(f"**Signed in as:**\n\n{hr_name}\n\n`{hr_email}`")
if st.sidebar.button("🚪 Sign out"):
    for k in DEFAULTS:
        st.session_state[k] = DEFAULTS[k]
    st.rerun()

st.sidebar.divider()


# ──────────────────────────────────────────────────────────
# SIDEBAR — greytHR (pre-filled from secrets, read-only display)
# ──────────────────────────────────────────────────────────

st.sidebar.title("⚙️ Setup")

with st.sidebar.expander("1. greytHR connection", expanded=True):
    if GT_USERNAME and GT_DOMAIN:
        st.success(f"Connected: `{GT_DOMAIN}`")
        st.caption(f"API user: `{GT_USERNAME}`")
    else:
        st.error("greytHR secrets missing. Add GT_USERNAME, GT_PASSWORD, GT_DOMAIN.")


# ──────────────────────────────────────────────────────────
# SIDEBAR — MONTH + LATE RULE
# ──────────────────────────────────────────────────────────

with st.sidebar.expander("2. Month & late-arrival rule", expanded=True):
    today = dt.date.today()
    col_y, col_m = st.columns(2)
    year  = col_y.number_input("Year",  min_value=2020, max_value=2100,
                               value=today.year, step=1)
    month = col_m.selectbox(
        "Month", options=list(range(1, 13)),
        index=today.month - 1,
        format_func=lambda m: calendar.month_name[m],
    )

    st.markdown("**How to decide 'late'?**")
    use_fixed = st.toggle(
        "Fixed cutoff time for everyone",
        value=True,
        help="ON = anyone clocking in after the cutoff is late.\n"
             "OFF = compared to each person's own shift start.",
    )

    if use_fixed:
        cutoff_time = st.slider(
            "Late if clock-in is after",
            min_value=dt.time(6,  0),
            max_value=dt.time(14, 0),
            value=dt.time(10, 0),
            step=dt.timedelta(minutes=15),
            format="HH:mm",
        )
        fixed_cutoff  = cutoff_time.strftime("%H:%M")
        grace_minutes = 0
        st.caption(f"⏰ Cutoff: **{fixed_cutoff}** — same for all")
    else:
        fixed_cutoff  = None
        grace_minutes = st.slider("Grace period (minutes)", 0, 60, 10)
        st.caption(f"⏰ Late if in-time > shift start + **{grace_minutes} min**")

    workers = st.slider("Parallel threads", 1, 15, 10)

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
                username=GT_USERNAME,
                password=GT_PASSWORD,
                domain=GT_DOMAIN,
                year=int(year),
                month=int(month),
                grace_minutes=int(grace_minutes),
                fixed_cutoff=fixed_cutoff,
                max_workers=int(workers),
                progress_cb=_cb,
            )
        st.session_state.result   = result
        st.session_state.send_log = []
        prog.progress(1.0, text="Done")
        st.sidebar.success(
            f"Found **{len(result['employees'])}** late-comer(s) "
            f"out of {result['all_employees_count']} employees."
        )
    except Exception as e:
        prog.empty()
        st.sidebar.error(f"Fetch failed: {e}")


# ──────────────────────────────────────────────────────────
# MAIN HEADER
# ──────────────────────────────────────────────────────────

st.title("⏰ Late-Comer Warning Dashboard")
st.caption(
    "Review late arrivals, filter by department, pick who gets a warning, "
    "and send warning emails from your own Google account."
)

result = st.session_state.result

if result is None:
    st.info("👈 Pick a month in the sidebar and click **Fetch late-comers** to begin.")
    with st.expander("ℹ️ How lateness & tiers work"):
        st.markdown("""
**Fixed cutoff mode (recommended):** Anyone clocking in after the set time
(e.g. 10:00 AM) is flagged as late. Holidays, Week-Offs, and leave days are skipped.

**Shift-relative mode:** Each person is compared to their own shift start + grace period.

**Warning tiers:**

| Late days | Tier | Email tone |
|---|---|---|
| 1 | 🟢 Normal | Gentle reminder |
| 2 | 🟡 Moderate | Formal warning |
| 3+ | 🔴 Strict | Final warning — salary deduction on further lateness |
        """)
    st.stop()


# ──────────────────────────────────────────────────────────
# BUILD DATAFRAME
# ──────────────────────────────────────────────────────────

month_label   = f"{calendar.month_name[int(month)]} {int(year)}"
all_employees = result["employees"]

if not all_employees:
    st.success(f"🎉 No late-comers found for {month_label}. Everyone was on time!")
    st.stop()


def _make_df(emp_list):
    rows = []
    for e in emp_list:
        tier = emailer.tier_for(e["late_count"])
        rows.append({
            "Employee ID": e["employee_id"],
            "Emp No":      e["employee_no"],
            "Name":        e["employee_name"],
            "Email":       e["employee_email"],
            "Department":  e.get("department", ""),
            "Late Days":   e["late_count"],
            "Tier":        emailer.TIER_META[tier]["label"],
            "_tier":       tier,
            "_orig_idx":   all_employees.index(e),
        })
    return pd.DataFrame(rows)

df_all = _make_df(all_employees)


# ──────────────────────────────────────────────────────────
# ① DEPARTMENT FILTER
# ──────────────────────────────────────────────────────────

st.subheader("🏢 Department Filter")
all_depts = result.get("departments") or sorted(
    d for d in df_all["Department"].dropna().unique() if d
)

if all_depts:
    fc1, fc2 = st.columns([3, 1])
    selected_depts = fc1.multiselect(
        "Show only these departments (blank = all)",
        options=all_depts, default=[],
        placeholder="Select departments...",
    )
    exclude_mode = fc2.checkbox("Exclude selected", value=False,
                                help="Tick to REMOVE chosen departments instead of keeping them.")

    if selected_depts:
        mask = (
            ~df_all["Department"].isin(selected_depts)
            if exclude_mode
            else df_all["Department"].isin(selected_depts)
        )
        df = df_all[mask].reset_index(drop=True)
    else:
        df = df_all.copy()
else:
    st.caption("No department data from API — showing all.")
    df = df_all.copy()

filtered_employees = [all_employees[i] for i in df["_orig_idx"].tolist()]

if df.empty:
    st.warning("No late-comers match the current department filter.")
    st.stop()

st.divider()


# ──────────────────────────────────────────────────────────
# ② INSIGHTS
# ──────────────────────────────────────────────────────────

st.subheader("📊 Insights")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Late-comers",        len(df))
c2.metric("Total employees",    result["all_employees_count"])
c3.metric("🟢 Normal (1)",      int((df["_tier"] == "normal").sum()))
c4.metric("🟡 Moderate (2)",    int((df["_tier"] == "moderate").sum()))
c5.metric("🔴 Strict (3+)",     int((df["_tier"] == "strict").sum()))

ch1, ch2, ch3 = st.columns(3)
with ch1:
    st.markdown("**By tier**")
    st.bar_chart(
        df["Tier"].value_counts()
        .reindex(["Normal Warning", "Moderate Warning", "Strict Warning"])
        .fillna(0).astype(int),
        color="#4361ee",
    )
with ch2:
    st.markdown("**By department**")
    dc = df["Department"].value_counts().head(10)
    if not dc.empty:
        st.bar_chart(dc, color="#7209b7")
    else:
        st.caption("No department data")
with ch3:
    st.markdown("**Top late-comers**")
    st.dataframe(
        df.sort_values("Late Days", ascending=False)
          .head(8)[["Name", "Department", "Late Days", "Tier"]],
        hide_index=True, use_container_width=True,
    )

st.divider()


# ──────────────────────────────────────────────────────────
# ③ SELECTION TABLE
# ──────────────────────────────────────────────────────────

st.subheader("✅ Select who receives a warning email")
st.caption("Tick the employees you want to email. Quick-select buttons below.")

sel_df = df.copy()
sel_df.insert(0, "Send", False)
sel_df["Has Email"] = sel_df["Email"].str.len() > 0

qb1, qb2, qb3, qb4, qb5 = st.columns(5)
if qb1.button("Select all"):         st.session_state._preselect = "all"
if qb2.button("🟢 Normal"):          st.session_state._preselect = "normal"
if qb3.button("🟡 Moderate"):        st.session_state._preselect = "moderate"
if qb4.button("🔴 Strict"):          st.session_state._preselect = "strict"
if qb5.button("Clear"):              st.session_state._preselect = "none"

pre = st.session_state.get("_preselect")
if pre == "all":
    sel_df["Send"] = sel_df["Has Email"]
elif pre in ("normal", "moderate", "strict"):
    sel_df["Send"] = (sel_df["_tier"] == pre) & sel_df["Has Email"]
elif pre == "none":
    sel_df["Send"] = False

edited = st.data_editor(
    sel_df[["Send", "Emp No", "Name", "Email", "Department", "Late Days", "Tier", "Has Email"]],
    hide_index=True,
    use_container_width=True,
    column_config={
        "Send":      st.column_config.CheckboxColumn("Send"),
        "Has Email": st.column_config.CheckboxColumn("Has Email", disabled=True),
    },
    disabled=["Emp No", "Name", "Email", "Department", "Late Days", "Tier", "Has Email"],
    key="sel_editor",
)

sel_local_idx     = edited.index[edited["Send"] & edited["Has Email"]].tolist()
selected_employees = [all_employees[df.iloc[i]["_orig_idx"]] for i in sel_local_idx]

missing = edited[edited["Send"] & ~edited["Has Email"]]
if not missing.empty:
    st.warning("No email — will be skipped: " + ", ".join(missing["Name"].tolist()))

st.info(f"**{len(selected_employees)}** employee(s) selected.")

st.divider()


# ──────────────────────────────────────────────────────────
# ④ EMAIL TEMPLATES
# ──────────────────────────────────────────────────────────

# defaults (overridden inside expander if HR edits them)
company       = "Growify"
signatory     = hr_name
include_table = True

with st.expander("✏️ Edit email templates"):
    st.caption("Placeholders: {name} {late_count} {month} {company} {hr_name} {late_days_table}")
    company       = st.text_input("Company name",   value="Growify")
    signatory     = st.text_input("HR signatory",   value=hr_name)
    include_table = st.checkbox("Include late-days table in email", value=True)

    for tier_key in ("normal", "moderate", "strict"):
        meta = emailer.TIER_META[tier_key]
        st.markdown(
            "<span class='tier-pill' style='background:" + meta["color"] + ";'>"
            + meta["label"] + "</span>",
            unsafe_allow_html=True,
        )
        st.session_state.subjects[tier_key] = st.text_input(
            f"Subject ({tier_key})",
            value=st.session_state.subjects[tier_key],
            key=f"subj_{tier_key}",
        )
        st.session_state.bodies[tier_key] = st.text_area(
            f"Body HTML ({tier_key})",
            value=st.session_state.bodies[tier_key],
            height=170, key=f"body_{tier_key}",
        )
        st.markdown("---")


# ──────────────────────────────────────────────────────────
# ⑤ EMAIL PREVIEW
# ──────────────────────────────────────────────────────────

if selected_employees:
    with st.expander("👀 Preview an email"):
        names = [e["employee_name"] for e in selected_employees]
        pick  = st.selectbox("Preview for", range(len(names)),
                             format_func=lambda i: names[i])
        emp   = selected_employees[pick]
        tier, subject, html = emailer.build_email(
            emp, month_label,
            company=company, hr_name=signatory,
            subjects=st.session_state.subjects,
            bodies=st.session_state.bodies,
            include_table=include_table,
        )
        meta = emailer.TIER_META[tier]
        st.markdown(
            f"**From:** {hr_email}  →  **To:** {emp['employee_email']}  |  "
            "<span class='tier-pill' style='background:" + meta["color"] + ";'>"
            + meta["label"] + "</span>",
            unsafe_allow_html=True,
        )
        st.markdown(f"**Subject:** {subject}")
        st.components.v1.html(html, height=380, scrolling=True)


# ──────────────────────────────────────────────────────────
# ⑥ SEND
# ──────────────────────────────────────────────────────────

st.subheader("🚀 Send warning emails")

st.markdown(
    f"<div class='info-box'>Emails will be sent <b>from your account: {hr_email}</b> "
    f"via Gmail API. Recipients will see your name and email as the sender.</div>",
    unsafe_allow_html=True,
)

dry_run = st.checkbox(
    "Dry run — build emails but DON'T actually send",
    value=True,
    help="Keep ON to test. Uncheck to send for real.",
)

confirm = st.checkbox(
    f"I have reviewed the {len(selected_employees)} selected employee(s) and want to proceed."
)

send_clicked = st.button(
    "Send selected warning emails",
    type="primary",
    disabled=(len(selected_employees) == 0 or not confirm),
)

if send_clicked:
    send_log = []
    prog     = st.progress(0.0)

    for i, emp in enumerate(selected_employees, start=1):
        tier, subject, html = emailer.build_email(
            emp, month_label,
            company=company, hr_name=signatory,
            subjects=st.session_state.subjects,
            bodies=st.session_state.bodies,
            include_table=include_table,
        )
        row = {
            "Name":       emp["employee_name"],
            "Email":      emp["employee_email"],
            "Department": emp.get("department", ""),
            "Tier":       emailer.TIER_META[tier]["label"],
        }
        try:
            if dry_run:
                row["Status"] = "DRY RUN — not sent"
            else:
                emailer.send_via_gmail_api(
                    st.session_state.gmail_service,
                    to_email=emp["employee_email"],
                    subject=subject,
                    html_body=html,
                    from_name=signatory,
                )
                row["Status"] = f"✅ Sent from {hr_email}"
        except Exception as e:
            row["Status"] = f"❌ Failed: {e}"

        send_log.append(row)
        prog.progress(i / len(selected_employees))

    st.session_state.send_log = send_log

    if dry_run:
        st.info("Dry run complete. Uncheck 'Dry run' and click again to send for real.")
    else:
        ok  = sum(1 for r in send_log if r["Status"].startswith("✅"))
        bad = len(send_log) - ok
        if bad:
            st.warning(f"Sent {ok}, failed {bad}. See log below.")
        else:
            st.success(f"✅ All {ok} emails sent successfully from {hr_email}!")


if st.session_state.send_log:
    st.markdown("#### Send log")
    log_df = pd.DataFrame(st.session_state.send_log)
    st.dataframe(log_df, hide_index=True, use_container_width=True)
    st.download_button(
        "⬇️ Download send log (CSV)",
        log_df.to_csv(index=False).encode("utf-8"),
        file_name=f"warning_log_{result['period']}.csv",
        mime="text/csv",
    )


# ──────────────────────────────────────────────────────────
# ⑦ FULL DATA EXPORT
# ──────────────────────────────────────────────────────────

with st.expander("📄 Full data & export"):
    st.dataframe(
        df.drop(columns=["_tier", "_orig_idx"]),
        hide_index=True, use_container_width=True,
    )
    detail = []
    for e in filtered_employees:
        for d in e["late_days"]:
            detail.append({
                "Emp No":        e["employee_no"],
                "Name":          e["employee_name"],
                "Department":    e.get("department", ""),
                "Email":         e["employee_email"],
                "Date":          d["date"],
                "Day":           d["day_of_week"],
                "Shift Start":   d["shift_start"],
                "In Time":       d["in_time"],
                "Late By (min)": d["late_by_minutes"],
            })
    detail_df = pd.DataFrame(detail)
    st.download_button(
        "⬇️ Download detailed late-day log (CSV)",
        detail_df.to_csv(index=False).encode("utf-8"),
        file_name=f"late_days_{result['period']}.csv",
        mime="text/csv",
    )
