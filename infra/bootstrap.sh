#!/usr/bin/env bash
# One-time GCP setup for mailsieve. Idempotent; safe to re-run.
#   infra/bootstrap.sh setup     APIs, service accounts, GitHub OIDC, secrets, Pub/Sub topic
#   infra/bootstrap.sh triggers  after the first deploy: Pub/Sub push + hourly Scheduler sweep
# Needs: PROJECT (an existing project with billing), REGION, REPO (owner/name).
set -euo pipefail
: "${PROJECT:?set PROJECT}" "${REGION:=us-west1}" "${REPO:=JVenberg/mailsieve}"
cd "$(dirname "$0")/.."
g() { gcloud --project "$PROJECT" --quiet "$@"; }
NUM=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
RUN_SA=mailsieve-run@$PROJECT.iam.gserviceaccount.com
INVOKER_SA=mailsieve-invoker@$PROJECT.iam.gserviceaccount.com
DEPLOY_SA=mailsieve-deploy@$PROJECT.iam.gserviceaccount.com

sa() { g iam service-accounts describe "$1@$PROJECT.iam.gserviceaccount.com" >/dev/null 2>&1 || g iam service-accounts create "$1"; }
bind() { g projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$1" --role "$2" --condition None >/dev/null; }

setup() {
  g services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
    secretmanager.googleapis.com pubsub.googleapis.com cloudscheduler.googleapis.com \
    iamcredentials.googleapis.com gmail.googleapis.com
  sa mailsieve-run; sa mailsieve-invoker; sa mailsieve-deploy
  bind "$RUN_SA" roles/secretmanager.secretAccessor
  for role in roles/run.admin roles/cloudbuild.builds.editor roles/artifactregistry.admin \
    roles/storage.admin roles/serviceusage.serviceUsageConsumer; do bind "$DEPLOY_SA" "$role"; done
  g iam service-accounts add-iam-policy-binding "$RUN_SA" --member "serviceAccount:$DEPLOY_SA" --role roles/iam.serviceAccountUser >/dev/null

  g iam workload-identity-pools describe github --location global >/dev/null 2>&1 ||
    g iam workload-identity-pools create github --location global
  g iam workload-identity-pools providers describe github --location global --workload-identity-pool github >/dev/null 2>&1 ||
    g iam workload-identity-pools providers create-oidc github --location global --workload-identity-pool github \
      --issuer-uri https://token.actions.githubusercontent.com \
      --attribute-mapping google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref \
      --attribute-condition "assertion.repository == '$REPO' && assertion.ref == 'refs/heads/main'"
  g iam service-accounts add-iam-policy-binding "$DEPLOY_SA" --role roles/iam.workloadIdentityUser \
    --member "principalSet://iam.googleapis.com/projects/$NUM/locations/global/workloadIdentityPools/github/attribute.repository/$REPO" >/dev/null

  secret gmail-token < .secrets/token.json
  grep '^OPENROUTER_API_KEY=' .env | cut -d= -f2- | tr -d '\n' | secret openrouter-api-key

  g pubsub topics describe gmail-inbox >/dev/null 2>&1 || g pubsub topics create gmail-inbox
  g pubsub topics add-iam-policy-binding gmail-inbox \
    --member serviceAccount:gmail-api-push@system.gserviceaccount.com --role roles/pubsub.publisher >/dev/null

  gh variable set GCP_PROJECT --repo "$REPO" --body "$PROJECT"
  gh variable set GCP_REGION --repo "$REPO" --body "$REGION"
  gh variable set WIF_PROVIDER --repo "$REPO" \
    --body "projects/$NUM/locations/global/workloadIdentityPools/github/providers/github"
  echo "Done. Merge to main (or run the deploy workflow), then: PROJECT=$PROJECT $0 triggers"
}

secret() {
  g secrets describe "$1" >/dev/null 2>&1 || g secrets create "$1" --replication-policy automatic
  g secrets versions add "$1" --data-file=- >/dev/null
}

triggers() {
  URL=$(g run services describe mailsieve --region "$REGION" --format='value(status.url)')
  g run services add-iam-policy-binding mailsieve --region "$REGION" \
    --member "serviceAccount:$INVOKER_SA" --role roles/run.invoker >/dev/null
  g iam service-accounts add-iam-policy-binding "$INVOKER_SA" --role roles/iam.serviceAccountTokenCreator \
    --member "serviceAccount:service-$NUM@gcp-sa-pubsub.iam.gserviceaccount.com" >/dev/null
  g pubsub subscriptions describe gmail-inbox-push >/dev/null 2>&1 ||
    g pubsub subscriptions create gmail-inbox-push --topic gmail-inbox --push-endpoint "$URL/push" \
      --push-auth-service-account "$INVOKER_SA" --ack-deadline 600 --message-retention-duration 1h
  g scheduler jobs describe mailsieve-sweep --location "$REGION" >/dev/null 2>&1 ||
    g scheduler jobs create http mailsieve-sweep --location "$REGION" --schedule "0 * * * *" \
      --uri "$URL/sweep" --http-method POST --oidc-service-account-email "$INVOKER_SA" --attempt-deadline 900s
  g scheduler jobs run mailsieve-sweep --location "$REGION"
  echo "Triggers ready; the first sweep also starts the Gmail watch."
}

"${1:?usage: $0 setup|triggers}"
