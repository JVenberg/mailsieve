"""Classify new inbox mail with Jev, label it, and apply time-delayed archive/trash actions."""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import gmail, jev
from .cache import open_cache
from .config import Config, Rule, first_match, matches, referenced

SEEN = "mailsieve/seen"
PENDING_WINDOW = "newer_than:3d"


def log(event: str, **fields) -> None:
    """One JSON line per decision; Cloud Run turns these into structured, searchable logs."""
    print(json.dumps({"severity": "INFO", "event": event, **fields}), flush=True)


def guard(cfg: Config) -> str:
    return "-{" + " ".join(cfg.protect) + "}"


def label_query(name: str) -> str:
    return 'label:"' + name.replace('"', "") + '"'


UNSUBSCRIBE = "Unsubscribe"


def action_label(rule: Rule) -> str:
    """Label marking mail whose rule confirmed its action. Archive/trash ones are hidden and the sweep
    acts on them; unsubscribe is a visible to-do label."""
    return UNSUBSCRIBE if rule.action == "unsubscribe" else f"mailsieve/{rule.name}"


def ask(cache, cfg: Config, mid: str, state: dict, qids: list[str]) -> tuple[dict, int]:
    """Answers for these questions, from the cache where possible. Returns (answers, newly asked count)."""
    fps = {q: cfg.fingerprint(q) for q in qids}
    answers = cache.get(mid, fps)
    missing = [q for q in qids if q not in answers]
    if missing:
        fresh = jev.ask(cfg.model, {q: cfg.questions[q] for q in missing}, state)
        cache.put(mid, fresh, fps)
        answers |= fresh
    return answers, len(missing)


def decide(cache, cfg: Config, mid: str, state: dict, known: dict) -> dict:
    """Route by the routing questions, then ask the matched rule's confirm questions only if it has an action."""
    answers, asked = ask(cache, cfg, mid, state, cfg.routing_questions)
    rule = first_match(cfg.rules, answers)
    confirmed = False
    if rule and rule.action:
        more, n = ask(cache, cfg, mid, state, sorted(referenced(rule.confirm)))
        answers, asked = answers | more, asked + n
        confirmed = matches(rule.confirm, answers | {"fact": known})
    return {
        "rule": rule,
        "confirmed": confirmed,
        "asked": asked,
        "answers": {q: a.get("noul", a.get("choice")) for q, a in answers.items()},
    }


def classify(
    svc,
    cfg: Config,
    query: str | None = None,
    limit: int | None = 200,
    dry_run: bool = False,
    workers: int = 1,
    cache=None,
) -> list[dict]:
    """Label each matching unseen message by the first matching rule, and mark it for the rule's
    action if its confirm check passes.

    Default query: inbox mail from the last few days. Seen messages get a hidden label so pushes and
    sweeps skip them; Jev answers are cached, so a dry run followed by a real run asks Jev once.
    """
    cache = cache or open_cache()
    query = query or f"in:inbox {PENDING_WINDOW}"
    ids = gmail.search(svc, f"{query} -{label_query(SEEN)} {guard(cfg)}", limit)
    hidden = [SEEN] + [action_label(r) for r in cfg.rules if r.action in ("archive", "trash")]
    visible = {r.label for r in cfg.rules if r.label} | {
        action_label(r) for r in cfg.rules if r.action == "unsubscribe"
    }
    names = hidden + sorted(visible)
    labels = {} if dry_run else gmail.label_ids(svc, names, hidden=tuple(hidden))
    local = threading.local()

    def one(mid: str) -> dict:
        if workers > 1 and not hasattr(local, "svc"):
            local.svc = gmail.service()
        s = local.svc if workers > 1 else svc
        msg = gmail.execute(s.users().messages().get(userId="me", id=mid, format="full"))
        state = gmail.summarize(msg, cfg.body_chars)
        d = decide(cache, cfg, mid, state, gmail.facts(msg))
        rule = d["rule"]
        result = {
            "id": mid,
            "from": state["from"],
            "subject": state["subject"],
            "rule": rule.name if rule else None,
            "label": rule.label if rule else None,
            "action": rule.action if d["confirmed"] else None,
            "answers": d["answers"],
            "jev_calls": d["asked"],
            "dry_run": dry_run,
        }
        log("classified", **result)
        if not dry_run:
            add = [labels[SEEN]]
            if rule and rule.label:
                add.append(labels[rule.label])
            if d["confirmed"]:
                add.append(labels[action_label(rule)])
            gmail.modify(s, [mid], add=add)
        return result

    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(one, ids))


def sweep(svc, cfg: Config, dry_run: bool = False, now: float | None = None) -> dict[str, int]:
    """Archive or trash mail whose action was confirmed, once it is older than its rule's `after`."""
    now = now or time.time()
    counts = {}
    for rule in cfg.rules:
        if rule.action not in ("archive", "trash"):
            continue
        cutoff = int(now) - rule.after_seconds
        where = "-in:trash" if rule.action == "trash" else "in:inbox"
        ids = gmail.search(svc, f"{label_query(action_label(rule))} {where} before:{cutoff} {guard(cfg)}")
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
