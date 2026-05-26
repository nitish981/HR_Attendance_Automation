"""
emailer.py
==========
Warning-email templates (3 tiers) + two sending backends:
  1. Gmail OAuth  — send via Gmail API using an OAuth2 access token
                    (no SMTP, no app passwords, just "Sign in with Google")
  2. SMTP fallback — original SMTP path kept as optional fallback

Tier logic (by late_count in the current month):
  1   -> normal   (gentle reminder)
  2   -> moderate (formal warning)
  3+  -> strict   (final warning: salary deduction)
"""

import smtplib
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# ──────────────────────────────────────────────────────────
# TIER SELECTION
# ──────────────────────────────────────────────────────────

def tier_for(late_count):
    if late_count <= 1:
        return "normal"
    if late_count == 2:
        return "moderate"
    return "strict"


TIER_META = {
    "normal":   {"label": "Normal Warning",   "color": "#2f9e44"},
    "moderate": {"label": "Moderate Warning",  "color": "#e8a317"},
    "strict":   {"label": "Strict Warning",    "color": "#c92a2a"},
}


# ──────────────────────────────────────────────────────────
# DEFAULT TEMPLATES
# ──────────────────────────────────────────────────────────

SUBJECTS = {
    "normal":   "Attendance Reminder - Late Arrival ({month})",
    "moderate": "Attendance Warning - Repeated Late Arrivals ({month})",
    "strict":   "Final Warning - Repeated Late Arrivals ({month})",
}

BODIES = {
    "normal": """\
<p>Dear {name},</p>
<p>This is a gentle reminder regarding your attendance for <b>{month}</b>. Our
records show that you reported late to work on <b>{late_count}</b> occasion
this month.</p>
{late_days_table}
<p>We understand occasional delays can happen. We kindly request that you
ensure timely arrival going forward, in line with your shift timings.</p>
<p>If there is a genuine reason, please feel free to reach out to the HR team.</p>
<p>Regards,<br>{hr_name}<br>HR Department, {company}</p>
""",
    "moderate": """\
<p>Dear {name},</p>
<p>This is a formal warning regarding your attendance for <b>{month}</b>. Our
records show that you reported late on <b>{late_count}</b> occasions this month.</p>
{late_days_table}
<p>Repeated late arrivals affect team productivity and are not in line with
company attendance policy. Please correct this pattern and ensure you report
on time as per your shift schedule.</p>
<p>Please treat this as a formal notice. Continued late arrivals may lead to
further disciplinary action.</p>
<p>Regards,<br>{hr_name}<br>HR Department, {company}</p>
""",
    "strict": """\
<p>Dear {name},</p>
<p>This is a <b>final warning</b> regarding your attendance for <b>{month}</b>.
Our records show that you reported late on <b>{late_count}</b> occasions this
month, despite earlier reminders.</p>
{late_days_table}
<p>This is a serious breach of company attendance policy. Please be advised
that <b>any further late arrival this month will result in a deduction from
your salary</b>, in accordance with policy.</p>
<p>We strongly urge you to report on time with immediate effect. Should you
wish to discuss this, please contact the HR team.</p>
<p>Regards,<br>{hr_name}<br>HR Department, {company}</p>
""",
}


# ──────────────────────────────────────────────────────────
# LATE DAYS TABLE
# ──────────────────────────────────────────────────────────

def _late_days_table_html(late_days):
    if not late_days:
        return ""
    rows = "".join(
        f"<tr>"
        f"<td style='padding:4px 10px;border:1px solid #ddd;'>{d.get('date','')}</td>"
        f"<td style='padding:4px 10px;border:1px solid #ddd;'>{d.get('day_of_week','')}</td>"
        f"<td style='padding:4px 10px;border:1px solid #ddd;'>{d.get('shift_start','')}</td>"
        f"<td style='padding:4px 10px;border:1px solid #ddd;'>{d.get('in_time','')}</td>"
        f"<td style='padding:4px 10px;border:1px solid #ddd;'>{d.get('late_by_minutes','')} min</td>"
        f"</tr>"
        for d in late_days
    )
    return (
        "<table style='border-collapse:collapse;margin:12px 0;font-size:13px;'>"
        "<thead><tr style='background:#f1f3f5;'>"
        "<th style='padding:4px 10px;border:1px solid #ddd;text-align:left;'>Date</th>"
        "<th style='padding:4px 10px;border:1px solid #ddd;text-align:left;'>Day</th>"
        "<th style='padding:4px 10px;border:1px solid #ddd;text-align:left;'>Shift Start</th>"
        "<th style='padding:4px 10px;border:1px solid #ddd;text-align:left;'>In Time</th>"
        "<th style='padding:4px 10px;border:1px solid #ddd;text-align:left;'>Late By</th>"
        "</tr></thead><tbody>"
        f"{rows}</tbody></table>"
    )


# ──────────────────────────────────────────────────────────
# BUILD EMAIL
# ──────────────────────────────────────────────────────────

def build_email(emp, month_label, company, hr_name,
                subjects=None, bodies=None, include_table=True):
    subjects = subjects or SUBJECTS
    bodies   = bodies   or BODIES
    tier = tier_for(emp["late_count"])
    ctx  = {
        "name":            emp.get("employee_name", ""),
        "late_count":      emp.get("late_count", 0),
        "month":           month_label,
        "company":         company,
        "hr_name":         hr_name,
        "late_days_table": _late_days_table_html(emp.get("late_days", [])) if include_table else "",
    }
    subject = subjects[tier].format(**ctx)
    body    = bodies[tier].format(**ctx)
    html = (
        "<html><body style=\"font-family:Arial,Helvetica,sans-serif;"
        "font-size:14px;color:#222;line-height:1.6;\">"
        f"{body}</body></html>"
    )
    return tier, subject, html


# ──────────────────────────────────────────────────────────
# GMAIL API SEND  (OAuth — no SMTP, no app password)
# ──────────────────────────────────────────────────────────

def send_via_gmail_api(gmail_service, to_email, subject, html_body, from_name=None):
    """
    Send using the Gmail API (google-api-python-client).
    gmail_service = googleapiclient.discovery.build('gmail','v1',credentials=...)
    """
    msg = MIMEMultipart("alternative")
    msg["To"]      = to_email
    msg["Subject"] = subject
    if from_name:
        msg["From"] = from_name
    msg.attach(MIMEText(html_body, "html"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    gmail_service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()
    return True


# ──────────────────────────────────────────────────────────
# SMTP SEND  (fallback)
# ──────────────────────────────────────────────────────────

def send_email(smtp_cfg, to_email, subject, html_body, from_name=None):
    msg = MIMEMultipart("alternative")
    from_email = smtp_cfg["from_email"]
    msg["From"]    = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"]      = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    host    = smtp_cfg["host"]
    port    = int(smtp_cfg["port"])
    use_tls = smtp_cfg.get("use_tls", True)

    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        if use_tls:
            server.starttls()
    try:
        if smtp_cfg.get("username"):
            server.login(smtp_cfg["username"], smtp_cfg["password"])
        server.sendmail(from_email, [to_email], msg.as_string())
    finally:
        server.quit()
    return True
