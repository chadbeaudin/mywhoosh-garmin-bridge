from bridge.mywhoosh import Activity
from bridge.strava_web import StravaWebError


class FakeMyWhoosh:
    def __init__(self, rides: dict[str, bytes] | None = None):
        self.rides = rides or {}
        self.downloads: list[str] = []

    def recent_activities(self, limit: int = 5) -> list[Activity]:
        return [Activity(ride_id, f"file-{ride_id}", "Ride") for ride_id in list(self.rides)[:limit]]

    def download(self, activity: Activity) -> bytes:
        self.downloads.append(activity.id)
        return self.rides[activity.id]


class FakeGarmin:
    def __init__(self, existing_starts: list[int] | None = None):
        self.existing_starts = existing_starts or []
        self.uploads: list[tuple[bytes, str]] = []
        self.fail_upload = False

    def has_activity_near(self, start_time: int, tolerance_seconds: int = 60) -> bool:
        return any(abs(s - start_time) <= tolerance_seconds for s in self.existing_starts)

    def upload(self, fit_data: bytes, name: str) -> None:
        if self.fail_upload:
            raise RuntimeError("Garmin down")
        self.uploads.append((fit_data, name))


class FakeStravaWeb:
    def __init__(self, error: Exception | None = None):
        self.deleted: list[int] = []
        self.error = error

    def delete_activity(self, activity_id: int) -> None:
        if self.error:
            raise self.error
        self.deleted.append(activity_id)


class FakeRememberToken:
    def __init__(self, expires_at: float | None = None, error: bool = False):
        self._expires_at = expires_at
        self._error = error

    def expires_at(self) -> float | None:
        if self._error:
            raise StravaWebError("STRAVA_REMEMBER_TOKEN is not a valid JWT")
        return self._expires_at


class FakeNotifier:
    def __init__(self):
        self.messages: list[str] = []

    def send(self, text: str, key: str | None = None, every_seconds: float = 0) -> None:
        self.messages.append(text)
