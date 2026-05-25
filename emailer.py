"""
emailer.py
==========
Warning-email templates (3 escalation tiers) + SMTP sending for the
HR Late-Comer Warning tool.

Tiers, keyed by number of late arrivals in the current month:
  1 late  -> NORMAL    (gentle reminder)
  2 late  -> MODERATE  (formal warning)
  3+ late -> STRICT    (final warning: further lateness => salary deduction)

Templates support simple {placeholder} substitution:
  {name}, {late_count}, {month}, {company}, {hr_name}, {late_days_table}
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# ──────────────────────────────────────────────────────────
# TIER SELECTION
# ──────────────────────────────────────────────────────────

def tier_for(late_count):
    """Map a late_count to a tier key."""
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
# Each template has a subject and an HTML body.
# ──────────────────────────────────────────────────────────

SUBJECTS = {
    "normal":   "Attendance Reminder - Late Arrival ({month})",
    "moderate": "Attendance Warning - Repeated Late Arrivals ({month})",
    "strict":   "Final Warning - Repeated Late Arrivals ({month})",
}

BODIES = {
    "normal": """\
<p>Dear {name},</p>

<p>This is a gentle reminder regarding your attendance for {month}. Our
records show that you reported late to work on <b>{late_count}</b> occasion
during this month.</p>

{late_days_table}

<p>We understand that occasional delays can happen. We kindly request that you
ensure timely arrival going forward, in line with your shift timings.</p>

<p>If there is a genuine reason behind this, please feel free to reach out to
the HR team.</p>

<p>Regards,<br>
{hr_name}<br>
HR Department, {company}</p>
""",

    "moderate": """\
<p>Dear {name},</p>

<p>This is a formal warning regarding your attendance for {month}. Our records
show that you reported late to work on <b>{late_count}</b> occasions during
this month.</p>

{late_days_table}

<p>Repeated late arrivals affect team productivity and are not in line with
company attendance policy. We request you to correct this pattern and ensure
that you report on time as per your shift schedule.</p>

<p>Please treat this as a formal notice. Continued late arrivals may lead to
further disciplinary action.</p>

<p>Regards,<br>
{hr_name}<br>
HR Department, {company}</p>
""",

    "strict": """\
<p>Dear {name},</p>

<p>This is a <b>final warning</b> regarding your attendance for {month}. Our
records show that you reported late to work on <b>{late_count}</b> occasions
during this month, despite earlier reminders.</p>

{late_days_table}

<p>This level of repeated lateness is a serious breach of company attendance
policy. Please be advised that <b>any further late arrival this month will
result in a deduction from your salary</b>, in accordance with policy.</p>

<p>We strongly urge you to report on time as per your shift schedule with
immediate effect. Should you wish to discuss this, please contact the HR team.</p>

<p>Regards,<br>
{hr_name}<br>
HR Department, {company}</p>
""",
}


# ──────────────────────────────────────────────────────────
# BUILD A MESSAGE FOR AN EMPLOYEE
# ──────────────────────────────────────────────────────────

def _late_days_table_html(late_days):
    """Render the employee's late days as a small HTML table."""
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
        f"{rows}"
        "</tbody></table>"
    )


def build_email(emp, month_label, company, hr_name,
                subjects=None, bodies=None, include_table=True):
    """
    Build (tier, subject, html_body) for a given employee summary dict.

    subjects / bodies allow the UI to override the defaults (edited templates).
    """
    subjects = subjects or SUBJECTS
    bodies = bodies or BODIES

    tier = tier_for(emp["late_count"])
    ctx = {
        "name": emp.get("employee_name", ""),
        "late_count": emp.get("late_count", 0),
        "month": month_label,
        "company": company,
        "hr_name": hr_name,
        "late_days_table": _late_days_table_html(emp.get("late_days", [])) if include_table else "",
    }

    subject = subjects[tier].format(**ctx)
    body = bodies[tier].format(**ctx)
    # wrap in a basic html shell
    html = (
        "<html><body style=\"font-family:Arial,Helvetica,sans-serif;"
        "font-size:14px;color:#222;line-height:1.55;\">"
        f"{body}</body></html>"
    )
    return tier, subject, html


# ──────────────────────────────────────────────────────────
# SMTP SENDING
# ──────────────────────────────────────────────────────────

def send_email(smtp_cfg, to_email, subject, html_body, from_name=None):
    """
    Send a single HTML email.

    smtp_cfg = {
        "host": "smtp.gmail.com",
        "port": 587,
        "username": "...",
        "password": "...",
        "from_email": "...",
        "use_tls": True,
    }

    Raises on failure; returns True on success.
    """
    msg = MIMEMultipart("alternative")
    from_email = smtp_cfg["from_email"]
    msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    host = smtp_cfg["host"]
    port = int(smtp_cfg["port"])
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
