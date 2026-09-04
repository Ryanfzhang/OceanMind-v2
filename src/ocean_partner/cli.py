"""Command-line entry points for the independent Ocean Partner application."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import cast

import typer

from ocean_partner import __version__
from ocean_partner.artifacts.files import ArtifactFileStore
from ocean_partner.artifacts.models import ArtifactRef
from ocean_partner.backend.host import run_stdio_backend
from ocean_partner.backend.store import RequestStore
from ocean_partner.doctor import ocean_doctor
from ocean_partner.exports import PortableExportService
from ocean_partner.protocol.v2.schema import write_protocol_artifacts
from ocean_partner.protocol.v2.models import ClientKind
from ocean_partner.sandbox_self_check import run_sandbox_self_check
from ocean_partner.storage import OceanPaths
from ocean_partner.scientific_runtime import (
    FROZEN_SCIENTIFIC_RUNTIME_MODULES,
    capture_frozen_scientific_runtime_manifest,
)


app = typer.Typer(
    help="Ocean Research Partner workbench.",
    no_args_is_help=True,
    add_completion=False,
)


def _desktop_provider_status() -> dict[str, object]:
    """Return display-safe active provider state for the Desktop Host bridge."""

    from ocean_partner.model_config import load_model_profile

    profiles = {
        role: load_model_profile(role, require_api_key=False)
        for role in ("coordinator", "expert", "skill_curator")
    }
    profile = profiles["coordinator"]
    role_payload = {
        role: {
            "profile": item.name,
            "label": item.label,
            "provider": item.provider,
            "model": item.model,
            "base_url": item.base_url,
            "configured": bool(item.api_key),
        }
        for role, item in profiles.items()
    }
    return {
        "profile": profile.name,
        "label": profile.label,
        "provider": profile.provider,
        "model": profile.model,
        "base_url": profile.base_url,
        "configured": bool(profile.api_key),
        "skill_reviewer_model": profiles["skill_curator"].model,
        "roles": role_payload,
    }


def _desktop_provider_setup(raw: object) -> dict[str, object]:
    """Persist one API-backed profile without serializing credentials into settings."""

    if not isinstance(raw, dict):
        raise ValueError("Invalid Desktop model setup payload")
    if set(raw) == {"provider", "model", "base_url", "api_key", "skill_reviewer_model"}:
        # Accept the former setup payload during desktop upgrades.
        role_payloads = {
            "coordinator": {key: raw[key] for key in ("provider", "model", "base_url", "api_key")},
            "expert": {key: raw[key] for key in ("provider", "model", "base_url", "api_key")},
            "skill_curator": {
                "provider": raw["provider"],
                "model": raw["skill_reviewer_model"],
                "base_url": raw["base_url"],
                "api_key": raw["api_key"],
            },
        }
    elif set(raw) == {"roles"} and isinstance(raw["roles"], dict):
        role_payloads = raw["roles"]
    else:
        raise ValueError("Invalid Desktop model setup payload")

    from ocean_partner.model_config import OceanModelSetup, save_desktop_model_profiles

    setups = {}
    for role in ("coordinator", "expert", "skill_curator"):
        item = role_payloads.get(role) if isinstance(role_payloads, dict) else None
        if not isinstance(item, dict) or set(item) != {"provider", "model", "base_url", "api_key"}:
            raise ValueError(f"Invalid {role} model setup")
        provider_kind = item["provider"]
        model = item["model"]
        base_url = item["base_url"]
        api_key = item["api_key"]
        if provider_kind not in {"openai", "anthropic"}:
            raise ValueError("Unsupported model provider")
        if not isinstance(model, str) or not model.strip() or model.strip().lower() == "default" or len(model.strip()) > 256:
            raise ValueError(f"{role} model name is invalid")
        if base_url is not None and (not isinstance(base_url, str) or len(base_url) > 2048):
            raise ValueError(f"{role} model endpoint is invalid")
        if api_key is not None and (not isinstance(api_key, str) or not api_key.strip() or len(api_key) > 4096):
            raise ValueError(f"{role} API key is invalid")
        setups[role] = OceanModelSetup(
            provider=provider_kind,
            model=model.strip(),
            base_url=base_url.strip() if isinstance(base_url, str) and base_url.strip() else None,
            api_key=api_key if isinstance(api_key, str) else None,
        )
    save_desktop_model_profiles(setups=cast(dict, setups))
    return _desktop_provider_status()


def default_state_dir(workspace_path: Path) -> Path:
    """Return the project-local Ocean state root when no override was requested."""

    return OceanPaths.for_project(workspace_path).root


@app.callback()
def main() -> None:
    """Expose maintenance commands for the OceanMind Desktop backend."""


@app.command("backend", hidden=True)
def backend(
    state_dir: Path | None = typer.Option(
        None,
        "--state-dir",
        help="Directory for the project-local Phase 1 request journal.",
    ),
    client_kind: str = typer.Option(
        "desktop",
        "--client-kind",
        help="Authenticated Protocol v2 Desktop transport kind.",
    ),
) -> None:
    """Run the Protocol v2 JSONL backend used by OceanMind Desktop."""

    if client_kind != "desktop":
        raise typer.BadParameter("must be desktop", param_hint="--client-kind")
    root = state_dir or default_state_dir(Path.cwd())
    raise typer.Exit(
        asyncio.run(run_stdio_backend(root, expected_client_kind=cast(ClientKind, client_kind)))
    )


@app.command("desktop-provider-status", hidden=True)
def desktop_provider_status() -> None:
    """Print the active model profile without emitting credential material."""

    try:
        payload = _desktop_provider_status()
    except (OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))


@app.command("desktop-provider-config", hidden=True)
def desktop_provider_config() -> None:
    """Read one model setup object from stdin and return only display-safe status."""

    try:
        raw = sys.stdin.buffer.read(16_384)
        payload = json.loads(raw.decode("utf-8"))
        result = _desktop_provider_setup(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(json.dumps(result, ensure_ascii=True, separators=(",", ":")))


@app.command("schema-export")
def schema_export() -> None:
    """Regenerate checked-in Protocol v2 schemas and TypeScript declarations."""

    request_path, event_path, type_path = write_protocol_artifacts()
    typer.echo(request_path)
    typer.echo(event_path)
    typer.echo(type_path)


@app.command("doctor")
def doctor() -> None:
    """Print local Ocean execution capabilities as stable JSON."""

    typer.echo(json.dumps(ocean_doctor(), indent=2, sort_keys=True))


@app.command("scientific-runtime", hidden=True)
def scientific_runtime() -> None:
    """Print the frozen-sidecar scientific runtime identity as stable JSON."""

    manifest = capture_frozen_scientific_runtime_manifest(
        required_modules=FROZEN_SCIENTIFIC_RUNTIME_MODULES
    )
    typer.echo(json.dumps(manifest.payload, indent=2, sort_keys=True))


@app.command("sandbox-self-check", hidden=True)
def sandbox_self_check() -> None:
    """Execute the local model-free sandbox contract used by desktop packaging."""

    report = asyncio.run(run_sandbox_self_check())
    typer.echo(json.dumps(report, indent=2, sort_keys=True))
    if not report["passed"]:
        raise typer.Exit(1)




@app.command("export")
def export_workspace(
    workspace_id: str = typer.Option("ws_default", "--workspace-id", help="Workspace to export."),
    workspace: Path | None = typer.Option(None, "--workspace", help="Workspace root used for path audit."),
    state_dir: Path | None = typer.Option(None, "--state-dir", help="Ocean project-local state root."),
    artifact: list[str] = typer.Option([], "--artifact", help="Exact ref: artifact_id@v0001."),
) -> None:
    """Create an explicit, redacted portable artifact bundle under exports/."""

    workspace_path = workspace or Path.cwd()
    paths = OceanPaths.for_state_root(state_dir or default_state_dir(workspace_path)).ensure()
    store = RequestStore(paths.database)
    try:
        refs = [_parse_artifact_ref(value) for value in artifact] or None
        result = PortableExportService(
            paths=paths,
            store=store,
            files=ArtifactFileStore(paths),
        ).export(
            workspace_id=workspace_id,
            refs=refs,
            workspace_root=workspace_path,
        )
    finally:
        store.close()
    typer.echo(result.bundle_directory)


def _parse_artifact_ref(value: str) -> ArtifactRef:
    """Parse the explicit version-pinned CLI form without accepting fuzzy identifiers."""

    artifact_id, separator, version_text = value.partition("@v")
    if not separator or not artifact_id or not version_text.isdigit() or int(version_text) < 1:
        raise typer.BadParameter("Artifact refs must use artifact_id@v0001")
    return ArtifactRef(artifact_id=artifact_id, version=int(version_text))


@app.command("version")
def version() -> None:
    """Print the Ocean Partner application version."""

    typer.echo(__version__)


__all__ = ["app", "default_state_dir"]
