"""Collect benchmark attempts into a portable review directory without calling models."""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from oceanx_delivery import checked_file


def collect_run(run: Path, destination: Path | None = None) -> Path:
    run = run.resolve()
    if not run.is_dir():
        raise ValueError(f"Run directory does not exist: {run}")
    destination = destination or run / "collected" / f"collection-{time.time_ns()}"
    destination = destination.resolve()
    attempts = sorted(run.glob("*/attempt-*/result.json"))
    if not attempts:
        raise ValueError("No finished attempt records found; a running task can be collected later")
    destination.mkdir(parents=True, exist_ok=False)
    summary = []
    for result_path in attempts:
        attempt = result_path.parent
        relative = Path(attempt.parent.name) / attempt.name
        target = destination / relative
        target.mkdir(parents=True)
        result = json.loads(result_path.read_text())
        errors = []
        for name in ("result.json", "query.json", "answer.md", "analysis.ipynb", "model_protocol.json", "delivery_protocol.json", "tree.json"):
            source = attempt / name
            if source.is_file() and not source.is_symlink():
                shutil.copyfile(source, target / name)
        answer = target / "answer.md"
        has_answer = answer.is_file() and bool(answer.read_text().strip())
        figures = []
        accepted = 0
        manifests = ([attempt / "delivery_manifest.json"] if (attempt / "delivery_manifest.json").exists()
                     else sorted((attempt / "benchmark_delivery").glob("*/manifest.json")))
        for manifest_path in manifests:
            receipt = manifest_path.parent
            flat = manifest_path.name == "delivery_manifest.json"
            bundle = target if flat else target / "delivery" / receipt.name
            try:
                manifest = json.loads(manifest_path.read_text())
                entries = manifest["outputs"]
                records = [record for entry in entries for record in entry["files"]]
                records += [entry["preview"] for entry in entries if entry.get("preview")]
                # Validate the complete receipt before copying any of its artifacts.
                for record in records:
                    checked_file(receipt, record["path"], record)
                renderer = manifest.get("renderer")
                if renderer:
                    checked_file(receipt, renderer["path"], {
                        **renderer, "bytes": (receipt / renderer["path"]).stat().st_size,
                    })
                bundle.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(manifest_path, bundle / manifest_path.name)
                for record in records:
                    source = checked_file(receipt, record["path"], record)
                    output = bundle / record["path"]
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, output)
                if renderer:
                    shutil.copyfile(receipt / renderer["path"], bundle / renderer["path"])
                accepted += len(entries)
                for entry in entries:
                    if entry.get("preview"):
                        figures.append((bundle / entry["preview"]["path"]).relative_to(target).as_posix())
            except (OSError, ValueError, KeyError) as exc:
                errors.append(f"{receipt.name}: {exc}")
        # Preserve the original answer byte-for-byte; the review copy gets portable PNG links.
        review = answer.read_text() if has_answer else "No final research answer was returned.\n"
        for figure in figures:
            # Receipts returned absolute PNG paths to the model during the run.
            parts = Path(figure).parts
            original = (attempt / figure if parts[0] == "figures"
                        else attempt / "benchmark_delivery" / Path(*parts[1:]))
            review = review.replace(str(original), figure)
        review += "\n\n## Collected figures\n\n"
        review += "\n\n".join(f"![Figure {i}]({path})" for i, path in enumerate(figures, 1))
        (target / "review.md").write_text(review, encoding="utf-8")
        summary.append({
            "id": result.get("id", attempt.parent.name), "attempt": attempt.name,
            "runtime_status": result.get("status"), "has_final_answer": has_answer,
            "accepted_outputs": accepted, "png_count": len(figures), "collection_errors": errors,
            "elapsed_seconds": result.get("elapsed_seconds"),
            "coordinator_usage": result.get("coordinator_usage"), "expert_usage": result.get("expert_usage"),
            "review": (relative / "review.md").as_posix(),
        })
    (destination / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    index = "# OceanX benchmark collection\n\nAll attempts are retained; runtime completion is not scientific correctness.\n\n"
    for item in summary:
        index += (f"- [{item['id']} / {item['attempt']}]({item['review']}): "
                  f"{item['runtime_status']}; final answer={item['has_final_answer']}; "
                  f"PNG={item['png_count']}; collection errors={len(item['collection_errors'])}\n")
    (destination / "index.md").write_text(index, encoding="utf-8")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="New collection directory")
    args = parser.parse_args()
    print(collect_run(args.run, args.output))
