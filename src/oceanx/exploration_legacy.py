"""Historical v1 research tree protocol, retained for archived research.

Evidence gaps determine the next experiment. Selection never calls a model.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from oceanx.backend.store import RequestStore, RequestStoreError


class ResearchPriority(BaseModel):
    priority: Literal["contradiction", "key_gap", "supporting"] = "key_gap"
    cost: Literal["low", "medium", "high"] = "medium"


class Candidate(ResearchPriority):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    idea: str = Field(min_length=1, max_length=600)
    rationale: str = Field(min_length=1, max_length=800)
    test: str = Field(min_length=1, max_length=800)


class Feedback(ResearchPriority):
    priority: Literal["contradiction", "key_gap", "supporting"] | None = None
    cost: Literal["low", "medium", "high"] | None = None
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    outcome: Literal["supported", "contradicted", "inconclusive", "deferred"]
    summary: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    information_gain: float = Field(default=0, ge=0, le=1)
    expandable: bool = True
    follow_up: str = Field(default="", max_length=800, description="Remaining testable question that could change this conclusion; empty when none. Block missing-data work instead of inventing tests.")
    branch_status: Literal["open", "solved", "exhausted", "blocked"] = Field(
        default="open",
        description=(
            "Coordinator decision, separate from this test's outcome. supported normally remains "
            "open. solved means this node's question has been answered with "
            "key checks passed, not that the whole research goal is resolved; exhausted means "
            "no useful continuation; blocked means missing "
            "evidence prevents progress. Explain the decision in summary."
        ),
    )

    @model_validator(mode="after")
    def require_evidence(self):
        if self.outcome != "deferred" and not self.evidence_ids:
            raise ValueError("Scientific feedback requires task observation IDs")
        if self.outcome == "deferred" and self.information_gain != 0:
            raise ValueError("Deferred work cannot earn a reward")
        if self.branch_status == "solved" and self.outcome not in {"supported", "contradicted"}:
            raise ValueError("A solved node requires a supported or contradicted verdict")
        if self.follow_up and self.branch_status == "solved":
            raise ValueError("Resolve the follow-up or keep the branch open")
        return self


class ExplorationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    action: Literal["read", "start", "propose", "record", "pause", "resume"]
    expected_revision: int | None = Field(default=None, ge=0)
    goal: str | None = Field(default=None, min_length=1, max_length=1000)
    completion_summary: str | None = Field(
        default=None, min_length=1, max_length=1000,
        description=(
            "For pause only: Coordinator explanation of why the evidence answers the original "
            "goal. Omit for interruptions or unresolved research."
        ),
    )
    mode: Literal["ideas", "iterative"] | None = None
    node_budget: int | None = Field(default=None, ge=1, le=64)
    node_id: str = Field(default="root", min_length=1, max_length=64)
    evidence_offset: int = Field(default=0, ge=0)
    candidates: list[Candidate] | None = Field(default=None, min_length=1)
    feedback: Feedback | None = None

    @model_validator(mode="after")
    def validate_action(self):
        if self.action != "read" and self.expected_revision is None:
            raise ValueError("Mutations require expected_revision from the last receipt")
        if self.action == "start" and (not self.goal or not self.mode):
            raise ValueError("start requires the user-requested goal and scope mode")
        if self.action == "propose" and not self.candidates:
            raise ValueError("propose requires candidates")
        if self.action == "record" and not self.feedback:
            raise ValueError("record requires feedback")
        allowed = {"action", "expected_revision", "node_id"}
        allowed |= {
            "read": {"evidence_offset"},
            "start": {"goal", "mode", "node_budget"},
            "propose": {"candidates"},
            "record": {"feedback"},
            "resume": {"mode", "node_budget"},
            "pause": {"completion_summary"},
        }.get(self.action, set())
        # The runtime subclasses this schema to inject tool_call_id. Only
        # domain fields participate in action checks. It also materializes
        # defaults before revalidation, so inactive default-valued fields must
        # remain valid. extra="forbid" still rejects unknown base-schema inputs.
        supplied = {
            name
            for name, field in ExplorationInput.model_fields.items()
            if name in self.model_fields_set and getattr(self, name) != field.default
        }
        if supplied - allowed:
            raise ValueError("Arguments are not applicable to this action")
        return self


def _statistics(tree):
    """Back up each experiment once per ancestor, even when reused in a synthesis node."""
    stats = {key: [0, 0.0] for key in tree["nodes"]}
    seen = {key: set() for key in tree["nodes"]}
    def depth(key):
        return 0 if tree["nodes"][key]["parent_id"] is None else 1 + depth(tree["nodes"][key]["parent_id"])
    # Prefer the concrete descendant experiment over a parent synthesis of it.
    for key in sorted(tree["nodes"], key=depth, reverse=True):
        node = tree["nodes"][key]
        feedback = node.get("feedback")
        if not feedback:
            continue
        identities = set(node.get("experiment_keys") or feedback.get("evidence_ids") or [key])
        reward = (node.get("belief_reward") or {}).get("reward", 0.0)
        while key is not None:
            if not seen[key].intersection(identities):
                stats[key][0] += 1
                stats[key][1] += reward
            seen[key].update(identities)
            key = tree["nodes"][key]["parent_id"]
    return stats


def _pending(tree):
    pending = []
    nodes = tree["nodes"]
    for key, node in nodes.items():
        if key == "root":
            continue
        feedback = node.get("feedback") or {}
        if feedback.get("branch_status") in {"solved", "blocked", "exhausted"} or feedback.get("outcome") in {"contradicted", "deferred"}:
            continue
        children = [n for n in nodes.values() if n["parent_id"] == key]
        # A parent's gap is represented by its children until their results are synthesized.
        if children:
            if all((n.get("feedback") or {}).get("branch_status") in {"solved", "blocked", "exhausted"} or (n.get("feedback") or {}).get("outcome") in {"contradicted", "deferred"} for n in children):
                action = "synthesize"
            else:
                continue
        elif not feedback:
            action = "test" if tree["mode"] == "iterative" else "expand"
        elif node.get("expandable"):
            action = "expand"
        else:
            action = "synthesize"
        priority = feedback.get("priority") or node.get("priority", "key_gap")
        cost = feedback.get("cost") or node.get("cost", "medium")
        pending.append((key, action, priority, cost))
    return pending


def _recommend(tree, stats):
    if tree["paused"]:
        return {"action": "paused"}
    pending = sorted(_pending(tree), key=lambda item: (
        {"contradiction": 0, "key_gap": 1, "supporting": 2}[item[2]],
        {"low": 0, "medium": 1, "high": 2}[item[3]],
        {"test": 0, "synthesize": 1, "expand": 2}[item[1]],
    ))
    budget_available = len(tree["nodes"]) - 1 < tree["node_budget"]
    for key, action, _, _ in pending:
        if action != "expand" or budget_available:
            return {"action": action, "node_id": key}
    if len(tree["nodes"]) == 1 and budget_available:
        return {"action": "expand", "node_id": "root"}
    return {
        "action": "pause" if tree["mode"] == "iterative" else "stop",
        "reason": "node_budget_exhausted" if pending and not budget_available else "no_eligible_branch",
        "question_resolved": False,
    }


def _sync_active_branch(tree):
    """Compatibility display only; selection never uses the previous branch."""
    tree.pop("active_branch_id", None)
    if tree["mode"] != "iterative" or tree["paused"]:
        return
    key = _recommend(tree, _statistics(tree)).get("node_id")
    if key and key != "root":
        while tree["nodes"][key]["parent_id"] != "root":
            key = tree["nodes"][key]["parent_id"]
        tree["active_branch_id"] = key


def _tree_text(tree):
    """Render persisted structure, never inferred relationships or execution steps."""
    nodes = tree["nodes"]

    def label(node):
        idea = " ".join(node["idea"].split())[:160]
        outcome = (node.get("feedback") or {}).get("outcome", "proposed")
        state = (node.get("feedback") or {}).get("branch_status", "open")
        return f"{idea} [{outcome}]" + (f" [{state}]" if state != "open" else "")

    lines = [" ".join(nodes["root"]["idea"].split())[:1000]]

    def visit(parent, prefix):
        children = [node for node in nodes.values() if node["parent_id"] == parent]
        for index, node in enumerate(children):
            last = index == len(children) - 1
            lines.append(prefix + ("└── " if last else "├── ") + label(node))
            visit(node["node_id"], prefix + ("    " if last else "│   "))

    visit("root", "")
    return "\n".join(lines)


def _view(tree, node_id):
    nodes = tree["nodes"]
    if node_id not in nodes:
        raise ValueError("Unknown exploration node")
    stats = _statistics(tree)
    path, key = [], node_id
    while key != "root" and len(path) < 3:
        path.append({k: v for k, v in nodes[key].items() if k != "feedback_history"})
        key = nodes[key]["parent_id"]
    return {
        "active": not tree["paused"],
        "revision": tree["revision"],
        "goal": tree["goal"],
        "mode": tree["mode"],
        "active_branch_id": tree.get("active_branch_id"),
        "node_budget": tree["node_budget"],
        "nodes_used": len(nodes) - 1,
        "stop_decision": tree.get("stop_decision"),
        "tree_text": _tree_text(tree),
        "recommendation": _recommend(tree, stats),
        "path": list(reversed(path)),
        "children": [
            {
                "node_id": k,
                "idea": n["idea"][:160],
                "expandable": n["expandable"],
                "outcome": (n.get("feedback") or {}).get("outcome", "proposed"),
                "branch_status": (n.get("feedback") or {}).get("branch_status", "open"),
                "priority": (n.get("feedback") or {}).get("priority") or n.get("priority", "key_gap"),
                "cost": (n.get("feedback") or {}).get("cost") or n.get("cost", "medium"),
                "follow_up": (n.get("feedback") or {}).get("follow_up", ""),
                "attempts": stats[k][0],
                "reward_sum": stats[k][1],
            }
            for k, n in nodes.items()
            if n["parent_id"] == node_id
        ],
        "reward_basis": "Evidence-gap priority: contradiction, key_gap, supporting; lower cost breaks ties; no model sampling",
    }


def exploration_action(
    store: RequestStore, workspace_id: str, task_id: str, args: ExplorationInput,
    *, belief_reward: dict | None = None,
):
    """Atomic optimistic task-scoped updates; ordinary tasks have no tree row."""
    with store._transaction() as db:
        task = db.execute(
            "SELECT workspace_id FROM research_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if task is None or task["workspace_id"] != workspace_id:
            raise RequestStoreError("Exploration requires the bound research task")
        row = db.execute(
            "SELECT * FROM research_exploration_trees WHERE task_id = ?", (task_id,)
        ).fetchone()
        tree = json.loads(row["tree_json"]) if row else None
        if tree:
            _sync_active_branch(tree)
        revision = row["revision"] if row else 0
        if args.action == "read":
            result = _view(tree, args.node_id) if tree else {"active": False, "revision": 0}
            # Compact IDs make the existing evidence journal usable without exposing raw logs.
            observations = (
                db.execute(
                    "SELECT observation_id, request_id, work_order_id, kind, statement, outcome FROM research_observations "
                    "WHERE task_id = ? AND workspace_id = ? ORDER BY created_at DESC, observation_id DESC LIMIT 8 OFFSET ?",
                    (task_id, workspace_id, args.evidence_offset),
                ).fetchall()
                if tree
                else []
            )
            result["recent_evidence"] = [
                {**dict(o), "statement": o["statement"][:400]} for o in observations
            ]
            result["next_evidence_offset"] = (
                args.evidence_offset + 8 if len(observations) == 8 else None
            )
            return result
        if args.expected_revision != revision:
            raise RequestStoreError("Exploration revision changed; read before retrying")
        if args.action == "start":
            if tree:
                raise ValueError("Task already has a tree; read or resume it")
            tree = {
                "goal": args.goal,
                "mode": args.mode,
                "node_budget": args.node_budget or 12,
                "paused": False,
                "nodes": {
                    "root": {
                        "node_id": "root",
                        "parent_id": None,
                        "idea": args.goal,
                        "expandable": True,
                    }
                },
            }
        elif tree is None:
            raise ValueError("No exploration tree; start with the research question as root")
        elif args.action == "pause":
            decision = _recommend({**tree, "paused": False}, _statistics(tree))
            tree["stop_decision"] = (
                decision
                if decision["action"] in {"stop", "pause"}
                else {
                    "action": "pause",
                    "reason": "interrupted",
                    "pending_recommendation": decision,
                    "question_resolved": False,
                }
            )
            if args.completion_summary is not None:
                unresolved = [key for key, _, priority, _ in _pending(tree) if priority != "supporting"]
                if tree["mode"] == "iterative" and unresolved:
                    raise ValueError("Cannot resolve the goal with testable critical gaps: " + ", ".join(unresolved) + "; resolve, block with evidence limits, or pause without completion_summary")
                tree["stop_decision"] = {
                    "action": "stop",
                    "reason": "question_resolved",
                    "question_resolved": True,
                    "summary": args.completion_summary,
                }
            tree["paused"] = True
        elif args.action == "resume":
            tree["paused"] = False
            tree.pop("stop_decision", None)
            if args.mode:
                tree["mode"] = args.mode
            if args.node_budget is not None:
                if args.node_budget < len(tree["nodes"]) - 1:
                    raise ValueError("Budget cannot be less than existing nodes")
                tree["node_budget"] = args.node_budget
        else:
            nodes = tree["nodes"]
            if args.node_id not in nodes:
                raise ValueError("Unknown exploration node")
            node = nodes[args.node_id]
            if args.action == "propose":
                if tree["paused"] or not node["expandable"]:
                    raise ValueError("Branch is paused or not expandable")
                if len(nodes) - 1 + len(args.candidates) > tree["node_budget"]:
                    raise ValueError("Node budget exhausted; do not expand the user's scope")
                existing = {" ".join(n["idea"].casefold().split()) for n in nodes.values()}
                for candidate in args.candidates:
                    normalized = " ".join(candidate.idea.casefold().split())
                    if normalized in existing:
                        raise ValueError("Duplicate idea; reuse its existing node")
                    existing.add(normalized)
                    key = f"idea_{len(nodes)}"
                    nodes[key] = {
                        "node_id": key,
                        "parent_id": args.node_id,
                        **candidate.model_dump(),
                        "expandable": True,
                    }
            elif args.action == "record":
                if args.node_id == "root":
                    raise ValueError("Record feedback on an idea, not the root")
                experiment_keys = []
                for evidence_id in args.feedback.evidence_ids:
                    evidence = db.execute(
                        "SELECT work_order_id FROM research_observations WHERE observation_id = ? "
                        "AND task_id = ? AND workspace_id = ?",
                        (evidence_id, task_id, workspace_id),
                    ).fetchone()
                    if evidence is None:
                        raise ValueError("Evidence must exist in this task's observation journal")
                    experiment_keys.append(evidence["work_order_id"] or evidence_id)
                history = node.setdefault("feedback_history", [])
                if len(history) >= 8:
                    raise ValueError("Feedback history full; add a follow-up idea for a new test")
                node["experiment_keys"] = sorted(set(experiment_keys))
                if belief_reward is not None:
                    node["belief_reward"] = belief_reward
                elif node.get("feedback") != args.feedback.model_dump():
                    node.pop("belief_reward", None)
                node["feedback"] = args.feedback.model_dump()
                history.append(node["feedback"])
                node["expandable"] = (
                    args.feedback.expandable and args.feedback.branch_status not in {"blocked", "exhausted"}
                )
        _sync_active_branch(tree)
        tree["revision"] = revision + 1
        result = _view(tree, args.node_id)
        db.execute(
            "INSERT INTO research_exploration_trees VALUES (?, ?, ?, ?) "
            "ON CONFLICT(task_id) DO UPDATE SET revision=excluded.revision, tree_json=excluded.tree_json",
            (task_id, workspace_id, tree["revision"], json.dumps(tree)),
        )
        return result
