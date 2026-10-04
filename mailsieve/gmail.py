"""Gmail client and message helpers."""

import base64
import html
import json
import os
import re
import time
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
SECRETS = Path(__file__).parent.parent / ".secrets"
LOCAL_TOKEN = SECRETS / "token.json"


def login() -> None:
    """Browser OAuth flow with the client in .secrets/credentials.json; writes .secrets/token.json."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(SECRETS / "credentials.json"), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    LOCAL_TOKEN.write_text(creds.to_json())
    LOCAL_TOKEN.chmod(0o600)


def service():
    """GMAIL_TOKEN (authorized-user JSON, from Secret Manager) in Cloud Run; .secrets/token.json locally."""
    info = json.loads(os.environ["GMAIL_TOKEN"]) if "GMAIL_TOKEN" in os.environ else json.loads(LOCAL_TOKEN.read_text())
    creds = Credentials.from_authorized_user_info(info)
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def execute(request):
    for attempt in range(6):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status not in (429, 500, 503) or attempt == 5:
                raise
            time.sleep(2**attempt)


def search(svc, query: str, limit: int | None = None) -> list[str]:
    ids, token = [], None
    while limit is None or len(ids) < limit:
        resp = execute(svc.users().messages().list(userId="me", q=query, maxResults=500, pageToken=token))
        ids += [m["id"] for m in resp.get("messages", [])]
        token = resp.get("nextPageToken")
        if not token:
            break
    return ids[:limit]


def label_ids(svc, names: list[str], hidden: tuple[str, ...] = ()) -> dict[str, str]:
    """Label name -> id, creating missing labels; `hidden` ones stay out of the Gmail sidebar."""
    existing = {lb["name"]: lb["id"] for lb in execute(svc.users().labels().list(userId="me"))["labels"]}
    for name in names:
        if name not in existing:
            body = {"name": name, "labelListVisibility": "labelHide" if name in hidden else "labelShow"}
            existing[name] = execute(svc.users().labels().create(userId="me", body=body))["id"]
    return {n: existing[n] for n in names}


def modify(svc, ids: list[str], add: list[str] = (), remove: list[str] = ()) -> None:
    for i in range(0, len(ids), 1000):
        body = {"ids": ids[i : i + 1000], "addLabelIds": list(add), "removeLabelIds": list(remove)}
        execute(svc.users().messages().batchModify(userId="me", body=body))


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")


def _parts(payload: dict):
    yield payload
    for p in payload.get("parts", []):
        yield from _parts(p)


STATE_VERSION = 3
UNSUB_LINK = re.compile(
    r"href=[\"'][^\"']*(unsubscribe|opt-?out|email-?preferences|manage-?preferences)"
    r"|>\s*(unsubscribe|opt[ -]?out|manage (your )?(email )?preferences)\b",
    re.IGNORECASE,
)
UNSUB_MAILTO = re.compile(r"mailto:[^\s\"'>]*\?subject=[^\"'>]*unsubscribe", re.IGNORECASE)
JUNK_PLAIN = re.compile(r"bodyplain|html (e-?mail )?reader|^[^{}]{0,200}\{[^}]*:[^}]*\}", re.IGNORECASE)
FILLER = re.compile("[\u00ad\u034f\u200b-\u200f\u2060\ufeff]")
CATEGORIES = {"CATEGORY_PERSONAL", "CATEGORY_SOCIAL", "CATEGORY_PROMOTIONS", "CATEGORY_UPDATES", "CATEGORY_FORUMS"}


def _bodies(msg: dict) -> tuple[str, str]:
    """First text/plain and text/html parts."""
    plain = html_ = ""
    for p in _parts(msg["payload"]):
        data = p.get("body", {}).get("data")
        if data and p.get("mimeType") == "text/plain" and not plain:
            plain = _decode(data)
        elif data and p.get("mimeType") == "text/html" and not html_:
            html_ = _decode(data)
    return plain, html_


def _html_text(html_: str) -> str:
    return html.unescape(re.sub(r"<(head|style|script).*?</\1>|<[^>]+>", " ", html_, flags=re.DOTALL | re.IGNORECASE))


def body_text(msg: dict) -> str:
    """Plain text body, taken from the HTML when the text/plain part is missing or junk."""
    plain, html_ = _bodies(msg)
    plain = re.sub(r"\s+", " ", FILLER.sub("", plain)).strip()
    if html_ and (len(plain) < 30 or JUNK_PLAIN.search(plain[:300])):
        plain = re.sub(r"\s+", " ", FILLER.sub("", _html_text(html_))).strip()
    return plain


def unsubscribe_via(msg: dict) -> str:
    """gmail: one-click List-Unsubscribe (Gmail's button, instant); email: a mailto address to write to;
    link: a web page to visit; none."""
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    header = headers.get("list-unsubscribe", "")
    if "list-unsubscribe-post" in headers and "<http" in header:
        return "gmail"
    if "<mailto:" in header:
        return "email"
    if "<http" in header:
        return "link"
    plain, html_ = _bodies(msg)
    if UNSUB_MAILTO.search(html_ + plain):
        return "email"
    if UNSUB_LINK.search(html_) or "unsubscribe" in plain.lower():
        return "link"
    return "none"


def facts(msg: dict) -> dict:
    """Things code can tell for certain, used by rule conditions but never asked of Jev."""
    return {"unsubscribe": unsubscribe_via(msg)}


def summarize(msg: dict, body_chars: int) -> dict:
    """The small state Jev sees: envelope hints plus the start of the body as plain text.

    Bump STATE_VERSION when this changes so cached answers about the old state are re-asked.
    """
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    attachments = [p["filename"] for p in _parts(msg["payload"]) if p.get("filename")]
    category = next((c for c in msg.get("labelIds", []) if c in CATEGORIES), "")
    return {
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "gmail_category": category.removeprefix("CATEGORY_").lower(),
        "bulk_mail": bool(headers.get("list-unsubscribe") or headers.get("list-id")),
        "mailing_list": headers.get("list-id", ""),
        "attachments": attachments,
        "body": body_text(msg)[:body_chars],
    }
