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


def summarize(msg: dict, body_chars: int) -> dict:
    """The small state Jev sees: sender, subject and the start of the body as plain text."""
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    plain = html_body = ""
    for p in _parts(msg["payload"]):
        data = p.get("body", {}).get("data")
        if not data:
            continue
        if p.get("mimeType") == "text/plain" and not plain:
            plain = _decode(data)
        elif p.get("mimeType") == "text/html" and not html_body:
            html_body = _decode(data)
    if not plain:
        plain = html.unescape(
            re.sub(r"<(style|script).*?</\1>|<[^>]+>", " ", html_body, flags=re.DOTALL | re.IGNORECASE)
        )
    return {
        "from": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "body": re.sub(r"\s+", " ", plain).strip()[:body_chars],
    }
