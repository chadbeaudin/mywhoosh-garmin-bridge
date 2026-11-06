from bridge.strava_web import StravaWebError


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
    def __init__(self, original: bytes = b"", error: Exception | None = None, download_error: Exception | None = None):
        self.original = original
        self.downloads: list[int] = []
        self.deleted: list[int] = []
        self.error = error
        self.download_error = download_error

    def download_original(self, activity_id: int) -> bytes:
        if self.download_error:
            raise self.download_error
        self.downloads.append(activity_id)
        return self.original

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
