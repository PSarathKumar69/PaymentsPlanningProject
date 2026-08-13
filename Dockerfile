# Docker task: one image, one process — FastAPI serves both the API and
# the built React app (see backend/api/main.py's frontend_dist handling).
# On Vercel these are two separate "services" wired together by
# vercel.json's rewrites; a single container has no second service to hand
# the frontend off to, so stage 1 builds it and stage 2 ships the result
# alongside the backend.

# ---- Stage 1: build the React frontend ----
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: the app itself ----
FROM python:3.11-slim AS backend
WORKDIR /app

# Every dependency here (bcrypt, psycopg2-binary, pandas, ...) ships a
# manylinux wheel for this base image — no compiler/apt install needed.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY data/ ./data/
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# No DATABASE_URL -> local SQLite at backend/db/app.db (session.py's own
# default) — fine for a quick look at the UI, but it lives INSIDE the
# container and is gone on the next `docker run` unless you mount a volume
# over /app/backend/db. For anything real, set DATABASE_URL to your
# Postgres/Neon instance instead, same as the Vercel deployment already
# does. JWT_SECRET_KEY is required whenever DATABASE_URL is set
# (backend/auth/security.py refuses to start without it in that case).
ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# "/" (not an API route) is the health-check target on purpose: it's
# reachable with no session (the React app itself shows the login page),
# so this checks "is the app up" without a 401 on every login-required
# API route making the container look unhealthy when it's really fine.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/', timeout=2)" || exit 1

# AWS App Runner (and any single-trusted-reverse-proxy AWS setup) terminates
# TLS in front of this container and forwards plain HTTP with
# X-Forwarded-Proto/X-Forwarded-For set. Without --proxy-headers, uvicorn/
# Starlette never learn the original request was HTTPS (request.url.scheme
# stays "http", client IP stays the proxy's own) — wrong for anything that
# inspects scheme or client IP (redirects, logging, security headers).
# --forwarded-allow-ips=* is safe here specifically because App Runner's
# network model means the proxy is the ONLY thing that can reach this
# container directly; it is not a wildcard trust of arbitrary internet
# traffic. COOKIE_SECURE itself stays driven by DATABASE_URL (unrelated),
# so this doesn't change cookie behavior — it fixes scheme/client-IP
# correctness for everything else running behind that same proxy.
CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
