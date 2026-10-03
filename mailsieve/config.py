"""Load rules.yaml and evaluate rule conditions against Jev answers."""

import operator
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

RULES_PATH = Path(__file__).parent / "rules.yaml"
OPS = {">=": operator.ge, ">": operator.gt, "<=": operator.le, "<": operator.lt}
CUTOFF = re.compile(r"^\s*(>=|>|<=|<)\s*([0-9.]+)\s*$")
ACTIONS = {"archive", "trash"}


@dataclass(frozen=True)
class Rule:
    name: str
    when: dict
    label: str | None = None
    action: str | None = None
    after_seconds: int | None = None


@dataclass(frozen=True)
class Config:
    model: str
    body_chars: int
    protect: list[str]
    questions: dict
    rules: list[Rule]


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
        if action and "after" not in r:
            raise ValueError(f"rule {r['name']}: action needs `after`")
        rule = Rule(r["name"], r["when"], r.get("label"), action, parse_duration(r["after"]) if action else None)
        _check(rule.when, raw["questions"], rule.name)
        rules.append(rule)
    return Config(raw["model"], raw["body_chars"], raw["protect"], raw["questions"], rules)


def _check(cond: dict, questions: dict, rule: str) -> None:
    for key, val in cond.items():
        if key in ("all", "any"):
            for c in val:
                _check(c, questions, rule)
            continue
        qid, _, option = key.partition(".")
        q = questions.get(qid)
        if q is None:
            raise ValueError(f"rule {rule}: unknown question {qid!r}")
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
            if "choice" in a and not option:
                ok = a["choice"] in (val if isinstance(val, list) else [val])
            else:
                p = a["probabilities"][option] if option else a["noul"]
                op, cutoff = CUTOFF.match(str(val)).groups()
                ok = OPS[op](p, float(cutoff))
        if not ok:
            return False
    return True


def first_match(rules: list[Rule], answers: dict) -> Rule | None:
    return next((r for r in rules if matches(r.when, answers)), None)
