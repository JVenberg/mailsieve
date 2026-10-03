# mailsieve

Keeps a Gmail inbox high-signal. Every new email is read by
[Jev](https://openrouter.ai/~typesafe/jev-latest), TypeSafe's small decision model, called through
OpenRouter's decisions API. Jev answers a set of plain-English questions about the email ("Does this give the
recipient a one-time code or login link?", "What kind of email is this?") with probabilities. Rules built
on those answers and cutoffs label the email, then archive or trash it once it is old enough.

The defaults in [`mailsieve/rules.yaml`](mailsieve/rules.yaml):

| Rule | When | Label | Then |
|---|---|---|---|
| codes | login/verification codes, magic links, password resets | Codes | trash after 24h |
| security | new sign-in / new device / settings-changed alerts | Security | archive after 24h |
| receipts | completed purchases, payments, bookings | Receipts | archive after 7d |
| ads | marketing, promos, surveys | Ads | label only |
| spam | junk, phishing | Spam? | label only |

Starred mail, mail from you, and replies/forwards are never touched. Trash is Gmail's 30-day trash, never a
permanent delete. Jev costs about $0.00006 per email for all four questions.

## Adding a rule

Edit `rules.yaml` only: add a question if you need a new judgment, then a rule with `when`, `label`, and
optionally `action` + `after`. Rules are checked in order and the first match wins. `uv run pytest` validates
that every rule references real questions, options and cutoffs.

Preview against your real inbox without changing anything:

```sh
uv run python -m mailsieve classify --dry-run --query "in:inbox newer_than:14d"
uv run python -m mailsieve sweep --dry-run
```

New mail is classified as it arrives. To run the rules over your whole mailbox once (about $0.06 per
1,000 emails; resumable, already-seen mail is skipped), backfill and then sweep:

```sh
uv run python -m mailsieve classify --all --workers 12 --dry-run   # preview
uv run python -m mailsieve classify --all --workers 12
uv run python -m mailsieve sweep
```

## How it runs

```
Gmail watch() ──► Pub/Sub ──► Cloud Run /push   classify new inbox mail immediately
Cloud Scheduler (hourly) ──► Cloud Run /sweep    catch-up classify, delayed archive/trash, renew watch
```

- Every decision is one structured JSON log line (message id, sender, subject, Jev's answers, rule), so
  Cloud Logging is the audit trail. A hidden `mailsieve/seen` label prevents paying twice for one message.
- Cloud Run requires an OIDC token: only the invoker service account (Pub/Sub, Scheduler) can call it.
- CI runs ruff, pytest and gitleaks. Merges to `main` deploy via GitHub Actions with Workload Identity
  Federation (no stored GCP keys). Set the repo variable `DRY_RUN=1` to deploy in log-only mode.

## Setup

1. OAuth client: Gmail API enabled, consent screen published to **In production** (Testing-mode refresh
   tokens expire after 7 days). Save the client as `.secrets/credentials.json`, then `uv run python -m mailsieve login`.
2. `.env` with `OPENROUTER_API_KEY=...`.
3. A GCP project with billing, then `PROJECT=<id> infra/bootstrap.sh setup`. This stores the token and key
   in Secret Manager and sets the GitHub repo variables.
4. Merge to `main` (or run the deploy workflow), then `PROJECT=<id> infra/bootstrap.sh triggers`.

Nothing secret lives in the repo: `.env`, `.secrets`, `data/` and all mail exports are gitignored, and CI
fails on anything gitleaks flags.

