import pytest
from fit_tool.fit_file import FitFile
from fit_tool.profile.messages.device_info_message import DeviceInfoMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage

from bridge import fit

from .conftest import RIDE_START, make_fit


def test_start_time_from_session():
    assert fit.start_time(make_fit(start=RIDE_START, created=RIDE_START + 5)) == RIDE_START


def test_start_time_falls_back_to_file_creation():
    assert fit.start_time(make_fit(start=None, created=RIDE_START)) == RIDE_START


def test_start_time_requires_a_timestamp():
    with pytest.raises(ValueError):
        fit.start_time(make_fit(start=None, created=None))


def test_as_garmin_edge_rewrites_device_and_keeps_start():
    rewritten = fit.as_garmin_edge(make_fit())
    messages = [r.message for r in FitFile.from_bytes(rewritten).records]
    devices = [m for m in messages if isinstance(m, (FileIdMessage, DeviceInfoMessage))]
    assert len(devices) == 2
    assert all(m.manufacturer == 1 and m.product == fit.EDGE_840_PRODUCT for m in devices)
    assert fit.start_time(rewritten) == RIDE_START


def test_as_garmin_edge_leaves_undefined_device_fields_alone():
    rewritten = fit.as_garmin_edge(make_fit(device_product=False))
    device = next(r.message for r in FitFile.from_bytes(rewritten).records if isinstance(r.message, DeviceInfoMessage))
    assert device.manufacturer == 1
    assert device.product is None
