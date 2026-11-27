# Privacy

mailsieve is a personal tool that sorts one Gmail inbox for its owner. It has no other users.

**What it reads.** With the `gmail.modify` scope, it reads new inbox messages: sender, recipients, subject,
Gmail's category, list headers, attachment names and the first 1,500 characters of the body.

**Where that goes.** That excerpt is sent to the Jev classifier through OpenRouter
(`openrouter.ai`) to decide what kind of email it is. Nothing else leaves Google Cloud.

**What it keeps.** Jev's answers, keyed by Gmail message ID, and a list of sender addresses with the email
types seen from each. Message bodies are not stored. Logs record each message's sender, subject and decision.

**What it changes.** It adds labels, archives, and moves mail to Gmail's 30-day trash. It never sends mail
and never permanently deletes anything.

**Revoking access.** Remove mailsieve at https://myaccount.google.com/permissions.
