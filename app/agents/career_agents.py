import asyncio
import json
import os
import re
import time
import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types
from app.brain import MASTER_CANDIDATE_BRAIN
from app.database import is_company_seen, log_event, save_application_draft
from app.tools.poland_hr_finder import poland_hr_finder

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
SHEET_WEBHOOK_URL = os.getenv("GOOGLE_SHEET_WEBHOOK_URL", "https://script.google.com/macros/s/AKfycbxuEFPVktWqvTVOQsC6iHFmHv9VRja1akSVkPg3AnHq2hxL7IOt9R9se_wg1bb0GzI/exec").strip()
FALLBACK_CHAIN = ["gemini-2.5-flash-lite", "gemini-2.0-flash-lite", "gemini-2.5-flash", "gemini-4-hydrogen"]
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

SCAN_INTERVAL_SECONDS = 120

RELEVANT_KEYWORDS = (
    "web", "software", "developer", "frontend", "front-end", "backend", "full-stack",
    "fullstack", "javascript", "python", "html", "css", "react", "wordpress", "php",
    "e-commerce", "ecommerce", "it ", "it,", "it.", "programist", "stron", "aplikacj",
    "digital", "agencja", "automatyz", "automation", "crm", "erp", "tech", "system"
)


def _passes_local_tech_filter(title: str, page_text: str) -> bool:
    blob = f"{title} {page_text}".lower()
    return any(kw in blob for kw in RELEVANT_KEYWORDS)


def _push_lead_to_google_sheet(payload: dict) -> bool:
    if not SHEET_WEBHOOK_URL:
        return False
    try:
        resp = requests.post(SHEET_WEBHOOK_URL, json=payload, timeout=12)
        return resp.status_code == 200
    except Exception as exc:
        log_event("Agent 03: Sheet Sync", f"Google Sheet sync warning: {exc}", "WARN")
        return False


def evaluate_and_draft_application(candidate_job: dict, hr_email: str, page_text: str) -> dict:
    if not client:
        return {"is_suitable": True, "quota_deferred": True}

    prompt = f"""
You are an expert European Tech Career Copywriter writing on behalf of Subrata Mondal for a company in Poland.
Read Subrata's complete honest profile and project history below:
{MASTER_CANDIDATE_BRAIN}

Now evaluate this company found in Poland:
- Company Name: {candidate_job['company_name']}
- Company Domain: {candidate_job['company_domain']}
- Page Title: {candidate_job['page_title']}
- Verified HR/Contact Email: {hr_email}
- Page Snippet/Content: {page_text[:1500]}

TASKS:
1. Determine if this is a legitimate company, software house, digital agency, e-commerce, or tech/business firm in Poland where a Junior Web Developer & Automation Builder could add value.
2. REJECT (set is_suitable = false) ONLY if it strictly demands 4+ years of senior experience, is a recruitment agency spam page, or is a government/university portal.
3. If suitable, write a warm, confident, human-sounding cover letter email (150-190 words) in polished English (or fluent Polish if the page strictly requires Polish).
- NEVER start with "I am writing to express my interest..." or "Dear Sir/Madam, I hope this email finds you well."
- Keep the English simple, honest, natural, and direct (140-170 words).
- Introduce Subrata as a Junior Web Developer and AI Vibe Coder based in Lowicz, Poland (open to Warsaw, Lodz & Remote). Explain clearly that he understands web languages and system logic well (HTML5, CSS3, JavaScript, Python, Google Apps Script, REST APIs) and knows how to leverage modern AI tools to build, debug, and ship working web apps and automations fast.
- Mention 2 real tools he built using this workflow (this automated Python FastAPI + Google Sheets career outreach pipeline, Google Sheets CRM/automation dashboards, or bilingual web portals) and ask for a quick 15-minute intro chat or practical test task, noting his Resume PDF is attached.
- Close cleanly with his contact details:
  Subrata Mondal
  Location: Lowicz, Poland (Open to Warsaw, Lodz & Remote)
  Phone / WhatsApp: +48 510 820 441
  Portfolio: https://webdev5047.github.io/potfolieo/
  LinkedIn: https://www.linkedin.com/in/subrata-mondal-30151725a/

Return ONLY valid JSON:
{{
  "is_suitable": true or false,
  "reason": "Short explanation if rejected",
  "clean_company_name": "Clean Company Name",
  "job_title": "Junior Web Developer & Automation Specialist",
  "city": "Warsaw, Lodz, or Poland (Remote/Hybrid)",
  "email_subject": "Junior Web Developer & AI Vibe Coder - Subrata Mondal",
  "cover_letter_body": "Full ready-to-send email body text"
}}
"""
    last_error = ""
    for idx, model_name in enumerate(FALLBACK_CHAIN):
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.5, max_output_tokens=800),
            )
            raw = (resp.text or "").strip()
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                return json.loads(match.group(0))
        except Exception as exc:
            last_error = str(exc)
            if any(code in last_error for code in ["503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "404", "NOT_FOUND"]):
                time.sleep(1.0)
                continue
            break

    # If all Gemini models hit quota/unavailable, keep the verified lead so Google Sheet Writer drafts it later!
    return {"is_suitable": True, "quota_deferred": True, "reason": last_error}


def run_poland_career_scan() -> dict:
    log_event("Agent 01: Poland Radar", "Scanning Warsaw, Lodz & all-Poland companies...")
    companies = poland_hr_finder.search_hiring_companies(max_results=25)
    log_event("Agent 01: Poland Radar", f"Found {len(companies)} candidate company domains to inspect.")

    drafts_created = 0

    for item in companies:
        dom = item["company_domain"]
        url = item["job_url"]
        if is_company_seen(dom, url):
            continue

        log_event("Agent 02: HR Verifier", f"Inspecting {dom} (+ /kariera, /kontakt, mailto)...")
        hr_info = poland_hr_finder.inspect_company_and_hr(url, dom)
        if not hr_info.get("ok"):
            log_event("Agent 02: HR Verifier", f"Skipped {dom}: {hr_info.get('reason')}", "WARN")
            time.sleep(1.0)
            continue

        hr_email = hr_info["hr_email"]
        page_text = hr_info.get("page_text", "")

        if not _passes_local_tech_filter(item.get("page_title", ""), page_text):
            log_event("Agent 02: HR Verifier", f"Skipped {dom} ({hr_email}): Non-tech business")
            continue

        log_event("Agent 02: HR Verifier", f"VERIFIED TECH LEAD: {dom} -> {hr_email}")
        ai_res = evaluate_and_draft_application(item, hr_email, page_text)

        if not ai_res.get("is_suitable"):
            log_event("Agent 03: Brain Writer", f"Skipped {dom}: {ai_res.get('reason', 'Not suitable')}")
            continue

        clean_name = ai_res.get("clean_company_name") or item["company_name"]
        job_title = ai_res.get("job_title") or "Junior Web Developer & Automation Specialist"
        city = ai_res.get("city") or "Poland"
        subject = ai_res.get("email_subject") or "Junior Web Developer & AI Vibe Coder - Subrata Mondal"
        body = ai_res.get("cover_letter_body") or ""

        # Always save verified lead to Google Sheet (with or without draft body)
        synced = _push_lead_to_google_sheet({
            "company_name": clean_name,
            "company_domain": dom,
            "hr_email": hr_email,
            "city": city,
            "job_url": url,
            "page_snippet": page_text[:1000],
            "email_subject": subject,
            "cover_letter_body": body,
        })
        if synced:
            log_event("Agent 03: Sheet Sync", f"SAVED TO GOOGLE SHEET: {clean_name} ({hr_email})")

        # Also save to local dashboard if draft was generated immediately
        if body:
            save_application_draft(
                company_name=clean_name,
                company_domain=dom,
                job_title=job_title,
                city=city,
                job_url=url,
                hr_email=hr_email,
                email_subject=subject,
                cover_letter_body=body,
            )
            drafts_created += 1
            log_event("Agent 03: Brain Writer", f"NEW HR DRAFT READY ON DASHBOARD: {clean_name} -> {hr_email}")

        if drafts_created >= 3:
            break

    return {"drafts_created": drafts_created}


async def background_career_loop():
    log_event("Agent 01: Poland Radar", "24/7 Radar + Google Sheet Sync started.")
    await asyncio.sleep(3)
    while True:
        try:
            await asyncio.to_thread(run_poland_career_scan)
        except Exception as exc:
            log_event("Agent 01: Poland Radar", f"Cycle recovered safely: {exc}", "WARN")
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)