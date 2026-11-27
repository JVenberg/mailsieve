import base64

import pytest
from googleapiclient.errors import HttpError

from mailsieve import gmail


def part(mime, text):
    return {"mimeType": mime, "body": {"data": base64.urlsafe_b64encode(text.encode()).decode()}}


def msg(*parts, headers=()):
    return {"payload": {"headers": [{"name": n, "value": v} for n, v in headers], "parts": list(parts)}}


HTML = "<html><head><style>p{color:red}</style></head><body><p>Your code is 123456. It expires soon.</p></body></html>"


@pytest.mark.parametrize(
    "plain",
    [
        "",
        "bodyPlain",
        "Please use an HTML email reader",
        "body { margin: 0; } .x { color: red }",
        "\u200c\u200b \u00ad",
    ],
)
def test_body_falls_back_to_html_when_plain_is_junk(plain):
    assert gmail.body_text(msg(part("text/plain", plain), part("text/html", HTML))) == (
        "Your code is 123456. It expires soon."
    )


def test_body_keeps_real_plain_text():
    text = "Hi Jack, your package was delivered to the front porch today."
    assert gmail.body_text(msg(part("text/plain", text), part("text/html", HTML))) == text


ONE_CLICK = [("List-Unsubscribe", "<https://x.com/u/1>"), ("List-Unsubscribe-Post", "List-Unsubscribe=One-Click")]


@pytest.mark.parametrize(
    "m, via",
    [
        (msg(part("text/html", "<p>hi</p>"), headers=ONE_CLICK), "gmail"),
        (
            msg(part("text/html", "<p>hi</p>"), headers=[("List-Unsubscribe", "<mailto:x@y>, <https://x.com/u>")]),
            "email",
        ),
        (msg(part("text/html", "<p>hi</p>"), headers=[("List-Unsubscribe", "<https://x.com/u>")]), "link"),
        (msg(part("text/html", '<a href="https://x.com/unsubscribe?u=1">here</a>')), "link"),
        (msg(part("text/html", '<a href="https://click.x.com/abc">Unsubscribe</a>')), "link"),
        (msg(part("text/plain", "To unsubscribe, reply STOP.")), "link"),
        (msg(part("text/html", '<a href="mailto:list@x.com?subject=Unsubscribe">Stop</a>')), "email"),
        (msg(part("text/html", "<p>Your receipt</p>")), "none"),
    ],
)
def test_unsubscribe_via(m, via):
    assert gmail.facts(m)["unsubscribe"] == via


class Flaky:
    def __init__(self, errors):
        self.errors = errors

    def execute(self):
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


def http_error(status, content=b"{}") -> HttpError:
    import httplib2

    return HttpError(httplib2.Response({"status": status}), content)


def test_execute_retries_per_user_rate_limit(monkeypatch):
    monkeypatch.setattr(gmail.time, "sleep", lambda s: None)
    assert gmail.execute(Flaky([http_error(403, b'{"reason": "rateLimitExceeded"}'), http_error(429)])) == "ok"
    with pytest.raises(HttpError):
        gmail.execute(Flaky([http_error(403, b'{"reason": "insufficientPermissions"}')]))
