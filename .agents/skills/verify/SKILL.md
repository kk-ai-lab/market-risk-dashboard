---
name: verify
description: "Drive the Market Risk Dashboard's React web UI in a disposable local Vite session to verify user-facing routes, filters, and rendered market data. Use after changing dashboard behavior or data presentation."
---

# Verify the Market Risk Dashboard

Use this skill to prove behavior through the dashboard UI. The app is a React/Vite single-page application with hash routes and checked-in JSON datasets under `public/data/`. It has no authentication or database. Its UI is read-only apart from browser-local theme, language, and session filter preferences.

## Launch

From the repository root, in a dedicated terminal session, start the app:

```bash
npm run dev -- --host 127.0.0.1 --port 4173 --strictPort
```

Vite prints `Local: http://127.0.0.1:4173/market-risk-dashboard/` when ready. Keep this terminal open while driving the app. The repo's Vite base path is `/market-risk-dashboard/`; include it in every URL. No migrations, secrets, or seed steps are required: the tracked `public/data/` files are the fixture data. Do not run pipeline jobs as part of UI verification; they may update published data.

## Doctor

Before driving an instance that looks unhealthy, check that the dedicated Vite server is still running, owns port 4173, and serves the app shell and overview data:

```bash
lsof -nP -iTCP:4173 -sTCP:LISTEN
curl -fsS http://127.0.0.1:4173/market-risk-dashboard/ >/dev/null
curl -fsS http://127.0.0.1:4173/market-risk-dashboard/data/latest/dashboard.json >/dev/null
curl -fsS http://127.0.0.1:4173/market-risk-dashboard/data/latest/risk.json >/dev/null
```

Confirm the listener command belongs to this verification run's `npm run dev`/Vite process before using it. If another process owns 4173, do not drive or stop it; choose an unused port and update the URLs. A successful HTML request alone is not enough: the dashboard and risk data requests must also succeed. There is no auth check.

## Drive

Use `playwright-cli`, with a fresh browser session for each run. It is available in this environment as a CLI backed by Playwright. Start from the English overview:

```bash
playwright-cli open http://127.0.0.1:4173/market-risk-dashboard/#/en/overview
playwright-cli snapshot
```

After every action, inspect the new accessibility snapshot and re-derive element refs. Prefer accessible link names and controls over coordinates or DOM position. Stable routes are `#/en/overview`, `#/en/macro`, `#/en/equities`, `#/en/themes`, `#/en/news`, `#/en/calendar`, `#/en/risklab`, and `#/en/status`; the corresponding Chinese routes use `#/zh/`.

To use a separate browser CLI session, give every command the same `-s=verify` prefix (for example, `playwright-cli -s=verify open ...`). Never attach to or close the user's existing browser. Do not use direct React setters, injected state, or test-only endpoints to establish a result.

## Evidence

Save proof under `artifacts/verify/<run-id>/<feature>/`. The `artifacts/` directory is git-ignored and evidence is local to the checkout. Record the run ID, route, feature, browser action, and result in a short `proof.md`. For a UI proof, save both the accessibility snapshot and screenshot:

```bash
playwright-cli snapshot --filename=artifacts/verify/<run-id>/<feature>/after.yml
playwright-cli screenshot --filename=artifacts/verify/<run-id>/<feature>/after.png
```

Capture the user action and resulting state, not only the final screen. Verify any relevant data side effects from a second user-facing view or the read-only source JSON alongside what is visible. Exercise the real route and controls. Do not claim a feature passed if its needed data is absent; record the unmet precondition and the observed empty/error state. Mocks are appropriate only at an existing production boundary. Do not assume the app is offline merely because it is local: observe network requests when a change touches network behavior.

Baseline signals observed on 2026-11-13: the overview loaded its checked-in data and displayed a risk score. The browser console also reported a missing `/favicon.ico` (404) and an accepted undeclared field warning for `history/risk/30d.json`; distinguish these known signals from new regressions and report them if relevant. Do not dismiss newly observed errors as baseline without checking their exact source.

## Cleanup

Close only the Playwright CLI session started for this run:

```bash
playwright-cli -s=verify close
```

Stop the Vite server with Ctrl-C in the dedicated terminal where this run launched it. Never kill by process name or stop a listener you did not start. Leave `artifacts/verify/<run-id>/` intact after teardown; evidence must survive cleanup. The UI verification does not mutate repository data files.

## Helpers

No project helper scripts are required. The commands in this skill are the complete launch, doctor, drive, evidence, and cleanup workflow.

## Feature map

Read [the feature map](./features/README.md) before choosing a journey. Each feature recipe names its preconditions, exact user-facing entry point, observable result, and known gotchas.
