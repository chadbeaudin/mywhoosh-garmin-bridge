from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "mywhoosh-garmin-bridge.xml"


def test_template_inherits_host_timezone_unraid_passes_as_tz():
    assert 'Target="TZ"' not in TEMPLATE.read_text()
