# HR Late-Comer Warning Tool

A Streamlit app for HR to review monthly late arrivals from greytHR and send
tiered warning emails — with HR making the final send decision (nothing is
automated).

## What it does

1. Pulls attendance (muster) for every employee for a chosen month from greytHR.
2. Flags a day as **late** when the recorded in-time is after *shift start +
   grace period*, on a working day (skips Holidays, Week-Offs, leave).
3. Counts late days per employee and assigns a warning tier:

   | Late days | Tier | Email |
   |-----------|------|-------|
   | 1   | 🟢 Normal   | Gentle reminder |
   | 2   | 🟡 Moderate | Formal warning |
   | 3+  | 🔴 Strict   | Final warning — further lateness → salary deduction |

4. Shows insights (totals, tier breakdown, top offenders, full table).
5. HR ticks checkboxes for who to email, can edit templates, preview, and send.
   A **Dry run** mode is on by default so you can test before sending for real.

## Files

- `app.py` — the Streamlit UI
- `greythr_api.py` — greytHR API client + lateness logic (no Streamlit)
- `emailer.py` — the 3 email templates + SMTP sending
- `requirements.txt`

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open the local URL it prints. Enter your greytHR API username, password
and domain in the sidebar, pick the month, and click **Fetch late-comers**.

## Email / SMTP

In the **SMTP settings** expander, enter your mail server details. For Gmail:
- Host: `smtp.gmail.com`, Port: `587`, TLS on
- Use a Gmail **App Password** (not your normal password)

Keep **Dry run** checked until you've previewed and are happy, then uncheck it
and tick the confirmation box to send.

## Host on Streamlit Community Cloud

1. Push these files to a GitHub repo.
2. Go to https://share.streamlit.io → New app → pick the repo and `app.py`.
3. It installs `requirements.txt` automatically and gives you a URL to share
   with the HR team.

**Important — secrets:** don't hard-code credentials. Either let HR type them
in the sidebar each session, or move them into Streamlit secrets
(`.streamlit/secrets.toml` / the "Secrets" box in Cloud settings) and read them
with `st.secrets[...]`. Same for SMTP credentials.

## Notes / things you may want to tune

- **Grace period** is adjustable in the sidebar (default 10 min).
- The "late" rule lives in `greythr_api.is_late()`. If your muster exposes a
  dedicated late-arrival flag, you can switch to using that instead of the
  time comparison.
- `_clean_time()` assumes times come as `HH:MM` or ISO `...THH:MM...`. If your
  tenant returns a different format, adjust there.
- Emails are sent one-by-one with a per-employee status log you can download.
