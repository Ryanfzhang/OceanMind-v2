"""Ocean CLI and package-boundary smoke tests."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from oceanx.cli import app, default_state_dir


def test_ocean_cli_exposes_independent_protocol_commands():
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "schema-export" in result.output


def test_desktop_provider_setup_persists_credential_outside_settings_and_never_echoes_it(
    monkeypatch, tmp_path: Path
):
    """The Desktop Host helper has no path through Protocol v2 or settings.json for a key."""

    monkeypatch.setenv("OCEANMIND_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    secret = "desktop-test-secret-must-not-echo"
    result = CliRunner().invoke(
        app,
        ["desktop-provider-config"],
        input=json.dumps(
            {
                "provider": "openai",
                "model": "gpt-test",
                "base_url": "https://example.invalid/v1",
                "api_key": secret,
                "skill_reviewer_model": "gpt-reviewer-test",
            }
        ),
    )

    assert result.exit_code == 0
    status = json.loads(result.output)
    assert status["model"] == "gpt-test"
    assert status["skill_reviewer_model"] == "gpt-reviewer-test"
    assert set(status["roles"]) == {"coordinator", "expert", "skill_curator"}
    assert status["roles"]["coordinator"]["model"] == "gpt-test"
    assert status["roles"]["expert"]["model"] == "gpt-test"
    assert status["roles"]["skill_curator"]["model"] == "gpt-reviewer-test"
    assert all(role["configured"] for role in status["roles"].values())
    assert secret not in result.output
    assert secret not in (tmp_path / "config" / "settings.json").read_text(encoding="utf-8")
    assert secret in (tmp_path / "config" / "credentials.json").read_text(encoding="utf-8")


def test_desktop_provider_setup_rejects_the_non_callable_default_model():
    result = CliRunner().invoke(
        app,
        ["desktop-provider-config"],
        input=json.dumps(
            {
                "provider": "openai",
                "model": "default",
                "base_url": None,
                "api_key": "not-persisted",
                "skill_reviewer_model": "gpt-reviewer-test",
            }
        ),
    )

    assert result.exit_code == 2
    assert "model name is invalid" in result.output


def test_desktop_provider_setup_accepts_independent_role_apis(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("OCEANMIND_CONFIG_DIR", str(tmp_path / "role-config"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = CliRunner().invoke(
        app,
        ["desktop-provider-config"],
        input=json.dumps({
            "roles": {
                "coordinator": {"provider": "anthropic", "model": "coordinator-strong", "base_url": "https://coordinator.invalid", "api_key": "key-c"},
                "expert": {"provider": "openai", "model": "expert-agent", "base_url": "https://expert.invalid/v1", "api_key": "key-e"},
                "skill_curator": {"provider": "openai", "model": "curator-review", "base_url": "https://curator.invalid/v1", "api_key": "key-s"},
            }
        }),
    )
    assert result.exit_code == 0
    roles = json.loads(result.output)["roles"]
    assert roles["coordinator"]["provider"] == "anthropic"
    assert roles["expert"]["model"] == "expert-agent"
    assert roles["skill_curator"]["base_url"] == "https://curator.invalid/v1"
    assert "key-c" not in result.output and "key-e" not in result.output and "key-s" not in result.output

    # A generic process credential must not collapse explicitly configured
    # role APIs back onto one shared key.
    monkeypatch.setenv("OPENAI_API_KEY", "generic-process-key")
    from oceanx.model_config import load_model_profile

    assert load_model_profile("coordinator").api_key == "key-c"
    assert load_model_profile("expert").api_key == "key-e"
    assert load_model_profile("skill_curator").api_key == "key-s"


def test_ocean_cli_reports_its_application_version():
    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.output.strip() == "0.1.0"


def test_ocean_cli_emits_a_path_free_frozen_scientific_runtime_manifest():
    result = CliRunner().invoke(app, ["scientific-runtime"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["schema_version"] == "ocean-frozen-scientific-runtime/v1"
    assert set(payload["interpreter"]) == {"implementation", "version"}
    assert set(payload["platform"]) == {"system", "machine"}
    assert "executable" not in result.output
    assert "release" not in payload["platform"]


def test_ocean_default_state_is_project_local(tmp_path):
    assert default_state_dir(tmp_path) == tmp_path / ".oceanmind"
