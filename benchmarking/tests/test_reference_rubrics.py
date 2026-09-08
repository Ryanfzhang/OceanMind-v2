"""Offline catalogue checks; these do not validate scientific answers."""
import hashlib
import json
import re
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TASK_IDS = [f"Q{i:02}" for i in range(7, 25)]
PANEL_COUNTS = dict(zip(TASK_IDS, [3, 3, 2, 4, 12, 1, 4, 1, 2, 2, 5, 4, 2, 8, 3, 7, 3, 4]))


def read(path):
    return json.loads(path.read_text())


def reference(task_id):
    return read(ROOT / "tasks" / task_id / "target_study" / "checklist.json")


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_rubric_matches_query_and_stays_draft(task_id):
    task = read(ROOT / "tasks" / task_id / "task_info.json")
    ref = reference(task_id)
    assert task["id"] == task_id and task["track"] == "autoresearch"
    assert task["catalog_version"] == ("2026-09-08-accessible-v1" if int(task_id[1:]) >= 13 else "2026-09-07-paper-aligned")
    assert task["data_groups"] and task["analysis_period"]
    assert ref["task_id"] == task_id
    assert ref["query_sha256"] == hashlib.sha256(task["query"].encode()).hexdigest()
    assert ref["status"] == "draft" and ref["data_fingerprint"] is None
    assert ref["release_requirements"]
    assert ref["paper"]["doi"] and ref["paper"]["url"].startswith("https://")
    assert [c["id"] for c in ref["criteria"]] == [f"{task_id}-C{i}" for i in range(1, 6)]
    assert all(c["weight"] > 0 for c in ref["criteria"])
    assert sum(c["weight"] for c in ref["criteria"]) == 100
    for criterion in ref["criteria"]:
        assert criterion["source_basis"] and criterion["evidence_requirements"]
        assert not re.search(r"[\u4e00-\u9fff]", criterion["description"])
    assert not re.search(r"[\u4e00-\u9fff]", task["query"])


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_images_are_real_contained_and_linked_to_criteria(task_id):
    base = ROOT / "tasks" / task_id / "target_study"
    ref = reference(task_id)
    figure_map = {f["path"]: f for f in ref["figures"]}
    assert len(figure_map) == len(ref["figures"]) == PANEL_COUNTS[task_id]
    assert set(figure_map) == {str(p.relative_to(base)) for p in (base / "images").glob("*.png")}
    criterion_ids = {c["id"] for c in ref["criteria"]}
    for name, figure in figure_map.items():
        assert not Path(name).is_absolute() and ".." not in Path(name).parts
        path = base / name
        assert not path.is_symlink()
        assert path.resolve().is_relative_to(base.resolve())
        body = path.read_bytes()
        assert body.startswith(b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", body[16:24])
        assert min(width, height) >= 250
        assert hashlib.sha256(body).hexdigest() == figure["sha256"]
        assert figure["source_doi"] == ref["paper"]["doi"]
        assert len(figure["source_pdf_sha256"]) == 64
        assert figure["pdf_page"] > 0
        assert figure["reviewed"] is True
        assert figure["source_panel"] and figure["grouping_reason"]
        assert figure["extraction"].startswith("Original PDF regions only")
        assert figure["source_regions"]
        for region in figure["source_regions"]:
            rx0, ry0, rx1, ry1 = region["crop_fraction_top_left"]
            assert 0 <= rx0 < rx1 <= 1 and 0 <= ry0 < ry1 <= 1
            assert region["role"] and 0 < region["uniform_scale"] <= 1
            assert all(value >= 0 for value in region["position_points_top_left"])
        x0, y0, x1, y1 = figure["crop_fraction_top_left"]
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
        assert set(figure["criterion_ids"]) <= criterion_ids
        assert figure["criterion_ids"]
        for cid in figure["criterion_ids"]:
            criterion = next(c for c in ref["criteria"] if c["id"] == cid)
            assert name in criterion["reference_figures"]
    for criterion in ref["criteria"]:
        for name in criterion.get("reference_figures", []):
            assert name in figure_map
            assert criterion["id"] in figure_map[name]["criterion_ids"]
        for check in criterion.get("visual_checks", []):
            assert check["reference_figure"] in figure_map
            assert check["instruction"]
            assert check["source_panel"] and check["source_basis"]
            assert check["acceptable_alternatives"] and check["evidence_group"]
            assert check["requirement"] in {"scientific_information", "alternative_example", "context_only"}
            if check["requirement"] != "context_only":
                assert check["required_information"]
            assert not re.search(r"[\u4e00-\u9fff]", json.dumps(check, ensure_ascii=False))


def test_no_duplicate_catalogue_or_source_images():
    assert len(list((ROOT / "tasks").glob("Q*/task_info.json"))) == 30
    assert len(list((ROOT / "tasks").glob("Q*/target_study/checklist.json"))) == 30
    hashes = [f["sha256"] for task_id in TASK_IDS for f in reference(task_id)["figures"]]
    assert len(hashes) == len(set(hashes)) == sum(PANEL_COUNTS.values()) == 70
    assert not (ROOT / "evaluation" / "references").exists()
    for old in ["CMOMS_RUBRICS.md", "AUTORESEARCH_RUBRICS.md"]:
        assert not (ROOT / "evaluation" / old).exists()
    assert not (ROOT / "preparation" / "AUTORESEARCH_RUBRIC_BASIS.md").exists()


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_scientific_equivalence_does_not_add_panel_points(task_id):
    ref = reference(task_id)
    assert ref["rubric_version"] == ("1.2-accessible-method-transfer-draft" if 13 <= int(task_id[1:]) <= 22 else "1.1-panel-evidence-draft")
    policy = ref["visual_scoring_policy"]
    assert policy["version"] == "1.0-scientific-information"
    assert "not pixel similarity" in policy["comparison_target"]
    assert "100-point" in policy["scoring_unit"]
    assert "One submitted figure" in policy["evidence_mapping"]
    assert "independently validated" in policy["tolerance_policy"]
    for criterion in ref["criteria"]:
        checks = criterion.get("visual_checks", [])
        assert len({check["id"] for check in checks}) == len(checks)
        if checks:
            assert criterion["visual_scoring"]["no_extra_panel_points"] is True
            assert criterion["visual_scoring"]["no_pixel_matching"] is True


def test_only_task_relevant_panels_are_required():
    assert {f["source_panel"] for f in reference("Q12")["figures"]} == {"Fig. 2C including its inset"}
    assert {f["source_panel"] for f in reference("Q14")["figures"]} == {"Fig. 14b"}
    maps = [f for f in reference("Q11")["figures"] if "Fig8" in f["path"]]
    assert len(maps) == 10
    assert all("summer" in f["source_panel"] or "winter" in f["source_panel"] for f in maps)
    for figure in maps:
        assert any("colorbar" in region["role"] for region in figure["source_regions"])
    q15 = [check for c in reference("Q15")["criteria"] for check in c.get("visual_checks", [])]
    assert q15 and all(check["requirement"] == "context_only" for check in q15)
    assert len({check["evidence_group"] for check in q15}) == 1
    normal = [check for c in reference("Q22")["criteria"] for check in c.get("visual_checks", [])
              if check["reference_figure"].endswith("normal.png")]
    assert normal and all(check["requirement"] == "context_only" for check in normal)


def test_cmoms_periods_and_observational_reference_routing():
    for task_id in ["Q09", "Q10", "Q11"]:
        task = read(ROOT / "tasks" / task_id / "task_info.json")
        assert task["analysis_period"] == "2018–2022"
        assert "method transfer" in reference(task_id)["reference_mode"]
    for task_id, year in [("Q07", "1992"), ("Q08", "1993"), ("Q12", "2071")]:
        task = read(ROOT / "tasks" / task_id / "task_info.json")
        assert year in task["analysis_period"]
    assert all("Sosa2020" in f["path"] for f in reference("Q17")["figures"])
    assert "10.5194/os-14-1303-2018" == reference("Q22")["paper"]["doi"]
    assert "2004–2012" in read(ROOT / "tasks/Q22/task_info.json")["analysis_period"]


def test_server_subset_matches_evaluator():
    from bench_eval import PUBLIC_TASKS
    subset = read(ROOT / "download" / "public_manifest.json")
    assert set(subset["tasks"]) == PUBLIC_TASKS
    assert {f"Q{i:02}" for i in range(13, 25)} <= PUBLIC_TASKS
    assert not ({f"Q{i:02}" for i in range(1, 13)} & PUBLIC_TASKS)


def test_document_links_and_data_groups():
    prep = (ROOT / "preparation" / "DATA_PREPARATION.md").read_text()
    for task_id in TASK_IDS:
        task = read(ROOT / "tasks" / task_id / "task_info.json")
        for group in task["data_groups"]:
            assert f"| {group} |" in prep
    for doc in [ROOT / "README.md", ROOT / "preparation/DATA_PREPARATION.md",
                ROOT / "evaluation/README.md", ROOT / "evaluation/IDEA_RUBRIC.md",
                ROOT / "preparation/IDEA_TASKS.md"]:
        for target in re.findall(r"\]\(([^)]+)\)", doc.read_text()):
            if target.startswith(("http:", "https:", "#")):
                continue
            assert (doc.parent / target.split("#")[0]).resolve().exists(), target


@pytest.mark.parametrize("task_id", [f"Q{i:02}" for i in range(1, 7)])
def test_basic_single_figure_rubric_and_shared_inputs(task_id):
    task = read(ROOT / "tasks" / task_id / "task_info.json")
    ref = reference(task_id)
    assert task["id"] == task_id and task["track"] == "data_analysis"
    assert task["catalog_version"] == "2026-09-07-basic-v1"
    assert task["analysis_period"] == "2011" and task["data_groups"] == ["B1"]
    assert "Only one figure and one short conclusion" in task["query"]
    assert not any(old in task["query"] for old in ["Gulf of Mexico", "MODIS", "2011–2022"])
    shared = read(ROOT / "tasks/Q01/task_info.json")["input_contract"]
    assert task["input_contract"] == shared
    assert set(shared["science_fields"]) == {"temp", "salt", "u", "v", "oxygen", "chlorophyll"}
    assert set(task["required_variables"]) <= set(shared["science_fields"])
    assert "B1" in (ROOT / "preparation/DATA_PREPARATION.md").read_text()
    assert ref["task_id"] == task_id and ref["status"] == "draft"
    assert ref["data_fingerprint"] is None
    assert ref["query_sha256"] == hashlib.sha256(task["query"].encode()).hexdigest()
    assert ref["rubric_version"] == "1.0-basic-single-figure-draft"
    assert ref["figures"] == [] and "paper" not in ref
    assert [c["id"] for c in ref["criteria"]] == [f"{task_id}-C{i}" for i in range(1, 6)]
    assert [c["weight"] for c in ref["criteria"]] == [20, 50, 10, 10, 10]
    assert all(c["description"] and c["evidence_requirements"] and c["source_basis"] for c in ref["criteria"])
    assert len([v for c in ref["criteria"] for v in c["visual_checks"]]) == 1
    assert ref["reference_preparation"]["expected_outputs"]
    assert ref["reference_preparation"]["edge_cases"]
    assert ref["reference_preparation"]["tolerance_status"] == "pending_independent_validation"
    assert ref["release_requirements"]


def test_basic_science_definitions_and_no_extra_deliverables():
    assert "0.03" in reference("Q03")["reference_text"] or "threshold" in reference("Q03")["reference_text"]
    assert "0.032" in reference("Q06")["reference_text"]
    assert "62.5" in reference("Q06")["reference_text"]
    assert "No peak-depth map or coarsening experiment" in reference("Q04")["reference_text"]
    assert "Magnitude of mean components is not mean speed" in reference("Q05")["reference_text"]
    fields = set()
    for i in range(1, 7):
        fields.update(read(ROOT / f"tasks/Q{i:02}/task_info.json")["required_variables"])
    assert fields == {"temp", "salt", "u", "v", "oxygen", "chlorophyll"}


@pytest.mark.parametrize("task_id", [f"Q{i:02}" for i in range(25, 31)])
def test_idea_anchors_readings_and_draft_identity(task_id):
    task = read(ROOT / "tasks" / task_id / "task_info.json")
    ref = reference(task_id)
    assert task["track"] == "idea_hypothesis"
    assert ref["query_sha256"] == hashlib.sha256(task["query"].encode()).hexdigest()
    assert ref["status"] == "draft"
    assert ref["data_fingerprint"] is None and ref["reading_pack_fingerprint"] is None
    assert task["reading_data_alignment"] == ref["reading_data_alignment"]
    assert ref["figures"] == []
    assert len(ref["criteria"]) == 14
    assert sum(c["weight"] for c in ref["criteria"]) == 100
    assert len({c["id"] for c in ref["criteria"]}) == 14
    category_totals = {}
    common = (ROOT / "evaluation/IDEA_RUBRIC.md").read_text()
    for criterion, baseline in zip(ref["criteria"], reference("Q25")["criteria"]):
        assert criterion["anchors"] == baseline["anchors"]
        assert criterion["weight"] == baseline["weight"]
        assert set(criterion["anchors"]) == {"0", "1", "2", "3", "4"}
        assert len(set(criterion["anchors"].values())) == 5
        assert all(text and text in common for text in criterion["anchors"].values())
        assert criterion["evidence_requirements"] and criterion["source_basis"]
        assert not re.search(r"[\u4e00-\u9fff]", json.dumps(criterion, ensure_ascii=False))
        category_totals[criterion["category"]] = category_totals.get(criterion["category"], 0) + criterion["weight"]
    assert sorted(category_totals.values()) == [10, 10, 15, 30, 35]
    assert not re.search(r"[\u4e00-\u9fff]", task["query"])
    assert 2 <= len(task["related_work"]) <= 3
    assert {p["doi"] for p in task["related_work"]} == {p["doi"] for p in ref["related_work_basis"]}
    for paper in task["related_work"]:
        assert paper["source_dataset"] and paper["verification_source"].startswith("https://")
        assert paper["staged_path"] is None and paper["sha256"] is None
    prep = (ROOT / "preparation/DATA_PREPARATION.md").read_text()
    assert all(f"| {group} |" in prep for group in task["data_groups"])


def test_idea_regional_data_alignment_is_explicit():
    tasks = {i: read(ROOT / f"tasks/Q{i}/task_info.json") for i in range(25, 31)}
    papers = lambda i: {p["id"] for p in tasks[i]["related_work"]}
    assert papers(25) == {"lu2023", "xu2023"}
    assert papers(26) == {"lu2020", "lu2023", "fang2026"}
    assert "MITgcm-Darwin" in tasks[25]["reading_data_alignment"]
    assert "provisional" in tasks[27]["reading_data_alignment"]
    assert papers(28) == {"ye2024", "fang2025"}
    assert tasks[28]["analysis_period"] == "2013–2017"
    assert "erddap" in tasks[28]["data_scope"]["access"]
    assert tasks[28]["data_scope"]["sampling"] == "Monthly"
    assert tasks[28]["data_groups"] == ["P_MODIS"]
    assert papers(29) == {"meunier2018", "sosa2020", "marquez2024"}
    assert "not identical" in tasks[30]["reading_data_alignment"]


@pytest.mark.parametrize("task_id", [f"Q{i:02}" for i in range(25, 31)])
def test_idea_value_does_not_require_worldwide_novelty(task_id):
    task = read(ROOT / "tasks" / task_id / "task_info.json")
    ref = reference(task_id)
    assert task["catalog_version"] == ("2026-09-08-accessible-v1" if int(task_id[1:]) >= 28 else "2026-09-07-idea-v2")
    assert "worldwide novelty is not required" in task["query"]
    assert "already studied elsewhere is not automatically penalized" in ref["contribution_policy"]
    item = next(c for c in ref["criteria"] if c["id"].endswith("-L2"))
    assert item["title"] == "Scientific value and development of the idea"
    assert "studied elsewhere" in item["anchors"]["3"]
    assert ref["reading_inspiration"]
