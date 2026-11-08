"""Downloads and deletes Strava activities through the website, since the bridge holds no API token
and the API has had no delete endpoint since 2017.

Follows pR0Ps/stravaweblib: password login is behind reCAPTCHA, so the session is the
`strava_remember_token` cookie (a JWT, ~30 day lifetime) copied from a logged-in browser.
"""
import base64
import json
import logging
import re
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://www.strava.com"
CSRF_PARAM = re.compile(r'<meta[^>]+name="csrf-param"[^>]+content="([^"]+)"')
CSRF_TOKEN = re.compile(r'<meta[^>]+name="csrf-token"[^>]+content="([^"]+)"')
FIT_SIGNATURE = b".FIT"


class StravaWebError(RuntimeError):
    pass


def jwt_claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as e:
        raise StravaWebError("STRAVA_REMEMBER_TOKEN is not a valid JWT") from e


class RememberToken:
    """The newest remember-token: from env, or a refreshed one Strava set that was saved to disk."""

    def __init__(self, env_token: str | None, store: Path):
        self._env_token = env_token
        self._store = store

    def current(self) -> str | None:
        candidates = [t for t in (self._env_token, self._read_store()) if t]
        return max(candidates, key=lambda t: jwt_claims(t).get("exp", 0), default=None)

    def expires_at(self) -> float | None:
        token = self.current()
        return jwt_claims(token).get("exp") if token else None

    def save(self, token: str) -> None:
        self._store.parent.mkdir(parents=True, exist_ok=True)
        self._store.touch(mode=0o600)
        # touch keeps an existing file's mode, so tighten it before the token is written.
        self._store.chmod(0o600)
        self._store.write_text(token)

    def _read_store(self) -> str | None:
        return self._store.read_text().strip() if self._store.exists() else None


class StravaWeb:
    def __init__(self, token: RememberToken, athlete_id: int):
        self._token = token
        self._athlete_id = athlete_id

    def _session(self) -> requests.Session:
        token = self._token.current()
        if not token:
            raise StravaWebError("STRAVA_REMEMBER_TOKEN is not set")
        claims = jwt_claims(token)
        if claims.get("exp", 0) < time.time():
            raise StravaWebError("Strava remember token has expired")
        if str(claims.get("sub")) != str(self._athlete_id):
            raise StravaWebError("Strava remember token belongs to a different athlete")
        session = requests.Session()
        session.headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        session.cookies.set("strava_remember_id", str(claims["sub"]), domain=".strava.com", secure=True)
        session.cookies.set("strava_remember_token", token, domain=".strava.com", secure=True)
        return session

    def _keep_refreshed_token(self, session: requests.Session) -> None:
        token = session.cookies.get("strava_remember_token", domain=".strava.com")
        if token and token != self._token.current() and jwt_claims(token).get("exp", 0) > (self._token.expires_at() or 0):
            self._token.save(token)
            log.info("Saved refreshed Strava remember token")

    def _logged_in_session(self) -> requests.Session:
        session = self._session()
        me = session.get(f"{BASE_URL}/me", allow_redirects=False, timeout=30)
        if not me.is_redirect or not me.headers.get("Location", "").rstrip("/").endswith(f"/{self._athlete_id}"):
            raise StravaWebError("Strava rejected the remember token (not logged in)")
        return session

    def check_login(self) -> None:
        self._keep_refreshed_token(self._logged_in_session())

    def download_original(self, activity_id: int) -> bytes:
        session = self._logged_in_session()
        response = session.get(f"{BASE_URL}/activities/{activity_id}/export_original", allow_redirects=False, timeout=60)
        self._keep_refreshed_token(session)
        if response.status_code != 200:
            raise StravaWebError(f"Strava download of {activity_id} failed (HTTP {response.status_code})")
        if response.content[8:12] != FIT_SIGNATURE:
            content_type = response.headers.get("Content-Type")
            raise StravaWebError(f"Strava's original file for {activity_id} is not a FIT file ({content_type})")
        log.info("Downloaded Strava activity %s (%d bytes)", activity_id, len(response.content))
        return response.content

    def delete_activity(self, activity_id: int) -> None:
        session = self._logged_in_session()
        page = session.get(f"{BASE_URL}/about", timeout=30).text
        param, token = CSRF_PARAM.search(page), CSRF_TOKEN.search(page)
        if not (param and token):
            raise StravaWebError("Couldn't find Strava's CSRF token")

        response = session.post(
            f"{BASE_URL}/activities/{activity_id}",
            data={"_method": "delete", param.group(1): token.group(1)},
            allow_redirects=False,
            timeout=30,
        )
        self._keep_refreshed_token(session)
        if not response.is_redirect or not response.headers.get("Location", "").endswith("/athlete/training"):
            raise StravaWebError(f"Strava delete of {activity_id} failed (HTTP {response.status_code})")
        log.info("Deleted Strava activity %s", activity_id)
