FROM node:22-slim

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
