# Explainer Studio: the built frontend and the Flask API in one image (Render, Fly, Railway...).
# Build from explainer-studio/:  docker build -t explainer-studio .
# Run:  docker run -p 8000:8000 -e ANTHROPIC_API_KEY=... -e JEV_API_KEY=... -e DATABASE_URL=postgres://... -e JWT_SECRET=... explainer-studio

FROM node:22-slim AS web
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# Explorer's checks (server/explainer/modes/explorer/playground.py) run in Node: scripts/check-playground.mjs dry-runs each
# playground; scripts/snap-widget.mjs takes the look check's screenshots with Playwright (its browser is installed in the
# final image below), loading Vega + Vega-Lite for widgets that use them (same versions as package.json).
FROM node:22-slim AS checker
WORKDIR /chk
RUN npm init -y >/dev/null && npm install --omit=dev jsdom@26.1.0 vega@6.4.0 vega-lite@6.5.0 playwright@1.64.1

FROM python:3.12-slim
WORKDIR /app
COPY server/ ./server/
RUN pip install --no-cache-dir ./server
COPY --from=web /src/dist ./dist
COPY --from=checker /usr/local/bin/node /usr/local/bin/node
COPY --from=checker /chk/node_modules ./node_modules
COPY scripts/snap-widget.mjs scripts/check-playground.mjs scripts/read-playground.mjs ./scripts/
# Headless Chromium for the look check (PG_LOOK=1), with the system libraries it needs (~+300 MB). Without it the look
# check is skipped (it fails open) and everything else works.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN node node_modules/playwright/cli.js install --with-deps --only-shell chromium && rm -rf /var/lib/apt/lists/*
# The helper kit (window.XW) the screenshots load, as in the real frame, and Vega's eval-free interpreter.
COPY shared/widget-kit.js shared/vega-interpreter.js ./shared/
# The page's colours (light/dark, palettes) the screenshots read, so follow-ups v5's look tool sees the page's own look.
COPY app/src/styles/tokens.css app/src/styles/themes.css ./app/src/styles/
# Playground mode's checker (scripts/check-playground.mjs) runs the playground validator and the runtime files it loads.
COPY shared/playground/ ./shared/playground/
ENV EXPLAINER_DIST=/app/dist PORT=8000 ENV=production
WORKDIR /app/server
EXPOSE 8000
# Migrate the database once, before the workers start, then serve.
CMD ["sh", "-c", "alembic upgrade head && exec gunicorn -c gunicorn.conf.py"]
