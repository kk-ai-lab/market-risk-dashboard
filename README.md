# Market Risk Dashboard

**Global Market Risk Intelligence Dashboard** — a cross-asset, bilingual (zh-CN / en) market risk
intelligence board for personal investment research. A data pipeline produces deterministic
market facts and a modeled risk estimate, and a bilingual static site serves the result. The
public dashboard does not generate or consume AI-written market briefs.

> ⚠️ Risk scores are a **modeled estimate of market stress**, not an exact crash probability, and do
> not constitute investment advice.

## Runbook

### Frontend

```bash
npm install
npm run dev      # local dev server (http://localhost:5173)
npm run build    # production build → dist/
npm test         # vitest (contract tests)
```

### Pipeline

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pipeline.run --dry-run   # config self-check + plan, no-op exit 0
python -m pytest tests/pipeline/ -v
```

License: MIT — see [LICENSE](LICENSE).
