#!/usr/bin/env python3
"""Prepare canonical runner inputs from a shared archive without copying data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re


TASKS = Path(__file__).resolve().parents[1] / "tasks"
MANIFEST = TASKS.parent / "download/public_manifest.json"


def preset_inputs(name, root, custom_bindings=None):
    """Fail early on incomplete/wrong-version downloads; this is not scientific validation."""
    if name not in {"autoresearch", "non-cmoms"}:
        raise ValueError("Unknown preset")
    root = Path(root).expanduser().resolve(strict=True)
    manifest = json.loads(MANIFEST.read_text())
    ids = sorted(task for task in manifest["tasks"] if name == "non-cmoms" or int(task[1:]) <= 24)
    control = root / "_download_all"
    coverage = json.loads((control / "coverage.json").read_text())
    if coverage.get("catalogue") != manifest["version"]:
        raise ValueError("Download catalogue differs from the current benchmark; rerun the matching downloader")
    groups = {group for task in ids for group in manifest["tasks"][task]}
    for group in groups:
        report = json.loads((control / f"{group}.report.json").read_text())
        identity = hashlib.sha256(json.dumps(manifest["groups"][group], sort_keys=True).encode()).hexdigest()
        if (report.get("group_sha256") != identity or report.get("complete") is not True
                or not report.get("expected_files") or report.get("completed_files") != report["expected_files"]):
            raise ValueError(f"Incomplete or obsolete download group: {group}; rerun download_all.py")
    resolved_bindings = {}
    for task_id in ids:
        state = coverage.get("tasks", {}).get(task_id, {})
        if state.get("numerical_inputs_complete") is not True or state.get("missing_groups") != []:
            raise ValueError(f"{task_id}: numerical download not complete")
        task = json.loads((TASKS / task_id / "task_info.json").read_text())
        if task["catalog_version"] != manifest["version"] or task["data_groups"] != manifest["tasks"][task_id]:
            raise ValueError(f"{task_id}: task and download manifest disagree")
        # Derive data paths from the one canonical manifest, not another hand-maintained task list.
        data = list(dict.fromkeys(f"{manifest['groups'][g]['data_type']}/{folder}"
                                 for g in manifest["tasks"][task_id] for folder in manifest["groups"][g]["folders"]))
        custom = (custom_bindings or {}).get(task_id, {})
        if "datasets" in custom and set(custom["datasets"]) != set(data):
            raise ValueError(f"{task_id}: preset dataset paths must match the canonical shared archive")
        papers = custom.get("papers", [])
        if not isinstance(papers, list) or not all(isinstance(p, str) for p in papers):
            raise ValueError(f"{task_id}: papers must be a list of PDF paths")
        if task["track"] == "idea_hypothesis":
            if len(set(papers)) != len(task["related_work"]):
                raise ValueError(f"{task_id}: supply its {len(task['related_work'])} related-work full-text PDFs in --bindings; use --preset autoresearch for the 12 data-only tasks")
            resolved_papers = [Path(resolve_resource(root, p)) for p in papers]
            if len(set(resolved_papers)) != len(resolved_papers):
                raise ValueError(f"{task_id}: duplicate related-work file")
            for path in resolved_papers:
                if path.suffix.lower() != ".pdf" or not path.is_file():
                    raise ValueError(f"{task_id}: related work must be PDF files")
                with path.open("rb") as stream:
                    if not stream.read(1024).lstrip().startswith(b"%PDF-"):
                        raise ValueError(f"{task_id}: not a PDF: {path}")
        resolved_bindings[task_id] = {"datasets": data, "papers": papers}
    return ids, resolved_bindings


def resolve_resource(root, value):
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    if ".." in candidate.parts:
        raise ValueError(f"Parent traversal is not allowed: {value}")
    for part in [candidate, *candidate.parents]:
        if part.is_symlink():
            raise ValueError(f"Use the original resource, not a symlink: {candidate}")
    candidate = candidate.resolve(strict=True)
    if not candidate.is_relative_to(root) or candidate == root:
        raise ValueError("Resources must be below --data-root, not the entire data root")
    if candidate.is_file():
        if candidate.suffix.lower() not in {".nc", ".nc4", ".cdf", ".pdf", ".json", ".csv"}:
            raise ValueError(f"Unsupported input file: {candidate}")
        if candidate.stat().st_size == 0:
            raise ValueError(f"Empty resource: {candidate}")
    elif candidate.is_dir():
        found = False
        for parent, dirs, files in os.walk(candidate, followlinks=False):
            for name in dirs + files:
                item = Path(parent) / name
                if item.is_symlink():
                    raise ValueError(f"Symlink inside input directory: {item}")
                if name == "target_study" or name == "checklist.json":
                    raise ValueError(f"Evaluator reference must not be exposed: {item}")
                if item.is_file() and item.suffix.lower() in {".nc", ".nc4", ".cdf"} and item.stat().st_size:
                    found = True
        if not found:
            raise ValueError(f"No nonempty NetCDF files in directory: {candidate}")
    else:
        raise ValueError(f"Not a regular file or directory: {candidate}")
    if "target_study" in candidate.parts or candidate.name == "checklist.json":
        raise ValueError("Evaluator references are not agent inputs")
    return str(candidate)


def build_cases(task_ids, root, bindings=None, datasets=None, timeout=3600, literature_mode="search_only", tasks_dir=TASKS):
    root = Path(root).expanduser().resolve(strict=True)
    if not root.is_dir() or root == Path(root.anchor):
        raise ValueError("Provide a dedicated existing data root")
    if not task_ids or len(set(task_ids)) != len(task_ids):
        raise ValueError("Choose unique task IDs")
    if not 0 < timeout <= 604800:
        raise ValueError("Timeout must be in (0, 604800]")
    cases = []
    for task_id in task_ids:
        if not re.fullmatch(r"Q(?:0[1-9]|[12][0-9]|30)", task_id):
            raise ValueError(f"Invalid task ID: {task_id}")
        task = json.loads((tasks_dir / task_id / "task_info.json").read_text())
        if datasets is not None:
            paths = list(datasets)
        else:
            binding = (bindings or {}).get(task_id)
            if not isinstance(binding, dict) or not binding.get("datasets"):
                raise ValueError(f"{task_id}: no explicit dataset binding; no product/run is guessed")
            if not isinstance(binding["datasets"], list) or not isinstance(binding.get("papers", []), list):
                raise ValueError(f"{task_id}: datasets and papers must be lists")
            paths = binding["datasets"] + binding.get("papers", [])
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
            raise ValueError(f"{task_id}: datasets/papers must be nonempty path lists")
        resolved = list(dict.fromkeys(resolve_resource(root, p) for p in paths))
        if len(resolved) > 64:
            raise ValueError(f"{task_id}: more than 64 resources; select variable directories instead of individual files")
        cases.append({"id": task_id, "query": task["query"], "datasets": resolved,
                      "timeout_seconds": timeout, "literature_mode": literature_mode})
    return cases


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--tasks", nargs="+")
    selector.add_argument("--preset", choices=["autoresearch", "non-cmoms"], help="12 autoresearch tasks or all 15 non-CMOMS tasks, checking downloader completion reports")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--bindings", type=Path, help="JSON mapping task IDs to datasets and optional paper paths")
    choice.add_argument("--dataset", action="append", help="Repeat for variable/year directories or individual files; same inputs for selected tasks")
    parser.add_argument("--output", type=Path, required=True, help="New JSONL file outside the data archive")
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--literature-mode", choices=["search_only", "ask_before_download", "auto_download_open_access"], default="search_only")
    args = parser.parse_args(argv)
    bindings = json.loads(args.bindings.read_text()) if args.bindings else None
    if args.preset:
        if args.dataset:
            raise ValueError("--preset uses the canonical shared archive; do not supply --dataset")
        args.tasks, bindings = preset_inputs(args.preset, args.data_root, bindings)
    elif bindings is None and args.dataset is None:
        raise ValueError("--tasks requires --bindings or --dataset")
    cases = build_cases(args.tasks, args.data_root, bindings, args.dataset, args.timeout, args.literature_mode)
    target = args.output.expanduser().absolute()
    if target.resolve().is_relative_to(args.data_root.expanduser().resolve()):
        raise ValueError("Write runner inputs outside the shared data archive")
    target.parent.mkdir(parents=True, exist_ok=True)
    # All resources are checked before creating anything. Never replace an existing query set.
    with target.open("x", encoding="utf-8") as stream:
        for case in cases:
            stream.write(json.dumps(case, ensure_ascii=False) + "\n")
    for case in cases:
        print(f"{case['id']}: {len(case['datasets'])} read-only local references; query SHA256={hashlib.sha256(case['query'].encode()).hexdigest()}")
    print(f"Wrote {target}. No data copied; no agent started. Paths checked, NOT scientific coverage or NetCDF integrity.")
    print("Preset checks use download completion reports, not a fresh file-hash audit. Use download_all.py verify to audit the archive. Paper identity/full-text completeness and independent scoring references still require review.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise SystemExit(f"ERROR: {exc}")
