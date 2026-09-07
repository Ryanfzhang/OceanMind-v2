"""Provider-independent web search through Exa's hosted MCP endpoint."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from oceanx.agent_tools import (
    BaseTool,
    ToolEffect,
    ToolExecutionContext,
    ToolResult,
)

EXA_HOSTED_MCP_ENDPOINT = "https://mcp.exa.ai/mcp"


class WebSearchToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=1_000)
    max_results: int = Field(default=5, ge=1, le=10)
    search_type: Literal["auto", "fast", "deep"] = "auto"
    live_crawl: Literal["fallback", "preferred"] = "fallback"

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value


class WebSearchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    title: str = Field(max_length=512)
    url: str = Field(max_length=2_048)
    snippet: str = Field(default="", max_length=4_000)
    author: str = Field(default="", max_length=256)
    published_at: str | None = Field(default=None, max_length=64)


class WebSearchResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    query: str
    provider: Literal["exa"] = "exa"
    results: tuple[WebSearchResult, ...]


class WebSearchTool(BaseTool):
    name = "web_search"
    description = (
        "Search the current web through Exa and return bounded titles, URLs, snippets, "
        "authors, and publication dates. Use only for external or time-sensitive evidence."
    )
    input_model = WebSearchToolInput

    def __init__(
        self,
        *,
        endpoint: str = EXA_HOSTED_MCP_ENDPOINT,
        on_response: Callable[[WebSearchResponse, ToolExecutionContext], None] | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._on_response = on_response

    def effect_for(self, arguments: WebSearchToolInput) -> ToolEffect:
        del arguments
        return ToolEffect.EXTERNAL_IO

    async def execute(
        self, arguments: WebSearchToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "web_search_exa",
                "arguments": {
                    "query": arguments.query,
                    "type": arguments.search_type,
                    "numResults": arguments.max_results,
                    "livecrawl": arguments.live_crawl,
                    "contextMaxCharacters": 10_000,
                },
            },
        }
        try:
            async with httpx.AsyncClient(timeout=25.0, follow_redirects=False) as client:
                response = await client.post(
                    self._endpoint,
                    headers={
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                response.raise_for_status()
            parsed = WebSearchResponse(
                query=arguments.query,
                results=tuple(
                    _parse_results(_parse_mcp_text(response.text), arguments.max_results)
                ),
            )
        except (httpx.HTTPError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return ToolResult(output=f"web_search failed: {exc}", is_error=True)
        if not parsed.results:
            return ToolResult(output="web_search returned no parseable results", is_error=True)
        if self._on_response is not None:
            self._on_response(parsed, context)
        return ToolResult(
            output=json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False),
            metadata={"display": "activity", "provider": "exa"},
        )


def _parse_mcp_text(body: str) -> str:
    candidates = [body.strip(), *[
        line.removeprefix("data:").strip()
        for line in body.splitlines()
        if line.startswith("data:")
    ]]
    for candidate in candidates:
        if not candidate.startswith("{"):
            continue
        payload = json.loads(candidate)
        if payload.get("error") is not None:
            raise ValueError(f"Exa MCP error: {payload['error']}")
        content = (payload.get("result") or {}).get("content")
        if not isinstance(content, list):
            continue
        text = "\n".join(
            str(item.get("text") or "").strip()
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ).strip()
        if text:
            return text
    raise ValueError("Exa MCP response contained no text result")


_RESULT = re.compile(
    r"(?:^|\n---\n)Title:\s*(?P<title>[^\n]+)\n"
    r"URL:\s*(?P<url>[^\n]+)\n"
    r"Published:\s*(?P<published>[^\n]+)\n"
    r"Author:\s*(?P<author>[^\n]+)\n"
    r"Highlights:\s*\n(?P<snippet>.*?)(?=\n---\nTitle:|\Z)",
    flags=re.DOTALL,
)


def _parse_results(body: str, limit: int) -> list[WebSearchResult]:
    results: list[WebSearchResult] = []
    for match in _RESULT.finditer(body):
        url = match.group("url").strip()[:2_048]
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        published = match.group("published").strip()[:64]
        author = match.group("author").strip()[:256]
        results.append(
            WebSearchResult(
                title=match.group("title").strip()[:512],
                url=url,
                snippet=match.group("snippet").strip()[:4_000],
                author="" if author == "N/A" else author,
                published_at=None if published == "N/A" else published,
            )
        )
        if len(results) >= limit:
            break
    return results


__all__ = ["WebSearchResponse", "WebSearchResult", "WebSearchTool", "WebSearchToolInput"]
