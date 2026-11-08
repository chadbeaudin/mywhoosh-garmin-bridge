import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_workflow_actions_are_pinned_to_commit_shas():
    uses = re.findall(r"uses:\s*(\S+)", (ROOT / ".github" / "workflows" / "docker-publish.yml").read_text())
    assert uses
    assert [u for u in uses if not re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u)] == []


def test_base_image_is_pinned_to_a_digest():
    base = next(line for line in (ROOT / "Dockerfile").read_text().splitlines() if line.startswith("FROM "))
    assert re.search(r"@sha256:[0-9a-f]{64}$", base)
