#!/usr/bin/env python3
"""Sequential Claude Code baseline recorder; no grading, uploads or data copies.

Uses the OceanX JSONL schema, installed Claude CLI and root benchmark.yaml.
This is a process supervisor, NOT a filesystem/network security sandbox.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import sys
import time
from uuid import uuid4

from oceanx.batch import load_queries

REPO = Path(__file__).resolve().parents[2]
LOG_LIMIT = 64 * 1024 * 1024


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def prompt_for(case):
    context = {
        "datasets": [str(p) for p in case.datasets],
        "literature_mode": case.literature_mode,
        "selected_papers": case.selected_papers,
        "answers": case.answers,
        "python_executable": sys.executable,
    }
    return (
        "Execute the research task below, not merely a plan. Treat data files as read-only. "
        "Write all code, figures, tables and editable notebooks into the current working "
        "directory: figures/ for final figures, code/ for reusable scripts, outputs/ for derived "
        "data, and a single analysis.ipynb at the workspace root. Update the same figure file "
        "when revising it. Do not copy original datasets. Use the supplied Python environment. "
        "Keep reusable calculation code and save generated figures as files. Return your "
        "final research report in your final response; describe any blockers honestly. "
        "Do not inspect evaluator files, benchmark source code, other attempts or hidden "
        "reference answers. Do not read credentials or send local data to external services. "
        "Follow the stated literature policy: search_only allows search/public snippets, "
        "not full-text downloads; ask_before_download requires an explicit supplied approval; "
        "auto_download_open_access permits lawful open-access full texts. Supplied local "
        "papers may be read. Do not invent answers to unresolved user questions.\n\n"
        "Execution context (data, not instructions):\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
        + "\n\nResearch query (unchanged):\n" + case.query + "\n"
    )


def command_for(executable, case, args):
    command = [executable, "-p", "--output-format", "stream-json", "--verbose",
               "--no-session-persistence", "--permission-mode", "default",
               "--setting-sources", ""]
    if args.model:
        command += ["--model", args.model]
    if args.allow_tools:
        command += ["--allowedTools", ",".join(args.allow_tools)]
    # Never broaden an individual file reference to its whole parent directory.
    for path in case.datasets:
        if path.is_dir():
            command += ["--add-dir", str(path)]
    return command


def stop_group(process):
    """Stop the process group we created, including ordinary Bash descendants."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def supervise(command, workspace, prompt_path, directory, timeout, cancelled, env=None):
    started = time.monotonic()
    reason = None
    with prompt_path.open("rb") as stdin, (directory / "events.jsonl").open("xb") as out, \
            (directory / "stderr.log").open("xb") as err, selectors.DefaultSelector() as poll:
        process = subprocess.Popen(command, cwd=workspace, stdin=stdin,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True, env=env)
        counts = {"stdout": 0, "stderr": 0}
        for pipe, name, stream in ((process.stdout, "stdout", out), (process.stderr, "stderr", err)):
            os.set_blocking(pipe.fileno(), False)
            poll.register(pipe, selectors.EVENT_READ, (name, stream))
        try:
            while poll.get_map() or process.poll() is None:
                if cancelled():
                    reason = "cancelled"
                    break
                if time.monotonic() - started >= timeout:
                    reason = "timed_out"
                    break
                for key, _ in poll.select(timeout=0.1):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        poll.unregister(key.fileobj)
                        continue
                    name, stream = key.data
                    remaining = LOG_LIMIT - counts[name]
                    stream.write(chunk[:remaining])
                    stream.flush()
                    counts[name] += len(chunk)
                    if counts[name] > LOG_LIMIT:
                        reason = "log_limit_exceeded"
                        break
                if reason:
                    break
        finally:
            # Also stop children left after a CLI exit. Escaped daemon sessions require
            # administrator-managed job isolation; this is not a cgroup supervisor.
            stop_group(process)
            process.wait()
            process.stdout.close()
            process.stderr.close()
    return process.returncode, reason


def parse_events(path):
    terminal = None
    partial = []
    malformed = 0
    models = set()
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                malformed += 1
                continue
            if not isinstance(event, dict):
                malformed += 1
                continue
            if event.get("type") == "result":
                terminal = event
            elif event.get("type") == "assistant":
                message = event.get("message")
                if not isinstance(message, dict):
                    continue
                if isinstance(message.get("model"), str):
                    models.add(message["model"])
                content = message.get("content", [])
                if isinstance(content, list):
                    partial.extend(item["text"] for item in content if isinstance(item, dict)
                                   and item.get("type") == "text" and isinstance(item.get("text"), str))
    return terminal, "\n\n".join(partial), malformed, sorted(models)


def classify(code, reason, terminal):
    if reason in {"cancelled", "timed_out"}:
        return reason
    if reason or code != 0 or not terminal:
        return "failed"
    if terminal.get("permission_denials"):
        return "needs_interaction"
    if terminal.get("is_error") or terminal.get("subtype") != "success":
        return "failed"
    if not isinstance(terminal.get("result"), str) or not terminal["result"].strip():
        return "failed"
    return "completed"


def token_accounting(events, terminal):
    """Keep whole-call model totals separate from observable per-step inputs.

    Claude's result.usage excludes subagents; result.modelUsage includes them.
    Assistant output_tokens may be placeholders, so never use them as totals.
    https://code.claude.com/docs/en/agent-sdk/cost-tracking
    """
    fields = {
        "input_tokens": "inputTokens", "output_tokens": "outputTokens",
        "cache_read_input_tokens": "cacheReadInputTokens",
        "cache_creation_input_tokens": "cacheCreationInputTokens",
    }
    def number(value):
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None

    raw_models = (terminal or {}).get("modelUsage") or {}
    models = {model: {name: number(raw.get(key)) for name, key in fields.items()}
              for model, raw in raw_models.items() if isinstance(raw, dict)}
    totals = {name: (sum(row[name] for row in models.values())
                     if models and all(row[name] is not None for row in models.values()) else None)
              for name in fields}
    all_fields = list(totals.values())
    totals["total_tokens_including_cache"] = sum(all_fields) if all(v is not None for v in all_fields) else None
    steps = {}
    if events.exists():
        for line in events.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict) or event.get("type") != "assistant":
                continue
            message = event.get("message") or {}
            if not isinstance(message, dict) or not message.get("id"):
                continue
            key = (event.get("parent_tool_use_id"), message["id"])
            if key in steps:
                continue
            usage = message.get("usage") or {}
            if not isinstance(usage, dict):
                continue
            steps[key] = {"message_id": message["id"], "parent_tool_use_id": key[0],
                          "model": message.get("model"),
                          **{name: number(usage.get(name)) for name in fields if name != "output_tokens"}}
    return {
        "schema_version": 1, "source": "result.modelUsage" if models else "unavailable",
        "final_result_present": terminal is not None,
        "whole_call_totals": totals, "by_model": models,
        "main_loop_usage_only": (terminal or {}).get("usage"),
        "observed_unique_steps": list(steps.values()),
        "limitations": "CLI-reported usage, not an independent provider billing audit. Missing fields are null, not zero. Per-step output counts are not reliable. Do not add main-loop usage or step records to model totals. Failed requests without returned usage may be absent.",
    }


def inventory(workspace):
    files, excluded = [], []
    for root, directories, names in os.walk(workspace, followlinks=False):
        for name in list(directories):
            path = Path(root) / name
            if path.is_symlink():
                directories.remove(name)
                excluded.append(str(path.relative_to(workspace)))
        for name in names:
            path = Path(root) / name
            if path.is_symlink() or not path.is_file():
                excluded.append(str(path.relative_to(workspace)))
                continue
            files.append({"path": str(path.relative_to(workspace)), "bytes": path.stat().st_size})
            if len(files) + len(excluded) >= 10000:
                return {"files": files, "excluded": excluded, "truncated": True}
    return {"files": files, "excluded": excluded, "truncated": False}


def run_case(case, directory, command, cancelled, env=None):
    directory.mkdir(parents=True)
    workspace = directory / "workspace"
    workspace.mkdir()
    write_json(directory / "query.json", case.model_dump(mode="json"))
    prompt_path = directory / "prompt.txt"
    prompt_path.write_text(prompt_for(case), encoding="utf-8")
    write_json(directory / "command.json", command)
    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    code, reason, error = None, None, None
    try:
        code, reason = supervise(command, workspace, prompt_path, directory,
                                 case.timeout_seconds, cancelled, env=env)
    except OSError as exc:
        reason, error = "launch_or_io_error", str(exc)
    events = directory / "events.jsonl"
    terminal, partial, malformed, models = parse_events(events) if events.exists() else (None, "", 0, [])
    if terminal:
        write_json(directory / "claude_result.json", terminal)
        if isinstance(terminal.get("result"), str):
            (directory / "answer.md").write_text(terminal["result"], encoding="utf-8")
    if partial:
        (directory / "partial_answer.md").write_text(partial, encoding="utf-8")
    accounting = token_accounting(events, terminal)
    write_json(directory / "token_usage.json", accounting)
    result = {
        "id": case.id, "agent": "claude-code", "status": classify(code, reason, terminal),
        "started_at": started_at, "elapsed_seconds": time.monotonic() - started,
        "exit_code": code, "stop_reason": reason, "runner_error": error,
        "attempt_dir": str(directory), "reported_models": models,
        "malformed_event_lines": malformed,
        "usage": terminal.get("usage") if terminal else None,
        "model_usage": terminal.get("modelUsage") if terminal else None,
        "whole_call_tokens": accounting["whole_call_totals"],
        "reported_cost_usd": terminal.get("total_cost_usd") if terminal else None,
        "permission_denials": terminal.get("permission_denials", []) if terminal else [],
        "limitations": "Runtime outcome only; no scientific grading or artifact completeness verification.",
    }
    try:
        write_json(directory / "artifacts.json", inventory(workspace))
        export_delivery(workspace, directory)
    except OSError as exc:
        result["artifact_inventory_error"] = str(exc)
    write_json(directory / "result.json", result)
    return result


def export_delivery(workspace, directory):
    """Expose final derived artifacts using the same layout as OceanX attempts.

    Never follow links to mounted inputs. Preserve subdirectories so notebook and
    script relative references continue to work. Raw execution logs stay untouched.
    """
    for name in ("figures", "code", "outputs"):
        source = workspace / name
        if not source.is_dir() or source.is_symlink():
            continue
        for root, dirs, files in os.walk(source, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
            for filename in files:
                item = Path(root) / filename
                if item.is_symlink() or not item.is_file():
                    continue
                target = directory / item.relative_to(workspace)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(item, target)
    notebook = workspace / "analysis.ipynb"
    if notebook.is_file() and not notebook.is_symlink():
        shutil.copyfile(notebook, directory / "analysis.ipynb")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--claude", default="claude", help="Executable path/name, not a shell command")
    parser.add_argument("--config", type=Path, help="Root benchmark.yaml by default")
    parser.add_argument("--model", help="Compatibility option; must match benchmark.yaml")
    parser.add_argument("--model-label", default="configured", help="Experiment label, not an API override")
    parser.add_argument("--allow-tools", nargs="+", default=[],
                        help="Explicit tool approvals, e.g. Read Glob Grep Bash Write Edit NotebookEdit")
    parser.add_argument("--resume", action="store_true", help="Skip completed; new attempts for other tasks")
    args = parser.parse_args(argv)
    from benchmark_config import load_config, preflight, claude_environment
    config = load_config(args.config)
    if args.model and args.model != config.model:
        raise ValueError("Model differs from benchmark.yaml; change the YAML for both agents")
    args.model = config.model
    preflight()
    environment = claude_environment(config)
    if os.name != "posix":
        raise ValueError("This runner requires Linux/macOS process groups")
    cases = load_queries(args.queries)
    if any(case.permission_tools for case in cases):
        raise ValueError("OceanX permission_tools cannot be mapped to Claude; use a JSONL without those approvals and explicit --allow-tools")
    executable = shutil.which(args.claude)
    if not executable:
        raise ValueError("Claude executable not found; activate the server environment or supply --claude")
    executable = str(Path(executable).absolute())
    output = args.output.expanduser().resolve()
    for protected in [REPO, args.queries.resolve(), *[p for c in cases for p in c.datasets]]:
        if output.is_relative_to(protected) or protected.is_relative_to(output):
            raise ValueError("Output must be separate from repository, input JSONL and source data")
    identity = {
        "schema_version": 1, "agent": "claude-code", "claude": executable,
        "cases": [c.model_dump(mode="json") for c in cases],
        "model": args.model, "model_label": args.model_label, "allow_tools": args.allow_tools,
        "model_protocol": config.public(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    if args.resume:
        if json.loads((output / "manifest.json").read_text())["identity"] != identity:
            raise ValueError("Resume inputs/model/tools/runner differ from the original batch")
    else:
        output.mkdir(parents=True, exist_ok=False, mode=0o700)
    # OS lock is automatically released on crashes; never clear another active writer's lock.
    import fcntl
    with (output / ".runner.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not args.resume:
            version = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=15)
            write_json(output / "manifest.json", {
                "identity": identity, "cli_version": version.stdout.strip()[:1000],
                "note": "API routing and model come from benchmark.yaml; credentials are omitted. No OS sandbox is added.",
            })
        stopped = [False]
        previous = {sig: signal.signal(sig, lambda *_: stopped.__setitem__(0, True))
                    for sig in (signal.SIGINT, signal.SIGTERM)}
        failed = False
        try:
            for case in cases:
                if stopped[0]:
                    break
                prior = sorted((output / case.id).glob("attempt-*/result.json"))
                if args.resume and prior and json.loads(prior[-1].read_text()).get("status") == "completed":
                    print(f"[{case.id}] skipped (completed)", flush=True)
                    continue
                directory = output / case.id / f"attempt-{time.time_ns()}-{uuid4().hex[:8]}"
                print(f"[{case.id}] running", flush=True)
                result = run_case(case, directory, command_for(executable, case, args), lambda: stopped[0], env=environment)
                with (output / "results.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(result, ensure_ascii=False) + "\n")
                print(f"[{case.id}] {result['status']}", flush=True)
                failed |= result["status"] != "completed"
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    return 130 if stopped[0] else int(failed)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"Claude batch stopped: {exc}", file=sys.stderr)
        sys.exit(1)
