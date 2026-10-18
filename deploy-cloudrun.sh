#!/usr/bin/env bash
# Deploy the Jagajanin prototype to Cloud Run (AI Builder Cup "deployed, working prototype").
#
#   PROJECT=<gcp-project-id> ./deploy-cloudrun.sh
#
# Free-tier setup: request-based billing and zero minimum instances, so nothing is billed while
# idle and judges' traffic stays inside the monthly free allowance. The project still needs a
# billing account linked. Trade-offs: the first visit after ~15 idle minutes waits a few seconds
# for a cold start, and a fresh instance starts with a freshly seeded demo caseload (cases that
# visitors created earlier are gone; the phone re-sends its own assessments automatically).
#
# Exactly one instance at most: the demo keeps its cases inside the container, so a second
# instance would show the console a different set of cases than the phone saved to.
set -euo pipefail

PROJECT=${PROJECT:?set PROJECT=<gcp-project-id>}
REGION=${REGION:-asia-east1}   # Taiwan: a Tier 1 price region (full free allowance) close to Indonesia
SERVICE=${SERVICE:-jagajanin}
cd "$(dirname "$0")"

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  --project "$PROJECT"

gcloud run deploy "$SERVICE" --project "$PROJECT" --region "$REGION" --source . --quiet \
  --allow-unauthenticated \
  --min-instances 0 --max-instances 1 --concurrency 80 \
  --cpu 1 --memory 1Gi --timeout 300 \
  --set-env-vars RESEED_HOURS=6,IDLE_MINUTES=30,MAX_VIDEO_MB=30,VIDEO_BUDGET_MB=300

URL=$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format 'value(status.url)')
echo
echo "Midwife app:        $URL/?lang=en"
echo "Specialist console: $URL/doctor.html?lang=en"
curl -fsS "$URL/healthz" && echo
