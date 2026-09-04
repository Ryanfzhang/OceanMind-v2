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

Task-local observations help the current investigation immediately. Cross-task
learning has a stricter boundary:

`Observation -> repeated cross-task evidence -> ExperienceCandidate -> historical replay evaluation -> versioned promotion`

A candidate requires support from at least three distinct tasks. It remains
inert until an evaluation beats its baseline by at least 0.05 on at least three
replays with no safety regression. Rejected candidates stay rejected. Promoted
revisions are versioned, older revisions remain available for rollback, and only
active promoted revisions enter later Coordinator and Expert prompts.

This is self-evolving behavior with an evaluation gate, not live prompt mutation.

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
available. Research observations and evolution candidates remain backend state
until a concrete review workflow requires exposing them.
