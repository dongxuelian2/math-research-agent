# Cloud Run deployment and visual proof

This deployment serves the GUI and proof backend from one Cloud Run service and
one `run.app` origin. Cloud Run supplies one `PORT`; cloud mode binds the GUI
to that port and dispatches `/health` plus `/v1/...` to the in-process proof
API. The container filesystem is temporary; this is suitable for a contest
demo, not a durable multi-instance research archive.

## Prerequisites

Install the Google Cloud CLI, run `gcloud auth login`, select or provide a
project, and make sure billing is enabled. The deployer needs Cloud Build submit
and Cloud Run deployment permissions. The Cloud Build service identity needs
permission to push to the existing Artifact Registry repository. The Cloud Run
service identity needs permission to call Vertex AI (for example, the
least-privilege Vertex AI User role), and the Vertex AI API must be enabled.

The runtime does not receive a credential JSON file. `@google/genai` uses
Application Default Credentials: the Cloud Run service identity in production,
or local ADC when testing outside Cloud Run.

Cloud Run intentionally uses `configs/math-agent-cloud-run.toml`. That profile
defaults to the non-formal `prove` mode and disables the process-backed
formalization gate; the Dockerfile does not install or upload Lean, Lake, or
Mathlib. The complete formalization source, session project manager, and
upstream Lean skill remain in the repository for a local/full-runtime deploy.

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
service identity is used. By default the script submits the repository
Dockerfile to Cloud Build in `global`, pushes a uniquely tagged image to the
existing `cloud-run-source-deploy` Artifact Registry repository, and deploys
that image. This avoids waiting on a regional `gcloud run deploy --source`
queue. It sets `--min 0` and a bounded service-level `--max` (default `2`),
starts the same-origin GUI/API container, and prints the deployed URL plus a
live `/health` response. Set `CLOUD_RUN_DEPLOY_MODE=source` only when the
regional source-build path is intentionally desired.

## Video/demo proof

1. Keep the successful `gcloud run deploy` output visible and open the printed
   `https://...run.app/health` URL in a browser.
2. Open the base `https://...run.app/` URL in another tab and show that the
   Proof Workbench GUI loads. Its footer should say `Proof API 同源连接`.
3. The JSON response must show `ok: true`, `service:
   "math-agent-proof-api"`, and `runtime.platform: "Google Cloud Run"`, along
   with the Cloud Run service and revision names.
4. In a second browser tab or terminal, use the same `run.app` origin for the
   public API. The Cloud Run demo is intentionally non-formal, so a minimal
   proof request is:

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

The first response should create the session quickly and should not contain a
`leanProject` object: that is the expected evidence that this Cloud Run image
does not attempt to initialize Lean. The GUI's proof mode defaults to `prove`.
Use `configs/math-agent.toml` with the full local toolchain when a real Lean
formalization run is needed; it is deliberately a separate runtime profile.

Poll `GET /v1/sessions/cloud-run-demo/proof-runs` and then the run's
`/result` endpoint. The visible combination of the GUI, the same-origin API,
the `run.app` origin, the Cloud Run revision fields from `/health`, and the
real backend API response is the demo evidence. After recording, scale the
service back down or delete it; the service does not need to remain
continuously running after the contest.
