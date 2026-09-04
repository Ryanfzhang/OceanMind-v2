"""Direct Jina Reader access for the Literature Expert.

This is deliberately a thin provider connection.  It does not create a paper
record, normalize a corpus, index text, or copy full text into OceanMind's
database.  The returned Markdown remains ordinary tool context.
"""

from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ocean_partner.agent_tools import (
    BaseTool,
    ToolEffect,
    ToolExecutionContext,
    ToolResult,
)

JINA_READER_ENDPOINT = "https://r.jina.ai"
# A whole paper must still leave room for the WorkOrder, prior evidence, and
# the Expert's answer in the next model turn.  This is a context bound, not a
# stored or preprocessed representation.
MAX_JINA_MARKDOWN_CHARS = 160_000


def _is_disallowed_host(hostname: str) -> bool:
    lowered = hostname.rstrip(".").lower()
    if lowered == "localhost" or lowered.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


class JinaReaderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=4_096)

    @field_validator("url")
    @classmethod
    def validate_public_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("url must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("url must not contain credentials")
        if _is_disallowed_host(parsed.hostname):
            raise ValueError("url must identify a public host")
        return value


class JinaReaderTool(BaseTool):
    """Read one selected public source through Jina Reader as Markdown."""

    name = "jina_reader"
    description = (
        "Read one selected public paper or web source directly through Jina Reader and return "
        "its Markdown. Use only after the acquisition preference permits reading that source. "
        "This is direct reading context, not an OceanMind paper database or normalized corpus."
    )
    input_model = JinaReaderInput

    def __init__(
        self,
        *,
        endpoint: str = JINA_READER_ENDPOINT,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = (
            api_key if api_key is not None else os.getenv("JINA_API_KEY", "").strip()
        )
        self._transport = transport

    def effect_for(self, arguments: JinaReaderInput) -> ToolEffect:
        del arguments
        return ToolEffect.EXTERNAL_IO

    async def execute(
        self,
        arguments: JinaReaderInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        headers = {
            "Accept": "text/plain",
            "X-Return-Format": "markdown",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        reader_url = f"{self._endpoint}/{arguments.url}"
        try:
            async with httpx.AsyncClient(
                timeout=60.0,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await client.get(reader_url, headers=headers)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return ToolResult(
                output=f"jina_reader failed for {arguments.url}: {exc}",
                is_error=True,
            )

        markdown = response.text.strip()
        if not markdown:
            return ToolResult(
                output=f"jina_reader returned no readable Markdown for {arguments.url}",
                is_error=True,
            )
        if len(markdown) > MAX_JINA_MARKDOWN_CHARS:
            markdown = (
                markdown[:MAX_JINA_MARKDOWN_CHARS].rstrip()
                + "\n\n[OceanMind context limit: Jina Markdown was truncated. "
                "Do not describe this source as fully reviewed.]"
            )
        return ToolResult(
            output=markdown,
            metadata={"display": "activity", "provider": "jina", "source_url": arguments.url},
        )


__all__ = [
    "JINA_READER_ENDPOINT",
    "MAX_JINA_MARKDOWN_CHARS",
    "JinaReaderInput",
    "JinaReaderTool",
]
