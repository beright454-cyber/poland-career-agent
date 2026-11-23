import base64
import mimetypes
import os
from email.message import EmailMessage
from pathlib import Path
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
BASE_DIR = Path(__file__).resolve().parent.parent.parent
SIBLING_DIR = BASE_DIR.parent / "BEright_AutomationBEright_Automation"


def _resolve_auth_file(filename: str) -> Path:
    local_path = BASE_DIR / filename
    if local_path.exists():
        return local_path
    sibling_path = SIBLING_DIR / filename
    if sibling_path.exists():
        return sibling_path
    return local_path


class GmailResumeSender:
    def __init__(self):
        self.sender_email = os.getenv("SENDER_EMAIL", "beright454@gmail.com").strip()
        self.resume_rel_path = os.getenv("RESUME_PDF_PATH", "attachments/Subrata_Mondal_Resume.pdf").strip()

    def _get_service(self):
        token_path = _resolve_auth_file("token.json")
        creds_path = _resolve_auth_file("credentials.json")
        creds = None

        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                token_path.write_text(creds.to_json(), encoding="utf-8")
            else:
                if not creds_path.exists():
                    raise FileNotFoundError("credentials.json not found in project or BEright_Automation folder.")
                flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
                creds = flow.run_local_server(port=0)
                token_path.write_text(creds.to_json(), encoding="utf-8")

        return build("gmail", "v1", credentials=creds)

    def send_application_email(self, to_email: str, subject: str, body_text: str) -> dict:
        resume_file = BASE_DIR / self.resume_rel_path
        if not resume_file.exists():
            return {
                "ok": False,
                "error": f"Resume PDF not found at {resume_file}. Please save Subrata_Mondal_Resume.pdf in attachments folder first."
            }

        try:
            service = self._get_service()
            msg = EmailMessage()
            msg["To"] = to_email
            msg["From"] = f"Subrata Mondal <{self.sender_email}>"
            msg["Subject"] = subject
            msg.set_content(body_text)

            mime_type, _ = mimetypes.guess_type(str(resume_file))
            maintype, subtype = (mime_type or "application/pdf").split("/", 1)
            with open(resume_file, "rb") as f:
                msg.add_attachment(
                    f.read(),
                    maintype=maintype,
                    subtype=subtype,
                    filename="Subrata_Mondal_Resume.pdf",
                )

            encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
            sent = service.users().messages().send(userId="me", body={"raw": encoded}).execute()
            return {"ok": True, "message_id": sent.get("id", "")}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:180]}


gmail_resume_sender = GmailResumeSender()
