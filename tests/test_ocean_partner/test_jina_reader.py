from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from ocean_partner.agent_tools import ToolExecutionContext
from ocean_partner.jina_reader import (
    MAX_JINA_MARKDOWN_CHARS,
    JinaReaderInput,
    JinaReaderTool,
)


def test_jina_reader_accepts_only_public_http_urls() -> None:
    assert JinaReaderInput(url="https://example.org/paper.pdf").url.endswith("paper.pdf")
    with pytest.raises(ValidationError):
        JinaReaderInput(url="file:///tmp/paper.pdf")
    with pytest.raises(ValidationError):
        JinaReaderInput(url="http://127.0.0.1/paper.pdf")


@pytest.mark.asyncio
async def test_jina_reader_returns_direct_markdown_without_persistence(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://r.jina.test/https://example.org/paper"
        assert request.headers["X-Return-Format"] == "markdown"
        return httpx.Response(200, text="# Paper\n\nA supported conclusion.")

    tool = JinaReaderTool(
        endpoint="https://r.jina.test",
        api_key="",
        transport=httpx.MockTransport(handler),
    )
    result = await tool.execute(
        JinaReaderInput(url="https://example.org/paper"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert not result.is_error
    assert result.output == "# Paper\n\nA supported conclusion."
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_jina_reader_marks_context_truncation(tmp_path) -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, text="x" * (MAX_JINA_MARKDOWN_CHARS + 1))
    )
    result = await JinaReaderTool(
        endpoint="https://r.jina.test",
        api_key="",
        transport=transport,
    ).execute(
        JinaReaderInput(url="https://example.org/paper"),
        ToolExecutionContext(cwd=tmp_path),
    )

    assert "Do not describe this source as fully reviewed" in result.output
