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


def pinned_requirements(name: str) -> dict[str, str]:
    """Each requirement's version, after checking that it is pinned and hashed."""
    entries = re.split(r"\n(?=\S)", (ROOT / name).read_text().replace("\\\n", " "))
    requirements = [e for e in entries if e.strip() and not e.startswith("#")]
    assert requirements
    assert [r for r in requirements if not re.match(r"[\w.-]+==\S+ .*--hash=sha256:[0-9a-f]{64}", r)] == []
    return dict(r.split()[0].split("==") for r in requirements)


def test_every_python_dependency_is_pinned_with_a_hash():
    runtime, dev = pinned_requirements("requirements.txt"), pinned_requirements("requirements-dev.txt")
    assert runtime.items() <= dev.items()


def test_image_and_ci_refuse_unhashed_dependencies():
    assert "pip install --no-cache-dir --require-hashes -r requirements.txt" in (ROOT / "Dockerfile").read_text()
    workflow = (ROOT / ".github" / "workflows" / "docker-publish.yml").read_text()
    assert "pip install --require-hashes -r requirements-dev.txt" in workflow


def test_dependabot_updates_python_dependencies():
    assert "package-ecosystem: pip" in (ROOT / ".github" / "dependabot.yml").read_text()
