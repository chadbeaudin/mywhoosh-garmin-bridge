import json

import responses

from bridge.notify import Notifier

URL = "https://api.telegram.org/botBOT/sendMessage"


@responses.activate
def test_sends_telegram_message(db):
    responses.post(URL, json={"ok": True})
    Notifier(db, "BOT", "42").send("hello")
    assert json.loads(responses.calls[0].request.body)["chat_id"] == "42"
    assert "hello" in json.loads(responses.calls[0].request.body)["text"]


@responses.activate
def test_keyed_messages_are_rate_limited(db):
    responses.post(URL, json={"ok": True})
    notifier = Notifier(db, "BOT", "42")
    notifier.send("one", key="k")
    notifier.send("two", key="k")
    notifier.send("other", key="k2")
    assert len(responses.calls) == 2


@responses.activate
def test_failed_send_is_retried_next_time(db):
    responses.post(URL, status=500)
    responses.post(URL, json={"ok": True})
    notifier = Notifier(db, "BOT", "42")
    notifier.send("one", key="k")
    notifier.send("one", key="k")
    assert len(responses.calls) == 2


@responses.activate
def test_unconfigured_only_logs(db):
    Notifier(db, None, None).send("hello")
    assert len(responses.calls) == 0
