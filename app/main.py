import asyncio
import base64
import html
import os
from contextlib import asynccontextmanager
from email.message import EmailMessage
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from app.agents.career_agents import background_career_loop, run_poland_career_scan
from app.database import get_conn, init_db, log_event

load_dotenv()

SHEET_WEBHOOK_URL = os.getenv("GOOGLE_SHEET_WEBHOOK_URL", "").strip()
RESUME_PATH = os.path.join(os.getcwd(), "Subrata_Mondal_Resume.pdf")
TOKEN_PATH = os.path.join(os.getcwd(), "token.json")
DAILY_SAFE_CAP = 8


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(background_career_loop())
    yield
    task.cancel()


app = FastAPI(title="Poland Web Dev & HR Outreach Agent", lifespan=lifespan)


def _fetch_sheet_leads() -> list[dict]:
    if not SHEET_WEBHOOK_URL:
        return []
    try:
        resp = requests.get(SHEET_WEBHOOK_URL, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("leads", [])
    except Exception:
        pass
    return []


def _send_gmail_with_resume(to_email: str, subject: str, body: str) -> tuple[bool, str]:
    if not os.path.exists(TOKEN_PATH):
        return False, "token.json missing"
    try:
        creds = Credentials.from_authorized_user_file(TOKEN_PATH)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        service = build("gmail", "v1", credentials=creds)

        msg = EmailMessage()
        msg["To"] = to_email
        msg["From"] = "beright454@gmail.com"
        msg["Subject"] = subject
        msg.set_content(body)

        if os.path.exists(RESUME_PATH):
            with open(RESUME_PATH, "rb") as f:
                pdf_data = f.read()
            msg.add_attachment(
                pdf_data,
                maintype="application",
                subtype="pdf",
                filename="Subrata_Mondal_Resume.pdf",
            )

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return True, "Sent with Resume PDF"
    except Exception as exc:
        return False, str(exc)


def _get_db_stats_and_logs(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    local_drafts = []
    sent_today = 0
    total_sent = 0
    logs = []

    if "application_drafts" in tables:
        local_drafts = conn.execute(
            "SELECT * FROM application_drafts WHERE status = 'DRAFT' ORDER BY id DESC"
        ).fetchall()
        total_sent = conn.execute(
            "SELECT COUNT(*) FROM application_drafts WHERE status = 'SENT'"
        ).fetchone()[0]
        sent_today = conn.execute(
            "SELECT COUNT(*) FROM application_drafts WHERE status = 'SENT' AND date(sent_at) = date('now')"
        ).fetchone()[0]

    for log_table in ("activity_logs", "agent_logs", "logs"):
        if log_table in tables:
            logs = conn.execute(f"SELECT * FROM {log_table} ORDER BY id DESC LIMIT 35").fetchall()
            break

    return local_drafts, sent_today, total_sent, logs


@app.get("/", response_class=HTMLResponse)
def dashboard():
    conn = get_conn()
    local_drafts, sent_today, total_sent, logs = _get_db_stats_and_logs(conn)

    sheet_leads = _fetch_sheet_leads()
    sheet_ready = [r for r in sheet_leads if str(r.get("Status", "")).strip() == "READY_FOR_DRAFT"]
    sheet_drafted = [r for r in sheet_leads if str(r.get("Status", "")).strip() == "DRAFTED"]

    resume_ok = os.path.exists(RESUME_PATH)
    gmail_ok = os.path.exists(TOKEN_PATH)
    sheet_ok = bool(SHEET_WEBHOOK_URL)

    cards_html = ""

    for row in reversed(sheet_drafted):
        comp = html.escape(str(row.get("Company Name", "")))
        dom = html.escape(str(row.get("Domain", "")))
        email = html.escape(str(row.get("HR Email", "")))
        city = html.escape(str(row.get("City", "Poland")))
        url = html.escape(str(row.get("Job URL", "")))
        subj = html.escape(str(row.get("Email Subject", "Junior Web Developer & AI Vibe Coder - Subrata Mondal")))
        body = html.escape(str(row.get("Cover Letter Draft", "")))

        cards_html += f"""
        <div class="draft-card">
          <div class="draft-top">
            <div>
              <span class="badge badge-sheet">GOOGLE SHEET DRAFT</span>
              <h3>{comp} <span class="muted">({dom})</span></h3>
              <p class="meta">To: <strong>{email}</strong> • {city} • <a href="{url}" target="_blank">Source Page</a></p>
            </div>
          </div>
          <form method="post" action="/send-sheet-lead">
            <input type="hidden" name="hr_email" value="{email}" />
            <input type="hidden" name="company_name" value="{comp}" />
            <input type="hidden" name="company_domain" value="{dom}" />
            <label>Email Subject</label>
            <input type="text" name="subject" value="{subj}" required />
            <label>Cover Letter (Editable before sending)</label>
            <textarea name="body" rows="8" required>{body}</textarea>
            <div class="btn-row">
              <button type="submit" class="btn-send">Approve & Send (+Resume PDF)</button>
            </div>
          </form>
        </div>
        """

    for d in local_drafts:
        comp = html.escape(str(d["company_name"]))
        dom = html.escape(str(d["company_domain"]))
        email = html.escape(str(d["hr_email"]))
        city = html.escape(str(d["city"]))
        url = html.escape(str(d["job_url"]))
        subj = html.escape(str(d["email_subject"]))
        body = html.escape(str(d["cover_letter_body"]))
        cards_html += f"""
        <div class="draft-card">
          <div class="draft-top">
            <div>
              <span class="badge badge-local">LOCAL BRAIN DRAFT</span>
              <h3>{comp} <span class="muted">({dom})</span></h3>
              <p class="meta">To: <strong>{email}</strong> • {city} • <a href="{url}" target="_blank">Source Page</a></p>
            </div>
          </div>
          <form method="post" action="/send-local/{d['id']}">
            <label>Email Subject</label>
            <input type="text" name="subject" value="{subj}" required />
            <label>Cover Letter</label>
            <textarea name="body" rows="8" required>{body}</textarea>
            <div class="btn-row">
              <button type="submit" class="btn-send">Approve & Send (+Resume PDF)</button>
            </div>
          </form>
        </div>
        """

    for row in reversed(sheet_ready):
        comp = html.escape(str(row.get("Company Name", "")))
        dom = html.escape(str(row.get("Domain", "")))
        email = html.escape(str(row.get("HR Email", "")))
        url = html.escape(str(row.get("Job URL", "")))
        snippet = html.escape(str(row.get("Page Snippet", ""))[:260])
        cards_html += f"""
        <div class="draft-card ready-card">
          <div class="draft-top">
            <div>
              <span class="badge badge-ready">COLLECTED IN GOOGLE SHEET (READY FOR DRAFT)</span>
              <h3>{comp} <span class="muted">({dom})</span></h3>
              <p class="meta">Verified Email: <strong>{email}</strong> • <a href="{url}" target="_blank">Open Website</a></p>
              <p class="snippet">{snippet}...</p>
            </div>
          </div>
        </div>
        """

    if not cards_html:
        cards_html = '<p class="empty">No leads in queue yet. The 24/7 radar is actively scanning Polish companies and syncing to your Google Sheet.</p>'

    logs_html = ""
    for lg in logs:
        keys = lg.keys() if hasattr(lg, "keys") else []
        agent = html.escape(str(lg["agent_name"] if "agent_name" in keys else "Agent"))
        msg_txt = html.escape(str(lg["message"] if "message" in keys else ""))
        lvl = str(lg["level"] if "level" in keys else "INFO")
        ts = html.escape(str(lg["created_at"] if "created_at" in keys else ""))
        lvl_cls = "log-warn" if lvl == "WARN" else "log-info"
        logs_html += f"""
        <div class="log-item {lvl_cls}">
          <div class="log-msg"><strong>[{agent}]</strong> {msg_txt}</div>
          <div class="log-time">{ts}</div>
        </div>
        """

    total_queue = len(sheet_drafted) + len(local_drafts) + len(sheet_ready)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Poland Web Dev & HR Outreach Agent | Subrata Mondal</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0b0f19; color: #e2e8f0; padding: 20px; }}
    .topbar {{ display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 20px; }}
    .topbar h1 {{ font-size: 20px; color: #f8fafc; }}
    .actions {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .btn {{ border: none; padding: 10px 16px; border-radius: 8px; font-weight: 600; cursor: pointer; text-decoration: none; font-size: 13px; }}
    .btn-primary {{ background: linear-gradient(135deg, #2563eb, #7c3aed); color: #fff; }}
    .btn-sheet {{ background: linear-gradient(135deg, #059669, #10b981); color: #fff; }}
    .btn-send {{ background: linear-gradient(135deg, #ec4899, #8b5cf6); color: #fff; padding: 10px 18px; border: none; border-radius: 8px; font-weight: 600; cursor: pointer; }}
    .status-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; margin-bottom: 20px; }}
    .card {{ background: #131a2a; border: 1px solid #1e293b; border-radius: 12px; padding: 16px; }}
    .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 8px; background: #10b981; }}
    .dot.off {{ background: #ef4444; }}
    .kpi {{ font-size: 28px; font-weight: 700; color: #38bdf8; margin-top: 6px; }}
    .main-grid {{ display: grid; grid-template-columns: 1.45fr 1fr; gap: 18px; }}
    @media (max-width: 960px) {{ .main-grid {{ grid-template-columns: 1fr; }} }}
    .panel {{ background: #131a2a; border: 1px solid #1e293b; border-radius: 12px; padding: 18px; max-height: 78vh; overflow-y: auto; }}
    .panel h2 {{ font-size: 16px; margin-bottom: 14px; color: #f1f5f9; border-bottom: 1px solid #1e293b; padding-bottom: 10px; }}
    .draft-card {{ background: #0f1523; border: 1px solid #263248; border-radius: 10px; padding: 14px; margin-bottom: 14px; }}
    .ready-card {{ border-left: 4px solid #10b981; }}
    .badge {{ display: inline-block; font-size: 11px; font-weight: 700; padding: 3px 8px; border-radius: 6px; margin-bottom: 6px; }}
    .badge-sheet {{ background: #1e3a8a; color: #93c5fd; }}
    .badge-local {{ background: #4c1d95; color: #ddd6fe; }}
    .badge-ready {{ background: #064e3b; color: #6ee7b7; }}
    .meta {{ font-size: 13px; color: #94a3b8; margin: 4px 0 10px; }}
    .meta a {{ color: #60a5fa; }}
    .snippet {{ font-size: 12px; color: #cbd5e1; background: #161f33; padding: 8px; border-radius: 6px; }}
    label {{ display: block; font-size: 12px; color: #94a3b8; margin: 8px 0 4px; }}
    input[type="text"], textarea {{ width: 100%; background: #161f33; border: 1px solid #2d3b55; color: #f8fafc; border-radius: 8px; padding: 9px; font-size: 13px; font-family: inherit; }}
    .btn-row {{ margin-top: 10px; display: flex; justify-content: flex-end; }}
    .log-item {{ padding: 10px; border-bottom: 1px solid #1e293b; font-size: 13px; }}
    .log-warn strong {{ color: #fbbf24; }}
    .log-info strong {{ color: #38bdf8; }}
    .log-time {{ font-size: 11px; color: #64748b; margin-top: 3px; }}
    .muted {{ color: #64748b; font-weight: 400; font-size: 13px; }}
    .empty {{ color: #94a3b8; font-size: 14px; }}
  </style>
</head>
<body>
  <div class="topbar">
    <div>
      <h1>Poland Web Dev & HR Outreach Agent</h1>
      <p class="muted">Google Sheet Synced • Verified HR Emails • Auto Resume PDF Attachment</p>
    </div>
    <div class="actions">
      <form method="post" action="/write-sheet-drafts" style="display:inline;">
        <button type="submit" class="btn btn-sheet">Write Drafts in Sheet ({len(sheet_ready)} Ready)</button>
      </form>
      <form method="post" action="/scan-now" style="display:inline;">
        <button type="submit" class="btn btn-primary">Scan Polish Companies Now</button>
      </form>
    </div>
  </div>

  <div class="status-grid">
    <div class="card">
      <div><span class="dot {' ' if sheet_ok else 'off'}"></span><strong>Google Sheet Sync</strong></div>
      <p class="meta" style="margin-top:6px;">{'Connected (Live Two-Way Sync)' if sheet_ok else 'Webhook Missing'}</p>
    </div>
    <div class="card">
      <div><span class="dot {' ' if resume_ok else 'off'}"></span><strong>Resume PDF</strong></div>
      <p class="meta" style="margin-top:6px;">Subrata_Mondal_Resume.pdf {'Ready' if resume_ok else 'Missing'}</p>
    </div>
    <div class="card">
      <div><span class="dot {' ' if gmail_ok else 'off'}"></span><strong>Gmail OAuth2</strong></div>
      <p class="meta" style="margin-top:6px;">beright454@gmail.com</p>
    </div>
    <div class="card">
      <div><span class="dot"></span><strong>Daily Safe Cap</strong></div>
      <p class="meta" style="margin-top:6px;">{sent_today} / {DAILY_SAFE_CAP} Sent Today</p>
    </div>
  </div>

  <div class="status-grid">
    <div class="card">
      <div class="muted">Google Sheet + Local Queue</div>
      <div class="kpi">{total_queue} <span class="muted">({len(sheet_ready)} collected, {len(sheet_drafted) + len(local_drafts)} drafted)</span></div>
    </div>
    <div class="card">
      <div class="muted">Total Applications Sent (+Resume)</div>
      <div class="kpi" style="color:#10b981;">{total_sent}</div>
    </div>
  </div>

  <div class="main-grid">
    <div class="panel">
      <h2>Google Sheet Leads & HR Outreach Drafts</h2>
      {cards_html}
    </div>
    <div class="panel">
      <h2>Live Agent Activity & Sent History</h2>
      {logs_html}
    </div>
  </div>
</body>
</html>"""


@app.post("/scan-now")
async def scan_now():
    await asyncio.to_thread(run_poland_career_scan)
    return RedirectResponse(url="/", status_code=303)


@app.post("/write-sheet-drafts")
async def write_sheet_drafts():
    if SHEET_WEBHOOK_URL:
        try:
            resp = await asyncio.to_thread(
                requests.get, f"{SHEET_WEBHOOK_URL}?action=write_drafts", timeout=45
            )
            log_event("Sheet Writer Agent", f"Triggered Google Sheet Draft Writer: {resp.text[:140]}")
        except Exception as exc:
            log_event("Sheet Writer Agent", f"Sheet Writer error: {exc}", "WARN")
    return RedirectResponse(url="/", status_code=303)


@app.post("/send-sheet-lead")
async def send_sheet_lead(
    hr_email: str = Form(...),
    company_name: str = Form(...),
    company_domain: str = Form(...),
    subject: str = Form(...),
    body: str = Form(...),
):
    _, sent_today, _, _ = _get_db_stats_and_logs(get_conn())
    if sent_today >= DAILY_SAFE_CAP:
        log_event("Gmail Sender", "Daily safe cap reached (8/8). Try again tomorrow.", "WARN")
        return RedirectResponse(url="/", status_code=303)

    ok, info = await asyncio.to_thread(_send_gmail_with_resume, hr_email, subject, body)
    if ok:
        conn = get_conn()
        conn.execute(
            """INSERT INTO application_drafts
               (company_name, company_domain, job_title, city, job_url, hr_email, email_subject, cover_letter_body, status, sent_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'SENT', datetime('now'))""",
            (company_name, company_domain, "Junior Web Developer", "Poland", "", hr_email, subject, body),
        )
        conn.commit()
        log_event("Gmail Sender", f"SENT APPLICATION (+Resume PDF) to {company_name} ({hr_email})")
    else:
        log_event("Gmail Sender", f"Failed sending to {hr_email}: {info}", "WARN")
    return RedirectResponse(url="/", status_code=303)


@app.post("/send-local/{draft_id}")
async def send_local_draft(draft_id: int, subject: str = Form(...), body: str = Form(...)):
    conn = get_conn()
    row = conn.execute("SELECT * FROM application_drafts WHERE id = ?", (draft_id,)).fetchone()
    if not row:
        return RedirectResponse(url="/", status_code=303)

    _, sent_today, _, _ = _get_db_stats_and_logs(conn)
    if sent_today >= DAILY_SAFE_CAP:
        log_event("Gmail Sender", "Daily safe cap reached (8/8).", "WARN")
        return RedirectResponse(url="/", status_code=303)

    ok, info = await asyncio.to_thread(_send_gmail_with_resume, row["hr_email"], subject, body)
    if ok:
        conn.execute(
            "UPDATE application_drafts SET email_subject = ?, cover_letter_body = ?, status = 'SENT', sent_at = datetime('now') WHERE id = ?",
            (subject, body, draft_id),
        )
        conn.commit()
        log_event("Gmail Sender", f"SENT APPLICATION (+Resume PDF) to {row['company_name']} ({row['hr_email']})")
    else:
        log_event("Gmail Sender", f"Failed sending to {row['hr_email']}: {info}", "WARN")
    return RedirectResponse(url="/", status_code=303)