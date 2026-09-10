"""Resolve output aliases and object citations against immutable result records."""

import re
from collections.abc import Sequence

from oceanx.task_results import TaskResultRecord

MARKER = re.compile(r"\[\[(?:result|output):([^|\]\r\n]+)(?:\|([^\]\r\n]+))?\]\]")


def canonical_result_citations(text: str, records: Sequence[TaskResultRecord]) -> str:
    aliases = {}
    for record in records:
        ref = record.ref
        key = f"{ref.task_id}/{ref.result_id}@v{ref.version}"
        names = [
            key,
            f"{ref.task_id}/{ref.result_id}@v{ref.version:04d}",
            f"{ref.task_id}/{ref.result_id}",
            ref.result_id,
            f"{ref.result_id}@v{ref.version}",
            f"{ref.result_id}@v{ref.version:04d}",
            record.content.get("output_path"),
            *getattr(record, "execution_output_names", ()),
        ]
        for name in names:
            if name:
                aliases.setdefault(name, []).append((key, record))

    def replace(match):
        base, separator, feature_id = match[1].strip().partition("#")
        candidates = {key: record for key, record in aliases.get(base, [])}
        if len(candidates) != 1:
            if separator:
                return f"[Result object unavailable: {feature_id}]"
            return match[0]  # Preserve unresolved legacy whole-result references.
        key, record = next(iter(candidates.items()))
        if separator:
            feature = next(
                (f for f in record.content.get("features", []) if f["id"] == feature_id), None
            )
            if feature is None:
                return f"[Result object unavailable: {feature_id}]"
            key += "#" + feature_id
            label = feature["label"]  # Same label as the rendered object.
        else:
            label = (match[2] or record.title) if record.kind == "report" else record.title
        label = re.sub(r"[|\]\r\n]", " ", label).strip()
        return f"[[result:{key}|{label}]]"

    # Don't rewrite example code; only references in ordinary Markdown.
    parts = re.split(r"(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)", text)

    def rewrite_prose(prose: str) -> str:
        output = []
        last_reference = None
        for line in MARKER.sub(replace, prose).splitlines(keepends=True):
            standalone = re.fullmatch(r"\s*(?:[-*]\s+)?(\[\[result:[^\]\n]+\]\])\s*", line)
            if standalone:
                if standalone[1] == last_reference:
                    continue
                last_reference = standalone[1]
            elif line.strip():
                last_reference = None
            output.append(line)
        return "".join(output)

    return "".join(part if i % 2 else rewrite_prose(part) for i, part in enumerate(parts))
