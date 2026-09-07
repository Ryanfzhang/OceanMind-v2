"""Discover capability-gated packaged Ocean research-process skills."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

LITERATURE_CAPABILITY = "literature.read"
WEB_SEARCH_CAPABILITY = "web.search"
JINA_READER_CAPABILITY = "jina.reader"


@dataclass(frozen=True)
class OceanSkillMetadata:
    """Model-visible metadata only; detailed skill content remains tool-loaded."""

    name: str
    description: str
    version: str
    roles: tuple[str, ...]


class OceanResourceUnavailableError(RuntimeError):
    """A caller requested a gated resource that is not visible in this runtime."""


class OceanSkillDocumentError(ValueError):
    """A generated SKILL.md does not satisfy OceanMind's routing contract."""


@dataclass(frozen=True)
class _SkillDocument:
    name: str
    description: str
    roles: tuple[str, ...]
    content: str


def _skill_frontmatter(content: str) -> dict[str, Any]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        boundary = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"
        )
    except StopIteration:
        return {}
    loaded = yaml.safe_load("\n".join(lines[1:boundary])) or {}
    return loaded if isinstance(loaded, dict) else {}


def _skill_roles(frontmatter: dict[str, Any]) -> tuple[str, ...]:
    metadata = frontmatter.get("metadata")
    raw = metadata.get("roles", ()) if isinstance(metadata, dict) else ()
    if isinstance(raw, str):
        raw = (raw,)
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))


def validate_ocean_skill_document(
    content: str,
    *,
    expected_name: str | None = None,
    allowed_roles: Iterable[str] | None = None,
) -> OceanSkillMetadata:
    """Validate a complete generated SKILL.md and return its routing metadata."""

    normalized = content.strip()
    frontmatter = _skill_frontmatter(normalized)
    name = str(frontmatter.get("name") or "").strip()
    description = str(frontmatter.get("description") or "").strip()
    roles = _skill_roles(frontmatter)
    if re.fullmatch(r"[a-z][a-z0-9-]{2,127}", name) is None:
        raise OceanSkillDocumentError("Skill frontmatter needs a valid kebab-case name")
    if expected_name is not None and name != expected_name:
        raise OceanSkillDocumentError("Skill frontmatter name does not match target_skill")
    if not description:
        raise OceanSkillDocumentError("Skill frontmatter needs a description")
    if not roles:
        raise OceanSkillDocumentError("Skill frontmatter metadata.roles must not be empty")
    if allowed_roles is not None:
        allowed = frozenset(allowed_roles)
        unknown = tuple(role for role in roles if role not in allowed)
        if unknown:
            raise OceanSkillDocumentError(
                "Skill frontmatter contains unknown roles: " + ", ".join(unknown)
            )
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return OceanSkillMetadata(
        name=name,
        description=description,
        version=f"sha256:{digest}",
        roles=roles,
    )


def _load_skills_from_dirs(directories: Iterable[Path]) -> list[_SkillDocument]:
    documents: list[_SkillDocument] = []
    for root in directories:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*/SKILL.md")):
            content = path.read_text(encoding="utf-8")
            frontmatter = _skill_frontmatter(content)
            name = str(frontmatter.get("name") or path.parent.name).strip()
            description = str(
                frontmatter.get("description") or f"Ocean research manual: {name}"
            ).strip()
            documents.append(
                _SkillDocument(
                    name=name,
                    description=description,
                    roles=_skill_roles(frontmatter),
                    content=content,
                )
            )
    return documents


def ocean_skill_dirs(*, capabilities: Iterable[str] = ()) -> tuple[Path, ...]:
    """Return only the packaged skill roots enabled by the current runtime capability set."""

    root = _resource_root() / "skills"
    dirs = [root / "core"]
    if LITERATURE_CAPABILITY in frozenset(capabilities):
        dirs.append(root / "gated")
    return tuple(directory for directory in dirs if directory.is_dir())


def ocean_reference_root() -> Path:
    """Return the packaged, read-on-demand reference directory without loading its contents."""

    return _resource_root() / "references"


def ocean_skill_metadata(
    *,
    capabilities: Iterable[str] = (),
    role: str | None = None,
) -> list[OceanSkillMetadata]:
    """List role/capability-enabled metadata without injecting full skill content.

    ``role=None`` is an administrative catalog view used by validation and tests.
    Agent runtimes always provide their concrete stable role.
    """

    metadata: list[OceanSkillMetadata] = []
    for skill in _load_skills_from_dirs(ocean_skill_dirs(capabilities=capabilities)):
        if role is not None and role not in skill.roles:
            continue
        digest = hashlib.sha256(skill.content.encode("utf-8")).hexdigest()[:12]
        metadata.append(
            OceanSkillMetadata(
                name=skill.name,
                description=skill.description,
                version=f"sha256:{digest}",
                roles=skill.roles,
            )
        )
    return sorted(metadata, key=lambda item: item.name)


def load_ocean_skill(
    name: str,
    *,
    capabilities: Iterable[str] = (),
    role: str | None = None,
) -> tuple[str, OceanSkillMetadata]:
    """Load full markdown only after role and capability checks pass."""

    for skill in _load_skills_from_dirs(ocean_skill_dirs(capabilities=capabilities)):
        if skill.name == name and (role is None or role in skill.roles):
            digest = hashlib.sha256(skill.content.encode("utf-8")).hexdigest()[:12]
            return skill.content, OceanSkillMetadata(
                name=skill.name,
                description=skill.description,
                version=f"sha256:{digest}",
                roles=skill.roles,
            )
    raise OceanResourceUnavailableError(f"Ocean skill is unavailable: {name}")


def load_ocean_reference(relative_path: str) -> tuple[str, str]:
    """Load one packaged reference by root-relative path and return its stable content version."""

    root = ocean_reference_root().resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise OceanResourceUnavailableError("Ocean reference path escapes the packaged root") from exc
    if not candidate.is_file() or candidate.suffix != ".md":
        raise OceanResourceUnavailableError(f"Ocean reference is unavailable: {relative_path}")
    content = candidate.read_text(encoding="utf-8")
    return content, f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()[:12]}"


def record_ocean_resource_use(
    store,
    *,
    workspace_id: str,
    resource_kind: str,
    resource_name: str,
    resource_version: str,
    work_order_id: str | None = None,
    request_id: str | None = None,
    agent_id: str | None = None,
) -> str:
    """Persist only a resource identity/version audit record after a skill or reference is used."""

    usage_id = f"resource_{uuid4().hex}"
    store.record_resource_usage(
        usage_id=usage_id,
        workspace_id=workspace_id,
        resource_kind=resource_kind,
        resource_name=resource_name,
        resource_version=resource_version,
        work_order_id=work_order_id,
        request_id=request_id,
        agent_id=agent_id,
    )
    return usage_id


def ocean_extra_skill_dirs(*, capabilities: Iterable[str] = ()) -> tuple[str, ...]:
    """Return normalized packaged skill roots for runtime metadata."""

    return tuple(str(directory.resolve()) for directory in ocean_skill_dirs(capabilities=capabilities))


def ocean_skill_prompt_section(
    *,
    capabilities: Iterable[str] = (),
    role: str | None = None,
) -> str:
    """Build a role-filtered metadata-only catalog; full markdown stays tool-loaded."""

    lines = [
        "# Ocean Research Manuals",
        "",
        (
            "These are private process manuals, not a method library or mandatory opening step. "
            "Load a manual only when it materially changes the method or safety of the current "
            "request; never narrate manual loading to the researcher."
        ),
        "",
    ]
    for skill in ocean_skill_metadata(capabilities=capabilities, role=role):
        lines.append(f"- {skill.name} ({skill.version}): {skill.description}")
    return "\n".join(lines)


def _resource_root() -> Path:
    return Path(__file__).resolve().parent / "resources"


__all__ = [
    "JINA_READER_CAPABILITY",
    "LITERATURE_CAPABILITY",
    "WEB_SEARCH_CAPABILITY",
    "OceanResourceUnavailableError",
    "OceanSkillDocumentError",
    "OceanSkillMetadata",
    "load_ocean_reference",
    "load_ocean_skill",
    "ocean_extra_skill_dirs",
    "ocean_reference_root",
    "ocean_skill_dirs",
    "ocean_skill_metadata",
    "ocean_skill_prompt_section",
    "record_ocean_resource_use",
    "validate_ocean_skill_document",
]
