# Moving the site to Cloudflare Pages (so the repo can be private)

## Status (12 November 2026): prepared, not started

- The Cloudflare deploy step is in the workflow and stays off: no Cloudflare secrets are set, so the site deploys to
  GitHub Pages only (last deploy: Nations Championship round 4, 12 Nov).
- Next: steps 1-4 below (yours), then step 5 (Claude, with your go-ahead).
- Related, also waiting: the "Scrape RFU stats" workflow needs a self-hosted runner and none is registered, so its runs
  queue and are cancelled. Until that is settled, run `python scripts/sync_rfu.py --push` the day after each round.
  A self-hosted runner on a public repository lets outside pull requests run code on that machine, so it is safest
  after the repository is private. The workflow can be paused meanwhile: *Actions* → *Scrape RFU stats* → *…* → *Disable workflow*.
- The RFU site briefly showed a "confirm you are human" check on 12 Nov; it cleared within the hour. The scrapers stop
  on such checks and never try to get past them.

The site is built by GitHub Actions as before. The workflow (`.github/workflows/update.yml`) already has a
"Deploy to Cloudflare Pages" step that stays switched off until the two secrets below exist. Until then the site
keeps deploying to GitHub Pages, so nothing breaks while you work through this list.

## 1. Cloudflare account and token (you)

1. Sign up at dash.cloudflare.com (the free plan is enough).
2. Copy your **Account ID**: it is shown on the account home page (right-hand side), or under *Workers & Pages*.
3. Create an API token: *My Profile* → *API Tokens* → *Create Token* → *Custom token*.
   - Permissions: **Account** → **Cloudflare Pages** → **Edit**. Nothing else.
   - Account resources: your account.
   - Copy the token when it is shown (it is shown once).

## 2. Add the two secrets to GitHub (you)

Repository → *Settings* → *Secrets and variables* → *Actions* → *New repository secret*:

| Name | Value |
|---|---|
| `CLOUDFLARE_API_TOKEN` | the token from step 1.3 |
| `CLOUDFLARE_ACCOUNT_ID` | the Account ID from step 1.2 |

Or from a terminal, pasting each value when asked: `gh secret set CLOUDFLARE_API_TOKEN` and `gh secret set CLOUDFLARE_ACCOUNT_ID`.

## 3. First Cloudflare deploy (you or Claude)

Repository → *Actions* → *Update projections* → *Run workflow*. The new step creates the Pages project
`fsn-site` and uploads the site. Check **https://fsn-site.pages.dev** opens and accepts the site password.
(If the name `fsn-site` is refused, change it in the workflow step.)

## 4. Point the domain at Cloudflare (you)

1. Cloudflare → *Workers & Pages* → `fsn-site` → *Custom domains* → *Set up a custom domain* → `fsn.campbelltom.co.uk`.
   Cloudflare shows the record it wants: a **CNAME** from `fsn` to `fsn-site.pages.dev`.
2. At IONOS (where `campbelltom.co.uk`'s DNS appears to be), edit the existing `fsn` CNAME record: change its target from
   `campbelltom.github.io` to `fsn-site.pages.dev`.
3. Wait for Cloudflare to show the domain as *Active* (minutes to a few hours), then check https://fsn.campbelltom.co.uk.

## 5. Switch off GitHub Pages and make the repo private (Claude, with your go-ahead)

Once the domain serves the Cloudflare copy:

1. Remove the GitHub Pages deploy job and the custom domain under repository *Settings* → *Pages*.
2. Make the repository private (*Settings* → *General* → *Danger zone* → *Change visibility*).
   Private repositories get 2,000 free Actions minutes a month; the site update uses about 880.

## Optional: a real login with Cloudflare Access

Cloudflare Zero Trust (free for up to 50 users) can require an email one-time code before anyone sees the site, instead
of the page password that can be tried offline. It needs `campbelltom.co.uk`'s DNS moved to Cloudflare (change the
nameservers at IONOS), so it is a separate step.
