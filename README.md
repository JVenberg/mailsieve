# mailsieve

Keeps a Gmail inbox high-signal. Every new email is read by
[Jev](https://openrouter.ai/~typesafe/jev-latest), TypeSafe's small decision model, called through
OpenRouter's decisions API. Jev answers plain-English questions about the email with probabilities, and
rules built on those answers label it, archive or trash it once it is old enough, or mark it for
unsubscribing.

## How decisions are made

1. **Route.** One exhaustive question, `email_type`, puts every email in exactly one of 23 types
   (login_code, security_alert, receipt, booking, marketing, newsletter, personal, official, ... other).
   Each type has a definition, exclusions and examples. Action rules also require Jev to give the type
   at least 0.5 probability.
2. **Settle overlaps.** A separate yes/no question exists only where a rule depends on telling two
   types apart. The defaults need none.
3. **Confirm.** Before any action, the matched rule asks one yes/no question about the property that
   makes the action safe. Only if it passes is the action scheduled; otherwise the email is just labeled.

The defaults in [`mailsieve/rules.yaml`](mailsieve/rules.yaml):

| Rule | When | Confirm | Label | Action |
|---|---|---|---|---|
| codes | login_code | worthless once used | Codes | trash after 24h |
| security | security_alert | routine, nothing to do | Security | archive after 24h |
| receipts | receipt | nothing to do, nothing upcoming | Receipts | archive after 7d |
| orders | order_update | routine status, nothing to do | Orders | archive after 7d |
| unwanted | marketing, survey, political, fundraising | unsubscribable list mail | Ads | mark Unsubscribe/Gmail, /Email or /Link |
| spam | spam_scam | | Spam? | label only |

Unsubscribe labels say how: Gmail (one-click, Gmail's own Unsubscribe button), Email (send to a mailto
address) or Link (visit a page).

Starred mail, mail from you, and replies/forwards are never touched. Trash is Gmail's 30-day trash, never a
permanent delete.

## Answer cache

Every Jev answer is cached under the message id plus a fingerprint of the question's exact wording, the
model and the input format (SQLite in `.cache/` locally, Firestore in Cloud Run). So:

- a dry run followed by a real run asks Jev once;
- changing rules or cutoffs re-decides from cached answers for free;
- rewording or adding a question re-asks only that question;
- the cache is the audit record of what Jev said about each email.

## Adding a rule

Edit `rules.yaml` only: extend `email_type` if mail has no fitting type, add a yes/no question only for an
overlap or an action's safety check, then a rule with `when`, `label`, and optionally `action`, `confirm`
and `after`. Rules are checked in order and the first match wins. `uv run pytest` validates that every rule
references real questions, options and cutoffs, and that every action has a confirm check.

Preview against your real inbox without changing anything:

```sh
uv run python -m mailsieve classify --dry-run --query "in:inbox newer_than:14d"
uv run python -m mailsieve sweep --dry-run
```

New mail is classified as it arrives. To run the rules over your whole mailbox once (about $0.10 per
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

