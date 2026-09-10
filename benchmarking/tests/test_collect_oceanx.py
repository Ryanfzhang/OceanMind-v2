import hashlib
import json

from collect_oceanx import collect_run


def test_collect_preserves_attempts_and_makes_portable_gallery(tmp_path):
    run = tmp_path / "run"
    for number, status in [(1, "timed_out"), (2, "completed")]:
        attempt = run / "Q13" / f"attempt-{number}"
        attempt.mkdir(parents=True)
        (attempt / "result.json").write_text(json.dumps({"id": "Q13", "status": status}))
    receipt = attempt / "benchmark_delivery" / "receipt1"
    receipt.mkdir(parents=True)
    png = receipt / "figure.png"
    png.write_bytes(b"test fixture image")
    record = {"path": "figure.png", "bytes": png.stat().st_size,
              "sha256": hashlib.sha256(png.read_bytes()).hexdigest()}
    (receipt / "manifest.json").write_text(json.dumps({"outputs": [{"files": [], "preview": record}]}))
    original = f"Research answer ![Figure]({png})"
    (attempt / "answer.md").write_text(original)
    collection = collect_run(run)
    summary = json.loads((collection / "summary.json").read_text())
    assert len(summary) == 2
    assert summary[0]["runtime_status"] == "timed_out"
    assert summary[0]["has_final_answer"] is False
    assert summary[1]["png_count"] == 1
    review = collection / summary[1]["review"]
    assert str(png) not in review.read_text()
    assert (review.parent / "answer.md").read_text() == original
    assert (review.parent / "delivery/receipt1/figure.png").read_bytes() == png.read_bytes()
    assert collect_run(run) != collection  # Recollection preserves the existing collection.


def test_collect_records_bad_receipt_without_faking_delivery(tmp_path):
    attempt = tmp_path / "run/Q13/attempt-1"
    receipt = attempt / "benchmark_delivery/receipt1"
    receipt.mkdir(parents=True)
    (attempt / "result.json").write_text('{"id":"Q13","status":"completed"}')
    (receipt / "manifest.json").write_text(json.dumps({"outputs": [{"files": [{
        "path": "../escape.png", "bytes": 0, "sha256": "0" * 64,
    }]}]}))
    collection = collect_run(tmp_path / "run")
    result = json.loads((collection / "summary.json").read_text())[0]
    assert result["collection_errors"]
    assert result["accepted_outputs"] == 0
    assert result["has_final_answer"] is False
