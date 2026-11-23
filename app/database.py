import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "poland_career_agent.db"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT,
                company_domain TEXT UNIQUE,
                job_title TEXT,
                city TEXT,
                job_url TEXT,
                hr_email TEXT,
                email_subject TEXT,
                cover_letter_body TEXT,
                status TEXT DEFAULT 'draft',
                rejection_reason TEXT DEFAULT '',
                created_at TEXT,
                sent_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_name TEXT,
                message TEXT,
                level TEXT DEFAULT 'INFO',
                created_at TEXT
            )
            """
        )


def is_company_seen(domain: str, job_url: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM job_applications WHERE company_domain = ? OR job_url = ? LIMIT 1",
            (domain, job_url),
        ).fetchone()
        return row is not None


def count_sent_today() -> int:
    prefix = today_str()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM job_applications WHERE status = 'sent' AND sent_at LIKE ?",
            (f"{prefix}%",),
        ).fetchone()
        return row["c"] if row else 0


def save_application_draft(
    company_name: str,
    company_domain: str,
    job_title: str,
    city: str,
    job_url: str,
    hr_email: str,
    email_subject: str,
    cover_letter_body: str,
):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO job_applications
            (company_name, company_domain, job_title, city, job_url, hr_email, email_subject, cover_letter_body, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)
            """,
            (
                company_name,
                company_domain,
                job_title,
                city,
                job_url,
                hr_email,
                email_subject,
                cover_letter_body,
                utc_now(),
            ),
        )


def log_event(agent_name: str, message: str, level: str = "INFO"):
    print(f"[{utc_now()}] [{agent_name}] [{level}] {message}")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO activity_logs (agent_name, message, level, created_at) VALUES (?, ?, ?, ?)",
            (agent_name, message, level, utc_now()),
        )


def get_dashboard_data() -> dict:
    with get_conn() as conn:
        total_drafts = conn.execute("SELECT COUNT(*) AS c FROM job_applications WHERE status = 'draft'").fetchone()["c"]
        total_sent = conn.execute("SELECT COUNT(*) AS c FROM job_applications WHERE status = 'sent'").fetchone()["c"]
        drafts = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM job_applications WHERE status = 'draft' ORDER BY id DESC LIMIT 30"
            ).fetchall()
        ]
        sent_list = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM job_applications WHERE status = 'sent' ORDER BY id DESC LIMIT 20"
            ).fetchall()
        ]
        logs = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM activity_logs ORDER BY id DESC LIMIT 30"
            ).fetchall()
        ]
    return {
        "total_drafts": total_drafts,
        "total_sent": total_sent,
        "sent_today": count_sent_today(),
        "drafts": drafts,
        "sent_list": sent_list,
        "logs": logs,
    }
