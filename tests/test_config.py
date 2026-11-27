import pytest

from mailsieve import config, engine


def answers(email_type="app_notification", **nouls):
    a = {"email_type": {"choice": email_type, "probabilities": {email_type: 0.9}}}
    a |= {
        q: {"noul": nouls.get(q, 0.0)}
        for q in (
            "code_disposable",
            "alert_routine",
            "receipt_done",
            "order_done",
            "subscription",
            "credit_score_update",
            "promo_disposable",
            "junk_or_scam",
        )
    }
    return a


@pytest.fixture(scope="module")
def cfg():
    return config.load()


@pytest.mark.parametrize(
    "a, rule",
    [
        (answers("login_code"), "codes"),
        (answers("security_alert"), "security"),
        (answers("fundraising"), "unwanted"),
        (answers("receipt"), "receipts"),
        (answers("order_update"), "orders"),
        (answers("marketing"), "unwanted"),
        (answers("political"), "unwanted"),
        (answers("spam_scam"), "spam"),
        (answers("personal"), None),
        (answers("booking"), None),
    ],
)
def test_routing(cfg, a, rule):
    match = config.matching(cfg.rules, a)
    assert (match[0].name if match else None) == rule


@pytest.mark.parametrize(
    "rule, a, facts, ok",
    [
        ("codes", answers(code_disposable=0.9), {}, True),
        ("codes", answers(code_disposable=0.5), {}, False),
        ("receipts", answers(receipt_done=0.95), {}, True),
        ("receipts", answers(receipt_done=0.2), {}, False),
        ("unwanted", answers(subscription=0.9), {"unsubscribe": "gmail"}, True),
        ("unwanted", answers(subscription=0.9), {"unsubscribe": "none"}, False),
        ("unwanted", answers(subscription=0.3), {"unsubscribe": "link"}, False),
    ],
)
def test_confirm(cfg, rule, a, facts, ok):
    r = next(r for r in cfg.rules if r.name == rule)
    assert config.matches(r.confirm, a | {"fact": facts}) is ok


def test_routing_questions_exclude_confirm_only(cfg):
    assert cfg.routing_questions == ["email_type"]


def test_fingerprint_tracks_wording(cfg):
    other = config.Config(
        cfg.model, cfg.body_chars, cfg.protect, cfg.questions | {"code_disposable": {"type": "noul"}}, cfg.rules
    )
    assert cfg.fingerprint("email_type") == other.fingerprint("email_type")
    assert cfg.fingerprint("code_disposable") != other.fingerprint("code_disposable")


def test_actions_have_durations(cfg):
    by_name = {r.name: r for r in cfg.rules}
    assert (by_name["codes"].action, by_name["codes"].after_seconds) == ("trash", 86400)
    assert (by_name["receipts"].action, by_name["receipts"].after_seconds) == ("archive", 7 * 86400)
    assert by_name["unwanted"].action == "unsubscribe"


def test_choice_probability_cutoff():
    a = {"t": {"choice": "x", "probabilities": {"x": 0.55, "y": 0.45}}}
    assert config.matches({"t.x": ">= 0.5"}, a)
    assert not config.matches({"t.x": ">= 0.6"}, a)


@pytest.mark.parametrize(
    "when, error",
    [
        ({"nope": ">= 0.5"}, "unknown question"),
        ({"email_type": "not_an_option"}, "has no option"),
        ({"code_disposable": "high"}, "needs a cutoff"),
        ({"fact.unknown": True}, "must be one of"),
    ],
)
def test_bad_rules_rejected(tmp_path, when, error):
    path = tmp_path / "rules.yaml"
    path.write_text(config.RULES_PATH.read_text().replace("when: {email_type: spam_scam}", f"when: {when}"))
    with pytest.raises(ValueError, match=error):
        config.load(path)


def test_label_on_confirm_needs_confirm(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text(config.RULES_PATH.read_text().replace('confirm: {junk_or_scam: ">= 0.6"}', ""))
    with pytest.raises(ValueError, match="needs a `confirm` check"):
        config.load(path)


def test_parse_duration():
    assert config.parse_duration("24h") == 86400
    with pytest.raises(ValueError):
        config.parse_duration("1w")


@pytest.mark.parametrize("email_type", ["login_code", "security_alert", "receipt", "marketing"])
def test_action_rules_need_type_confidence(cfg, email_type):
    a = answers(email_type)
    a["email_type"]["probabilities"][email_type] = 0.3
    assert config.matching(cfg.rules, a) == []


def test_unwanted_labels_only_when_confirmed(cfg):
    assert next(r for r in cfg.rules if r.name == "unwanted").label_on == "confirm"
    assert "finance" in cfg.mixed_sender_types


def test_sender_types_roundtrip(tmp_path):
    from mailsieve.cache import SqliteCache

    c = SqliteCache(tmp_path / "a.db")
    c.note_sender("news@shop.com", "marketing")
    c.note_sender("news@shop.com", "receipt")
    c.note_sender("news@shop.com", "receipt")
    assert c.sender_types("news@shop.com") == {"marketing", "receipt"}
    assert c.sender_types("other@shop.com") == set()


def test_statements_archive_at_classify_time(cfg):
    r = next(r for r in cfg.rules if r.name == "statements")
    assert (r.action, r.after_seconds, r.label) == ("archive", 0, "Statements")


class FixedCache:
    def __init__(self, a):
        self.a = a

    def get(self, mid, fps):
        return {q: v for q, v in self.a.items() if q in fps}

    def put(self, *args):
        pass


@pytest.mark.parametrize(
    "a, facts, rule, confirmed",
    [
        (answers("finance", credit_score_update=0.1), {"unsubscribe": "none"}, None, False),
        (answers("finance", credit_score_update=0.9), {"unsubscribe": "none"}, "credit_scores", True),
        (answers("marketing", subscription=0.9), {"unsubscribe": "gmail"}, "unwanted", True),
        (answers("marketing", subscription=0.9, promo_disposable=0.9), {"unsubscribe": "none"}, "promos", True),
        (answers("survey", subscription=0.2, promo_disposable=0.9), {"unsubscribe": "gmail"}, "promos", True),
        (answers("marketing", promo_disposable=0.2), {"unsubscribe": "none"}, None, False),
        (answers("fundraising", subscription=0.2), {"unsubscribe": "gmail"}, None, False),
        (answers("receipt", receipt_done=0.2), {}, "receipts", False),
        (answers("spam_scam", junk_or_scam=0.9), {}, "spam", True),
        (answers("spam_scam", junk_or_scam=0.2), {}, None, False),
    ],
)
def test_decide_passes_over_rules_that_would_do_nothing(cfg, monkeypatch, a, facts, rule, confirmed):
    monkeypatch.setattr(engine.jev, "ask", lambda *_: pytest.fail("asked Jev"))
    d = engine.decide(FixedCache(a), cfg, "m", {}, facts)
    assert ((d["rule"].name if d["rule"] else None), d["confirmed"]) == (rule, confirmed)
