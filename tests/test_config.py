import pytest

from mailsieve import config


def answers(email_type="app_notification", **nouls):
    a = {"email_type": {"choice": email_type, "probabilities": {email_type: 0.9}}}
    a |= {q: {"noul": nouls.get(q, 0.0)} for q in ("code_disposable", "alert_routine", "receipt_done", "subscription")}
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
        (answers("marketing"), "unwanted"),
        (answers("political"), "unwanted"),
        (answers("spam_scam"), "spam"),
        (answers("personal"), None),
        (answers("booking"), None),
    ],
)
def test_routing(cfg, a, rule):
    match = config.first_match(cfg.rules, a)
    assert (match.name if match else None) == rule


@pytest.mark.parametrize(
    "rule, a, facts, ok",
    [
        ("codes", answers(code_disposable=0.9), {}, True),
        ("codes", answers(code_disposable=0.5), {}, False),
        ("receipts", answers(receipt_done=0.95), {}, True),
        ("receipts", answers(receipt_done=0.2), {}, False),
        ("unwanted", answers(subscription=0.9), {"unsubscribable": True}, True),
        ("unwanted", answers(subscription=0.9), {"unsubscribable": False}, False),
        ("unwanted", answers(subscription=0.3), {"unsubscribable": True}, False),
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


def test_parse_duration():
    assert config.parse_duration("24h") == 86400
    with pytest.raises(ValueError):
        config.parse_duration("1w")


@pytest.mark.parametrize("email_type", ["login_code", "security_alert", "receipt", "marketing"])
def test_action_rules_need_type_confidence(cfg, email_type):
    a = answers(email_type)
    a["email_type"]["probabilities"][email_type] = 0.3
    assert config.first_match(cfg.rules, a) is None
