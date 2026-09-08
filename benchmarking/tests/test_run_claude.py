"""Real subprocess supervision with a fake CLI; no model calls or server access."""
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

import run_claude as runner


@pytest.fixture
def setup(tmp_path):
    executable = tmp_path / "fake-claude"
    executable.write_text(f"#!{sys.executable}\n" + '''
import json, os, pathlib, subprocess, sys, time
if "--version" in sys.argv:
    print("fake-claude 1.0")
    sys.exit(0)
prompt = sys.stdin.read()
pathlib.Path("code.py").write_text("print('analysis')")
pathlib.Path("figure.png").write_bytes(b"test-image")
pathlib.Path("argv.json").write_text(json.dumps(sys.argv))
print(json.dumps({"type":"assistant", "message":{"model":"test-deepseek", "content":[{"type":"text", "text":"partial work"}]}}), flush=True)
if "TIMEOUT" in prompt or "CANCEL" in prompt:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1); open('escaped.txt', 'w').write('bad')"])
    pathlib.Path("child.pid").write_text(str(child.pid))
    time.sleep(30)
if "MALFORMED" in prompt:
    print("not-json")
    sys.exit(0)
result = {"type":"result", "subtype":"success", "is_error":False, "result":"Research answer", "usage":{"input_tokens":10}}
if "API_ERROR" in prompt:
    result.update(subtype="error_during_execution", is_error=True, result="API failed")
if "DENIED" in prompt:
    result["permission_denials"] = [{"tool_name":"Bash"}]
print(json.dumps(result), flush=True)
sys.exit(4 if "NONZERO" in prompt else 0)
''')
    executable.chmod(0o700)
    data = tmp_path / "data"
    data.mkdir()
    (data / "input.nc").write_bytes(b"input unchanged")
    queries = tmp_path / "queries.jsonl"
    output = tmp_path / "runs"
    def invoke(items, extra=()):
        queries.write_text("\n".join(json.dumps({"id": f"Q{i:02}", "query": q,
            "datasets": ["data"], "timeout_seconds": timeout})
            for i, (q, timeout) in enumerate(items, 1)))
        return ["--queries", str(queries), "--output", str(output), "--claude", str(executable), *extra]
    return tmp_path, output, invoke


def results(output):
    return [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]


def test_success_files_model_config_and_resume(setup):
    root, output, invoke = setup
    args = invoke([("Analyze data", 5)], ["--allow-tools", "Read", "Bash", "Write"])
    assert runner.main(args) == 0
    result = results(output)[0]
    attempt = Path(result["attempt_dir"])
    assert result["agent"] == "claude-code"
    assert result["elapsed_seconds"] > 0
    assert result["usage"]["input_tokens"] == 10
    assert (attempt / "answer.md").read_text() == "Research answer"
    assert (attempt / "workspace/figure.png").exists()
    assert (root / "data/input.nc").read_bytes() == b"input unchanged"
    assert not list(output.rglob("*.nc"))
    command = json.loads((attempt / "command.json").read_text())
    assert "--model" not in command
    assert "--dangerously-skip-permissions" not in command
    assert "--no-session-persistence" in command
    assert runner.main([*args, "--resume"]) == 0
    assert len(results(output)) == 1
    with pytest.raises(ValueError, match="Resume inputs"):
        runner.main([*args, "--resume", "--model", "changed-model"])


@pytest.mark.parametrize("query,status", [("API_ERROR", "failed"), ("MALFORMED", "failed"),
                                           ("NONZERO", "failed"), ("DENIED", "needs_interaction")])
def test_failures_continue_and_preserve_partial(setup, query, status):
    _, output, invoke = setup
    assert runner.main(invoke([(query, 5), ("OK", 5)])) == 1
    first, second = results(output)
    assert first["status"] == status
    assert second["status"] == "completed"
    assert (Path(first["attempt_dir"]) / "partial_answer.md").read_text() == "partial work"


def test_timeout_kills_children_retains_outputs(setup):
    _, output, invoke = setup
    assert runner.main(invoke([("TIMEOUT", 0.3), ("OK", 5)])) == 1
    assert [r["status"] for r in results(output)] == ["timed_out", "completed"]
    time.sleep(1.1)
    assert not list(output.rglob("escaped.txt"))
    assert list(output.rglob("figure.png"))


def test_sigterm_records_cancelled_and_stops_batch(setup):
    _, output, invoke = setup
    args = invoke([("CANCEL", 20), ("SHOULD NOT START", 5)])
    process = subprocess.Popen([sys.executable, str(Path(runner.__file__)), *args],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not list(output.rglob("child.pid")) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert list(output.rglob("child.pid"))
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=5)
        assert process.returncode == 130
        assert [r["status"] for r in results(output)] == ["cancelled"]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_log_limit(setup, monkeypatch):
    _, output, invoke = setup
    monkeypatch.setattr(runner, "LOG_LIMIT", 20)
    assert runner.main(invoke([("OK", 5)])) == 1
    result = results(output)[0]
    assert result["stop_reason"] == "log_limit_exceeded"
    assert (Path(result["attempt_dir"]) / "events.jsonl").stat().st_size <= 20


def test_refuse_existing_output_and_source_overlap(setup):
    root, output, invoke = setup
    args = invoke([("OK", 5)])
    with pytest.raises(ValueError, match="separate"):
        runner.main([*args, "--output", str(root / "data/results")])
    output.mkdir()
    with pytest.raises(FileExistsError):
        runner.main(args)


def test_inventory_does_not_follow_symlinks(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source").symlink_to(tmp_path, target_is_directory=True)
    record = runner.inventory(workspace)
    assert record["files"] == []
    assert record["excluded"] == ["source"]
