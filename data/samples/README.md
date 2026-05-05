# Sample JD fixtures

Three plain-text job descriptions for smoke-testing the scoring pipeline.
The expected `Match.decision` assumes a typical Python-backend resume
(FastAPI / PostgreSQL / AWS / 6-9 yrs) and `RISK_TOLERANCE=0.3`.

| File                    | Stack            | Expected decision |
| ----------------------- | ---------------- | ----------------- |
| `jd_strong_match.txt`   | Python + FastAPI | `DRAFT`           |
| `jd_medium_match.txt`   | Java + Spring    | `ALERT`           |
| `jd_weak_match.txt`     | iOS + Swift      | `SKIP`            |

Run any of them through the CLI:

```bash
jobhunter score --jd data/samples/jd_strong_match.txt
jobhunter score --jd data/samples/jd_medium_match.txt
jobhunter score --jd data/samples/jd_weak_match.txt
```

Use `--risk-tolerance 0.5` to see borderline matches flip from `ALERT` to `DRAFT`.
