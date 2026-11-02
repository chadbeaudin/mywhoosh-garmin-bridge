"""MyWhoosh client. Login and download flow adapted from marcelorodrigo/mywhoosh-to-garmin (GPL-3.0)."""
import logging
import uuid
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

LOGIN_URL = "https://services.mywhoosh.com/http-service/api/login"
ACTIVITIES_URL = "https://service14.mywhoosh.com/v2/rider/profile/activities"
DOWNLOAD_URL = "https://service14.mywhoosh.com/v2/rider/profile/download-activity-file"


class MyWhooshError(RuntimeError):
    pass


@dataclass(frozen=True)
class Activity:
    id: str
    file_id: str
    name: str


class MyWhooshClient:
    def __init__(self, email: str, password: str):
        self._email = email
        self._password = password
        self._device_id = str(uuid.uuid4())
        self._token: str | None = None
        self._whoosh_id: str | None = None

    def _login(self) -> None:
        # The Android app's login; the website's download endpoint encrypts its request body.
        response = requests.post(
            LOGIN_URL,
            json={
                "Username": self._email,
                "Password": self._password,
                "Platform": "Android",
                "Action": 1001,
                "CorrelationId": str(uuid.uuid4()),
                "DeviceId": self._device_id,
                "Authorization": "",
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("Success"):
            raise MyWhooshError(f"MyWhoosh login failed: {data.get('Message', 'unknown error')}")
        self._token = data["AccessToken"]
        self._whoosh_id = data["WhooshId"]

    def _post(self, url: str, payload: dict) -> dict:
        if not self._token:
            self._login()
        response = requests.post(url, json=payload, headers={"Authorization": f"Bearer {self._token}"}, timeout=30)
        if response.status_code == 401:
            self._login()
            response = requests.post(url, json=payload, headers={"Authorization": f"Bearer {self._token}"}, timeout=30)
        response.raise_for_status()
        return response.json()

    def recent_activities(self, limit: int = 5) -> list[Activity]:
        data = self._post(ACTIVITIES_URL, {"page": 1, "limit": limit, "sortDate": "DESC"})
        results = data.get("data", {}).get("results", [])
        return [
            Activity(id=str(a.get("id") or a["_id"]), file_id=a["activityFileId"], name=a.get("name", ""))
            for a in results
            if a.get("activityFileId")
        ]

    def download(self, activity: Activity) -> bytes:
        if not self._whoosh_id:
            self._login()
        data = self._post(DOWNLOAD_URL, {"key": self._whoosh_id, "fileId": activity.file_id})
        url = data.get("data")
        if data.get("error") or not isinstance(url, str) or not url.startswith("http"):
            raise MyWhooshError(f"MyWhoosh returned no download URL for activity {activity.id}: {data}")
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        log.info("Downloaded MyWhoosh activity %s (%d bytes)", activity.id, len(response.content))
        return response.content
