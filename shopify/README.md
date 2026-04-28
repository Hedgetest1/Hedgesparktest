# Shopify CLI app — HedgeSpark

Holds the Shopify Checkout UI Extensions for HedgeSpark. The
HedgeSpark app itself lives in `/opt/wishspark/backend/` (FastAPI
handles OAuth, webhooks, billing). This directory is **only** for
client-side extensions deployed via Shopify CLI.

## Why no `shopify.app.toml` is committed here

The HedgeSpark Partners app is TIER_2 (per `CLAUDE.md` §10) — its
OAuth + billing config must NEVER be overwritten by accident. A
hand-written `shopify.app.toml` could be pushed to Partners by a
stray `shopify app deploy --reset` and clobber production.

The safe path: pull the file from Partners truth **at deploy time**
via `shopify app config link --client-id=<existing>`. The file is
then `.gitignore`d (see `.gitignore` in this dir) so it never gets
committed and never overwrites Partners.

## One-time bootstrap (founder action)

Before the first deploy:

```bash
cd /opt/wishspark/shopify

# 1. Authenticate with Shopify Partners (browser flow)
#    Or set SHOPIFY_CLI_PARTNERS_TOKEN env var (Partners → API access).
shopify auth login

# 2. Pull existing Partners app config into local shopify.app.toml.
#    SHOPIFY_API_KEY in /opt/wishspark/backend/.env IS the client_id.
shopify app config link --client-id=$(grep SHOPIFY_API_KEY /opt/wishspark/backend/.env | cut -d= -f2)

# 3. Verify it links to "HedgeSpark" — NOT a new app.
cat shopify.app.toml | grep name
```

If step 2 reports "no app found", the Partners account is missing
the HedgeSpark app — STOP and reconcile before continuing.

## Deploy a new extension version (Claude action, post-bootstrap)

```bash
cd /opt/wishspark/shopify
shopify app deploy --version=$(grep SURVEY_EXTENSION_VERSION \
  /opt/wishspark/backend/app/core/tracker_version.py | head -1 | \
  sed 's/.*= //')
```

This pushes ONLY the extension. The Partners app config is
read-only from CLI's perspective at this point — `shopify app
deploy` warns and aborts on any divergence with Partners truth.

## Local dev (smoke test before deploy)

```bash
cd /opt/wishspark/shopify
shopify app dev --store=hedgespark-dev.myshopify.com
```

Opens the dev store with the extension hot-reloaded. Place a test
order via Shopify checkout simulator → the survey card renders on
Thank-You and Order-Status pages. Submit → check Postgres
`survey_responses` for the row.

## Layout

```
/opt/wishspark/shopify/
├── README.md                 (this file)
├── .gitignore                (ignores shopify.app.toml + caches)
├── shopify.app.toml          (PARTNERS-OWNED, gitignored, created by `shopify app config link`)
└── extensions/
    └── post-purchase-survey/
        ├── shopify.extension.toml
        ├── package.json
        └── src/
            ├── ThankYou.jsx
            └── OrderStatus.jsx
```
