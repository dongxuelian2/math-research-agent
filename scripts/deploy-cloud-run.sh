#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v gcloud >/dev/null 2>&1; then
  printf '%s\n' 'Google Cloud CLI (gcloud) is required. Install it and run gcloud auth login first.' >&2
  exit 2
fi

PROJECT_ID="${CLOUD_RUN_PROJECT:-${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}}"
SERVICE_NAME="${CLOUD_RUN_SERVICE:-math-research-agent}"
REGION="${CLOUD_RUN_REGION:-us-central1}"
VERTEX_LOCATION="${GOOGLE_CLOUD_LOCATION:-global}"
MAX_INSTANCES="${CLOUD_RUN_MAX_INSTANCES:-2}"
DEPLOY_MODE="${CLOUD_RUN_DEPLOY_MODE:-image}"
BUILD_REGION="${CLOUD_RUN_BUILD_REGION:-global}"
CLOUD_CONFIG_PATH="${CLOUD_RUN_CONFIG_PATH:-/app/configs/math-agent-cloud-run.toml}"
IMAGE_REPOSITORY="${CLOUD_RUN_IMAGE_REPOSITORY:-${REGION}-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE_NAME}}"
IMAGE_TAG="${CLOUD_RUN_IMAGE_TAG:-$(date -u +%Y%m%d%H%M%S)}"

if [[ -z "$PROJECT_ID" || "$PROJECT_ID" == "(unset)" ]]; then
  printf '%s\n' 'Set CLOUD_RUN_PROJECT or GOOGLE_CLOUD_PROJECT, or configure gcloud config set project PROJECT_ID.' >&2
  exit 2
fi
if [[ ! "$SERVICE_NAME" =~ ^[a-z][a-z0-9-]{0,48}[a-z0-9]$ ]]; then
  printf '%s\n' 'CLOUD_RUN_SERVICE must be a lowercase Cloud Run service name.' >&2
  exit 2
fi
if [[ ! "$MAX_INSTANCES" =~ ^[1-9][0-9]*$ ]]; then
  printf '%s\n' 'CLOUD_RUN_MAX_INSTANCES must be a positive integer.' >&2
  exit 2
fi
if [[ "$DEPLOY_MODE" != "image" && "$DEPLOY_MODE" != "source" ]]; then
  printf '%s\n' 'CLOUD_RUN_DEPLOY_MODE must be image or source.' >&2
  exit 2
fi

deploy_args=(
  "$SERVICE_NAME"
  --project "$PROJECT_ID"
  --region "$REGION"
  --platform managed
  --allow-unauthenticated
  --min 0
  --max "$MAX_INSTANCES"
  --concurrency 1
  --memory 1Gi
  --cpu 1
  --timeout 900
  --port 8080
  --set-env-vars "MATH_AGENT_CLOUD_RUN=1,MATH_AGENT_CONFIG=$CLOUD_CONFIG_PATH,MATH_AGENT_DATA_DIR=/tmp/math-agent,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=$VERTEX_LOCATION"
)

if [[ "$DEPLOY_MODE" == "image" ]]; then
  IMAGE_URI="${IMAGE_REPOSITORY}:${IMAGE_TAG}"
  printf '%s\n' "Building $IMAGE_URI with Cloud Build in $BUILD_REGION"
  BUILD_ID="$(gcloud builds submit "$ROOT_DIR" \
    --project "$PROJECT_ID" \
    --region "$BUILD_REGION" \
    --tag "$IMAGE_URI" \
    --async \
    --format='value(id)')"
  if [[ -z "$BUILD_ID" ]]; then
    printf '%s\n' 'Cloud Build submission returned no build id.' >&2
    exit 1
  fi
  printf '%s\n' "Cloud Build submitted: $BUILD_ID"
  while :; do
    BUILD_STATUS="$(gcloud builds describe "$BUILD_ID" \
      --project "$PROJECT_ID" \
      --region "$BUILD_REGION" \
      --format='value(status)')"
    case "$BUILD_STATUS" in
      SUCCESS)
        break
        ;;
      FAILURE|INTERNAL_ERROR|TIMEOUT|CANCELLED|EXPIRED)
        printf '%s\n' "Cloud Build $BUILD_ID ended with status $BUILD_STATUS. See the Cloud Build console for logs." >&2
        exit 1
        ;;
      *)
        printf '%s\n' "Cloud Build $BUILD_ID: ${BUILD_STATUS:-UNKNOWN}"
        sleep 5
        ;;
    esac
  done
  deploy_args+=(--image "$IMAGE_URI")
else
  deploy_args+=(--source "$ROOT_DIR")
fi

if [[ -n "${CLOUD_RUN_SERVICE_ACCOUNT:-}" ]]; then
  deploy_args+=(--service-account "$CLOUD_RUN_SERVICE_ACCOUNT")
fi

printf '%s\n' "Deploying $SERVICE_NAME to Cloud Run in $REGION (mode: $DEPLOY_MODE, min instances: 0, max instances: $MAX_INSTANCES)"
gcloud run deploy "${deploy_args[@]}"

SERVICE_URL="$(gcloud run services describe "$SERVICE_NAME" --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')"
printf '%s\n' "Cloud Run URL: $SERVICE_URL"
printf '%s\n' 'Runtime proof from the deployed backend:'
curl --fail --silent --show-error "$SERVICE_URL/health"
printf '\n%s\n' 'Open the /health URL in a browser for the visual Cloud Run proof; the response includes K_SERVICE and K_REVISION-derived evidence.'
