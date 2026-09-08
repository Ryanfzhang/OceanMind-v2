# ADR 0021: Coordinator-owned research exploration

Status: Implemented backend prototype.

## Scope

Only a Coordinator bound to a research task receives `ocean_exploration`. The
Coordinator maintains it whenever investigating an open question or competing
explanations; users need not request a tree or say "continued research". Standalone
factual questions, literature summaries, downloads, plots and specified calculations
do not use it, including when an old tree exists. Those activities may also be
supporting work within a larger investigation. This semantic distinction is made
by the Coordinator, not a keyword classifier.
The additional Coordinator schema/policy has a small static context cost; there
are no automatic tree-related model calls or background workers.

`ideas` means propose alternatives, deliver the requested shortlist and pause.
`iterative` is the default for investigations and recommends tests through the existing Expert
workflow, within the user's requested scope. Merely having a tree or an anomaly
does not authorize experiments or continued research. The node budget defaults
to 12, is capped at 64, and is not an instruction to consume every available node.
Increasing the budget or resuming must follow the user's scope. A new topic belongs
in another task; resume preserves the original goal.

## State and search

Migration 47 creates a task-owned SQLite row, lazily populated on `start` and
deleted with its research task. Existing databases are backed up before migration.
Writes use optimistic revisions; tool calls use existing durable operation receipts.
Actions are read/start/propose/record/pause/resume. No action dispatches an Expert,
executes code, or changes task completion authority.

On research assignments the Coordinator supplies `research_question` to
`ocean_assign`. The tool initializes a missing root (or resumes the current tree)
before delegation, strips this field before passing the unchanged assignment to
Experts, and returns fresh tree context and evidence IDs to the Coordinator after
the assignment finishes. Ordinary assignments omit the field and neither create
nor resume a tree. Durable tool receipts prevent duplicate dispatch on replay.
Experts never receive tree state or tree tools: they answer bounded questions.
The Coordinator alone proposes hypotheses, selects tests, records feedback, and
expands or pauses branches. Research intent detection remains model-driven; the
assignment hook guarantees root persistence once the Coordinator declares it.

Nodes contain an idea, rationale, feasible test, parent, compact feedback and
existing task observation IDs. Edges mean inspiration, not entailment. Feedback
records support, contradiction, ambiguity or deferral; contradictory findings can
still generate useful descendants. Branches can be marked non-expandable.

Test outcomes and branch completion are separate. `supported` defaults to an
`open` branch, and UCB continues selecting tests or expansion. The Coordinator
records `branch_status=solved` only on a main hypothesis directly under the question
root, with supported evidence and a summary explaining how its evidence chain
answers the question. One such main branch ends the search successfully. Local
child support alone cannot declare the whole question resolved.

On an expansion recommendation the Coordinator either proposes useful children,
or records `exhausted`/`blocked` and explains why before selecting another branch.
All main branches becoming unavailable ends search without a resolved answer;
blocked evidence is preserved and is not counted as scientific refutation. Existing
open children remain reachable when a parent merely disallows additional children.
After initial main hypotheses, iterative UCB searches within those branches rather
than automatically widening the root. The Coordinator may still explicitly add a
new main hypothesis when evidence warrants one.

Node budget exhaustion is a pause, not successful research completion. A pause
before a terminal recommendation stores the pending recommendation and marks the
search interrupted. These are scheduling records of Coordinator judgments, not
backend verification of scientific truth or a guarantee against a model ending
its response without calling the tree tool.

Recommendations combine UCB branch ranking with progressive widening. They are
advisory; Coordinator judgment and the user's scope remain decisive. Rewards are
explicit 0..1 Coordinator assessments of information gained, linked to existing
task observations. This is an adaptation, not a reproduction of AutoDiscovery's
Bayesian-surprise reward. There is no repeated prior/posterior sampling. Deferred
work counts as an attempt but cannot earn reward. Checking evidence IDs establishes provenance, not truth.
Corrections retain history and replace the current reward; subtree aggregates are
recomputed so one experiment is never counted again merely because it was corrected.

Reads return at most three nodes on the selected path, compact child summaries,
and eight recent evidence summaries per page. `tree_text` renders the full bounded
saved structure for the Coordinator to include in its final answer. Full experiments remain in existing
WorkOrders, Expert results and execution storage. Exact normalized duplicates are
rejected; semantic deduplication remains the Coordinator's responsibility. Skills
continue to store reusable procedural lessons, separately from task hypotheses.

## Try it

After restarting the backend, in a new research task ask:

> Based on these papers and data, explore several distinct explanations for the
> marine heatwave pattern. Give me ideas and feasible tests first; do not run them.

Then follow up:

> Continue the exploration with the available data, testing the most informative
> branch and using the result to choose the next step. Limit the tree to 8 ideas.

There is no dedicated frontend tree viewer. The final answer includes the saved
tree as a fenced text block on the existing conversation surface. Real-provider evaluation is
still needed to measure activation accuracy, token usage and scientific usefulness.

## Verification

`tests/test_oceanx/test_exploration.py` covers lazy activation, role surfaces,
restart/resume, migration/backup, deletion, evidence scope, stale revisions,
atomic duplicate rejection, feedback corrections and exploration recommendations.
