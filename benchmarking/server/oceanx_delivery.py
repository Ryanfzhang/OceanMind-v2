"""Opt-in benchmark file delivery; never registers Desktop task results."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath


def render_view(staging: Path, relative: Path) -> dict:
    """Execute the existing fixed backend renderer, never an agent-generated program."""
    from oceanx.figure_reproduction import build_figure_reproduction_notebook

    notebook, _ = build_figure_reproduction_notebook(request_id="benchmark", sources=())
    source = "import matplotlib\nmatplotlib.use('Agg')\n" + "".join(notebook["cells"][1]["source"])
    source += (
        "\nif __name__ == '__main__':\n"
        "    import sys\n"
        "    fig = render_oceanmind_view(sys.argv[1])\n"
        "    fig.savefig(sys.argv[2], dpi=200, bbox_inches='tight')\n"
        "    plt.close(fig)\n"
    )
    (staging / "render_figures.py").write_text(source, encoding="utf-8")
    png = relative.with_suffix(".png")
    completed = subprocess.run(
        [os.environ.get("OCEAN_BENCH_RENDER_PYTHON", os.environ.get("OCEAN_SANDBOX_PYTHON", sys.executable)),
         "render_figures.py", relative.as_posix(), png.as_posix()],
        cwd=staging, capture_output=True, text=True, timeout=120, check=False,
    )
    if completed.returncode or not (staging / png).is_file():
        raise ValueError("Fixed renderer failed: " + completed.stderr[-2000:])
    data = (staging / png).read_bytes()
    return {"path": png.as_posix(), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def checked_file(root: Path, name: str, record: dict) -> Path:
    relative = PurePosixPath(name)
    if not name or relative.is_absolute() or ".." in relative.parts or "\\" in name:
        raise ValueError("Invalid execution output path")
    source = root
    for part in relative.parts:
        source = source / part
        if source.is_symlink():
            raise ValueError("Execution output must not be a symlink")
    if not source.is_file() or not source.resolve().is_relative_to(root.resolve()):
        raise ValueError("Execution output is missing or outside its output directory")
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if source.stat().st_size != record["bytes"] or digest.hexdigest() != record["sha256"]:
        raise ValueError("Execution output no longer matches its recorded hash")
    return source


def execution_output_root(execution) -> Path:
    """Resolve the persisted execution contract, not the tool's transient payload."""
    from oceanx.expert_recovery import execution_result_fingerprint

    payload = execution.result
    work_root = Path(payload["work_root"]).resolve()
    bundle_path = Path(payload["result_bundle_path"])
    expected_bundle = work_root / "result-bundles" / f"{execution.execution_id}.json"
    if bundle_path.is_symlink() or bundle_path.resolve() != expected_bundle:
        raise ValueError("Execution manifest is outside its recorded location")
    bundle = json.loads(bundle_path.read_text())
    if (bundle.get("execution_id") != execution.execution_id
        or execution_result_fingerprint(bundle) != payload["result_fingerprint"]):
        raise ValueError("Execution manifest does not match its recorded fingerprint")
    root = Path(bundle["output_root"])
    expected_root = work_root / "executions" / execution.execution_id / "outputs"
    if root.resolve() != expected_root or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("Execution output directory is outside its recorded location")
    return root


def collect(services, arguments, destination: Path) -> dict:
    """Copy only accepted, task-scoped candidates and their declared support files."""
    candidates = {}
    works = services.store.list_task_team_work(
        workspace_id=services.workspace_id, task_id=services.task_id
    )
    for work in sorted(works, key=lambda w: str(getattr(w, "updated_at", ""))):
        if work.result:
            for output in work.result.outputs:
                # Continuations of one Expert update its current file, not the number
                # of candidates. Different Expert sessions retain separate namespaces.
                owner = getattr(work.work_order, "job_key", "default")
                candidates.setdefault(output.path, {})[owner] = (work, output)
    selected = []
    for path in arguments.accepted_paths:
        owner_filter, separator, candidate_path = path.partition("::")
        pool = candidates.get(candidate_path if separator else path, {})
        matches = [item for owner, item in pool.items() if not separator or owner == owner_filter]
        matches = list({(c.execution_id, c.output_name, c.sha256): (w, c)
                        for w, c in matches}.values())
        if len(matches) != 1:
            choices = [f"{owner}::{candidate_path if separator else path}" for owner in pool]
            raise ValueError(f"Candidate not uniquely identified: {path}. Select an Expert file: {choices}")
        work, candidate = matches[0]
        execution = services.store.get_code_execution(candidate.execution_id)
        if (execution is None or execution.result is None
            or execution.workspace_id != services.workspace_id
            or execution.task_id != services.task_id):
            raise ValueError("Candidate has no task-local execution record")
        root = execution_output_root(execution)
        inventory = {item["name"]: item for item in execution.result.get("outputs", [])}
        names = dict.fromkeys((candidate.output_name, *candidate.supporting_output_names))
        files = []
        for name in names:
            if name not in inventory:
                raise ValueError(f"Unrecorded execution output: {name}")
            record = inventory[name]
            source = checked_file(root, name, record)
            if name == candidate.output_name and record["sha256"] != candidate.sha256:
                raise ValueError("Candidate hash differs from execution record")
            files.append((source, name, record))
        selected.append((work, candidate, files))

    destination.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 2, "delivery_mode": "benchmark_files",
        "task_id": services.task_id, "review_summary": arguments.review_summary,
        "desktop_published": False, "outputs": [],
        "limitations": "Figures use the existing backend renderer without recomputing the analysis; no scientific correctness claim.",
    }
    with tempfile.TemporaryDirectory(prefix=".collect-", dir=destination) as temporary:
        staging = Path(temporary) / "receipt"
        staging.mkdir()
        for work, candidate, files in selected:
            owner = hashlib.sha256(str(getattr(work.work_order, "job_key", "default")).encode()).hexdigest()[:12]
            entry = {
                "candidate": candidate.model_dump(mode="json"),
                "input_refs": [ref.model_dump(mode="json") for ref in work.work_order.input_refs],
                "files": [],
            }
            for source, name, record in files:
                relative = Path("artifacts") / owner / name
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                checked_file(staging, relative.as_posix(), record)
                entry["files"].append({"path": relative.as_posix(), **record})
            if candidate.result_kind == "interactive_view":
                relative = Path("artifacts") / owner / candidate.output_name
                entry["preview"] = render_view(staging, relative)
                preview = Path(entry["preview"]["path"])
                figure = Path("figures") / owner / Path(candidate.output_name).with_suffix(".png")
                (staging / figure).parent.mkdir(parents=True, exist_ok=True)
                (staging / preview).rename(staging / figure)
                entry["preview"]["path"] = figure.as_posix()
            manifest["outputs"].append(entry)
        if (staging / "render_figures.py").exists():
            manifest["renderer"] = {
                "path": "render_figures.py",
                "sha256": hashlib.sha256((staging / "render_figures.py").read_bytes()).hexdigest(),
            }
        receipt = destination
        previous = destination / "delivery_manifest.json"
        if previous.exists():
            old = json.loads(previous.read_text())
            current = {entry["candidate"]["execution_id"] + ":" + entry["candidate"]["output_name"]: entry
                       for entry in old["outputs"]}
            # Replace the same stable output location; retain other accepted files.
            locations = {e["files"][0]["path"] for e in manifest["outputs"]}
            merged = [e for e in current.values() if e["files"][0]["path"] not in locations]
            manifest["outputs"] = merged + manifest["outputs"]
        for source in staging.rglob("*"):
            if source.is_file():
                target = destination / source.relative_to(staging)
                if any(p.is_symlink() for p in [target, *target.parents]):
                    raise ValueError("Delivery target must not be a symlink")
                target.parent.mkdir(parents=True, exist_ok=True)
                source.replace(target)
        notebook_source = "# Editable visualization of this analysis's saved derived data.\n"
        if any(e.get("preview") for e in manifest["outputs"]):
            notebook_source += "from pathlib import Path\nimport runpy\nimport matplotlib.pyplot as plt\nroot = Path.cwd()\nrender = runpy.run_path(str(root / 'render_figures.py'))['render_oceanmind_view']\n"
        notebook_source += "\n".join(
            f"render(str(root / {e['files'][0]['path']!r})); plt.show()"
            for e in manifest["outputs"] if e.get("preview"))
        notebook = {"nbformat": 4, "nbformat_minor": 5,
                    "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}},
                    "cells": [{"cell_type": "code", "id": "figures", "metadata": {}, "execution_count": None,
                               "outputs": [], "source": notebook_source}]}
        for name, payload in [("analysis.ipynb", notebook), ("delivery_manifest.json", manifest)]:
            target = destination / name
            if target.is_symlink():
                raise ValueError("Delivery target must not be a symlink")
            temporary_file = staging / name
            temporary_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            temporary_file.replace(target)
    return {
        "delivery_mode": "benchmark_files", "desktop_published": False,
        "manifest": str(receipt / "delivery_manifest.json"),
        "accepted_paths": list(arguments.accepted_paths),
        "figures": [{"candidate_path": entry["candidate"]["output_name"],
                     "png": str(receipt / entry["preview"]["path"])}
                    for entry in manifest["outputs"] if entry.get("preview")],
        "message": "Accepted files and rendered PNG figures collected for benchmarking. Include these PNG paths in the final research answer; no Desktop publication is required.",
    }


def install(destination: Path) -> None:
    """Install only inside the explicitly launched benchmark backend process."""
    from oceanx.tools import OceanPublishOutputsTool

    async def execute(self, arguments, context):
        async def action():
            import asyncio
            return self._json(await asyncio.to_thread(collect, self.services, arguments, destination))
        try:
            return await self._run_mutation(context, action)
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
            return self._error(f"Benchmark file delivery failed: {exc}")

    OceanPublishOutputsTool.execute = execute
    OceanPublishOutputsTool.description = (
        "Accept reviewed Expert candidate files for benchmark delivery. "
        "Use accepted_paths and review_summary. Revisions update the same file in the run's "
        "fixed figures/ and artifacts/ directories. If different Experts use the same path, "
        "select the owner-qualified job_key::outputs/name returned in the error. "
        "The benchmark collects and hashes the files "
        "and their assigned source references, and renders NetCDF views to PNG using the "
        "existing backend plotting templates. It does not register Desktop interactive views. "
        "After acceptance, provide the final research answer using the returned PNG paths."
    )
