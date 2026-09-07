# ADR 0008: Project-Local State, Backup, And Portable Export

- Status: Accepted
- Date: 2026-07-11
- Decision IDs: OD-03

## Context

Ocean workspaces need durable local metadata, immutable artifact files, request
records, and audit trails.  A plain SQLite file in a repository root is too
easy to commit accidentally, while a global user directory makes a scientific
workspace non-portable.  The storage design must make local ownership,
recovery, and explicit export testable without claiming encryption or
multi-user isolation that the application does not provide.

## Decision

1. The authoritative local store lives at
   `<project>/.oceanmind/`. Its SQLite database is
   `workspace.sqlite3`; immutable artifacts, runs, datasets, staging,
   quarantine, and explicit exports are sibling directories under that root.
   `.oceanmind/` is ignored by Git. Artifact source content is not
   silently added to a repository.
2. On POSIX, the state root, artifact/run/dataset/staging/quarantine/export
   directories are created with mode `0700`; SQLite databases, WAL/SHM files,
   manifests, local logs, and audit records are created with mode `0600`.
   The backend verifies these modes after creation where the platform supports
   them.  Platforms without POSIX modes report a machine-readable
   `permission_policy=best_effort` capability rather than pretending the same
   guarantee exists.
3. SQLite remains the authority for metadata, projections, reviews, request
   records, event records, commit intents, and migrations.  Version-directory
   `manifest.json` files are immutable portable evidence, but cannot overwrite
   a SQLite projection or approval state.
4. Before a schema migration, the store performs a SQLite online backup into
   `backups/` and retains the three newest successful backups.  Backups are
   mode-restricted and are never a source for automatic rollback; recovery is
   an explicit operator action after a verified restore test.
5. A portable export is an explicit operation.  It writes a new bundle under
   `exports/` containing selected immutable version directories, a filtered
   SQLite metadata extract, a checksum inventory, and an export manifest.  It
   excludes raw logs, unapproved model disclosure content, credentials, and
   absolute home paths by default.  Paths under the workspace root become
   `<workspace>/...`; paths under a home directory become `<home>/...`.
   Values that cannot pass this audit fail the export instead of being
   best-effort copied.
6. Artifact files are addressed by project-relative `ocean://` URIs.  The DB
   records MIME type, byte size, and checksum; large files and arrays are never
   serialized into DB JSON payload columns or Protocol envelopes.

## Consequences

- A fresh clone does not contain the user's private research state.  Moving or
  sharing a workspace uses an explicit export/import flow rather than Git
  history as an accidental database transport.
- The backend has a small filesystem-security layer even before the full
  artifact store: it must create restricted paths, reject path escapes, and
  audit exports.
- Phase 2 tests must cover migration backups, permissions where supported,
  export redaction, manifest/checksum consistency, and restart recovery.

## Verification

- `tests/test_oceanx/test_storage_policy.py`
- `tests/test_oceanx/test_artifact_store.py`
- `tests/test_oceanx/test_portable_export.py`
