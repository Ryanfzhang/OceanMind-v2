# ADR 0020: Unified AutoResearch, evaluated learning, and model-independent search

## Status

Accepted.

## Decision

OceanMind keeps one user experience and one Coordinator. Ordinary conversation,
paper reading, bounded analysis, and long-running AutoResearch are not separate
modes. AutoResearch is repeated execution of the existing evidence loop:

1. identify one material evidence gap;
2. answer directly, search, or issue a bounded WorkOrder;
3. persist every formal result in the current ResultBundle immediately;
4. evaluate the returned evidence against the user-level stopping condition;
5. stop, or issue only the next missing question.

`ResearchState` is a projection of durable WorkOrders, WorkResults,
ResultBundles, and typed observations. It is not a second workflow record and
cannot disagree with the task journal.

## Learning boundary

OceanMind records typed observations from the explicit Expert handoff, not from
private model reasoning or an unbounded transcript. Recorded kinds include data,
literature, method, quality checks, search, execution, results, evaluation,
limitations, and evidence gaps.

Task-local observations help the current investigation. The current cross-task learning
path is an explicit Agent-authored experience inbox, not automatic promotion of observations:

`save_experience -> periodic LLM Curator -> read relevant Skills -> versioned Skill commit`

The Curator receives short notes and Skill metadata across all roles, then reads full
documents on demand. It decides whether to ignore, defer, create, update, or report a product
bug. There is no fixed repetition count or numerical scientific-value threshold. Updating
requires reading the current Skill; an optimistic version check prevents overwriting a newer
revision. A failed review leaves experience pending. Installed Skills remain role-scoped and
Agents decide which ones to load. This does not alter an active research workflow or its
completion authority.

## Search boundary

OceanMind exposes one ordinary `web_search` function tool. Following OpenCode's
built-in search design, the tool calls Exa's hosted MCP endpoint with the
`web_search_exa` JSON-RPC tool and does not require a separate search API key.
DeepSeek, Qwen, GLM, OpenAI-compatible, and Anthropic-compatible chat models all
use the same function contract. The model may choose the query, result count,
search depth, and live-crawl preference. It cannot override the provider endpoint.

The backend accepts both JSON and server-sent-event MCP responses, bounds Exa's
potentially large output, and projects it into structured title, URL, snippet,
author, and publication-date evidence. Search snippets are retrieval evidence,
not accepted scientific claims. The Coordinator always has the tool; the
Literature & Reproduction Expert receives it for bounded literature work.

The previous DuckDuckGo HTML scraping and Zhipu credential-specific adapters are
removed.

## UI consequence

No research-mode UI is added. Existing task workflow activity presents search
and evidence-review progress, existing tool events carry search execution, and
the existing Research runtime settings section reports whether Web search is
available. Research observations remain backend state
for task-local traceability. Skill Curator commits are presented in the affected task's
results, with a background result-change notification for an already-open Desktop task.
