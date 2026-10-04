"""Load rules.yaml and evaluate rule conditions against Jev answers."""

import hashlib
import json
import operator
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .gmail import STATE_VERSION

RULES_PATH = Path(__file__).parent / "rules.yaml"
OPS = {">=": operator.ge, ">": operator.gt, "<=": operator.le, "<": operator.lt}
CUTOFF = re.compile(r"^\s*(>=|>|<=|<)\s*([0-9.]+)\s*$")
ACTIONS = {"archive", "trash", "unsubscribe"}
FACTS = {"unsubscribe": {"gmail", "email", "link", "none"}, "mixed_sender": {True, False}}


@dataclass(frozen=True)
class Rule:
    name: str
    when: dict
    label: str | None = None
    action: str | None = None
    after_seconds: int | None = None
    confirm: dict = field(default_factory=dict)
    label_on: str = "match"


@dataclass(frozen=True)
class Config:
    model: str
    body_chars: int
    protect: list[str]
    questions: dict
    rules: list[Rule]
    mixed_sender_types: tuple[str, ...] = ()

    @property
    def routing_questions(self) -> list[str]:
        """Asked about every email: everything any rule's `when` refers to."""
        return sorted({q for r in self.rules for q in referenced(r.when)})

    def fingerprint(self, qid: str) -> str:
        """Changes whenever the model or the question's wording changes, invalidating cached answers."""
        q = self.questions[qid]
        blob = json.dumps(
            {"model": self.model, "question": q, "state": [STATE_VERSION, self.body_chars]}, sort_keys=True
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def parse_duration(text: str) -> int:
    m = re.fullmatch(r"(\d+)([hd])", text.strip())
    if not m:
        raise ValueError(f"bad duration {text!r}; use e.g. 24h or 7d")
    return int(m[1]) * (3600 if m[2] == "h" else 86400)


def load(path: Path = RULES_PATH) -> Config:
    raw = yaml.safe_load(path.read_text())
    rules = []
    for r in raw["rules"]:
        action = r.get("action")
        if action and action not in ACTIONS:
            raise ValueError(f"rule {r['name']}: unknown action {action!r}")
        if action and "confirm" not in r:
            raise ValueError(f"rule {r['name']}: an action needs a `confirm` safety check")
        if action in ("archive", "trash") and "after" not in r:
            raise ValueError(f"rule {r['name']}: {action} needs `after`")
        if r.get("label_on", "match") not in ("match", "confirm"):
            raise ValueError(f"rule {r['name']}: label_on must be match or confirm")
        rule = Rule(
            r["name"],
            r["when"],
            r.get("label"),
            action,
            parse_duration(r["after"]) if "after" in r else None,
            r.get("confirm", {}),
            r.get("label_on", "match"),
        )
        _check(rule.when, raw["questions"], rule.name)
        _check(rule.confirm, raw["questions"], rule.name)
        rules.append(rule)
    return Config(
        raw["model"],
        raw["body_chars"],
        raw["protect"],
        raw["questions"],
        rules,
        tuple(raw.get("mixed_sender_types", ())),
    )


def referenced(cond: dict) -> set[str]:
    out = set()
    for key, val in cond.items():
        if key in ("all", "any"):
            for c in val:
                out |= referenced(c)
        elif not key.startswith("fact."):
            out.add(key.partition(".")[0])
    return out


def _check(cond: dict, questions: dict, rule: str) -> None:
    for key, val in cond.items():
        if key in ("all", "any"):
            for c in val:
                _check(c, questions, rule)
            continue
        qid, _, option = key.partition(".")
        if qid == "fact":
            values = val if isinstance(val, list) else [val]
            if option not in FACTS or not set(values) <= FACTS[option]:
                raise ValueError(f"rule {rule}: {key} must be one of {FACTS}")
            continue
        q = questions.get(qid)
        if q is None:
            raise ValueError(f"rule {rule}: unknown question {qid!r}")
        if q["type"] == "choice" and option and option not in q["criteria"]:
            raise ValueError(f"rule {rule}: {qid} has no option {option!r}")
        if q["type"] == "choice" and not option:
            for opt in val if isinstance(val, list) else [val]:
                if opt not in q["criteria"]:
                    raise ValueError(f"rule {rule}: {qid} has no option {opt!r}")
        elif not CUTOFF.match(str(val)):
            raise ValueError(f"rule {rule}: {key} needs a cutoff like '>= 0.8', got {val!r}")


def matches(cond: dict, answers: dict) -> bool:
    """True when every condition in `cond` holds for these Jev answers."""
    for key, val in cond.items():
        if key == "all":
            ok = all(matches(c, answers) for c in val)
        elif key == "any":
            ok = any(matches(c, answers) for c in val)
        else:
            qid, _, option = key.partition(".")
            a = answers[qid]
            if qid == "fact":
                ok = a[option] in (val if isinstance(val, list) else [val])
            elif "choice" in a and not option:
                ok = a["choice"] in (val if isinstance(val, list) else [val])
            else:
                p = a["probabilities"].get(option, 0.0) if option else a["noul"]
                op, cutoff = CUTOFF.match(str(val)).groups()
                ok = OPS[op](p, float(cutoff))
        if not ok:
            return False
    return True


def first_match(rules: list[Rule], answers: dict) -> Rule | None:
    return next((r for r in rules if matches(r.when, answers)), None)
