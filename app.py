"""
app.py
======
Streamlit frontend for the HR Late-Comer Warning tool.

Flow:
  1. HR enters credentials + picks the month (sidebar).
  2. Click "Fetch late-comers" -> calls greythr_api.get_late_comers_for_month.
  3. Dashboard shows insights (totals, tier breakdown, charts, full table).
  4. HR reviews each late-comer, ticks the checkbox for those to email.
  5. HR (optionally) edits the email templates and SMTP settings.
  6. HR clicks "Send selected warning emails" -> emails go out one by one,
     with a per-employee success/failure log.

Run:
  streamlit run app.py
"""

import calendar
from datetime import date

import pandas as pd
import streamlit as st

import greythr_api as api
import emailer


# ──────────────────────────────────────────────────────────
# PAGE CONFIG + LIGHT STYLING
# ──────────────────────────────────────────────────────────

st.set_page_config(
    page_title="HR Late-Comer Warnings",
    page_icon="⏰",
    layout="wide",
)

st.markdown(
    """
    <style>
      .metric-card {padding: 0; }
      .tier-pill {
        display:inline-block; padding:2px 10px; border-radius:12px;
        color:#fff; font-size:12px; font-weight:600;
      }
      .stDataFrame {font-size: 13px;}
    </style>
    """,
    unsafe_allow_html=True,
)


# ──────────────────────────────────────────────────────────
# SESSION STATE INIT
# ──────────────────────────────────────────────────────────

if "result" not in st.session_state:
    st.session_state.result = None
if "subjects" not in st.session_state:
    st.session_state.subjects = dict(emailer.SUBJECTS)
if "bodies" not in st.session_state:
    st.session_state.bodies = dict(emailer.BODIES)
if "send_log" not in st.session_state:
    st.session_state.send_log = []


# ──────────────────────────────────────────────────────────
# SIDEBAR: CONNECTION + MONTH
# ──────────────────────────────────────────────────────────

st.sidebar.title("⚙️ Setup")

with st.sidebar.expander("1. greytHR connection", expanded=True):
    username = st.text_input("API Username", key="gt_user")
    password = st.text_input("API Password", type="password", key="gt_pass")
    domain = st.text_input("Domain", placeholder="yourcompany.greythr.com", key="gt_domain")

with st.sidebar.expander("2. Month & rules", expanded=True):
    today = date.today()
    col_y, col_m = st.columns(2)
    year = col_y.number_input("Year", min_value=2020, max_value=2100,
                              value=today.year, step=1)
    month = col_m.selectbox(
        "Month",
        options=list(range(1, 13)),
        index=today.month - 1,
        format_func=lambda m: calendar.month_name[m],
    )
    grace = st.slider("Grace period (minutes)", 0, 60, 10,
                      help="An arrival within this many minutes after shift "
                           "start is NOT counted as late.")
    workers = st.slider("Parallel threads", 1, 15, 10)

fetch_clicked = st.sidebar.button("🔄 Fetch late-comers", type="primary",
                                  use_container_width=True)


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
                grace_minutes=int(grace),
                max_workers=int(workers),
                progress_cb=_cb,
            )
        st.session_state.result = result
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
# MAIN HEADER
# ──────────────────────────────────────────────────────────

st.title("⏰ Late-Comer Warning Dashboard")
st.caption(
    "Review late arrivals for the month, pick who gets a warning, and send "
    "emails. Nothing is sent automatically — the final send is your decision."
)

result = st.session_state.result

if result is None:
    st.info(
        "👈 Enter your greytHR connection details and a month in the sidebar, "
        "then click **Fetch late-comers** to begin."
    )
    with st.expander("How lateness & tiers are decided"):
        st.markdown(
            """
            **Late day** = a working day (not Holiday/WeekOff, not on leave)
            where the recorded in-time is after *shift start + grace period*.

            **Warning tiers** (by number of late days this month):

            | Late days | Tier | Email |
            |---|---|---|
            | 1 | 🟢 Normal | Gentle reminder |
            | 2 | 🟡 Moderate | Formal warning |
            | 3+ | 🔴 Strict | Final warning — further lateness → salary deduction |
            """
        )
    st.stop()


# ──────────────────────────────────────────────────────────
# We have a result — build the working dataframe
# ──────────────────────────────────────────────────────────

month_label = f"{calendar.month_name[int(month)]} {int(year)}"
employees = result["employees"]

if not employees:
    st.success(f"🎉 No late-comers found for {month_label}. Everyone was on time!")
    st.stop()

# Flat table for display
table_rows = []
for e in employees:
    tier = emailer.tier_for(e["late_count"])
    table_rows.append({
        "Employee ID": e["employee_id"],
        "Emp No": e["employee_no"],
        "Name": e["employee_name"],
        "Email": e["employee_email"],
        "Late Days": e["late_count"],
        "Tier": emailer.TIER_META[tier]["label"],
        "_tier": tier,
    })
df = pd.DataFrame(table_rows)


# ──────────────────────────────────────────────────────────
# INSIGHTS
# ──────────────────────────────────────────────────────────

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Late-comers", len(df))
c2.metric("Total employees", result["all_employees_count"])
c3.metric("🟢 Normal (1)", int((df["_tier"] == "normal").sum()))
c4.metric("🟡 Moderate (2)", int((df["_tier"] == "moderate").sum()))
c5.metric("🔴 Strict (3+)", int((df["_tier"] == "strict").sum()))

st.divider()

col_chart, col_top = st.columns([1, 1])

with col_chart:
    st.subheader("Late days by tier")
    tier_counts = (
        df["Tier"].value_counts()
        .reindex(["Normal Warning", "Moderate Warning", "Strict Warning"])
        .fillna(0)
        .astype(int)
    )
    st.bar_chart(tier_counts)

with col_top:
    st.subheader("Most frequent late-comers")
    top = df.sort_values("Late Days", ascending=False).head(10)
    st.dataframe(
        top[["Name", "Late Days", "Tier"]],
        hide_index=True,
        use_container_width=True,
    )

st.divider()


# ──────────────────────────────────────────────────────────
# SELECTION TABLE (checkboxes)
# ──────────────────────────────────────────────────────────

st.subheader("✅ Select who should receive a warning email")
st.caption(
    "Tick the employees you want to email. Use the buttons to select by tier. "
    "Rows with no email address can't be sent and are flagged below."
)

# build editable selection frame
sel_df = df.copy()
sel_df.insert(0, "Send", False)
sel_df["Has Email"] = sel_df["Email"].astype(bool)

# quick-select buttons
b1, b2, b3, b4, b5 = st.columns(5)
if b1.button("Select all"):
    st.session_state._preselect = "all"
if b2.button("Select Normal"):
    st.session_state._preselect = "normal"
if b3.button("Select Moderate"):
    st.session_state._preselect = "moderate"
if b4.button("Select Strict"):
    st.session_state._preselect = "strict"
if b5.button("Clear"):
    st.session_state._preselect = "none"

pre = st.session_state.get("_preselect")
if pre == "all":
    sel_df["Send"] = sel_df["Has Email"]
elif pre in ("normal", "moderate", "strict"):
    sel_df["Send"] = (sel_df["_tier"] == pre) & sel_df["Has Email"]
elif pre == "none":
    sel_df["Send"] = False

edited = st.data_editor(
    sel_df[["Send", "Emp No", "Name", "Email", "Late Days", "Tier", "Has Email"]],
    hide_index=True,
    use_container_width=True,
    column_config={
        "Send": st.column_config.CheckboxColumn("Send", help="Email this person"),
        "Has Email": st.column_config.CheckboxColumn("Has Email", disabled=True),
    },
    disabled=["Emp No", "Name", "Email", "Late Days", "Tier", "Has Email"],
    key="selection_editor",
)

# resolve selected employees (must have email)
selected_idx = edited.index[edited["Send"] & edited["Has Email"]].tolist()
selected_employees = [employees[i] for i in selected_idx]

missing_email = edited[edited["Send"] & ~edited["Has Email"]]
if not missing_email.empty:
    st.warning(
        "These selected employees have no email address and will be skipped: "
        + ", ".join(missing_email["Name"].tolist())
    )

st.info(f"**{len(selected_employees)}** employee(s) selected to receive an email.")


# ──────────────────────────────────────────────────────────
# TEMPLATES + SMTP (expanders)
# ──────────────────────────────────────────────────────────

with st.expander("✏️ Edit email templates"):
    st.caption(
        "Placeholders you can use: {name}, {late_count}, {month}, {company}, "
        "{hr_name}, {late_days_table}"
    )
    company = st.text_input("Company name", value="Our Company")
    hr_name = st.text_input("HR signatory name", value="HR Team")
    include_table = st.checkbox("Include table of late days in the email", value=True)

    for tier in ("normal", "moderate", "strict"):
        meta = emailer.TIER_META[tier]
        st.markdown(
            f"<span class='tier-pill' style='background:{meta['color']}'>"
            f"{meta['label']}</span>",
            unsafe_allow_html=True,
        )
        st.session_state.subjects[tier] = st.text_input(
            f"Subject ({tier})", value=st.session_state.subjects[tier],
            key=f"subj_{tier}",
        )
        st.session_state.bodies[tier] = st.text_area(
            f"Body HTML ({tier})", value=st.session_state.bodies[tier],
            height=180, key=f"body_{tier}",
        )
        st.markdown("---")

with st.expander("📤 SMTP settings (for sending)"):
    st.caption(
        "For Gmail, use an App Password (not your normal password) with host "
        "smtp.gmail.com and port 587."
    )
    sc1, sc2 = st.columns(2)
    smtp_host = sc1.text_input("SMTP host", value="smtp.gmail.com")
    smtp_port = sc2.number_input("Port", value=587, step=1)
    smtp_user = sc1.text_input("SMTP username", value="")
    smtp_pass = sc2.text_input("SMTP password / app password", type="password")
    from_email = sc1.text_input("From email", value="")
    use_tls = sc2.checkbox("Use TLS (STARTTLS)", value=True)


# ──────────────────────────────────────────────────────────
# PREVIEW
# ──────────────────────────────────────────────────────────

if selected_employees:
    with st.expander("👀 Preview an email"):
        names = [e["employee_name"] for e in selected_employees]
        pick = st.selectbox("Preview for", options=range(len(names)),
                            format_func=lambda i: names[i])
        emp = selected_employees[pick]
        tier, subject, html = emailer.build_email(
            emp, month_label,
            company=st.session_state.get("company", "Our Company") if False else company,
            hr_name=hr_name,
            subjects=st.session_state.subjects,
            bodies=st.session_state.bodies,
            include_table=include_table,
        )
        meta = emailer.TIER_META[tier]
        st.markdown(
            f"**To:** {emp['employee_email']}  |  **Tier:** "
            f"<span class='tier-pill' style='background:{meta['color']}'>"
            f"{meta['label']}</span>",
            unsafe_allow_html=True,
        )
        st.markdown(f"**Subject:** {subject}")
        st.components.v1.html(html, height=360, scrolling=True)


# ──────────────────────────────────────────────────────────
# SEND
# ──────────────────────────────────────────────────────────

st.divider()
st.subheader("🚀 Send warning emails")

dry_run = st.checkbox(
    "Dry run (build emails but DON'T actually send)", value=True,
    help="Leave this on to test the flow. Uncheck to send for real.",
)

confirm = st.checkbox(
    f"I have reviewed the {len(selected_employees)} selected employee(s) and "
    f"want to proceed."
)

send_clicked = st.button(
    "Send selected warning emails",
    type="primary",
    disabled=(len(selected_employees) == 0 or not confirm),
)

if send_clicked:
    smtp_cfg = {
        "host": smtp_host,
        "port": smtp_port,
        "username": smtp_user,
        "password": smtp_pass,
        "from_email": from_email or smtp_user,
        "use_tls": use_tls,
    }

    if not dry_run and not smtp_cfg["from_email"]:
        st.error("Set a From email / SMTP username before sending for real.")
    else:
        log = []
        prog = st.progress(0.0)
        for i, emp in enumerate(selected_employees, start=1):
            tier, subject, html = emailer.build_email(
                emp, month_label,
                company=company, hr_name=hr_name,
                subjects=st.session_state.subjects,
                bodies=st.session_state.bodies,
                include_table=include_table,
            )
            try:
                if dry_run:
                    log.append({
                        "Name": emp["employee_name"],
                        "Email": emp["employee_email"],
                        "Tier": emailer.TIER_META[tier]["label"],
                        "Status": "DRY RUN — not sent",
                    })
                else:
                    emailer.send_email(
                        smtp_cfg,
                        to_email=emp["employee_email"],
                        subject=subject,
                        html_body=html,
                        from_name=f"{hr_name}",
                    )
                    log.append({
                        "Name": emp["employee_name"],
                        "Email": emp["employee_email"],
                        "Tier": emailer.TIER_META[tier]["label"],
                        "Status": "✅ Sent",
                    })
            except Exception as e:
                log.append({
                    "Name": emp["employee_name"],
                    "Email": emp["employee_email"],
                    "Tier": emailer.TIER_META[tier]["label"],
                    "Status": f"❌ Failed: {e}",
                })
            prog.progress(i / len(selected_employees))

        st.session_state.send_log = log
        if dry_run:
            st.info("Dry run complete — review the log below, then uncheck "
                    "'Dry run' to send for real.")
        else:
            st.success("Sending complete. See the log below.")

if st.session_state.send_log:
    st.markdown("#### Send log")
    log_df = pd.DataFrame(st.session_state.send_log)
    st.dataframe(log_df, hide_index=True, use_container_width=True)
    st.download_button(
        "Download log as CSV",
        log_df.to_csv(index=False).encode("utf-8"),
        file_name=f"warning_email_log_{result['period']}.csv",
        mime="text/csv",
    )


# ──────────────────────────────────────────────────────────
# RAW DATA / EXPORT
# ──────────────────────────────────────────────────────────

with st.expander("📄 Full late-comer data & export"):
    st.dataframe(
        df.drop(columns=["_tier"]),
        hide_index=True,
        use_container_width=True,
    )
    # detailed long-form (one row per late day)
    detail = []
    for e in employees:
        for d in e["late_days"]:
            detail.append({
                "Emp No": e["employee_no"],
                "Name": e["employee_name"],
                "Email": e["employee_email"],
                "Date": d["date"],
                "Day": d["day_of_week"],
                "Shift Start": d["shift_start"],
                "In Time": d["in_time"],
                "Late By (min)": d["late_by_minutes"],
            })
    detail_df = pd.DataFrame(detail)
    st.download_button(
        "Download detailed late-day log (CSV)",
        detail_df.to_csv(index=False).encode("utf-8"),
        file_name=f"late_days_detail_{result['period']}.csv",
        mime="text/csv",
    )
