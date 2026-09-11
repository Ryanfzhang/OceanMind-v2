"""Hypotheses, cross-hypothesis tests and evidence references (protocol v2).

The Coordinator judges scientific states and answer sufficiency. Test records
are facts, never automatic status effects. Existing execution budgets and durable
identities protect runtime work without prescribing a scientific workflow.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from oceanx import exploration_legacy as legacy
from oceanx.backend.store import RequestStore, RequestStoreError
from oceanx.research_learning import ResearchObservationDraft

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
    interpretation: str | None = Field(default=None, max_length=2000)


class Test(StrictModel):
    id: str = Field(
        default_factory=lambda: f"test_{uuid4().hex}", pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$"
    )
    targets: list[str] = Field(default_factory=list)
    method: str = Field(min_length=1, max_length=2000)
    discriminates: list[Discriminator] = Field(default_factory=list)
    purpose: Literal["exploratory", "discrimination", "condition_check", "numerical_audit"] = (
        "exploratory"
    )
    condition_checked: str | None = Field(default=None, max_length=1000)
    feasible: bool = True
    cost: float = Field(
        default=1,
        gt=0,
        allow_inf_nan=False,
        description="Relative cost, only breaks equal-depth ties",
    )
    infeasible_reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def valid_discriminators(self):
        if len(set(self.targets)) != len(self.targets):
            raise ValueError("Test targets must be unique")
        outcomes = [d.outcome for d in self.discriminates]
        if len(set(outcomes)) != len(outcomes):
            raise ValueError("Outcome labels must be unique")
        return self


class TestResult(StrictModel):
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="Optional existing task observation IDs; Expert code is cited with execution_id",
    )
    outcome_observed: str = Field(min_length=1, max_length=1000)
    summary: str = Field(min_length=1, max_length=2000)


class ExplorationInput(StrictModel):
    action: Literal[
        "read",
        "start",
        "propose",
        "plan_test",
        "record",
        "adjudicate",
        "mark_infeasible",
        "reflect",
        "pause",
        "resume",
    ]
    expected_revision: int | None = Field(default=None, ge=0)
    goal: str | None = Field(default=None, min_length=1, max_length=1000)
    mode: Literal["ideas", "iterative"] | None = None
    execution_budget: int | None = Field(
        default=None, ge=1, description="Maximum reserved execution attempts; default 20"
    )
    reflection_interval_percent: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="Historical planning hint; reflection is optional and never gates execution",
    )
    node_id: str = "root"
    candidates: list[Hypothesis] | None = Field(default=None, min_length=1)
    tests: list[Test] | None = Field(default=None, min_length=1)
    test_id: str | None = None
    result: TestResult | None = None
    status: Status | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    coverage_complete: bool | None = None
    summary: str | None = Field(default=None, min_length=1, max_length=4000)
    completion_summary: str | None = Field(default=None, min_length=1, max_length=4000)
    decision: Literal["answered", "unable_to_answer"] | None = None
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
            "adjudicate": ("status",),
            "reflect": ("summary",),
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
            "adjudicate": {"node_id", "status", "summary", "evidence_refs", "test_id"},
            "mark_infeasible": {"test_id", "summary"},
            "reflect": {"coverage_complete", "summary", "candidates", "tests"},
            "pause": {"completion_summary", "decision"},
            "resume": {"mode", "execution_budget", "reflection_interval_percent"},
        }[self.action]
        supplied = {
            name
            for name, field in ExplorationInput.model_fields.items()
            if name in self.model_fields_set
            and getattr(self, name) != field.get_default(call_default_factory=True)
        }
        if supplied - allowed:
            raise ValueError("Arguments are not applicable to this action")
        return self


class ExpertTestInput(StrictModel):
    """Facts-only interface; the tool binds the task, WorkOrder and node scope."""

    action: Literal["read", "plan_test", "record"]
    tests: list[Test] | None = Field(default=None, min_length=1, max_length=16)
    test_id: str | None = None
    result: TestResult | None = None
    execution_id: str | None = None

    @model_validator(mode="after")
    def action_contract(self):
        if self.action == "read" and any(
            v is not None for v in (self.tests, self.test_id, self.result, self.execution_id)
        ):
            raise ValueError("read accepts no test mutations")
        if self.action == "plan_test" and (
            not self.tests
            or any(v is not None for v in (self.test_id, self.result, self.execution_id))
        ):
            raise ValueError("plan_test requires tests without a result")
        if self.action == "record" and (not self.test_id or self.result is None):
            raise ValueError("record requires test_id and result; include tests for a new test")
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
    return [t for t in tree["tests"].values() if t["feasible"] and t["status"] == "todo"]


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


def _set_status(tree, node, status, summary, *, evidence_refs=(), test_id=None):
    del tree
    before = {
        name: node.get(name)
        for name in ("status", "summary", "evidence_refs", "test_id", "status_source")
    }
    updated = {
        "status": status,
        "summary": summary,
        "evidence_refs": list(evidence_refs),
        "test_id": test_id,
        "status_source": "coordinator",
    }
    if before != updated:
        node["history"].append(before)
        node.update(updated)


def _synchronize(tree):
    """Refresh child summaries without changing scientific status or judgment."""
    for key in sorted(tree["hypotheses"], key=lambda k: _depth(tree, k), reverse=True):
        node, children = tree["hypotheses"][key], _children(tree, key)
        if children:
            node["children_summary"] = "\n".join(
                f"{c['id']} [{c['status']}]: {c.get('summary') or ''}"
                + ("\n" + c["children_summary"] if c.get("children_summary") else "")
                for c in children
            )


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
    if tree["executions_used"] >= tree["execution_budget"]:
        return {"action": "pause", "reason": "execution_budget_exhausted"}
    tests = sorted(
        _frontier(tree),
        key=lambda t: (
            min((_depth(tree, key) for key in t["targets"]), default=0),
            t["cost"],
            t["id"],
        ),
    )
    if tests:
        return {"action": "test", "test_id": tests[0]["id"], "targets": tests[0]["targets"]}
    return {"action": "assess", "node_ids": _repair_needed(tree)}


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
        "stop_request_id": tree.get("stop_request_id"),
        "unverifiable": [
            {"id": h["id"], "claim": h["claim"], "summary": h["summary"]}
            for h in tree["hypotheses"].values()
            if h["status"] == "unverifiable"
        ],
    }


def _new_hypotheses(tree, parent_id, candidates):
    if parent_id not in tree["hypotheses"]:
        raise ValueError("Unknown parent")
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
            "decompose_depth_without_test": 0,
        }


def _add_tests(tree, tests, *, work_order_id=None, retrospective=False):
    added = []
    for draft in tests or []:
        if draft.id in tree["tests"]:
            existing = tree["tests"][draft.id]
            if (
                work_order_id is not None
                and existing.get("work_order_id") == work_order_id
                and all(existing.get(key) == value for key, value in draft.model_dump().items())
            ):
                continue
            raise ValueError("Test identity already exists; keep executed tests immutable")
        if any(k == "root" or k not in tree["hypotheses"] for k in draft.targets):
            raise ValueError("Test must reference existing non-root hypotheses")
        now = datetime.now(UTC).isoformat()
        tree["tests"][draft.id] = {
            **draft.model_dump(),
            "status": "todo" if draft.feasible else "infeasible",
            "result": None,
            "work_order_id": work_order_id,
            "recorded_at": now,
            "registered_at": None if retrospective else now,
        }
        added.append(draft.id)
    return added


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


def _validate_evidence(db, workspace_id, task_id, refs):
    for ref in refs:
        if (
            db.execute(
                "SELECT 1 FROM research_observations WHERE observation_id = ? "
                "AND task_id = ? AND workspace_id = ?",
                (ref, task_id, workspace_id),
            ).fetchone()
            is None
        ):
            raise ValueError("Evidence must exist in this task's observation journal")


def _record_result(db, workspace_id, task_id, test, result):
    _validate_evidence(db, workspace_id, task_id, result.evidence_refs)
    if test["status"] == "done":
        if test["result"] == result.model_dump():
            return False
        raise ValueError("Keep a recorded result immutable; use a new test for new evidence")
    test["result"], test["status"] = result.model_dump(), "done"
    test["result_recorded_at"] = datetime.now(UTC).isoformat()
    return True


def begin_tests(store, workspace_id, task_id, test_ids, *, execution_id=None, execution_ids=None):
    """Reserve research attempts by real Expert WorkOrder, not by Test count.

    The router passes all Expert WorkOrder IDs in the selected wave. Recovered
    WorkOrders retain their reservation; a new follow-up WorkOrder costs another
    attempt. Runtime code/token budgets still account for the work within it.
    Test status and feasibility are research annotations, not dispatch gates:
    the Coordinator may revisit the same Test to retry or supplement evidence.
    Existing results and hypothesis judgments are never reset by a reservation.
    The singular execution_id and implicit per-test keys support older callers.
    """
    with store._transaction() as db:
        tree, revision = _load(db, workspace_id, task_id)
        if not tree or tree.get("schema_version") != 2:
            raise ValueError("A v2 research tree is required for test dispatch")
        if tree["paused"] or tree["mode"] != "iterative":
            raise ValueError("Resume iterative research before executing tests")
        if not test_ids or len(set(test_ids)) != len(test_ids):
            raise ValueError("Supply unique research_test_ids for this execution wave")
        if execution_id is not None and execution_ids is not None:
            raise ValueError("Supply one execution identity form")
        explicit = execution_id is not None or execution_ids is not None
        keys = (
            list(dict.fromkeys(execution_ids))
            if execution_ids is not None
            else [execution_id]
            if execution_id is not None
            else [f"test:{key}" for key in test_ids]
        )
        if not keys:
            raise ValueError("An execution reservation needs an execution identity")
        tests = []
        for key in test_ids:
            test = tree["tests"].get(key)
            if test is None:
                raise ValueError(f"Unknown research test in this task: {key}")
            linked = set(test.get("execution_ids", ()))
            if test.get("execution_id"):
                linked.add(test["execution_id"])
            tests.append((test, linked))
        charged = tree.setdefault("execution_ids", [])
        new_keys = [key for key in keys if key not in charged]
        if tree["executions_used"] + len(new_keys) > tree["execution_budget"]:
            raise ValueError("Execution budget exhausted; pause or explicitly raise the budget")
        changed = bool(new_keys)
        for test, linked in tests:
            if test["status"] == "todo":
                test["status"] = "running"
                changed = True
            if not test.get("attempt_charged"):
                test["attempt_charged"] = True
                changed = True
            if explicit and not set(keys) <= linked:
                test["execution_ids"] = list(dict.fromkeys((*sorted(linked), *keys)))
                if len(test["execution_ids"]) == 1:
                    test["execution_id"] = test["execution_ids"][0]
                changed = True
        charged.extend(new_keys)
        tree["executions_used"] += len(new_keys)
        if changed:
            _save(db, workspace_id, task_id, tree, revision)
        return _view(tree)


def exploration_action(
    store: RequestStore,
    workspace_id: str,
    task_id: str,
    args: ExplorationInput,
    *,
    belief_reward=None,
    request_id=None,
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
            tree.pop("stop_request_id", None)
            if args.mode:
                tree["mode"] = args.mode
        elif args.action == "pause":
            if args.completion_summary or args.decision:
                if any(t["status"] == "running" for t in tree["tests"].values()):
                    raise ValueError("Settle running tests before final completion")
                decision = (
                    "ideas_delivered" if tree["mode"] == "ideas" else args.decision or "answered"
                )
                tree["stop_decision"] = {
                    "exit": decision,
                    "question_resolved": decision == "answered",
                    "summary": args.completion_summary or "",
                }
            else:
                tree["stop_decision"] = {
                    "exit": "budget_exhausted"
                    if tree["executions_used"] >= tree["execution_budget"]
                    else "interrupted",
                    "question_resolved": False,
                    "next_step": _recommend(tree),
                }
            tree["stop_request_id"] = request_id
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
                if not test:
                    raise ValueError("Unknown test; include it in plan_test first")
                if not _record_result(db, workspace_id, task_id, test, args.result):
                    return _view(tree)
            elif args.action == "adjudicate":
                node = tree["hypotheses"].get(args.node_id)
                if node is None:
                    raise ValueError("Unknown hypothesis")
                test = tree["tests"].get(args.test_id) if args.test_id else None
                if args.test_id and (not test or args.node_id not in test["targets"]):
                    raise ValueError("Judgment test must reference this hypothesis")
                if args.status == "established" and (not test or len(set(test["targets"])) < 2):
                    raise ValueError("established requires targets >= 2 in its cited test")
                _validate_evidence(db, workspace_id, task_id, args.evidence_refs)
                _set_status(
                    tree,
                    node,
                    args.status,
                    args.summary,
                    evidence_refs=args.evidence_refs,
                    test_id=args.test_id,
                )
            _synchronize(tree)
        _save(db, workspace_id, task_id, tree, revision)
        return _view(tree)


def _expert_view(tree, authorized_nodes, work_order_id):
    """Project only authorized claims and the caller's own in-scope test facts."""
    return {
        "active": bool(tree and not tree["paused"]),
        "hypotheses": {
            key: {name: node.get(name) for name in ("id", "claim", "status")}
            for key, node in (tree["hypotheses"] if tree else {}).items()
            if key in authorized_nodes
        },
        "tests": {
            test.get("local_id", key): test
            for key, test in (tree["tests"] if tree else {}).items()
            if test.get("work_order_id") == work_order_id
            and set(test["targets"]) <= authorized_nodes
        },
    }


def _read_test_notes(rows):
    """Reassemble larger facts inside the journal's existing statement limit."""
    tests, chunks = {}, {}
    for row in rows:
        try:
            payload = json.loads(row["statement"])
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("kind") == "expert_test_fact_chunk":
            snapshot = payload.get("snapshot")
            part, total = payload.get("part"), payload.get("total")
            if (
                not isinstance(snapshot, str)
                or not isinstance(part, int)
                or not isinstance(total, int)
            ):
                continue
            if not 0 <= part < total or not isinstance(payload.get("text"), str):
                continue
            pieces = chunks.setdefault(snapshot, {})
            pieces[part] = payload["text"]
            if len(pieces) != total or set(pieces) != set(range(total)):
                continue
            encoded = "".join(pieces[i] for i in range(total))
            if hashlib.sha256(encoded.encode()).hexdigest() != snapshot:
                continue
            payload = json.loads(encoded)
            del chunks[snapshot]
        if payload.get("kind") == "expert_test_fact" and isinstance(payload.get("test"), dict):
            test = payload["test"]
            tests[test["id"]] = test
    return tests


def _write_test_note(store, db, workspace_id, task_id, work_order_id, request_id, test):
    encoded = json.dumps({"kind": "expert_test_fact", "test": test}, ensure_ascii=False)
    statements = [encoded]
    if len(encoded) > 8000:
        pieces = [encoded[i : i + 3000] for i in range(0, len(encoded), 3000)]
        snapshot = hashlib.sha256(encoded.encode()).hexdigest()
        statements = [
            json.dumps(
                {
                    "kind": "expert_test_fact_chunk",
                    "snapshot": snapshot,
                    "part": i,
                    "total": len(pieces),
                    "text": piece,
                },
                ensure_ascii=False,
            )
            for i, piece in enumerate(pieces)
        ]
    for statement in statements:
        store._insert_research_observation(
            db,
            ResearchObservationDraft(
                workspace_id=workspace_id,
                task_id=task_id,
                request_id=request_id,
                work_order_id=work_order_id,
                kind="method",
                statement=statement,
                evidence_refs=tuple((test.get("result") or {}).get("evidence_refs", [])),
                outcome="retrieved",
            ),
        )


def expert_test_action(
    store: RequestStore,
    workspace_id: str,
    task_id: str,
    work_order_id: str,
    authorized_nodes,
    args: ExpertTestInput,
):
    """Record optional test facts without changing or creating a hypothesis tree.

    The tool supplies server-bound identity and node scope. A result may include
    a new retrospective test; no pre-registration receipt is required. Ordinary
    tasks use existing method observations, and test summaries do not charge a
    second execution after the Expert runtime has already accounted for its work.
    """
    with store._transaction() as db:
        tree, revision = _load(db, workspace_id, task_id)
        work = db.execute(
            "SELECT workspace_id, work_order_json, authority FROM team_work_records "
            "WHERE work_order_id = ?",
            (work_order_id,),
        ).fetchone()
        if work is None or work["workspace_id"] != workspace_id or work["authority"] != "expert":
            raise RequestStoreError("Test facts require the bound Expert WorkOrder")
        order = json.loads(work["work_order_json"])
        if order.get("task_id") != task_id:
            raise RequestStoreError("Expert WorkOrder belongs to another task")
        scope = set(authorized_nodes)
        if tree and tree.get("schema_version") != 2:
            if args.action != "read":
                raise ValueError("Historical v1 tree is read-only; use a new research task")
            return {"active": False, "legacy_read_only": True, "hypotheses": {}, "tests": {}}
        if any(key == "root" or not tree or key not in tree["hypotheses"] for key in scope):
            raise ValueError("Authorized nodes must exist in the bound research tree")
        execution = None
        if args.execution_id:
            execution = db.execute(
                "SELECT task_id, workspace_id, work_order_id, started_at FROM code_executions "
                "WHERE execution_id = ?",
                (args.execution_id,),
            ).fetchone()
            if execution is None or (
                execution["task_id"],
                execution["workspace_id"],
                execution["work_order_id"],
            ) != (task_id, workspace_id, work_order_id):
                raise ValueError("Execution must belong to this Expert WorkOrder")
        notes = tree if tree else {"hypotheses": {}, "tests": {}, "paused": False}
        if tree is None:
            rows = db.execute(
                "SELECT statement FROM research_observations WHERE workspace_id = ? "
                "AND task_id = ? AND work_order_id = ? AND kind = 'method' "
                "ORDER BY created_at, rowid",
                (workspace_id, task_id, work_order_id),
            ).fetchall()
            notes["tests"] = _read_test_notes(rows)
        if args.action == "read":
            view = _expert_view(notes, scope, work_order_id)
            view["active"] = bool(tree and not tree["paused"])
            return view

        # Late results remain facts after a pause; they never reopen the question.
        def own_test_id(local_id):
            existing = notes["tests"].get(local_id)
            if existing is not None and existing.get("work_order_id") == work_order_id:
                return local_id
            identity = f"{work_order_id}\0{local_id}".encode()
            return "xt_" + hashlib.sha256(identity).hexdigest()[:32]

        drafts = []
        local_ids = {}
        for draft in args.tests or []:
            if not set(draft.targets) <= scope:
                raise ValueError("Test targets exceed the Expert's authorized nodes")
            key = own_test_id(draft.id)
            drafts.append(draft.model_copy(update={"id": key}))
            local_ids[key] = draft.id
        changed_ids = _add_tests(
            notes, drafts, work_order_id=work_order_id, retrospective=args.action == "record"
        )
        for key in changed_ids:
            notes["tests"][key]["local_id"] = local_ids[key]
        if args.action == "record":
            key = own_test_id(args.test_id)
            test = notes["tests"].get(key)
            if not test or test.get("work_order_id") != work_order_id:
                raise ValueError("Experts may record only their own tests")
            if not set(test["targets"]) <= scope:
                raise ValueError("Test targets exceed the Expert's authorized nodes")
            if execution is not None and test.get("execution_id") not in (None, args.execution_id):
                raise ValueError("Keep a recorded test execution immutable")
            if _record_result(db, workspace_id, task_id, test, args.result):
                changed_ids.append(key)
                if execution is not None:
                    test["execution_id"] = args.execution_id
                    test["executed_at"] = execution["started_at"]
        if tree is not None:
            if changed_ids:
                _save(db, workspace_id, task_id, tree, revision)
        else:
            for key in dict.fromkeys(changed_ids):
                test = notes["tests"][key]
                _write_test_note(
                    store,
                    db,
                    workspace_id,
                    task_id,
                    work_order_id,
                    order["parent_request_id"],
                    test,
                )
        view = _expert_view(notes, scope, work_order_id)
        view["active"] = bool(tree and not tree["paused"])
        return view
