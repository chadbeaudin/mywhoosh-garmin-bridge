import base64
import json
import time

import pytest
from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.device_info_message import DeviceInfoMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import FileType, Manufacturer

from bridge.db import Database

ATHLETE_ID = 1234567
RIDE_START = 1762099200  # 2025-11-02T16:00:00Z


def make_fit(start: int | None = RIDE_START, created: int | None = RIDE_START, device_product: bool = True) -> bytes:
    builder = FitFileBuilder(auto_define=True)
    file_id = FileIdMessage()
    file_id.type = FileType.ACTIVITY
    file_id.manufacturer = Manufacturer.DEVELOPMENT.value
    file_id.product = 0
    file_id.serial_number = 1
    if created is not None:
        file_id.time_created = created * 1000
    builder.add(file_id)
    device = DeviceInfoMessage()
    device.manufacturer = Manufacturer.DEVELOPMENT.value
    if device_product:
        device.product = 0
    builder.add(device)
    if start is not None:
        session = SessionMessage()
        session.start_time = start * 1000
        session.total_elapsed_time = 3600
        builder.add(session)
    return builder.build().to_bytes()


def make_jwt(sub: int = ATHLETE_ID, exp: float | None = None) -> str:
    def b64(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'HS256'})}.{b64({'sub': sub, 'exp': exp or time.time() + 30 * 86400})}.sig"


def strava_payload(**overrides) -> dict:
    payload = {
        "id": 1001,
        "athlete_id": ATHLETE_ID,
        "aspect_type": "create",
        "start_date": "2025-11-02T16:00:00Z",
        "device_name": "MyWhoosh",
        "sport_type": "VirtualRide",
        "total_photo_count": 1,
        "name": "MyWhoosh - Tempo Ride",
    }
    payload.update(overrides)
    return payload


def garmin_duplicate_payload(**overrides) -> dict:
    return strava_payload(**{
        "id": 1002, "device_name": "Garmin Edge 840", "total_photo_count": 0, "name": "Virtual Cycling", **overrides,
    })


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(tmp_path / "state.db")
