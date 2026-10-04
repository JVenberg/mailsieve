import base64

import pytest

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


@pytest.mark.parametrize(
    "m, ok",
    [
        (msg(part("text/html", "<p>hi</p>"), headers=[("List-Unsubscribe", "<mailto:x@y>")]), True),
        (msg(part("text/html", '<a href="https://x.com/unsubscribe?u=1">here</a>')), True),
        (msg(part("text/html", '<a href="https://click.x.com/abc">Unsubscribe</a>')), True),
        (msg(part("text/plain", "To unsubscribe, reply STOP.")), True),
        (msg(part("text/html", "<p>Your receipt</p>")), False),
    ],
)
def test_unsubscribable(m, ok):
    assert gmail.facts(m)["unsubscribable"] is ok
