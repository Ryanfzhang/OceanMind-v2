"""Run OceanX with a benchmark-only file delivery adapter.

Q07–Q30 append an explicit research-tree instruction to OceanX's submitted prompt.
The scientific query and execution sandbox are unchanged. All model roles use the
root benchmark.yaml API configuration in this process only.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from uuid import uuid4

from oceanx import __version__, batch

_original_interaction_answer = batch.interaction_answer


def oceanx_prompt(case):
    if re.fullmatch(r"Q(?:0[7-9]|[12][0-9]|30)", case.id):
        return case.query + (
            "\n\nUse Research Tree to guide this investigation. As Coordinator, create and "
            "maintain the tree as the research progresses, and include the saved tree "
            "in your final answer."
        )
    return case.query


def benchmark_interaction_answer(case, payload):
    if payload.get("kind") == "paper_selection":
        return json.dumps({"selected_paper_ids": [
            option["paper_id"] for option in payload.get("options", [])
        ]})
    return _original_interaction_answer(case, payload)


class BenchmarkClient(batch.BatchClient):
    async def send(self, kind, payload):
        if kind == "session.submit":
            payload = {**payload, "text": oceanx_prompt(self.case)}
            (self.directory / "submitted_prompt.txt").write_text(payload["text"], encoding="utf-8")
        return await super().send(kind, payload)

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            sys.executable, str(Path(__file__).resolve()), "--backend",
            str(self.directory.resolve()),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=batch._LOG_LIMIT,
            start_new_session=os.name == "posix",
        )
        self.stderr_task = asyncio.create_task(self._drain_stderr())
        await self.request("system.handshake", {
            "client_kind": "desktop", "client_version": __version__,
            "supported_protocol_versions": [2],
        })
        self.context["workspace_id"] = f"ws_batch_{uuid4().hex}"
        workspace = self.directory / "workspace"
        workspace.mkdir()
        await self.request("workspace.open", {"path": str(workspace)})
        task = await self.request("task.create", {"title": self.case.id})
        self.context["task_id"] = task["payload"]["result"]["task"]["task_id"]
        for path in self.case.datasets:
            await self.request("dataset.import", {
                "local_path": str(path), "materialization_level": "local_reference",
            })


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--backend":
        from oceanx_delivery import install
        from benchmark_models import install_oceanx_models

        model_policy = install_oceanx_models()

        from oceanx.cli import app

        attempt = Path(sys.argv[2]).resolve()
        install(attempt)
        batch._write_json(attempt / "model_protocol.json", model_policy)
        batch._write_json(attempt / "delivery_protocol.json", {
            "delivery_mode": "benchmark_files", "schema_version": 1,
            "desktop_publication_bypassed": True,
            "paper_selection": "select_all",
            "adapter_sha256": hashlib.sha256(
                Path(__file__).with_name("oceanx_delivery.py").read_bytes()
            ).hexdigest(),
        })
        app(args=["backend", "--state-dir", str(attempt / "state"), "--no-skill-curator"])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    query_input = parser.add_mutually_exclusive_group(required=True)
    query_input.add_argument("--queries", type=Path)
    query_input.add_argument("--query", help="A single ad-hoc test question")
    parser.add_argument("--dataset", action="append", default=[], type=Path)
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--config", type=Path, help="Root benchmark.yaml by default")
    args = parser.parse_args()
    from benchmark_config import DEFAULT_CONFIG, load_config, preflight
    config_path = (args.config or DEFAULT_CONFIG).expanduser().resolve()
    config = load_config(config_path)
    config.endpoint(config.oceanx_api)
    os.environ["OCEAN_BENCH_CONFIG"] = str(config_path)
    preflight(require_sandbox=True)
    if args.queries and args.dataset:
        parser.error("Use --dataset with --query; JSONL cases contain their own datasets")
    cases = batch.load_queries(args.queries) if args.queries else [
        batch.resolve_datasets(batch.QueryCase(
            id="QUERY", query=args.query, datasets=args.dataset, timeout_seconds=args.timeout,
        ), Path.cwd())
    ]
    # This process and its dedicated children only; the installed OceanX entrypoint is untouched.
    batch.BatchClient = BenchmarkClient
    batch.interaction_answer = benchmark_interaction_answer
    try:
        results = asyncio.run(batch.run_batch(
            cases, args.output, resume=args.resume
        ))
    finally:
        from collect_oceanx import collect_run

        if args.output.exists() and any(args.output.glob("*/attempt-*/result.json")):
            try:
                print(f"Collected results: {collect_run(args.output)}", file=sys.stderr)
            except (OSError, ValueError, KeyError) as exc:
                print(f"Result collection failed: {exc}", file=sys.stderr)
    raise SystemExit(0 if all(item["status"] == "completed" for item in results) else 1)


if __name__ == "__main__":
    main()
