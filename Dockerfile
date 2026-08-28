FROM node:22-bookworm-slim

WORKDIR /app
ENV NODE_ENV=production

RUN corepack enable && corepack prepare pnpm@11.7.0 --activate

# Install from the lockfile before copying the rest of the source so Cloud
# Build can reuse the dependency layer between revisions.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY backend/package.json backend/package.json
COPY apps/proof-workbench/package.json apps/proof-workbench/package.json
# The published SDK already contains its runtime distribution. Avoid running
# transitive package install hooks in the build image; no application build
# step depends on them and this keeps the container build deterministic under
# pnpm's build-script approval policy.
RUN pnpm install --frozen-lockfile --ignore-scripts

COPY . .
RUN pnpm run build

EXPOSE 8080

# Cloud Run exposes one HTTP port. The API-only mode binds to the injected
# PORT, which makes /health and /v1/* directly reachable at the run.app URL.
CMD ["node", "apps/proof-workbench/server/main.mjs", "--api-only"]
