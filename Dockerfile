FROM node:22-bookworm-slim AS build

WORKDIR /build
ENV NODE_ENV=production

# Build from the complete repository. The final stage below copies only the
# compiled backend, GUI, runtime dependencies, and Cloud Run profile, so the
# full Lean source/skill stays in the repository rather than the runtime image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

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

FROM node:22-bookworm-slim

WORKDIR /app
ENV NODE_ENV=production

# Cloud Run deliberately uses the lightweight proof profile. The deployed
# image has no Lean/Lake/Mathlib binaries or package checkout.
ENV MATH_AGENT_CONFIG=/app/configs/math-agent-cloud-run.toml
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=build /build/package.json ./package.json
COPY --from=build /build/node_modules ./node_modules
COPY --from=build /build/backend/package.json ./backend/package.json
COPY --from=build /build/backend/node_modules ./backend/node_modules
COPY --from=build /build/backend/dist ./backend/dist
COPY --from=build /build/apps/proof-workbench/package.json ./apps/proof-workbench/package.json
COPY --from=build /build/apps/proof-workbench/server ./apps/proof-workbench/server
COPY --from=build /build/apps/proof-workbench/web ./apps/proof-workbench/web
COPY --from=build /build/configs/math-agent-cloud-run.toml ./configs/math-agent-cloud-run.toml

EXPOSE 8080

# Cloud Run exposes one HTTP port. Cloud mode serves the GUI and dispatches
# /health and /v1/* to the in-process proof API on that same run.app origin.
CMD ["node", "apps/proof-workbench/server/main.mjs", "--cloud-run"]
