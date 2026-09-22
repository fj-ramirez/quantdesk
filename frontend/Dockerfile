# Two targets from one file:
#
#   --target dev      what compose.override.yaml builds. The Vite dev server, source arriving
#                     via a bind mount, hot reload.
#   --target runtime  the default and what ships. A static bundle built at image-build time
#                     and served by nginx. No node, no dev server, no source.

FROM node:22-slim AS deps

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci


# --- dev -----------------------------------------------------------------------------------
FROM node:22-slim AS dev

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm install

COPY . .

EXPOSE 5173

# compose mounts an anonymous volume over /app/node_modules so the Windows-built host
# node_modules can't shadow the Linux-built one (esbuild/rollup ship per-platform binaries).
# That volume outlives `docker compose down` and keeps serving whatever was installed when it
# was first created, so adding a dependency produces a "Failed to resolve import" that a
# rebuild does not fix. Reinstall whenever the lockfile differs from what the volume holds.
CMD ["sh", "-c", "cmp -s package-lock.json node_modules/.lock-stamp || { npm ci && cp package-lock.json node_modules/.lock-stamp; }; exec npm run dev -- --host 0.0.0.0"]


# --- build ---------------------------------------------------------------------------------
FROM deps AS build

WORKDIR /app

COPY . .

# Vite inlines every VITE_* variable into the bundle at BUILD time -- there is no runtime
# lookup to override later, which is why this is an ARG and not an environment variable on the
# running container.
#
# The default is empty, and that is the whole same-origin story. `src/api/client.ts` treats a
# value that is not an absolute http(s) URL as "resolve against the page's own origin", so the
# bundle ships with no hostname baked in and works unchanged whether it was reached as
# homeserver.local over the LAN or as a Tailscale name from elsewhere. Every request path in
# that module already begins with /api, so the reverse proxy in front just has to route /api/*
# to the backend and leave the prefix alone.
#
# Pass a full origin here only for a deployment that genuinely serves the API from somewhere
# else -- and then it must be an absolute URL, since anything relative resolves to same-origin.
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

# The bundle's own build stamp, inlined by Vite exactly like the base URL above. The frontend
# reports itself because nothing else can: it is a static bundle behind nginx with no database
# and no shared volume, and on 2026-09-21 a deploy updated four containers and failed on this
# one -- which a single stack-wide version number would have hidden.
ARG VITE_BUILD_SHA=""
ARG VITE_BUILD_TIME=""
ENV VITE_BUILD_SHA=$VITE_BUILD_SHA     VITE_BUILD_TIME=$VITE_BUILD_TIME

# `npm run build` is `tsc -b && vite build`, so a type error fails the image build rather than
# shipping. MSW is not a concern here: src/main.tsx gates its import on `import.meta.env.DEV`,
# which is false for a production build, so the mock worker and its fixtures are tree-shaken
# out entirely.
RUN npm run build


# --- runtime -------------------------------------------------------------------------------
FROM nginx:alpine AS runtime

# Replaces the stock server block, which has no SPA fallback -- without it every deep link
# (/scan, /history, anything the React router owns) 404s on reload because there is no file at
# that path.
COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# nginx:alpine's own CMD already runs in the foreground; inherited rather than repeated.
