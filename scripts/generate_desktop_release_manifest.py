"""Generate auditable release metadata for a packaged Ocean Desktop application.

This is intentionally a post-packaging step.  It does not sign, notarize, or
publish an application; it makes the exact local bundle and its dependency
inventory reviewable before those external release operations happen.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from typing import Any, Iterable


MANIFEST_SCHEMA_VERSION = 1
SBOM_FORMAT = "CycloneDX"
SBOM_SPEC_VERSION = "1.5"


@dataclass(frozen=True)
class BundleEntry:
    path: str
    size_bytes: int
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "size_bytes": self.size_bytes, "sha256": self.sha256}


@dataclass(frozen=True)
class BundleSymlink:
    path: str
    target: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "target": self.target}


class ReleaseManifestError(RuntimeError):
    """The package cannot produce a safe, auditable release manifest."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1_048_576), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _require_contained(path: Path, root: Path) -> None:
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ReleaseManifestError(f"Bundle entry escapes the application root: {path}") from exc


def collect_bundle_inventory(app_path: Path) -> tuple[list[BundleEntry], list[BundleSymlink]]:
    """Return a stable bundle inventory while rejecting symlink escapes and special files."""

    app = app_path.resolve(strict=True)
    if not app.is_dir():
        raise ReleaseManifestError(f"Packaged application is not a directory: {app}")
    files: list[BundleEntry] = []
    symlinks: list[BundleSymlink] = []
    for entry in sorted(app.rglob("*"), key=lambda item: item.as_posix()):
        relative = _relative(entry, app)
        if entry.is_symlink():
            _require_contained(entry, app)
            symlinks.append(BundleSymlink(path=relative, target=os.readlink(entry)))
        elif entry.is_file():
            files.append(
                BundleEntry(path=relative, size_bytes=entry.stat().st_size, sha256=_sha256(entry))
            )
        elif entry.is_dir():
            continue
        else:
            raise ReleaseManifestError(f"Unsupported special file in application bundle: {entry}")
    if not files:
        raise ReleaseManifestError(f"Packaged application contains no regular files: {app}")
    return files, symlinks


def _npm_components(package_lock: Path) -> list[dict[str, Any]]:
    if not package_lock.is_file():
        return []
    payload = json.loads(package_lock.read_text(encoding="utf-8"))
    packages = payload.get("packages")
    if not isinstance(packages, dict):
        raise ReleaseManifestError(f"Unsupported npm lockfile format: {package_lock}")
    components: list[dict[str, Any]] = []
    for location, metadata in packages.items():
        if not isinstance(location, str) or not location.startswith("node_modules/"):
            continue
        if not isinstance(metadata, dict):
            continue
        name = metadata.get("name") or location.removeprefix("node_modules/")
        version = metadata.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            continue
        component: dict[str, Any] = {
            "type": "library",
            "name": name,
            "version": version,
            "purl": f"pkg:npm/{name}@{version}",
        }
        if isinstance(metadata.get("license"), str):
            component["licenses"] = [{"license": {"name": metadata["license"]}}]
        components.append(component)
    return components


def _python_components(sidecar_root: Path) -> list[dict[str, Any]]:
    if not sidecar_root.is_dir():
        return []
    components: list[dict[str, Any]] = []
    for metadata_path in sorted(sidecar_root.rglob("*.dist-info/METADATA")):
        metadata = BytesParser(policy=default).parsebytes(metadata_path.read_bytes())
        name = metadata.get("Name")
        version = metadata.get("Version")
        if not name or not version:
            continue
        component: dict[str, Any] = {
            "type": "library",
            "name": name,
            "version": version,
            "purl": f"pkg:pypi/{name}@{version}",
        }
        license_name = metadata.get("License")
        if license_name and license_name.strip() and license_name.strip().upper() != "UNKNOWN":
            component["licenses"] = [{"license": {"name": license_name.strip()}}]
        components.append(component)
    return components


def _packaged_sidecar_root(app_path: Path) -> Path:
    """Locate the frozen sidecar in one supported target-platform bundle layout."""

    candidates = (
        app_path / "Contents" / "Resources" / "sidecar",
        app_path / "resources" / "sidecar",
    )
    matches = tuple(path for path in candidates if path.is_dir())
    if len(matches) != 1:
        raise ReleaseManifestError(
            "Packaged application must contain exactly one frozen sidecar under "
            "Contents/Resources or resources"
        )
    return matches[0]


def _deduplicate_components(components: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed: dict[tuple[str, str, str], dict[str, Any]] = {}
    for component in components:
        key = (
            str(component.get("purl", "")),
            str(component.get("name", "")),
            str(component.get("version", "")),
        )
        indexed[key] = component
    return [indexed[key] for key in sorted(indexed)]


def _bundle_sha256(files: Iterable[BundleEntry], symlinks: Iterable[BundleSymlink]) -> str:
    digest = hashlib.sha256()
    for entry in files:
        digest.update(f"file\0{entry.path}\0{entry.size_bytes}\0{entry.sha256}\n".encode("utf-8"))
    for entry in symlinks:
        digest.update(f"symlink\0{entry.path}\0{entry.target}\n".encode("utf-8"))
    return digest.hexdigest()


def build_release_documents(
    *, app_path: Path, desktop_root: Path, application_name: str, application_version: str
) -> dict[str, dict[str, Any]]:
    """Build deterministic manifest, SBOM, and license documents for one bundle."""

    files, symlinks = collect_bundle_inventory(app_path)
    bundle_sha256 = _bundle_sha256(files, symlinks)
    components = _deduplicate_components(
        [
            *_npm_components(desktop_root / "package-lock.json"),
            *_python_components(_packaged_sidecar_root(app_path)),
        ]
    )
    release_identity = f"{application_name}:{application_version}:{bundle_sha256}"
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "application": {"name": application_name, "version": application_version},
        "bundle": {
            "name": app_path.name,
            "sha256": bundle_sha256,
            "files": [entry.as_dict() for entry in files],
            "symlinks": [entry.as_dict() for entry in symlinks],
        },
    }
    sbom = {
        "bomFormat": SBOM_FORMAT,
        "specVersion": SBOM_SPEC_VERSION,
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, release_identity)}",
        "metadata": {
            "component": {
                "type": "application",
                "name": application_name,
                "version": application_version,
            }
        },
        "components": components,
    }
    licenses = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "application": {"name": application_name, "version": application_version},
        "components": [
            {
                "purl": component["purl"],
                "name": component["name"],
                "version": component["version"],
                "licenses": component.get("licenses", []),
            }
            for component in components
        ],
    }
    return {
        "checksums.json": manifest,
        "sbom.cdx.json": sbom,
        "licenses.json": licenses,
    }


def write_release_documents(documents: dict[str, dict[str, Any]], output: Path) -> None:
    """Write a new complete metadata directory in one rename operation."""

    destination = output.resolve()
    if destination.exists():
        raise ReleaseManifestError(f"Release metadata output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=destination.parent))
    try:
        for name, payload in documents.items():
            (temporary / name).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        os.replace(temporary, destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--app", type=Path, required=True, help="Packaged .app or Windows bundle directory"
    )
    parser.add_argument("--output", type=Path, required=True, help="New release metadata directory")
    parser.add_argument(
        "--desktop-root",
        type=Path,
        default=Path("frontend/ocean-desktop"),
        help="Desktop package directory containing package-lock.json",
    )
    parser.add_argument("--name", default="Ocean Research Partner")
    parser.add_argument("--version", required=True)
    arguments = parser.parse_args()
    try:
        documents = build_release_documents(
            app_path=arguments.app,
            desktop_root=arguments.desktop_root.resolve(),
            application_name=arguments.name,
            application_version=arguments.version,
        )
        write_release_documents(documents, arguments.output)
    except (OSError, ValueError, json.JSONDecodeError, ReleaseManifestError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
