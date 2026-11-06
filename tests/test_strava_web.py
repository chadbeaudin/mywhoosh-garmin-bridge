import time
from urllib.parse import parse_qs

import pytest
import responses

from bridge.strava_web import BASE_URL, RememberToken, StravaWeb, StravaWebError

from .conftest import ATHLETE_ID, make_fit, make_jwt

FIT_BYTES = make_fit()
ABOUT_HTML = '<html><head><meta name="csrf-param" content="authenticity_token" /><meta name="csrf-token" content="CSRF123" /></head></html>'


def web(tmp_path, token: str | None) -> StravaWeb:
    return StravaWeb(RememberToken(token, tmp_path / "token"), ATHLETE_ID)


def mock_logged_in():
    responses.get(f"{BASE_URL}/me", status=302, headers={"Location": f"{BASE_URL}/athletes/{ATHLETE_ID}"})
    responses.get(f"{BASE_URL}/about", body=ABOUT_HTML)


@responses.activate
def test_delete_posts_method_override_with_csrf(tmp_path):
    mock_logged_in()
    responses.post(f"{BASE_URL}/activities/42", status=302, headers={"Location": f"{BASE_URL}/athlete/training"})
    web(tmp_path, make_jwt()).delete_activity(42)
    form = parse_qs(responses.calls[2].request.body)
    assert form == {"_method": ["delete"], "authenticity_token": ["CSRF123"]}
    assert "strava_remember_token=" in responses.calls[0].request.headers["Cookie"]


@responses.activate
def test_delete_rejected_when_not_logged_in(tmp_path):
    responses.get(f"{BASE_URL}/me", status=302, headers={"Location": f"{BASE_URL}/login"})
    with pytest.raises(StravaWebError, match="not logged in"):
        web(tmp_path, make_jwt()).delete_activity(42)


@responses.activate
def test_delete_without_expected_redirect_fails(tmp_path):
    mock_logged_in()
    responses.post(f"{BASE_URL}/activities/42", status=200)
    with pytest.raises(StravaWebError, match="HTTP 200"):
        web(tmp_path, make_jwt()).delete_activity(42)


@responses.activate
def test_missing_csrf_fails(tmp_path):
    responses.get(f"{BASE_URL}/me", status=302, headers={"Location": f"{BASE_URL}/athletes/{ATHLETE_ID}"})
    responses.get(f"{BASE_URL}/about", body="<html></html>")
    with pytest.raises(StravaWebError, match="CSRF"):
        web(tmp_path, make_jwt()).delete_activity(42)


@responses.activate
def test_download_returns_the_original_fit_file(tmp_path):
    mock_logged_in()
    responses.get(f"{BASE_URL}/activities/42/export_original", body=FIT_BYTES)
    assert web(tmp_path, make_jwt()).download_original(42) == FIT_BYTES


@responses.activate
def test_download_rejected_when_not_logged_in(tmp_path):
    responses.get(f"{BASE_URL}/me", status=302, headers={"Location": f"{BASE_URL}/login"})
    with pytest.raises(StravaWebError, match="not logged in"):
        web(tmp_path, make_jwt()).download_original(42)


@responses.activate
def test_download_failure_reports_the_status(tmp_path):
    mock_logged_in()
    responses.get(f"{BASE_URL}/activities/42/export_original", status=404)
    with pytest.raises(StravaWebError, match="HTTP 404"):
        web(tmp_path, make_jwt()).download_original(42)


@responses.activate
def test_download_rejects_an_original_that_is_not_fit(tmp_path):
    mock_logged_in()
    responses.get(f"{BASE_URL}/activities/42/export_original", json={"stream": []})
    with pytest.raises(StravaWebError, match="not a FIT file .*application/json"):
        web(tmp_path, make_jwt()).download_original(42)


@pytest.mark.parametrize(
    ("token", "message"),
    [
        (None, "not set"),
        (make_jwt(exp=time.time() - 1), "expired"),
        (make_jwt(sub=999), "different athlete"),
        ("garbage", "not a valid JWT"),
    ],
)
def test_unusable_token_fails_before_any_request(tmp_path, token, message):
    with pytest.raises(StravaWebError, match=message):
        web(tmp_path, token).delete_activity(42)


@responses.activate
def test_refreshed_token_from_strava_is_saved(tmp_path):
    fresh = make_jwt(exp=time.time() + 60 * 86400)
    mock_logged_in()
    responses.post(
        f"{BASE_URL}/activities/42", status=302,
        headers={"Location": f"{BASE_URL}/athlete/training",
                 "Set-Cookie": f"strava_remember_token={fresh}; Domain=.strava.com; Path=/; Secure"},
    )
    web(tmp_path, make_jwt()).delete_activity(42)
    assert (tmp_path / "token").read_text() == fresh


def test_remember_token_prefers_latest_expiry(tmp_path):
    older, newer = make_jwt(exp=time.time() + 86400), make_jwt(exp=time.time() + 5 * 86400)
    store = RememberToken(older, tmp_path / "token")
    assert store.current() == older
    store.save(newer)
    assert store.current() == newer
    assert RememberToken(newer, tmp_path / "token").current() == newer


def test_remember_token_absent(tmp_path):
    store = RememberToken(None, tmp_path / "token")
    assert store.current() is None
    assert store.expires_at() is None
