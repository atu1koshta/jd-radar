"""PageExtractor adapter using `trafilatura` + `markdownify`.

trafilatura strips boilerplate (nav/footer/ads), markdownify converts the
remaining HTML to markdown. Both are pure-Python, no headless browser, no
network. Fast enough to call once per job page.
"""

from __future__ import annotations

import trafilatura
from markdownify import markdownify as _md


class TrafilaturaPageExtractor:
    def to_markdown(self, html: str) -> str:
        # `trafilatura.extract(..., output_format="markdown")` exists, but
        # markdownify gives us better link / list rendering. So: trafilatura
        # filters noise, markdownify formats what's left.
        cleaned_html = trafilatura.extract(
            html,
            output_format="html",
            include_links=True,
            include_tables=True,
            with_metadata=False,
            no_fallback=False,
            include_comments=False,
        )
        if not cleaned_html:
            # Last-ditch: convert the raw HTML directly. Better than empty.
            return _md(html, heading_style="ATX").strip()
        return _md(cleaned_html, heading_style="ATX").strip()

    def main_text(self, html: str) -> str:
        text = trafilatura.extract(
            html,
            output_format="txt",
            include_links=False,
            include_tables=False,
            with_metadata=False,
            no_fallback=False,
            include_comments=False,
        )
        return (text or "").strip()
