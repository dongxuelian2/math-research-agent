# Cloud Run deployment and visual proof

This deployment is intentionally API-only. Cloud Run supplies one `PORT`, so
the container binds the proof backend directly to that port. The container
filesystem is temporary; this is suitable for a contest demo, not a durable
multi-instance research archive.

## Prerequisites

Install the Google Cloud CLI, run `gcloud auth login`, select or provide a
project, and make sure billing is enabled. The deployer needs Cloud Run source
deployment permissions. The Cloud Run service identity needs permission to
call Vertex AI (for example, the least-privilege Vertex AI User role), and the
Vertex AI API must be enabled.

The runtime does not receive a credential JSON file. `@google/genai` uses
Application Default Credentials: the Cloud Run service identity in production,
or local ADC when testing outside Cloud Run.

## Deploy

From the repository root:

```bash
export CLOUD_RUN_PROJECT="your-gcp-project-id"
export CLOUD_RUN_REGION="us-central1"       # Cloud Run service region
export GOOGLE_CLOUD_LOCATION="global"       # Vertex model location
export CLOUD_RUN_SERVICE_ACCOUNT="runner@your-gcp-project-id.iam.gserviceaccount.com"
bash scripts/deploy-cloud-run.sh
```

`CLOUD_RUN_SERVICE_ACCOUNT` is optional; when omitted, Cloud Run's default
service identity is used. The script deploys from the repository Dockerfile;
`.gcloudignore` also keeps local credentials and runtime state out of the
Cloud Build source upload. It sets `--min 0` and a bounded `--max` (default
`2`), and prints the deployed URL plus a live `/health` response.

## Video/demo proof

1. Keep the successful `gcloud run deploy` output visible and open the printed
   `https://...run.app/health` URL in a browser.
2. The JSON response must show `ok: true`, `service:
   "math-agent-proof-api"`, and `runtime.platform: "Google Cloud Run"`, along
   with the Cloud Run service and revision names.
3. In a second browser tab or terminal, use the same `run.app` origin for the
   public API. A minimal proof request is:

```bash
BASE_URL="https://your-service-xxxxx-uc.a.run.app"
curl -fsS -X POST "$BASE_URL/v1/sessions" \
  -H 'content-type: application/json' -d '{"sessionId":"cloud-run-demo"}'
curl -fsS -X POST "$BASE_URL/v1/sessions/cloud-run-demo/theorem" \
  -H 'content-type: application/json' \
  -d '{"theorem":"For every integer n >= 1, 1 + 3 + ... + (2n - 1) = n^2."}'
curl -fsS -X POST "$BASE_URL/v1/sessions/cloud-run-demo/proof-runs" \
  -H 'content-type: application/json' -d '{"mode":"prove"}'
```

Poll `GET /v1/sessions/cloud-run-demo/proof-runs` and then the run's
`/result` endpoint. The visible combination of the `run.app` origin, the
Cloud Run revision fields from `/health`, and the real backend API response is
the demo evidence. After recording, scale the service back down or delete it;
the service does not need to remain continuously running after the contest.
