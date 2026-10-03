"""Ask Jev (TypeSafe's decision model, via OpenRouter) the configured questions about one email."""

import json
import os
import time
import urllib.request

URL = "https://openrouter.ai/api/alpha/decisions"


def ask(model: str, questions: dict, state: dict, retries: int = 3) -> dict:
    body = json.dumps({"model": model, "questions": questions, "state": state}).encode()
    headers = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}", "Content-Type": "application/json"}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(URL, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.load(resp)["answers"]
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2**attempt)
