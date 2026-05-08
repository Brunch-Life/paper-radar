#!/usr/bin/env python3
"""
paper-radar MCP server (stdio).

Thin pass-through over `paper_radar.digests`. The same library is used
by the FastAPI web server (paper_radar.api). DO NOT add data-access logic
here — put it in digests.py so the web app stays in sync.

Register in:
- ~/.claude.json (Claude Code)              → claude mcp add -s user paper-radar ...
- ~/Library/Application Support/Claude/      → mcpServers.paper-radar
  claude_desktop_config.json (Claude Desktop  / Cowork bridge)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT))
from paper_radar import digests  # noqa: E402

mcp = FastMCP("paper-radar")


@mcp.tool()
def list_dates(limit: int = 14) -> dict:
    """List the most recent digest dates available (YYYY-MM-DD).

    Args:
        limit: maximum number of dates to return.
    """
    return digests.list_dates(limit)


@mcp.tool()
def list_papers(date: Optional[str] = None) -> dict:
    """List the top-10 ranked papers for a given digest date.

    Args:
        date: YYYY-MM-DD or annual-YYYY-MM-DD. Default = most recent.
    """
    return digests.list_papers(date)


@mcp.tool()
def get_paper(arxiv_id: str, date: Optional[str] = None) -> dict:
    """Return the full 6-section deep-read markdown for one paper.

    Args:
        arxiv_id: e.g. "2509.09674". Strip any version suffix.
        date: optional digest date.
    """
    return digests.get_paper(arxiv_id, date)


@mcp.tool()
def search_papers(query: str, limit: int = 20) -> dict:
    """Fuzzy search papers across all digests by title / tagline / arxiv_id.

    Args:
        query: search string (case-insensitive).
        limit: max hits.
    """
    return digests.search_papers(query, limit)


@mcp.tool()
def get_top5_annual(year_tag: Optional[str] = None) -> dict:
    """Return the TOP 5 highlight markdown from the annual review.

    Args:
        year_tag: e.g. "annual-2026-05-07". Default = most recent annual.
    """
    return digests.get_top5_annual(year_tag)


@mcp.tool()
def get_candidates(date: Optional[str] = None, top_n: int = 30) -> dict:
    """Return Stage-1 candidates (pre-deep-read) for a digest date.

    Args:
        date: digest date. Default = most recent.
        top_n: how many candidates to return (sorted by score desc).
    """
    return digests.get_candidates(date, top_n)


if __name__ == "__main__":
    mcp.run()
