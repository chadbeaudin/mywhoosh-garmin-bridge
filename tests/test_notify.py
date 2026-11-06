import json

import pytest
import requests
import responses

from bridge.notify import Notifier

URL = "https://api.telegram.org/botBOT/sendMessage"
GET_ME = "https://api.telegram.org/botBOT/getMe"


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


def test_status_without_credentials_is_not_configured(db):
    assert Notifier(db, None, None).status() == "not configured"


@responses.activate
def test_status_reports_a_working_bot_as_connected(db):
    responses.get(GET_ME, json={"ok": True})
    assert Notifier(db, "BOT", "42").status() == "connected"


@responses.activate
@pytest.mark.parametrize(
    "failure",
    [{"status": 401}, {"body": requests.ConnectionError(f"Max retries exceeded with url: {GET_ME}")}],
    ids=["rejected", "unreachable"],
)
def test_status_failure_never_exposes_the_bot_token(db, failure):
    responses.get(GET_ME, **failure)
    with pytest.raises(RuntimeError) as error:
        Notifier(db, "BOT", "42").status()
    assert "BOT" not in str(error.value)
    assert error.value.__cause__ is None


@responses.activate
@pytest.mark.parametrize(
    "failure",
    [{"status": 401}, {"body": requests.ConnectionError(f"Max retries exceeded with url: {URL}")}],
    ids=["rejected", "unreachable"],
)
def test_failed_send_never_logs_the_bot_token(db, caplog, failure):
    responses.post(URL, **failure)
    Notifier(db, "BOT", "42").send("hello")
    assert "Telegram notification failed" in caplog.text
    assert "BOT" not in caplog.text
