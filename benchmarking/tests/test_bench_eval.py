from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

spec = importlib.util.spec_from_file_location(
    "bench_eval", Path(__file__).resolve().parents[1] / "evaluation/bench_eval.py"
)
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


def write(path, value):
    path.write_bytes(ev.encode(value))
    return path


@pytest.fixture
def case(tmp_path):
    source = tmp_path / "attempt"
    source.mkdir()
    task = json.loads((ev.CATALOG / "Q21" / "task_info.json").read_text())
    write(source / "result.json", {"id": "Q21", "status": "completed"})
    write(source / "query.json", {"query": task["query"]})
    (source / "answer.md").write_text("Computed anomalies from the frozen input.")
    (source / "private.log").write_text("DO NOT EXPORT")
    (source / "small.csv").write_text("month,anomaly\nJanuary,12.3\n")
    data = write(
        tmp_path / "inputs.json",
        {
            "validated": True,
            "task_id": "Q21",
            "files": [{"path": "input.nc", "sha256": "a" * 64}],
        },
    )
    args = argparse.Namespace(
        agent="oceanx",
        task="Q21",
        run_id="r1",
        model_label="private-model-label",
        source=source,
        data_manifest=data,
        report="answer.md",
        include=["small.csv"],
        status=None,
        out=tmp_path / "bundle.zip",
    )
    ev.pack(args)
    manifest, _ = ev.load_bundle(args.out)
    ref = {key: manifest[key] for key in ("task_id", "query_sha256", "data_fingerprint")}
    ref.update(
        status="validated",
        reference_text="Verified fixture ONLY",
        criteria=[
            {"id": "method", "description": "Method and evidence", "weight": 40},
            {"id": "result", "description": "Numerical result", "weight": 60},
        ],
    )
    reference = write(tmp_path / "reference.json", ref)
    return args, reference


def test_export_is_explicit_and_hash_checked(case):
    args, _ = case
    manifest, files = ev.load_bundle(args.out)
    assert set(files) == {"answer.md", "evidence/000.csv"}
    assert manifest["usage"]["coordinator_usage"] is None
    assert b"DO NOT EXPORT" not in b"".join(files.values())
    with pytest.raises(FileExistsError):
        ev.pack(args)


def test_blinded_judge_payload_and_validated_reference(case):
    args, reference = case
    _, _, messages = ev.judge_payload(args.out, reference)
    assert "private-model-label" not in json.dumps(messages)
    ref = json.loads(reference.read_text())
    ref["status"] = "draft"
    write(reference, ref)
    with pytest.raises(ValueError, match="draft"):
        ev.judge_payload(args.out, reference)


def test_cannot_judge_against_different_data(case):
    args, reference = case
    ref = json.loads(reference.read_text())
    ref["data_fingerprint"] = "wrong"
    write(reference, ref)
    with pytest.raises(ValueError, match="data_fingerprint"):
        ev.judge_payload(args.out, reference)


def test_export_rejects_symlinks_traversal_and_cmoms(case, tmp_path):
    args, _ = case
    with pytest.raises(ValueError):
        ev.local_file(args.source, "../inputs.json")
    (args.source / "escape.txt").symlink_to(tmp_path / "inputs.json")
    with pytest.raises(ValueError, match="Symlinks"):
        ev.local_file(args.source, "escape.txt")
    args.task = "Q01"
    with pytest.raises(ValueError, match="non-CMOMS"):
        ev.pack(args)


def test_export_requires_matching_real_oceanx_query(case):
    args, _ = case
    write(args.source / "query.json", {"query": "different query"})
    with pytest.raises(ValueError, match="canonical"):
        ev.pack(args)


def test_science_requires_explicit_status_and_failure_has_no_quality_score(case, tmp_path):
    args, reference = case
    args.agent = "claude-science"
    args.out = tmp_path / "science.zip"
    with pytest.raises(ValueError, match="explicit"):
        ev.pack(args)
    args.status = "timed_out"
    args.report = None
    ev.pack(args)
    with pytest.raises(ValueError, match="denominator"):
        ev.judge_payload(args.out, reference)


def test_hash_tampering_rejected(case, tmp_path):
    args, _ = case
    manifest, files = ev.load_bundle(args.out)
    modified = tmp_path / "modified.zip"
    with ZipFile(modified, "w") as archive:
        archive.writestr("manifest.json", ev.encode(manifest))
        for name, body in files.items():
            archive.writestr(name, b"wrong" if name == "answer.md" else body)
    with pytest.raises(ValueError, match="hash"):
        ev.load_bundle(modified)


def test_oversized_context_is_not_silently_truncated(case):
    args, reference = case
    with pytest.raises(ValueError, match="budget"):
        ev.judge_payload(args.out, reference, max_chars=2)


def test_notebook_is_read_not_executed_and_images_explicit(case, tmp_path):
    args, reference = case
    write(
        args.source / "analysis.ipynb",
        {
            "cells": [
                {
                    "cell_type": "code",
                    "source": "raise RuntimeError('never run')",
                    "outputs": [{"data": {"image/png": "not-sent"}, "text": "visible output"}],
                }
            ]
        },
    )
    (args.source / "figure.png").write_bytes(b"fixture")
    args.include = ["analysis.ipynb", "figure.png"]
    args.out = tmp_path / "notebook.zip"
    ev.pack(args)
    _, _, messages = ev.judge_payload(args.out, reference)
    assert "not-sent" not in json.dumps(messages)
    assert "NOT re-executed" in json.dumps(messages)
    assert "not inspected" in json.dumps(messages)
    _, _, messages = ev.judge_payload(args.out, reference, images=True)
    assert isinstance(messages[1]["content"], list)
    assert any(item["type"] == "image_url" for item in messages[1]["content"])


def test_structured_scores_and_evidence_references(case):
    _, reference = case
    ref = json.loads(reference.read_text())
    value = {
        "criteria": [
            {"id": "method", "score": 4, "reason": "Verified", "evidence_ids": ["answer.md"]},
            {"id": "result", "score": 2, "reason": "Partly supported", "evidence_ids": []},
        ]
    }
    assert ev.validate_score(value, ref, {"answer.md"}) == 70
    value["criteria"][1]["id"] = "method"
    with pytest.raises(ValueError):
        ev.validate_score(value, ref, {"answer.md"})
    value["criteria"][1]["id"] = "result"
    value["criteria"][0]["evidence_ids"] = ["private.log"]
    with pytest.raises(ValueError, match="unavailable"):
        ev.validate_score(value, ref, {"answer.md"})


def add_reference_panels(reference, count):
    ref = json.loads(reference.read_text())
    ref["figures"] = []
    for index in range(count):
        name = f"reference-panel-{index}.png"
        body = f"image fixture {index}".encode()
        (reference.parent / name).write_bytes(body)
        ref["figures"].append({"path": name, "sha256": ev.digest(body),
                               "source_panel": f"Fig. 1 panel {index}",
                               "source_context": "Shared units are retained."})
    return ref


def pack_candidate_panels(args, count, tmp_path):
    args.include = []
    for index in range(count):
        name = f"candidate-{index}.png"
        (args.source / name).write_bytes(b"image fixture")
        args.include.append(name)
    args.out = tmp_path / "with-panels.zip"
    ev.pack(args)


def test_visual_scoring_policy_and_context_reach_judge(case):
    args, reference = case
    ref = add_reference_panels(reference, 1)
    ref["visual_scoring_policy"] = {"comparison_target": "Scientific information, not pixels"}
    ref["reference_mode"] = "Approved historical method transfer"
    ref["criteria"][0]["visual_checks"] = [{"required_information": ["Depth-resolved contrast"]}]
    write(reference, ref)
    _, _, messages = ev.judge_payload(args.out, reference)
    payload = json.loads(messages[1]["content"])
    assert payload["visual_scoring_policy"] == ref["visual_scoring_policy"]
    assert payload["reference_mode"] == ref["reference_mode"]
    assert payload["criteria"][0]["visual_checks"][0]["required_information"] == ["Depth-resolved contrast"]
    assert payload["reference_figure_context"][0]["source_context"] == "Shared units are retained."
    assert "not inspected" in " ".join(payload["not_inspected"])
    assert "not pixel similarity" in messages[0]["content"]
    assert "Do not add scores per image" in messages[0]["content"]


def test_twelve_reference_panels_and_four_candidate_images_fit(case, tmp_path):
    args, reference = case
    write(reference, add_reference_panels(reference, 12))
    pack_candidate_panels(args, 4, tmp_path)
    _, _, messages = ev.judge_payload(args.out, reference, images=True)
    assert sum(item["type"] == "image_url" for item in messages[1]["content"]) == 16


@pytest.mark.parametrize("reference_count,candidate_count", [(17, 0), (0, 17), (12, 5)])
def test_panel_limit_never_silently_drops_images(case, tmp_path, reference_count, candidate_count):
    args, reference = case
    write(reference, add_reference_panels(reference, reference_count))
    pack_candidate_panels(args, candidate_count, tmp_path)
    with pytest.raises(ValueError, match="16 total candidate/reference"):
        ev.judge_payload(args.out, reference, images=True)


def test_combined_reference_and_candidate_image_byte_budget(case, tmp_path, monkeypatch):
    args, reference = case
    write(reference, add_reference_panels(reference, 1))
    pack_candidate_panels(args, 1, tmp_path)
    monkeypatch.setattr(ev, "MAX_REVIEW_IMAGE_BYTES", 20)
    with pytest.raises(ValueError, match="Review images exceed"):
        ev.judge_payload(args.out, reference, images=True)


def test_reference_panel_hash_tampering_is_rejected(case):
    args, reference = case
    ref = add_reference_panels(reference, 1)
    ref["figures"][0]["sha256"] = "0" * 64
    write(reference, ref)
    with pytest.raises(ValueError, match="format/hash"):
        ev.judge_payload(args.out, reference, images=True)


def test_default_judge_is_offline(case, tmp_path, monkeypatch):
    args, reference = case
    monkeypatch.delenv("BENCH_JUDGE_API_KEY", raising=False)
    result = ev.judge(
        argparse.Namespace(
            bundle=args.out,
            reference=reference,
            images=False,
            send=False,
            model="fixture",
            out=tmp_path / "score.json",
        )
    )
    assert result["ready"] and not (tmp_path / "score.json").exists()


def test_scalar_comparison_missing_and_tolerance(tmp_path):
    identity = {"task_id": "Q21", "query_sha256": "query", "data_fingerprint": "data"}
    actual = write(tmp_path / "actual.json", {**identity, "metrics": {"a": 10.001}})
    reference = write(
        tmp_path / "reference.json",
        {
            **identity,
            "status": "validated",
            "metrics": {
                "a": {"value": 10, "atol": 0.01, "rtol": 0},
                "b": {"value": 0, "atol": 0, "rtol": 0},
            },
        },
    )
    result = ev.compare(
        argparse.Namespace(actual=actual, reference=reference, out=tmp_path / "checks.json")
    )
    assert result["metrics"][0]["passed"]
    assert not result["metrics"][1]["passed"] and not result["passed"]


@pytest.mark.parametrize("outcome", ["success", "truncated", "timeout"])
def test_judge_http_without_paid_calls(case, tmp_path, monkeypatch, outcome):
    import httpx

    packed, reference = case
    args = argparse.Namespace(
        bundle=packed.out,
        reference=reference,
        images=False,
        send=True,
        model="fixture",
        base_url="https://judge.example/v1",
        out=tmp_path / "score.json",
    )
    monkeypatch.setenv("BENCH_JUDGE_API_KEY", "fixture-secret")
    calls = []

    def respond(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 4096
        assert payload["response_format"] == {"type": "json_object"}
        if outcome == "timeout":
            raise httpx.ReadTimeout("fixture", request=request)
        review = {
            "criteria": [
                {
                    "id": name,
                    "score": 4,
                    "reason": "Fixture evidence",
                    "evidence_ids": ["answer.md"],
                }
                for name in ("method", "result")
            ],
            "limitations": [],
        }
        return httpx.Response(
            200,
            json={
                "model": "fixture",
                "usage": {"total_tokens": 123},
                "choices": [
                    {
                        "finish_reason": "stop" if outcome == "success" else "length",
                        "message": {"content": json.dumps(review)},
                    }
                ],
            },
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw)
    )
    if outcome == "success":
        record = ev.judge(args)
        assert record["score_100"] == 100
        assert record["usage"]["total_tokens"] == 123
        assert "fixture-secret" not in args.out.read_text()
    else:
        with pytest.raises(ValueError):
            ev.judge(args)
        assert not args.out.exists()
    assert len(calls) == 1
