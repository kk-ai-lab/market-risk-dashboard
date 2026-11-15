# Economic calendar

Calendar shows published economic and earnings events and lets the user filter by event type.

## Sub-features

- `calendar-route` opens the calendar page.
- `calendar-all` shows every available event.
- `calendar-type-filter` filters to an event type present in the dataset.

## How to get to it (user POV)

- Choose `Calendar` in `Main navigation`.

## Driving it with playwright-cli

Preconditions:

- The verification instance passes the `../SKILL.md` doctor checks.
- `public/data/latest/calendar.json` is present and valid JSON. For a type-filter transition, it contains events of at least two types.

- **Open Calendar.** Choose `Calendar` in `Main navigation`. Run `playwright-cli -s=verify click "getByRole('link', { name: 'Calendar' })"`. The URL ends in `#/en/calendar` and the heading is `Calendar`.
- **Show all events.** Activate `All` inside the calendar filter group. Its `aria-pressed` state is true and event rows/cards appear, or the page shows the explicit no-events state.
- **Filter by type.** Activate a visible type button such as `Economic` or `Earnings`. Its `aria-pressed` state becomes true and only matching event types remain. Confirm event text/date against `public/data/latest/calendar.json`.
- **Proof.** Capture the selected type action and the resulting event list in both snapshot and screenshot. Record the source event count and filtered count in `proof.md`.

## Gotchas

- Filter chips are derived from types present in the dataset. Do not expect an absent type button.
- If the fixture has no events or only one event type, report that precondition and do not claim a filter transition passed.
- Dates and event counts depend on published data and should not be hard-coded.
