"""Optional cross-repository contract check against an installed ARC agent."""

import os
from pathlib import Path
import subprocess

import pytest


def test_installed_agent_http_binding():
    agent = os.environ.get("ARC_WEBUI_TEST_AGENT")
    if not agent:
        pytest.skip("Set ARC_WEBUI_TEST_AGENT to a clean ARC agent checkout with its frozen .venv installed.")
    root = Path(__file__).resolve().parents[1]
    agent = Path(agent).resolve()
    env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    env.update(HERMES_WEBUI_AGENT_DIR=str(agent), HERMES_WEBUI_PYTHON=str(agent / ".venv/bin/python"))
    result = subprocess.run(
        [str(agent / ".venv/bin/python"), str(root / "tests/fixtures/messaging_native_probe.py")],
        cwd=root, env=env, text=True, capture_output=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: real agent binding" in result.stdout
