"""Hypotheses, cross-hypothesis tests and evidence references (protocol v2).

The backend checks declared effects, not scientific truth. Only dispatched tests
consume the execution-count budget; history never consumes research capacity.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from oceanx import exploration_legacy as legacy
from oceanx.backend.store import RequestStore, RequestStoreError

Status = Literal["untested", "supported", "contested", "refuted", "established", "unverifiable"]
Relation = Literal["alternatives", "prerequisites"]
TERMINAL = {"established", "refuted", "unverifiable"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Hypothesis(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    claim: str = Field(min_length=1, max_length=1000)
    relation_to_children: Relation


class Discriminator(StrictModel):
    outcome: str = Field(min_length=1, max_length=1000)
    effects: dict[str, Literal["supported", "contested", "refuted", "established"]] = Field(
        min_length=1
    )


class Test(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    targets: list[str] = Field(min_length=1)
    method: str = Field(min_length=1, max_length=2000)
    discriminates: list[Discriminator] = Field(min_length=1)
    feasible: bool
    cost: float = Field(
        gt=0, allow_inf_nan=False, description="Relative cost, only breaks equal-depth ties"
    )
    infeasible_reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def valid_discriminators(self):
        if len(set(self.targets)) != len(self.targets):
            raise ValueError("Test targets must be unique")
        outcomes = [d.outcome for d in self.discriminates]
        if len(set(outcomes)) != len(outcomes):
            raise ValueError("Outcome labels must be unique")
        affected = {key for d in self.discriminates for key in d.effects}
        if affected != set(self.targets):
            raise ValueError(
                "Every target needs an effect; effects must reference this test's targets"
            )
        if not self.feasible and not self.infeasible_reason:
            raise ValueError("An infeasible test requires a reason")
        if (
            any(s == "established" for d in self.discriminates for s in d.effects.values())
            and len(self.targets) < 2
        ):
            raise ValueError("established requires at least two distinct test targets")
        return self


class TestResult(StrictModel):
    evidence_refs: list[str] = Field(min_length=1)
    outcome_observed: str = Field(min_length=1, max_length=1000)
    summary: str = Field(min_length=1, max_length=2000)


class ExplorationInput(StrictModel):
    action: Literal[
        "read",
        "start",
        "propose",
        "plan_test",
        "record",
        "mark_infeasible",
        "reflect",
        "pause",
        "resume",
    ]
    expected_revision: int | None = Field(default=None, ge=0)
    goal: str | None = Field(default=None, min_length=1, max_length=1000)
    mode: Literal["ideas", "iterative"] | None = None
    execution_budget: int | None = Field(
        default=None, ge=1, description="Maximum dispatched test attempts; default 20"
    )
    reflection_interval_percent: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="Default 25; only test executions advance this counter",
    )
    node_id: str = "root"
    candidates: list[Hypothesis] | None = Field(default=None, min_length=1)
    tests: list[Test] | None = Field(default=None, min_length=1)
    test_id: str | None = None
    result: TestResult | None = None
    coverage_complete: bool | None = None
    summary: str | None = Field(default=None, min_length=1, max_length=4000)
    completion_summary: str | None = Field(default=None, min_length=1, max_length=4000)
    evidence_offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def action_contract(self):
        if self.action != "read" and self.expected_revision is None:
            raise ValueError("Mutations require expected_revision")
        required = {
            "start": ("goal", "mode"),
            "propose": ("candidates",),
            "plan_test": ("tests",),
            "record": ("test_id", "result"),
            "reflect": ("coverage_complete", "summary"),
            "mark_infeasible": ("test_id", "summary"),
        }.get(self.action, ())
        if any(getattr(self, name) is None for name in required):
            raise ValueError(f"{self.action} requires {', '.join(required)}")
        allowed = {"action", "expected_revision"} | {
            "read": {"node_id", "evidence_offset"},
            "start": {"goal", "mode", "execution_budget", "reflection_interval_percent"},
            "propose": {"node_id", "candidates", "tests"},
            "plan_test": {"tests"},
            "record": {"test_id", "result"},
            "mark_infeasible": {"test_id", "summary"},
            "reflect": {"coverage_complete", "summary", "candidates", "tests"},
            "pause": {"completion_summary"},
            "resume": {"mode", "execution_budget", "reflection_interval_percent"},
        }[self.action]
        supplied = {
            name
            for name, field in ExplorationInput.model_fields.items()
            if name in self.model_fields_set and getattr(self, name) != field.default
        }
        if supplied - allowed:
            raise ValueError("Arguments are not applicable to this action")
        return self


def _children(tree, key):
    return [h for h in tree["hypotheses"].values() if h["parent"] == key]


def _depth(tree, key):
    depth = 0
    while tree["hypotheses"][key]["parent"] is not None:
        depth += 1
        key = tree["hypotheses"][key]["parent"]
    return depth


def _frontier(tree):
    return [
        t
        for t in tree["tests"].values()
        if t["feasible"]
        and t["status"] == "todo"
        and any(tree["hypotheses"][k]["status"] not in TERMINAL for k in t["targets"])
    ]


def _repair_needed(tree):
    covered = {
        k
        for t in tree["tests"].values()
        if t["feasible"] and t["status"] in {"todo", "running"}
        for k in t["targets"]
    }
    return [
        h["id"]
        for h in tree["hypotheses"].values()
        if h["status"] not in TERMINAL
        and (
            not _children(tree, h["id"])
            or (h["status"] == "contested" and h.get("status_source") == "test")
        )
        and h["id"] not in covered
    ]


def _set_status(tree, node, status, summary):
    before = node["status"]
    if before == status and node.get("summary") == summary:
        return
    node["history"].append({"status": before, "summary": node.get("summary")})
    node["status"], node["summary"] = status, summary
    if node["parent"] == "root" and status in TERMINAL and before != status:
        tree["reflection_pending"].append(f"{node['id']} became {status}")


def _synchronize(tree):
    for node in list(tree["hypotheses"].values()):
        if (
            node["status"] not in TERMINAL
            and node["decompose_depth_without_test"] >= 2
            and not _children(tree, node["id"])
            and not any(
                t["feasible"] and node["id"] in t["targets"] for t in tree["tests"].values()
            )
        ):
            reasons = [
                t["infeasible_reason"]
                for t in tree["tests"].values()
                if not t["feasible"] and node["id"] in t["targets"]
            ]
            _set_status(
                tree,
                node,
                "unverifiable",
                "No feasible test after two consecutive decompositions. " + " ".join(reasons),
            )
    # Children first; retain every child's summary, including unknown reasons.
    for key in sorted(tree["hypotheses"], key=lambda k: _depth(tree, k), reverse=True):
        node, children = tree["hypotheses"][key], _children(tree, key)
        if not children:
            continue
        # A direct evidence conflict needs another test, not a replay of the
        # same child synthesis. This is provenance, not another belief state.
        if node["status"] == "contested" and node.get("status_source") == "test":
            continue
        states = {c["status"] for c in children}
        if not states <= TERMINAL:
            if node["status"] in TERMINAL:
                _set_status(
                    tree,
                    node,
                    "contested" if "contested" in states else "untested",
                    "Awaiting nonterminal children; previous synthesis retained in history.",
                )
            continue
        if node["relation_to_children"] == "alternatives":
            status = (
                "established"
                if "established" in states
                else "refuted"
                if states == {"refuted"}
                else "unverifiable"
            )
        else:
            status = (
                "refuted"
                if "refuted" in states
                else "established"
                if states == {"established"}
                else "unverifiable"
            )
        summary = f"{node['claim']} [{status}; {node['relation_to_children']}]\n" + "\n".join(
            f"{c['id']} [{c['status']}]: {c['summary']}" for c in children
        )
        _set_status(tree, node, status, summary)
        node["status_source"] = "children"


def _all_terminal(tree):
    children = _children(tree, "root")
    return (
        bool(children)
        and all(c["status"] in TERMINAL for c in children)
        and not any(t["status"] == "running" for t in tree["tests"].values())
    )


def _recommend(tree):
    if tree["paused"]:
        return {"action": "paused"}
    running = [t["id"] for t in tree["tests"].values() if t["status"] == "running"]
    if running:
        return {"action": "await_results", "test_ids": running}
    if tree["reflection_pending"]:
        return {"action": "reflect", "reasons": tree["reflection_pending"]}
    if _all_terminal(tree):
        return {
            "action": "finish",
            "exit": "answered"
            if any(c["status"] == "established" for c in _children(tree, "root"))
            else "unable_to_answer",
        }
    if tree["executions_used"] >= tree["execution_budget"]:
        return {
            "action": "pause",
            "reason": "execution_budget_exhausted",
            "question_resolved": False,
        }
    missing = _repair_needed(tree)
    if missing:
        return {"action": "repair", "node_ids": missing}
    tests = sorted(
        _frontier(tree),
        key=lambda t: (
            min(_depth(tree, key) for d in t["discriminates"] for key in d["effects"]),
            t["cost"],
            t["id"],
        ),
    )
    if tests:
        return {"action": "test", "test_id": tests[0]["id"], "targets": tests[0]["targets"]}
    return {"action": "repair", "node_ids": _repair_needed(tree)}


def _view(tree, node_id="root"):
    if node_id not in tree["hypotheses"]:
        raise ValueError("Unknown hypothesis")
    lines = [tree["goal"]]

    def visit(key, prefix):
        children = _children(tree, key)
        for i, node in enumerate(children):
            last = i == len(children) - 1
            lines.append(
                prefix
                + ("└── " if last else "├── ")
                + f"{node['id']}: {node['claim']} [{node['status']}]"
            )
            visit(node["id"], prefix + ("    " if last else "│   "))

    visit("root", "")
    return {
        "schema_version": 2,
        "active": not tree["paused"],
        "revision": tree["revision"],
        "goal": tree["goal"],
        "mode": tree["mode"],
        "tree_text": "\n".join(lines),
        "hypotheses": tree["hypotheses"],
        "tests": tree["tests"],
        "children": _children(tree, node_id),
        "recommendation": _recommend(tree),
        "execution_budget": tree["execution_budget"],
        "executions_used": tree["executions_used"],
        "reflection_interval_percent": tree["reflection_interval_percent"],
        "reflection_pending": tree["reflection_pending"],
        "reflections": tree["reflections"],
        "stop_decision": tree.get("stop_decision"),
        "unverifiable": [
            {"id": h["id"], "claim": h["claim"], "summary": h["summary"]}
            for h in tree["hypotheses"].values()
            if h["status"] == "unverifiable"
        ],
    }


def _new_hypotheses(tree, parent_id, candidates):
    if parent_id not in tree["hypotheses"]:
        raise ValueError("Unknown parent")
    parent = tree["hypotheses"][parent_id]
    if parent_id != "root" and parent["status"] in TERMINAL:
        raise ValueError(
            "Do not decompose a terminal hypothesis; propose a distinct root alternative or add new feasible evidence"
        )
    tested = any(t["feasible"] and parent_id in t["targets"] for t in tree["tests"].values())
    depth = 0 if parent_id == "root" or tested else parent["decompose_depth_without_test"] + 1
    existing = {" ".join(h["claim"].casefold().split()) for h in tree["hypotheses"].values()}
    for draft in candidates:
        claim_key = " ".join(draft.claim.casefold().split())
        if draft.id in tree["hypotheses"] or claim_key in existing:
            raise ValueError("Duplicate hypothesis; reuse existing identity")
        existing.add(claim_key)
        tree["hypotheses"][draft.id] = {
            **draft.model_dump(),
            "parent": parent_id,
            "status": "untested",
            "summary": None,
            "history": [],
            "decompose_depth_without_test": depth,
        }


def _add_tests(tree, tests):
    for draft in tests or []:
        if draft.id in tree["tests"]:
            raise ValueError("Test identity already exists; keep executed tests immutable")
        if any(k == "root" or k not in tree["hypotheses"] for k in draft.targets):
            raise ValueError("Test must reference existing non-root hypotheses")
        tree["tests"][draft.id] = {
            **draft.model_dump(),
            "status": "todo" if draft.feasible else "infeasible",
            "result": None,
        }
        if draft.feasible:
            for key in draft.targets:
                node = tree["hypotheses"][key]
                if node["status"] == "unverifiable":
                    _set_status(
                        tree,
                        node,
                        "untested",
                        "New feasible test available; prior limitation retained in history.",
                    )


def _load(db, workspace_id, task_id):
    task = db.execute(
        "SELECT workspace_id FROM research_tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    if task is None or task["workspace_id"] != workspace_id:
        raise RequestStoreError("Exploration requires the bound research task")
    row = db.execute(
        "SELECT * FROM research_exploration_trees WHERE task_id = ? AND workspace_id = ?",
        (task_id, workspace_id),
    ).fetchone()
    return (json.loads(row["tree_json"]), row["revision"]) if row else (None, 0)


def _save(db, workspace_id, task_id, tree, revision):
    tree["revision"] = revision + 1
    db.execute(
        "INSERT INTO research_exploration_trees VALUES (?, ?, ?, ?) "
        "ON CONFLICT(task_id) DO UPDATE SET revision=excluded.revision, tree_json=excluded.tree_json",
        (task_id, workspace_id, tree["revision"], json.dumps(tree)),
    )


def begin_tests(store, workspace_id, task_id, test_ids):
    """Reserve attempts before external execution, inside the durable assign action.

    Failed/uncertain dispatches remain charged and running until settled. Retrying
    a completed tool receipt never enters this function again.
    """
    with store._transaction() as db:
        tree, revision = _load(db, workspace_id, task_id)
        if not tree or tree.get("schema_version") != 2:
            raise ValueError("A v2 research tree is required for test dispatch")
        if tree["paused"] or tree["mode"] != "iterative":
            raise ValueError("Resume iterative research before executing tests")
        if not test_ids or len(set(test_ids)) != len(test_ids):
            raise ValueError("Supply unique research_test_ids for this execution wave")
        if tree["reflection_pending"] or _repair_needed(tree):
            raise ValueError(
                "Restore the invariant and complete pending reflection before dispatch"
            )
        if not set(test_ids) <= {t["id"] for t in _frontier(tree)}:
            raise ValueError("Only feasible, unexecuted frontier tests can be dispatched")
        if tree["executions_used"] + len(test_ids) > tree["execution_budget"]:
            raise ValueError("Execution budget exhausted; pause or explicitly raise the budget")
        for key in test_ids:
            tree["tests"][key]["status"] = "running"
            tree["tests"][key]["attempt_charged"] = True
        tree["executions_used"] += len(test_ids)
        step = tree["reflection_interval_percent"]
        bucket = tree["executions_used"] * 100 // (tree["execution_budget"] * step)
        if bucket > tree["reflection_bucket"]:
            tree["reflection_pending"].append(
                f"Execution budget crossed periodic reflection interval ({step}%)"
            )
            tree["reflection_bucket"] = bucket
        _save(db, workspace_id, task_id, tree, revision)
        return _view(tree)


def exploration_action(
    store: RequestStore,
    workspace_id: str,
    task_id: str,
    args: ExplorationInput,
    *,
    belief_reward=None,
):
    # Python-only compatibility. The registered tool schema exposes only v2.
    if isinstance(args, legacy.ExplorationInput):
        return legacy.exploration_action(
            store, workspace_id, task_id, args, belief_reward=belief_reward
        )
    with store._transaction() as db:
        tree, revision = _load(db, workspace_id, task_id)
        if tree and tree.get("schema_version") != 2:
            if args.action != "read":
                raise ValueError(
                    "Historical v1 tree is read-only in v2; use a new research task for the new protocol"
                )
            return {
                **legacy._view(tree, args.node_id),
                "schema_version": 1,
                "legacy_read_only": True,
            }
        if args.action == "read":
            result = _view(tree, args.node_id) if tree else {"active": False, "revision": 0}
            rows = (
                db.execute(
                    "SELECT observation_id, request_id, work_order_id, kind, statement, outcome "
                    "FROM research_observations WHERE task_id = ? AND workspace_id = ? "
                    "ORDER BY created_at DESC, observation_id DESC LIMIT 8 OFFSET ?",
                    (task_id, workspace_id, args.evidence_offset),
                ).fetchall()
                if tree
                else []
            )
            result["recent_evidence"] = [
                {**dict(r), "statement": r["statement"][:400]} for r in rows
            ]
            result["next_evidence_offset"] = args.evidence_offset + 8 if len(rows) == 8 else None
            return result
        if args.expected_revision != revision:
            raise RequestStoreError("Exploration revision changed; read before retrying")
        if args.action == "start":
            if tree:
                raise ValueError("Task already has a tree; read or resume it")
            tree = {
                "schema_version": 2,
                "revision": 0,
                "goal": args.goal,
                "mode": args.mode,
                "paused": False,
                "execution_budget": args.execution_budget or 20,
                "executions_used": 0,
                "reflection_interval_percent": args.reflection_interval_percent or 25,
                "reflection_bucket": 0,
                "reflection_pending": [],
                "reflections": [],
                "tests": {},
                "hypotheses": {
                    "root": {
                        "id": "root",
                        "parent": None,
                        "claim": args.goal,
                        "status": "untested",
                        "relation_to_children": "alternatives",
                        "summary": None,
                        "history": [],
                        "decompose_depth_without_test": 0,
                    }
                },
            }
        elif tree is None:
            raise ValueError("Start research first")
        elif args.action == "resume":
            if args.execution_budget is not None:
                if args.execution_budget < tree["executions_used"]:
                    raise ValueError("Budget cannot be less than dispatched tests")
                tree["execution_budget"] = args.execution_budget
            if args.reflection_interval_percent is not None:
                tree["reflection_interval_percent"] = args.reflection_interval_percent
            tree["reflection_bucket"] = (
                tree["executions_used"]
                * 100
                // (tree["execution_budget"] * tree["reflection_interval_percent"])
            )
            tree["paused"] = False
            tree.pop("stop_decision", None)
            if args.mode:
                tree["mode"] = args.mode
        elif args.action == "pause":
            if args.completion_summary:
                if tree["mode"] == "iterative" and (
                    not _all_terminal(tree) or tree["reflection_pending"]
                ):
                    raise ValueError(
                        "Complete all root hypotheses and pending reflection before final classification"
                    )
                answered = any(c["status"] == "established" for c in _children(tree, "root"))
                tree["stop_decision"] = {
                    "exit": "ideas_delivered"
                    if tree["mode"] == "ideas"
                    else "answered"
                    if answered
                    else "unable_to_answer",
                    "question_resolved": tree["mode"] == "iterative" and answered,
                    "summary": args.completion_summary
                    + "\n"
                    + (tree["hypotheses"]["root"].get("summary") or ""),
                }
            else:
                tree["stop_decision"] = {
                    "exit": "budget_exhausted"
                    if tree["executions_used"] >= tree["execution_budget"]
                    else "interrupted",
                    "question_resolved": False,
                    "next_step": _recommend(tree),
                }
            tree["paused"] = True
        else:
            if tree["paused"]:
                raise ValueError("Resume before updating research")
            if args.action == "propose":
                _new_hypotheses(tree, args.node_id, args.candidates)
                _add_tests(tree, args.tests)
            elif args.action == "plan_test":
                _add_tests(tree, args.tests)
            elif args.action == "reflect":
                if any(t["status"] == "running" for t in tree["tests"].values()):
                    raise ValueError(
                        "Record or settle the running tests before reflecting on this wave"
                    )
                if not tree["reflection_pending"]:
                    raise ValueError("No reflection trigger is pending")
                if args.coverage_complete and (args.candidates or args.tests):
                    raise ValueError("Coverage complete reflection must not manufacture branches")
                if not args.coverage_complete and not args.candidates:
                    raise ValueError("Incomplete coverage requires new root hypotheses")
                tree["reflections"].append(
                    {
                        "reasons": tree["reflection_pending"],
                        "summary": args.summary,
                        "coverage_complete": args.coverage_complete,
                    }
                )
                tree["reflection_pending"] = []
                if args.candidates:
                    _new_hypotheses(tree, "root", args.candidates)
                    _add_tests(tree, args.tests)
            elif args.action == "mark_infeasible":
                test = tree["tests"].get(args.test_id)
                if not test or test["status"] not in {"todo", "running"}:
                    raise ValueError("Only a pending/running test can become infeasible")
                test["feasible"], test["status"] = False, "infeasible"
                test["infeasible_reason"] = args.summary
            elif args.action == "record":
                test = tree["tests"].get(args.test_id)
                if not test or test["status"] != "running":
                    raise ValueError("Record evidence only for a dispatched, unfinished test")
                discriminator = next(
                    (
                        d
                        for d in test["discriminates"]
                        if d["outcome"] == args.result.outcome_observed
                    ),
                    None,
                )
                for ref in args.result.evidence_refs:
                    if (
                        db.execute(
                            "SELECT 1 FROM research_observations WHERE observation_id = ? AND task_id = ? AND workspace_id = ?",
                            (ref, task_id, workspace_id),
                        ).fetchone()
                        is None
                    ):
                        raise ValueError("Evidence must exist in this task's observation journal")
                test["result"], test["status"] = args.result.model_dump(), "done"
                # Unexpected evidence remains immutable and consumes its attempt;
                # no invented effect. The Coordinator must plan a new valid test.
                for key, proposed in (discriminator["effects"] if discriminator else {}).items():
                    node = tree["hypotheses"][key]
                    old = node["status"]
                    opposite = (old in {"supported", "established"} and proposed == "refuted") or (
                        old == "refuted" and proposed in {"supported", "established"}
                    )
                    status = "contested" if opposite else proposed
                    if status == "established" and len(test["targets"]) < 2:
                        raise ValueError("established requires targets >= 2")
                    summary = f"Test {test['id']}; evidence {', '.join(args.result.evidence_refs)}: {args.result.summary}"
                    if opposite:
                        summary += "\nConflicting prior evidence: " + (node.get("summary") or old)
                    _set_status(tree, node, status, summary)
                    node["status_source"] = "test"
            _synchronize(tree)
        _save(db, workspace_id, task_id, tree, revision)
        return _view(tree)
