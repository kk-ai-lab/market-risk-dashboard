# Market Risk Dashboard verification map

This map covers the primary user-facing risk intelligence workflows. Read the matching feature file before driving a route. The UI consumes checked-in static JSON; don't refresh or rewrite those inputs during verification.

## Baseline preconditions

- Start Vite on `127.0.0.1:4173` with the command in `../SKILL.md`.
- Use the `/market-risk-dashboard/` base path and a fresh `playwright-cli -s=verify` browser session.
- Run the read-only doctor checks in `../SKILL.md` and confirm this run owns the listener.
- Confirm required files exist under `public/data/latest/` and, for trend charts, `public/data/history/`.
- The app requires no login. All mapped journeys are read-only.

## Driving conventions

- Start each feature at its documented route; use the actual navigation links when a journey checks navigation.
- Read a fresh accessibility snapshot after each action; refs change between snapshots.
- Prefer the named navigation links, buttons, and controls. Use exact hash routes only to set the initial state.
- Save a `proof.md`, snapshot, and screenshot in `artifacts/verify/<run-id>/<feature>/`.
- Report absent or invalid fixture data as a failed precondition; do not weaken the expected result.

## Features

- [Overview risk panorama](./overview.md) covers the global score, regime, trend, cross-asset picture, drivers, and brief.
- [Macro indicators](./macro.md) covers the macro page and its rendered indicator sections.
- [News feed](./news.md) covers the importance filter and visible news results.
- [Economic calendar](./calendar.md) covers calendar events and the data-driven event type filters.
