import pytest

from mailsieve import config


def answers(email_type="notification", auth_code=0.0, activity_notice=0.0, receipt=0.0):
    return {
        "email_type": {"choice": email_type, "probabilities": {email_type: 0.9}},
        "auth_code": {"noul": auth_code},
        "activity_notice": {"noul": activity_notice},
        "receipt": {"noul": receipt},
    }


@pytest.fixture(scope="module")
def cfg():
    return config.load()


@pytest.mark.parametrize(
    "a, rule",
    [
        (answers("auth_code", auth_code=0.99), "codes"),
        (answers("security_alert", auth_code=0.95, activity_notice=0.1), "codes"),
        (answers("security_alert", auth_code=0.95, activity_notice=0.6), "security"),
        (answers("security_alert", auth_code=0.3), "security"),
        (answers("transaction", receipt=0.9), "receipts"),
        (answers("transaction", receipt=0.5), None),
        (answers("marketing"), "ads"),
        (answers("spam_scam"), "spam"),
        (answers("personal", auth_code=0.99), None),
    ],
)
def test_rules(cfg, a, rule):
    match = config.first_match(cfg.rules, a)
    assert (match.name if match else None) == rule


def test_actions_have_durations(cfg):
    by_name = {r.name: r for r in cfg.rules}
    assert (by_name["codes"].action, by_name["codes"].after_seconds) == ("trash", 86400)
    assert (by_name["receipts"].action, by_name["receipts"].after_seconds) == ("archive", 7 * 86400)


def test_choice_probability_cutoff():
    a = {"t": {"choice": "x", "probabilities": {"x": 0.55, "y": 0.45}}}
    assert config.matches({"t.x": ">= 0.5"}, a)
    assert not config.matches({"t.x": ">= 0.6"}, a)


@pytest.mark.parametrize(
    "when, error",
    [
        ({"nope": ">= 0.5"}, "unknown question"),
        ({"email_type": "not_an_option"}, "has no option"),
        ({"auth_code": "high"}, "needs a cutoff"),
    ],
)
def test_bad_rules_rejected(tmp_path, when, error):
    path = tmp_path / "rules.yaml"
    path.write_text(config.RULES_PATH.read_text().replace("when: {email_type: marketing}", f"when: {when}"))
    with pytest.raises(ValueError, match=error):
        config.load(path)


def test_parse_duration():
    assert config.parse_duration("24h") == 86400
    with pytest.raises(ValueError):
        config.parse_duration("1w")
