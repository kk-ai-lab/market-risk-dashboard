# News feed

News lists published items and lets the user narrow them by importance without changing the source dataset.

## Sub-features

- `news-route` opens the news feed.
- `news-filter-all` shows all available items.
- `news-filter-high` shows only items with importance at least 70.
- `news-filter-medium-plus` shows items with importance at least 40.

## How to get to it (user POV)

- Choose `News` in `Main navigation`.

## Driving it with playwright-cli

Preconditions:

- The verification instance passes the `../SKILL.md` doctor checks.
- `public/data/latest/news.json` is present and valid. To verify filter transitions, the dataset must include items in distinct importance bands.

- **Open News.** Choose `News` in `Main navigation`. Run `playwright-cli -s=verify click "getByRole('link', { name: 'News' })"`. The URL ends in `#/en/news` and the page heading is `News`.
- **Show all items.** Activate the `All` filter using its current snapshot ref. The `All` button has `aria-pressed="true"`, and all available news items appear.
- **Filter high importance.** Activate `High`. The `High` button becomes pressed and each visible item has importance of at least 70 (check the displayed badges; if a card does not expose the number, inspect its corresponding source item in `public/data/latest/news.json`).
- **Filter medium or higher.** Activate `Medium+`. The `Medium+` button becomes pressed and each visible item has importance of at least 40.
- **Proof.** Capture the action and resulting state for at least the `All` to `High` transition. Save snapshot, screenshot, and the observed before/after item counts in `proof.md`.

## Gotchas

- The filter is stored in `sessionStorage` under `mrd:news-filter`; start a fresh isolated browser session so an earlier filter does not silently affect the baseline.
- A high filter may legitimately return no items if the current dataset has no importance score of 70 or more. Record the source data condition; do not report filtering as verified unless a transition can be observed.
- Translation may be unavailable for some item text; it does not imply the feed failed to load.
