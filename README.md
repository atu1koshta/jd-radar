# Job Hunter AI

Multi-portal job-search assistant. Searches Naukri / LinkedIn / Indeed (extensible),
parses JDs, scores skill match against your resume, and produces two outputs:

- **Telegram alert** for matched jobs
- **Personalized email draft** persisted for review-before-send

No auto-apply in v1. Hexagonal (ports & adapters) architecture — swap any LLM,
portal, browser, notifier, or database via env without touching core logic.

## Quick start

```bash
# 1. Install Ollama + pull the default model
ollama pull qwen2.5:7b-instruct-q4_K_M

# 2. Install package + dev deps
pip install -e ".[dev,browser,notify]"
playwright install chromium

# 3. Configure
cp .env.example .env
# edit .env with your IMAP / Telegram / SMTP creds

# 4. Smoke test the LLM wiring
jobhunter test-llm
```

## Architecture

See the design plan at
`/Users/atulkoshta/.claude/plans/give-me-overall-plan-scalable-platypus.md`
for the full hexagonal layout, port contracts, pipeline diagrams, and phase plan.

```
src/jobhunter/
├── core/         # pure domain — entities, scoring, state machine
├── ports/        # Protocols (interfaces)
├── adapters/     # vendor / framework impls — swappable
├── application/  # use cases that orchestrate ports
├── bootstrap/    # composition root: DI + plugin discovery
└── cli.py        # typer entry point
```

## License

MIT
