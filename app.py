"""
app.py  —  HR Late-Comer Warning Tool  (v2)
==========================================
New in this version:
  1. Gmail OAuth "Sign in with Google" — no SMTP / app passwords needed.
  2. Fixed cutoff time slider — flag anyone who arrives after a specific
     clock time (e.g. 10:00 AM) regardless of their individual shift.
  3. Department filter — HR can exclude entire departments or individual
     employees from the late-comer list.

Run:
  streamlit run app.py
"""

import calendar
from datetime import date, datetime, time as dtime

import pandas as pd
import streamlit as st

import greythr_api as api
import emailer

# Gmail OAuth imports (only used when HR chooses OAuth mode)
try:
    from google_auth_oauthlib.flow import Flow
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build as gbuild
    GMAIL_OAUTH_AVAILABLE = True
except ImportError:
    GMAIL_OAUTH_AVAILABLE = False


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
  .tier-pill {
    display:inline-block; padding:2px 10px; border-radius:12px;
    color:#fff; font-size:12px; font-weight:600;
  }
  .info-box {
    background:#f0f4ff; border-left:4px solid #4361ee;
    padding:12px 16px; border-radius:4px; margin:8px 0;
  }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────
# SESSION STATE
# ──────────────────────────────────────────────────────────

for key, default in [
    ("result", None),
    ("subjects", dict(emailer.SUBJECTS)),
    ("bodies",   dict(emailer.BODIES)),
    ("send_log", []),
    ("gmail_creds", None),    # stores OAuth Credentials object
    ("gmail_service", None),  # stores Gmail API service
]:
    if key not in st.session_state:
        st.session_state[key] = default


# ──────────────────────────────────────────────────────────
# SIDEBAR — greytHR CONNECTION
# ──────────────────────────────────────────────────────────

st.sidebar.title("⚙️ Setup")

with st.sidebar.expander("1. greytHR connection", expanded=True):
    username = st.text_input("API Username", key="gt_user")
    password = st.text_input("API Password", type="password", key="gt_pass")
    domain   = st.text_input("Domain", placeholder="yourcompany.greythr.com", key="gt_domain")

# ──────────────────────────────────────────────────────────
# SIDEBAR — MONTH + LATE RULE
# ──────────────────────────────────────────────────────────

with st.sidebar.expander("2. Month & late-arrival rule", expanded=True):
    today = date.today()
    col_y, col_m = st.columns(2)
    year  = col_y.number_input("Year",  min_value=2020, max_value=2100,
                               value=today.year,  step=1)
    month = col_m.selectbox(
        "Month", options=list(range(1, 13)),
        index=today.month - 1,
        format_func=lambda m: calendar.month_name[m],
    )

    st.markdown("**How to decide 'late'?**")
    use_fixed = st.toggle(
        "Use a fixed cutoff time for everyone",
        value=True,
        help="ON = anyone clocking in after the cutoff time is late.\n"
             "OFF = each person is compared to their own shift start.",
    )

    if use_fixed:
        cutoff_time = st.slider(
            "Late if clock-in is after (HH:MM)",
            min_value=dtime(6,  0),
            max_value=dtime(14, 0),
            value=dtime(10, 0),
            step=__import__("datetime").timedelta(minutes=15),
            format="HH:mm",
            help="Everyone who arrives after this time is counted as late.",
        )
        fixed_cutoff   = cutoff_time.strftime("%H:%M")
        grace_minutes  = 0   # not used in fixed mode
        st.caption(f"⏰ Cutoff: **{fixed_cutoff}** — same for all employees")
    else:
        fixed_cutoff  = None
        grace_minutes = st.slider(
            "Grace period (minutes)",
            0, 60, 10,
            help="Minutes added on top of each person's shift start.",
        )
        st.caption(f"⏰ Late if in-time > shift start + **{grace_minutes} min**")

    workers = st.slider("Parallel threads", 1, 15, 10)

fetch_clicked = st.sidebar.button(
    "🔄 Fetch late-comers", type="primary", use_container_width=True
)


# ──────────────────────────────────────────────────────────
# FETCH
# ──────────────────────────────────────────────────────────

def _validate_conn():
    if not username or not password or not domain:
        st.sidebar.error("Fill in username, password and domain first.")
        return False
    return True


if fetch_clicked and _validate_conn():
    prog = st.sidebar.progress(0.0, text="Starting...")

    def _cb(done, total):
        prog.progress(done / total, text=f"Fetched {done}/{total} employees")

    try:
        with st.spinner("Authenticating and fetching attendance..."):
            result = api.get_late_comers_for_month(
                username=username,
                password=password,
                domain=domain,
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
            f"Found {len(result['employees'])} late-comer(s) "
            f"out of {result['all_employees_count']} employees."
        )
    except Exception as e:
        prog.empty()
        st.sidebar.error(f"Failed: {e}")


# ──────────────────────────────────────────────────────────
# HEADER
# ──────────────────────────────────────────────────────────

st.title("⏰ Late-Comer Warning Dashboard")
st.caption(
    "Review late arrivals, filter by department, pick who gets a warning, "
    "and send emails. Nothing is sent automatically — HR makes the final call."
)

result = st.session_state.result

if result is None:
    st.info(
        "👈 Configure greytHR credentials and the month in the sidebar, "
        "then click **Fetch late-comers**."
    )
    with st.expander("ℹ️ How lateness & tiers work"):
        st.markdown("""
**Late-arrival rule (Fixed cutoff mode — recommended)**
Anyone whose in-time is after the cutoff (e.g. 10:00 AM) is flagged as late,
regardless of their shift. Holidays, Week-Offs, and leave days are skipped.

**Late-arrival rule (Shift-relative mode)**
Each person is compared to their own shift start + a grace period.

**Warning tiers (by number of late days this month):**

| Late days | Tier | Email |
|---|---|---|
| 1 | 🟢 Normal | Gentle reminder |
| 2 | 🟡 Moderate | Formal warning |
| 3+ | 🔴 Strict | Final warning — further lateness → salary deduction |
        """)
    st.stop()


# ──────────────────────────────────────────────────────────
# BUILD BASE DATAFRAME
# ──────────────────────────────────────────────────────────

month_label = f"{calendar.month_name[int(month)]} {int(year)}"
all_employees = result["employees"]   # all late comers before filtering

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
            "_idx":        all_employees.index(e),  # keep reference to original
        })
    return pd.DataFrame(rows)

df_all = _make_df(all_employees)


# ──────────────────────────────────────────────────────────
# ① DEPARTMENT FILTER
# ──────────────────────────────────────────────────────────

st.subheader("🏢 Department Filter")

all_depts = result.get("departments") or sorted(df_all["Department"].dropna().unique().tolist())

if all_depts:
    col_f1, col_f2 = st.columns([2, 1])
    with col_f1:
        selected_depts = st.multiselect(
            "Show only these departments (leave blank = show all)",
            options=all_depts,
            default=[],
            placeholder="Select departments to include...",
        )
    with col_f2:
        exclude_mode = st.checkbox(
            "Exclude selected (instead of include)",
            value=False,
            help="Tick this to REMOVE the chosen departments from view.",
        )

    if selected_depts:
        if exclude_mode:
            mask = ~df_all["Department"].isin(selected_depts)
        else:
            mask = df_all["Department"].isin(selected_depts)
        df = df_all[mask].reset_index(drop=True)
        filtered_employees = [all_employees[i] for i in df["_idx"].tolist()]
    else:
        df = df_all.copy()
        filtered_employees = all_employees
else:
    st.info("No department data returned by the API — showing all employees.")
    df = df_all.copy()
    filtered_employees = all_employees

if df.empty:
    st.warning("No late-comers match the current department filter.")
    st.stop()

st.divider()


# ──────────────────────────────────────────────────────────
# ② INSIGHTS (after filter)
# ──────────────────────────────────────────────────────────

st.subheader("📊 Insights")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Late-comers (filtered)", len(df))
c2.metric("Total employees",  result["all_employees_count"])
c3.metric("🟢 Normal (1)",    int((df["_tier"] == "normal").sum()))
c4.metric("🟡 Moderate (2)",  int((df["_tier"] == "moderate").sum()))
c5.metric("🔴 Strict (3+)",   int((df["_tier"] == "strict").sum()))

col_chart, col_dept, col_top = st.columns(3)

with col_chart:
    st.markdown("**By tier**")
    tier_counts = (
        df["Tier"].value_counts()
        .reindex(["Normal Warning", "Moderate Warning", "Strict Warning"])
        .fillna(0).astype(int)
    )
    st.bar_chart(tier_counts, color="#4361ee")

with col_dept:
    st.markdown("**By department**")
    dept_counts = df["Department"].value_counts().head(10)
    if not dept_counts.empty:
        st.bar_chart(dept_counts, color="#7209b7")
    else:
        st.caption("No department data")

with col_top:
    st.markdown("**Top late-comers**")
    top = df.sort_values("Late Days", ascending=False).head(8)
    st.dataframe(
        top[["Name", "Department", "Late Days", "Tier"]],
        hide_index=True, use_container_width=True,
    )

st.divider()


# ──────────────────────────────────────────────────────────
# ③ EMPLOYEE SELECTION TABLE (checkboxes)
# ──────────────────────────────────────────────────────────

st.subheader("✅ Select who should receive a warning email")
st.caption(
    "Tick the employees you want to email. Use quick-select buttons to "
    "pre-select by tier. Only employees with a valid email can be sent to."
)

sel_df = df.copy()
sel_df.insert(0, "Send", False)
sel_df["Has Email"] = sel_df["Email"].str.len() > 0

# quick-select buttons
qb1, qb2, qb3, qb4, qb5 = st.columns(5)
if qb1.button("Select all"):          st.session_state._preselect = "all"
if qb2.button("🟢 Select Normal"):    st.session_state._preselect = "normal"
if qb3.button("🟡 Select Moderate"):  st.session_state._preselect = "moderate"
if qb4.button("🔴 Select Strict"):    st.session_state._preselect = "strict"
if qb5.button("Clear all"):           st.session_state._preselect = "none"

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
        "Send":      st.column_config.CheckboxColumn("Send", help="Email this person"),
        "Has Email": st.column_config.CheckboxColumn("Has Email", disabled=True),
    },
    disabled=["Emp No", "Name", "Email", "Department", "Late Days", "Tier", "Has Email"],
    key="selection_editor",
)

# resolve selection — map back to original employees list
selected_local_idx  = edited.index[edited["Send"] & edited["Has Email"]].tolist()
selected_orig_idx   = [df.iloc[i]["_idx"] for i in selected_local_idx]
selected_employees  = [all_employees[i] for i in selected_orig_idx]

missing = edited[edited["Send"] & ~edited["Has Email"]]
if not missing.empty:
    st.warning(
        "No email address — will be skipped: "
        + ", ".join(missing["Name"].tolist())
    )

st.info(f"**{len(selected_employees)}** employee(s) selected to receive a warning email.")

st.divider()


# ──────────────────────────────────────────────────────────
# ④ EMAIL SETTINGS
# ──────────────────────────────────────────────────────────

with st.expander("✏️ Edit email templates"):
    st.caption(
        "Placeholders: {name}, {late_count}, {month}, {company}, "
        "{hr_name}, {late_days_table}"
    )
    company      = st.text_input("Company name",      value="Our Company")
    hr_name      = st.text_input("HR signatory name", value="HR Team")
    include_table = st.checkbox("Include late-days table in email", value=True)

    for tier in ("normal", "moderate", "strict"):
        meta = emailer.TIER_META[tier]
        st.markdown(
            "<span class='tier-pill' style='background:" + meta["color"] + "'>"
            + meta["label"] + "</span>", unsafe_allow_html=True,
        )
        st.session_state.subjects[tier] = st.text_input(
            f"Subject ({tier})",
            value=st.session_state.subjects[tier],
            key=f"subj_{tier}",
        )
        st.session_state.bodies[tier] = st.text_area(
            f"Body HTML ({tier})",
            value=st.session_state.bodies[tier],
            height=180, key=f"body_{tier}",
        )
        st.markdown("---")


# ──────────────────────────────────────────────────────────
# ④-b  GMAIL OAUTH  (primary send method)
# ──────────────────────────────────────────────────────────

with st.expander("📧 Gmail — Sign in with Google (recommended)", expanded=True):

    st.markdown("""
<div class='info-box'>
<b>How it works:</b> Paste your Google OAuth Client ID & Secret below
(one-time setup, takes ~5 minutes). Then click <b>Sign in with Google</b>.
HR simply approves the popup — no app passwords, no SMTP, nothing else.
</div>
""", unsafe_allow_html=True)

    st.markdown("#### Step-by-step: Get your Client ID & Secret")
    st.markdown("""
1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. **Create a project** (or pick an existing one) → top-left dropdown → New Project
3. Left menu → **APIs & Services → Library** → search **Gmail API** → Enable it
4. Left menu → **APIs & Services → OAuth consent screen**
   - User type: **Internal** (if Google Workspace) or External
   - Fill app name (e.g. "HR Warning Tool"), support email → Save
5. Left menu → **APIs & Services → Credentials → + Create Credentials → OAuth Client ID**
   - Application type: **Web application**
   - Authorised redirect URIs: add `http://localhost:8501` *(and your Streamlit Cloud URL if deployed)*
   - Click Create → copy the **Client ID** and **Client Secret**
6. Paste them below ↓
    """)

    oa_col1, oa_col2 = st.columns(2)
    client_id     = oa_col1.text_input("Google OAuth Client ID",     key="oa_client_id")
    client_secret = oa_col2.text_input("Google OAuth Client Secret",
                                       type="password", key="oa_client_secret")
    redirect_uri  = st.text_input(
        "Redirect URI",
        value="http://localhost:8501",
        help="Must match exactly what you put in Google Cloud Console.",
        key="oa_redirect",
    )

    SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

    # ── Already signed in ──
    if st.session_state.gmail_service:
        st.success("✅ Signed in to Gmail — emails will be sent via Gmail API.")
        if st.button("Sign out / use different account"):
            st.session_state.gmail_creds   = None
            st.session_state.gmail_service = None
            st.rerun()

    # ── Handle OAuth callback (code in URL params) ──
    elif "code" in st.query_params and client_id and client_secret:
        try:
            flow = Flow.from_client_config(
                {
                    "web": {
                        "client_id":     client_id,
                        "client_secret": client_secret,
                        "redirect_uris": [redirect_uri],
                        "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
                        "token_uri":     "https://oauth2.googleapis.com/token",
                    }
                },
                scopes=SCOPES,
                redirect_uri=redirect_uri,
            )
            flow.fetch_token(code=st.query_params["code"])
            creds = flow.credentials
            st.session_state.gmail_creds   = creds
            st.session_state.gmail_service = gbuild("gmail", "v1", credentials=creds)
            # clear the code from URL
            st.query_params.clear()
            st.success("✅ Signed in! You can now send emails.")
            st.rerun()
        except Exception as e:
            st.error(f"OAuth callback failed: {e}")

    # ── Sign-in button ──
    else:
        if client_id and client_secret:
            if st.button("🔐 Sign in with Google", type="primary"):
                try:
                    flow = Flow.from_client_config(
                        {
                            "web": {
                                "client_id":     client_id,
                                "client_secret": client_secret,
                                "redirect_uris": [redirect_uri],
                                "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
                                "token_uri":     "https://oauth2.googleapis.com/token",
                            }
                        },
                        scopes=SCOPES,
                        redirect_uri=redirect_uri,
                    )
                    auth_url, _ = flow.authorization_url(
                        prompt="consent",
                        access_type="offline",
                    )
                    st.markdown(
                        f"**[👉 Click here to sign in with Google]({auth_url})**\n\n"
                        "After approving, Google will redirect you back here automatically.",
                    )
                except Exception as e:
                    st.error(f"Could not build auth URL: {e}")
        else:
            st.info("Enter your Client ID and Client Secret above to enable Gmail sign-in.")

    st.markdown("---")
    st.markdown("#### Prefer SMTP instead?")
    use_smtp_fallback = st.checkbox("Use SMTP (app password) instead of Gmail OAuth")

    if use_smtp_fallback:
        sc1, sc2 = st.columns(2)
        smtp_host  = sc1.text_input("SMTP host",  value="smtp.gmail.com")
        smtp_port  = sc2.number_input("Port",      value=587, step=1)
        smtp_user  = sc1.text_input("SMTP username (full email)", value="")
        smtp_pass  = sc2.text_input("App password", type="password")
        from_email = sc1.text_input("From email",  value="")
        use_tls    = sc2.checkbox("Use TLS (STARTTLS)", value=True)
    else:
        smtp_host = smtp_port = smtp_user = smtp_pass = from_email = use_tls = None


# ──────────────────────────────────────────────────────────
# ⑤ EMAIL PREVIEW
# ──────────────────────────────────────────────────────────

if selected_employees:
    with st.expander("👀 Preview an email"):
        names = [e["employee_name"] for e in selected_employees]
        pick  = st.selectbox("Preview for",
                             options=range(len(names)),
                             format_func=lambda i: names[i])
        emp  = selected_employees[pick]
        tier, subject, html = emailer.build_email(
            emp, month_label,
            company=company, hr_name=hr_name,
            subjects=st.session_state.subjects,
            bodies=st.session_state.bodies,
            include_table=include_table,
        )
        meta = emailer.TIER_META[tier]
        st.markdown(
            f"**To:** {emp['employee_email']}  |  **Tier:** "
            "<span class='tier-pill' style='background:" + meta["color"] + "'>"
            + meta["label"] + "</span>",
            unsafe_allow_html=True,
        )
        st.markdown(f"**Subject:** {subject}")
        st.components.v1.html(html, height=380, scrolling=True)


# ──────────────────────────────────────────────────────────
# ⑥ SEND
# ──────────────────────────────────────────────────────────

st.divider()
st.subheader("🚀 Send warning emails")

dry_run = st.checkbox(
    "Dry run (build emails but DON'T actually send)",
    value=True,
    help="Leave ON to test. Uncheck to send for real.",
)

confirm = st.checkbox(
    f"I have reviewed the {len(selected_employees)} selected employee(s) "
    f"and want to proceed."
)

send_clicked = st.button(
    "Send selected warning emails",
    type="primary",
    disabled=(len(selected_employees) == 0 or not confirm),
)

if send_clicked:
    # decide sending method
    gmail_svc   = st.session_state.gmail_service
    smtp_ready  = use_smtp_fallback and smtp_user and smtp_pass

    if not dry_run and not gmail_svc and not smtp_ready:
        st.error(
            "No sending method configured. Either sign in with Google (above) "
            "or fill in the SMTP settings."
        )
    else:
        send_log = []
        prog     = st.progress(0.0)

        for i, emp in enumerate(selected_employees, start=1):
            tier, subject, html = emailer.build_email(
                emp, month_label,
                company=company, hr_name=hr_name,
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
                elif gmail_svc:
                    emailer.send_via_gmail_api(
                        gmail_svc,
                        to_email=emp["employee_email"],
                        subject=subject,
                        html_body=html,
                        from_name=hr_name,
                    )
                    row["Status"] = "✅ Sent via Gmail"
                else:
                    emailer.send_email(
                        {
                            "host":       smtp_host,
                            "port":       smtp_port,
                            "username":   smtp_user,
                            "password":   smtp_pass,
                            "from_email": from_email or smtp_user,
                            "use_tls":    use_tls,
                        },
                        to_email=emp["employee_email"],
                        subject=subject,
                        html_body=html,
                        from_name=hr_name,
                    )
                    row["Status"] = "✅ Sent via SMTP"
            except Exception as e:
                row["Status"] = f"❌ Failed: {e}"

            send_log.append(row)
            prog.progress(i / len(selected_employees))

        st.session_state.send_log = send_log
        if dry_run:
            st.info("Dry run done. Review the log, then uncheck Dry run to send for real.")
        else:
            success = sum(1 for r in send_log if r["Status"].startswith("✅"))
            failed  = len(send_log) - success
            if failed:
                st.warning(f"Sent {success}, failed {failed}. See log below.")
            else:
                st.success(f"All {success} emails sent successfully! 🎉")


if st.session_state.send_log:
    st.markdown("#### Send log")
    log_df = pd.DataFrame(st.session_state.send_log)
    st.dataframe(log_df, hide_index=True, use_container_width=True)
    st.download_button(
        "⬇️ Download send log (CSV)",
        log_df.to_csv(index=False).encode("utf-8"),
        file_name=f"warning_email_log_{result['period']}.csv",
        mime="text/csv",
    )


# ──────────────────────────────────────────────────────────
# ⑦ EXPORT
# ──────────────────────────────────────────────────────────

with st.expander("📄 Full data & export"):
    st.dataframe(
        df.drop(columns=["_tier", "_idx"]),
        hide_index=True, use_container_width=True,
    )
    detail = []
    for e in filtered_employees:
        for d in e["late_days"]:
            detail.append({
                "Emp No":       e["employee_no"],
                "Name":         e["employee_name"],
                "Department":   e.get("department", ""),
                "Email":        e["employee_email"],
                "Date":         d["date"],
                "Day":          d["day_of_week"],
                "Shift Start":  d["shift_start"],
                "In Time":      d["in_time"],
                "Late By (min)":d["late_by_minutes"],
            })
    detail_df = pd.DataFrame(detail)
    st.download_button(
        "⬇️ Download detailed late-day log (CSV)",
        detail_df.to_csv(index=False).encode("utf-8"),
        file_name=f"late_days_detail_{result['period']}.csv",
        mime="text/csv",
    )
