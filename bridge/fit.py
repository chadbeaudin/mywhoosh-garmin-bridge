from fit_tool.fit_file import FitFile
from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.device_info_message import DeviceInfoMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import Manufacturer

# Garmin Edge 840; not in fit_tool's GarminProduct enum.
EDGE_840_PRODUCT = 4024
EDGE_840_SOFTWARE_VERSION = 20.19


def start_time(data: bytes) -> int:
    """Ride start as unix seconds, from the session message (file_id time as fallback)."""
    created = None
    for record in FitFile.from_bytes(data).records:
        message = record.message
        if isinstance(message, SessionMessage) and message.start_time:
            return message.start_time // 1000
        if isinstance(message, FileIdMessage) and message.time_created:
            created = message.time_created // 1000
    if created is None:
        raise ValueError("FIT file has no session start time or file_id creation time")
    return created


def as_garmin_edge(data: bytes) -> bytes:
    """Re-attribute the file to a Garmin Edge 840 so Garmin Connect counts it for training metrics."""
    builder = FitFileBuilder(auto_define=True, min_string_size=50)
    for record in FitFile.from_bytes(data).records:
        message = record.message
        if isinstance(message, FileIdMessage):
            message.manufacturer = Manufacturer.GARMIN.value
            message.product = EDGE_840_PRODUCT
        elif isinstance(message, DeviceInfoMessage):
            # fit_tool can't add fields to a decoded message, so only overwrite ones the file defines.
            for field, value in (
                ("manufacturer", Manufacturer.GARMIN.value),
                ("product", EDGE_840_PRODUCT),
                ("software_version", EDGE_840_SOFTWARE_VERSION),
            ):
                if getattr(message, field) is not None:
                    setattr(message, field, value)
        builder.add(message)
    return builder.build().to_bytes()
