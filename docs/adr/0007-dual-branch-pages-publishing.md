# Serialized dual-branch GitHub Pages publishing

`deploy-pages.yml` was main-only: the trigger watched `main` and the deploy job was guarded by `if: github.ref == 'refs/heads/main'`, with two CI tests pinning that boundary. Pushes to `dev` — the integration branch the scheduled data pipeline actually commits to — never deployed. The workflow now triggers on `push: branches: [dev, main]`, lets the deploy job run for both refs, and uses one global, non-cancelling publication queue. One owner serializes code releases and scheduled data generations so they cannot race or replace newer data with an older checkout.

We rejected a separate `github-pages-dev` preview environment and a build-only `dev` run. Consequence, accepted: a `dev` push updates the production URL until a later `main` push replaces it — both branches share one live site by design. The publisher preserves the active application revision while new data generations advance.
