from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "mywhoosh-garmin-bridge.xml"


def test_template_inherits_host_timezone_unraid_passes_as_tz():
    assert 'Target="TZ"' not in TEMPLATE.read_text()


def test_template_asks_for_no_mywhoosh_login_and_requires_the_strava_token():
    template = TEMPLATE.read_text()
    assert "MYWHOOSH_" not in template
    assert 'Target="STRAVA_REMEMBER_TOKEN"' in template
    token_line = next(line for line in template.splitlines() if 'Target="STRAVA_REMEMBER_TOKEN"' in line)
    assert 'Required="true"' in token_line
