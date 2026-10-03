"""Classify new inbox mail with Jev, label it, and apply time-delayed archive/trash actions."""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import gmail, jev
from .config import Config, first_match

SEEN = "mailsieve/seen"
PENDING_WINDOW = "newer_than:3d"


def log(event: str, **fields) -> None:
    """One JSON line per decision; Cloud Run turns these into structured, searchable logs."""
    print(json.dumps({"severity": "INFO", "event": event, **fields}), flush=True)


def guard(cfg: Config) -> str:
    return "-{" + " ".join(cfg.protect) + "}"


def label_query(name: str) -> str:
    return 'label:"' + name.replace('"', "") + '"'


def classify(
    svc,
    cfg: Config,
    query: str | None = None,
    limit: int | None = 200,
    dry_run: bool = False,
    workers: int = 1,
) -> list[dict]:
    """Ask Jev about each matching unseen message and apply the first matching rule's label.

    Default query: inbox mail from the last few days. Seen messages get a hidden label so pushes,
    sweeps and backfills never pay for the same message twice, and an interrupted backfill resumes.
    """
    query = query or f"in:inbox {PENDING_WINDOW}"
    ids = gmail.search(svc, f"{query} -{label_query(SEEN)} {guard(cfg)}", limit)
    names = [SEEN] + sorted({r.label for r in cfg.rules if r.label})
    labels = {} if dry_run else gmail.label_ids(svc, names, hidden=(SEEN,))
    local = threading.local()

    def one(mid: str) -> dict:
        if workers > 1 and not hasattr(local, "svc"):
            local.svc = gmail.service()
        s = local.svc if workers > 1 else svc
        msg = gmail.execute(s.users().messages().get(userId="me", id=mid, format="full"))
        state = gmail.summarize(msg, cfg.body_chars)
        answers = jev.ask(cfg.model, cfg.questions, state)
        rule = first_match(cfg.rules, answers)
        result = {
            "id": mid,
            "from": state["from"],
            "subject": state["subject"],
            "rule": rule.name if rule else None,
            "label": rule.label if rule else None,
            "answers": {q: a.get("noul", a.get("choice")) for q, a in answers.items()},
            "dry_run": dry_run,
        }
        log("classified", **result)
        if not dry_run:
            add = [labels[SEEN]] + ([labels[rule.label]] if rule and rule.label else [])
            gmail.modify(s, [mid], add=add)
        return result

    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(one, ids))
    return results


def sweep(svc, cfg: Config, dry_run: bool = False, now: float | None = None) -> dict[str, int]:
    """Archive or trash labeled mail once it is older than its rule's `after`."""
    now = now or time.time()
    counts = {}
    for rule in cfg.rules:
        if not rule.action:
            continue
        cutoff = int(now) - rule.after_seconds
        where = "-in:trash" if rule.action == "trash" else "in:inbox"
        ids = gmail.search(svc, f"{label_query(rule.label)} {where} before:{cutoff} {guard(cfg)}")
        log("sweep", rule=rule.name, action=rule.action, count=len(ids), ids=ids, dry_run=dry_run)
        if ids and not dry_run:
            if rule.action == "trash":
                gmail.modify(svc, ids, add=["TRASH"])
            else:
                gmail.modify(svc, ids, remove=["INBOX"])
        counts[rule.name] = len(ids)
    return counts


def watch(svc) -> dict:
    """(Re)start Gmail push notifications for the inbox; they expire after 7 days."""
    body = {"topicName": os.environ["WATCH_TOPIC"], "labelIds": ["INBOX"], "labelFilterBehavior": "include"}
    resp = gmail.execute(svc.users().watch(userId="me", body=body))
    log("watch", **resp)
    return resp
