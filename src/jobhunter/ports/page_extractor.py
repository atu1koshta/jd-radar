"""PageExtractor port — clean HTML into LLM-friendly text.

Pure: takes an HTML string, returns markdown / main-text. No network, no
browser. Adapters: trafilatura + markdownify for v1; could wrap crawl4ai
or readability-lxml later.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PageExtractor(Protocol):
    def to_markdown(self, html: str) -> str:
        """Render the page's main content as markdown.

        Should strip nav, footers, ads, scripts. Return value lands in the
        scoring + JD-extraction prompts so quality matters more than
        fidelity.
        """
        ...

    def main_text(self, html: str) -> str:
        """Return readability-style plain text. Useful for JD-content
        hashing and quick keyword sniffs."""
        ...
