"""TrafilaturaPageExtractor: HTML cleanup + markdown rendering."""

from __future__ import annotations

from jobhunter.adapters.page_extractor.trafilatura_md import TrafilaturaPageExtractor

# trafilatura needs reasonable content density before it considers a page
# "extractable". Mirror a real Naukri-style JD so the test reflects production.
_HTML = """
<!DOCTYPE html>
<html>
  <head><title>Sr. Backend Engineer</title></head>
  <body>
    <nav><a href="/x">SKIP NAV</a></nav>
    <header><h2>SKIP HEADER</h2></header>
    <main>
      <article>
        <h1>Sr. Backend Engineer</h1>
        <p>We are hiring a senior Python backend engineer with FastAPI and PostgreSQL.</p>
        <p>You will own service design, code reviews, and mentor 2-3 mid-level engineers across the
        payments platform. The team ships microservices on AWS and runs a nontrivial async
        pipeline built on asyncio + Celery.</p>
        <p>Required:</p>
        <ul>
          <li>5+ years of professional Python backend experience.</li>
          <li>Strong PostgreSQL: schema design, indexing, query plans, transactions.</li>
          <li>AWS production experience (any of: ECS, EKS, Lambda, RDS).</li>
          <li>Docker, Linux, git fluency.</li>
        </ul>
        <p>Nice to have: Kafka, OpenTelemetry, Terraform, mentoring exposure.</p>
        <p>Location: Remote (India hours). Compensation: competitive + equity.</p>
        <a href="https://example.com/apply">Apply now</a>
      </article>
    </main>
    <footer>SKIP FOOTER</footer>
    <script>alert('xss')</script>
  </body>
</html>
"""


def test_to_markdown_extracts_main_content_and_drops_chrome() -> None:
    md = TrafilaturaPageExtractor().to_markdown(_HTML)
    assert "Sr. Backend Engineer" in md
    assert "FastAPI" in md
    assert "5+ years" in md
    assert "SKIP NAV" not in md
    assert "SKIP FOOTER" not in md
    assert "<script>" not in md


def test_main_text_returns_plain_text_no_markup() -> None:
    text = TrafilaturaPageExtractor().main_text(_HTML)
    assert "Sr. Backend Engineer" in text
    assert "FastAPI" in text
    assert "<" not in text
