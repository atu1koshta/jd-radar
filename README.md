# Job Hunter AI

Multi-portal job-search assistant. Searches Naukri (extensible to LinkedIn / Indeed),
parses JDs, scores skill match against your resume, and pushes a Telegram alert for
matched jobs.

No auto-apply in v1. Hexagonal (ports & adapters) architecture — swap any LLM, portal,
browser, notifier, or database via env without touching core logic.

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python ≥ 3.11 | |
| [Ollama](https://ollama.ai) | Local LLM backend |
| Telegram bot | `TELEGRAM_TOKEN` + `TELEGRAM_CHAT_ID` |
| Naukri account | `NAUKRI_EMAIL` + `NAUKRI_PASSWORD` |
| Resume YAML on GitHub | Public or private repo (set `GITHUB_TOKEN` for private) |

---

## Setup

### 1. Install Ollama and pull the default model

```bash
ollama pull qwen2.5:7b-instruct-q4_K_M
```

### 2. Create and activate a virtual environment

```bash
# with venv (stdlib)
python3 -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows

# or with uv (faster)
uv venv .venv
source .venv/bin/activate
```

### 3. Install the package

```bash
# with pip (venv must be active)
pip install -e ".[dev,browser,notify]"

# or with uv
uv pip install -e ".[dev,browser,notify]"
```

### 4. Install Playwright browser

```bash
playwright install chromium
```

### 5. Configure environment

```bash
cp .env.example .env
# edit .env — fill in credentials and search defaults
```

Key variables to set:

| Variable | Description |
|----------|-------------|
| `LLM_MODEL` | Ollama model tag (default: `qwen2.5:7b-instruct-q4_K_M`) |
| `RESUME_URL` | Raw GitHub URL of your `resume.yaml` |
| `NAUKRI_EMAIL` / `NAUKRI_PASSWORD` | Portal credentials |
| `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` | Notification target |
| `DEFAULT_EXPERIENCE_YEARS` | Filter — years of experience |
| `DEFAULT_POSTED_WITHIN_DAYS` | Filter — recency of job posting |
| `DEFAULT_EXPECTED_CTC_LPA` | Filter — minimum CTC in ₹L/A |
| `RISK_TOLERANCE` | `0..1` — threshold to fire an alert (default `0.3`) |
| `DRY_RUN` | `true` = score but don't send alerts |

See `.env.example` for the full list with inline comments.

---

## Usage

### Smoke-test the LLM

```bash
jobhunter test-llm
```

Sends a structured-output prompt to the configured Ollama model and prints the JSON
response. Exit code 2 on failure.

---

### Load and inspect the resume

```bash
# Use cached copy if younger than RESUME_REFRESH_TTL_MIN
jobhunter load-resume

# Force re-fetch from GitHub
jobhunter load-resume --refresh

# Also print the full raw YAML body
jobhunter load-resume --raw
```

Prints a JSON summary: cache age, body hash, interpreted fields (skills, seniority,
search terms, etc.).

---

### Score a job description

```bash
jobhunter score --jd path/to/job.txt
```

Options:

| Flag | Description |
|------|-------------|
| `--jd PATH` | Path to a plain-text JD file (required) |
| `--job-id TEXT` | Synthetic ID for the resulting Match record |
| `--refresh-resume` | Force resume re-fetch before scoring |
| `--risk-tolerance FLOAT` | Override `RISK_TOLERANCE` for this run (0..1) |
| `--debug` | Print the resume summary sent to the LLM |

Output is a JSON object with `match` (confidence, risk, verdict) and `rubric`.

---

### Smoke-test a portal

```bash
# Search Naukri with defaults
jobhunter portal-test naukri --query "backend engineer"

# Custom filters, headed browser (useful for validating selectors)
jobhunter portal-test naukri \
  --query "python developer" \
  --location "bangalore" \
  --limit 10 \
  --experience 5 \
  --posted-within 3 \
  --ctc 25 \
  --headed
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--query / -q` | `software engineer` | Search keywords |
| `--location` | — | Location filter |
| `--limit` | `5` | Max jobs to fetch (1–50) |
| `--experience` | `DEFAULT_EXPERIENCE_YEARS` | Years of experience |
| `--posted-within` | `DEFAULT_POSTED_WITHIN_DAYS` | Recency in days |
| `--ctc` | `DEFAULT_EXPECTED_CTC_LPA` | Min CTC in ₹L/A |
| `--fetch-jds / --no-fetch-jds` | on | Navigate to each job URL and extract JD |
| `--headless / --headed` | `BROWSER_HEADLESS` | Browser visibility override |

---

### Smoke-test Telegram alerts

```bash
jobhunter alert-test

# Custom message
jobhunter alert-test --text "testing 1-2-3"
```

Requires `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.

---

### Full pipeline run

```bash
# Minimal — 1 job, dry run by default
jobhunter run --portal naukri --query "senior python engineer"

# Production-style
jobhunter run \
  --portal naukri \
  --query "backend engineer" \
  --location "remote" \
  --limit 20 \
  --experience 6 \
  --posted-within 1 \
  --ctc 30 \
  --workers 4 \
  --refresh-resume
```

Options:

| Flag | Default | Description |
|------|---------|-------------|
| `--portal` | `naukri` | Registered portal name |
| `--query / -q` | `software engineer` | Search keywords |
| `--location` | — | Location filter |
| `--limit` | `1` | Max jobs this run (1–50) |
| `--experience` | `DEFAULT_EXPERIENCE_YEARS` | Years of experience filter |
| `--posted-within` | `DEFAULT_POSTED_WITHIN_DAYS` | Recency in days |
| `--ctc` | `DEFAULT_EXPECTED_CTC_LPA` | Min CTC in ₹L/A |
| `--workers / -w` | `PIPELINE_WORKERS` | Parallel workers (1–8) |
| `--headless / --headed` | `BROWSER_HEADLESS` | Browser visibility override |
| `--refresh-resume` | off | Force resume re-fetch before run |

Pipeline stages: search → fetch JD → score → decide action → send alert.
Set `DRY_RUN=false` in `.env` to enable live Telegram alerts.

---

## Architecture

```
src/jobhunter/
├── core/          # pure domain — entities, scoring, state machine
├── ports/         # Protocols (interfaces)
├── adapters/      # vendor / framework impls — swappable
│   ├── llm/       # ollama
│   ├── portals/   # naukri (+ extend via entry points)
│   ├── actions/   # alert
│   ├── notifier/  # telegram
│   ├── browser/   # playwright
│   ├── queue/     # asyncio in-process
│   └── repository/# sqlite
├── application/   # use cases that orchestrate ports
├── bootstrap/     # composition root: DI + plugin discovery
└── cli.py         # typer entry point
```

Adapters are registered via `pyproject.toml` entry points (`jobhunter.llm`,
`jobhunter.portals`, `jobhunter.actions`, `jobhunter.notifiers`). Third-party
packages can add new portals or actions without modifying core.

---

## Extending

Add a new portal by implementing `jobhunter.ports.portal.PortalAdapter` and
registering it in your package's `pyproject.toml`:

```toml
[project.entry-points."jobhunter.portals"]
linkedin = "mypkg.adapters.linkedin:LinkedInAdapter"
```

Then set `ENABLED_PORTALS=linkedin` in `.env`.

---

## License

MIT
