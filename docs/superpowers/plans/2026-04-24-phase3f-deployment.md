# Phase 3F: Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Production-ready Docker Compose deployment for datahub-gateway + datahub-core + PostgreSQL

**Architecture:** Three-service Docker Compose with shared PostgreSQL, health checks, and volume management

**Tech Stack:** Docker, Docker Compose, PostgreSQL 16, GitHub Actions

---

## Task 1: Database Init Script

**Files:** `~/code/datahub-gateway/scripts/init-db.sql`

- [ ] 1. Create the scripts directory:
  ```bash
  mkdir -p ~/code/datahub-gateway/scripts
  ```

- [ ] 2. Create `~/code/datahub-gateway/scripts/init-db.sql` with the following content:
  ```sql
  -- Forgejo uses the default 'forgejo' database (created by POSTGRES_DB env)

  -- Create datahub database and user
  CREATE USER datahub WITH PASSWORD 'datahub';
  CREATE DATABASE datahub OWNER datahub;

  -- Connect to datahub database and create schema
  \c datahub
  CREATE SCHEMA IF NOT EXISTS datahub AUTHORIZATION datahub;
  ```

- [ ] 3. Test the script manually:
  ```bash
  docker run --rm \
    -e POSTGRES_USER=forgejo \
    -e POSTGRES_PASSWORD=forgejo \
    -e POSTGRES_DB=forgejo \
    -v ~/code/datahub-gateway/scripts/init-db.sql:/docker-entrypoint-initdb.d/init-db.sql \
    -p 5432:5432 \
    postgres:16
  ```
  In a second terminal, verify both databases exist:
  ```bash
  docker exec -it <container_id> psql -U forgejo -c "\l"
  ```
  Expected output: rows for both `forgejo` and `datahub` databases.

- [ ] 4. Commit:
  ```bash
  cd ~/code/datahub-gateway
  git add scripts/init-db.sql
  git commit -m "feat: add PostgreSQL init script for forgejo + datahub databases"
  ```

---

## Task 2: Gateway Dockerfile

**Files:** `~/code/datahub-gateway/Dockerfile`

> Note: Check whether a Dockerfile already exists in the repo. If it does, adapt rather than overwrite.

- [ ] 1. Check for existing Dockerfile:
  ```bash
  ls ~/code/datahub-gateway/Dockerfile 2>/dev/null && echo "EXISTS" || echo "MISSING"
  ```

- [ ] 2. If MISSING, create `~/code/datahub-gateway/Dockerfile`:
  ```dockerfile
  # Stage 1: Build Go backend
  FROM golang:1.23-alpine AS backend
  WORKDIR /src
  COPY go.mod go.sum ./
  RUN go mod download
  COPY . .
  RUN CGO_ENABLED=0 go build -o forgejo ./cmd

  # Stage 2: Build frontend assets
  FROM node:22-alpine AS frontend
  WORKDIR /src
  COPY package.json package-lock.json ./
  RUN npm ci
  COPY web_src/ web_src/
  COPY webpack.config.js .
  RUN npx webpack

  # Stage 3: Final minimal image
  FROM alpine:3.20
  RUN apk add --no-cache git curl
  COPY --from=backend /src/forgejo /usr/local/bin/forgejo
  COPY --from=frontend /src/public/ /usr/local/share/forgejo/public/
  COPY templates/ /usr/local/share/forgejo/templates/

  EXPOSE 3000
  ENTRYPOINT ["forgejo"]
  CMD ["web"]
  ```

- [ ] 3. If EXISTS, ensure it has all three stages (backend Go build, frontend webpack, alpine final) and the `RUN apk add --no-cache git curl` line in the final stage (needed by Forgejo and for health-check curl calls). Patch as needed.

- [ ] 4. Test the Docker build:
  ```bash
  cd ~/code/datahub-gateway
  docker build -t datahub-gateway:dev .
  ```
  Expected: build completes without error and image is listed by `docker images datahub-gateway`.

- [ ] 5. Commit:
  ```bash
  cd ~/code/datahub-gateway
  git add Dockerfile
  git commit -m "feat: multi-stage Dockerfile for datahub-gateway (Go + webpack + alpine)"
  ```

---

## Task 3: Core Dockerfile

**Files:** `~/code/datahub/Dockerfile`

> datahub-core is a FastAPI/uvicorn service. The project uses `pyproject.toml` with the `[server]` optional group providing `fastapi`, `uvicorn`, `sqlalchemy[asyncio]`, `asyncpg`, `pydantic-settings`, and `alembic`. No Dockerfile currently exists in the repo.

- [ ] 1. Confirm no Dockerfile exists:
  ```bash
  ls ~/code/datahub/Dockerfile 2>/dev/null && echo "EXISTS" || echo "MISSING"
  ```

- [ ] 2. Create `~/code/datahub/Dockerfile`:
  ```dockerfile
  FROM python:3.12-slim

  WORKDIR /app

  # Install system deps needed by asyncpg and general tooling
  RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      curl \
      && rm -rf /var/lib/apt/lists/*

  # Copy project metadata first for layer caching
  COPY pyproject.toml ./
  # Copy source so hatchling can build the package
  COPY src/ ./src/

  # Install package with server extras
  RUN pip install --no-cache-dir ".[server]"

  # Data directory for object storage
  RUN mkdir -p /data/objects

  EXPOSE 8000

  HEALTHCHECK --interval=5s --timeout=3s --retries=3 --start-period=10s \
    CMD curl -f http://localhost:8000/health || exit 1

  CMD ["uvicorn", "dit.server.app:app", "--host", "0.0.0.0", "--port", "8000"]
  ```

- [ ] 3. Verify the health endpoint exists in the application. Check:
  ```bash
  grep -r "health" ~/code/datahub/src/dit/server/ --include="*.py" -l
  ```
  If `/health` is not found, add it to the app. Open `~/code/datahub/src/dit/server/app.py` and add:
  ```python
  @app.get("/health")
  async def health():
      return {"status": "ok"}
  ```

- [ ] 4. Test the Docker build:
  ```bash
  cd ~/code/datahub
  docker build -t datahub-core:dev .
  ```
  Expected: build completes without error.

- [ ] 5. Smoke-test the container starts and health endpoint responds:
  ```bash
  docker run --rm -d -p 8000:8000 \
    -e DATABASE_URL="sqlite+aiosqlite:///./test.db" \
    -e DATA_DIR="/data/objects" \
    --name core-test datahub-core:dev
  sleep 5
  curl -f http://localhost:8000/health
  docker stop core-test
  ```
  Expected output: `{"status":"ok"}`

- [ ] 6. Commit:
  ```bash
  cd ~/code/datahub
  git add Dockerfile src/dit/server/app.py  # include app.py only if /health was added
  git commit -m "feat: Dockerfile for datahub-core (Python 3.12 + uvicorn + health check)"
  ```

---

## Task 4: Docker Compose

**Files:** `~/code/datahub-gateway/docker-compose.yml`

- [ ] 1. Create `~/code/datahub-gateway/docker-compose.yml`:
  ```yaml
  version: "3.8"

  services:
    gateway:
      build:
        context: .
        dockerfile: Dockerfile
      ports:
        - "3000:3000"
      environment:
        FORGEJO__database__DB_TYPE: postgres
        FORGEJO__database__HOST: db:5432
        FORGEJO__database__NAME: forgejo
        FORGEJO__database__USER: forgejo
        FORGEJO__database__PASSWD: forgejo
        FORGEJO__datahub__ENABLED: "true"
        FORGEJO__datahub__CORE_URL: "http://core:8000"
        FORGEJO__datahub__SERVICE_TOKEN: "${SERVICE_TOKEN}"
      depends_on:
        db:
          condition: service_healthy
        core:
          condition: service_healthy
      volumes:
        - forgejo-data:/data
      healthcheck:
        test: ["CMD", "curl", "-f", "http://localhost:3000/api/v1/version"]
        interval: 10s
        timeout: 5s
        retries: 5
        start_period: 30s
      restart: unless-stopped

    core:
      build:
        context: ../datahub
        dockerfile: Dockerfile
      ports:
        - "8000:8000"
      environment:
        DATABASE_URL: "postgresql+asyncpg://datahub:datahub@db:5432/datahub"
        DATA_DIR: "/data/objects"
      depends_on:
        db:
          condition: service_healthy
      volumes:
        - core-data:/data
      healthcheck:
        test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
        interval: 5s
        timeout: 3s
        retries: 3
        start_period: 10s
      restart: unless-stopped

    db:
      image: postgres:16
      environment:
        POSTGRES_USER: forgejo
        POSTGRES_PASSWORD: "${POSTGRES_PASSWORD:-forgejo}"
        POSTGRES_DB: forgejo
      healthcheck:
        test: ["CMD-SHELL", "pg_isready -U forgejo"]
        interval: 5s
        timeout: 3s
        retries: 3
      volumes:
        - pg-data:/var/lib/postgresql/data
        - ./scripts/init-db.sql:/docker-entrypoint-initdb.d/init-db.sql
      restart: unless-stopped

  volumes:
    forgejo-data:
    core-data:
    pg-data:
  ```

- [ ] 2. Test the full stack builds and starts:
  ```bash
  cd ~/code/datahub-gateway
  docker compose up --build
  ```
  Expected: all three services reach healthy state. Gateway accessible at http://localhost:3000, core at http://localhost:8000/health.

- [ ] 3. Verify health checks pass:
  ```bash
  docker compose ps
  ```
  Expected: all services show `healthy` in the STATUS column.

- [ ] 4. Tear down:
  ```bash
  docker compose down
  ```

- [ ] 5. Commit:
  ```bash
  cd ~/code/datahub-gateway
  git add docker-compose.yml
  git commit -m "feat: Docker Compose for gateway + core + PostgreSQL with health checks"
  ```

---

## Task 5: Environment Config

**Files:**
- `~/code/datahub-gateway/.env.example`
- `~/code/datahub-gateway/.gitignore` (update)

- [ ] 1. Create `~/code/datahub-gateway/.env.example`:
  ```env
  # Copy this file to .env and fill in values before running docker compose
  SERVICE_TOKEN=
  POSTGRES_PASSWORD=
  ```

- [ ] 2. Check if `.gitignore` exists:
  ```bash
  ls ~/code/datahub-gateway/.gitignore 2>/dev/null && echo "EXISTS" || echo "MISSING"
  ```

- [ ] 3. If EXISTS, append `.env` to it (only if not already present):
  ```bash
  grep -q "^\.env$" ~/code/datahub-gateway/.gitignore || echo ".env" >> ~/code/datahub-gateway/.gitignore
  ```

- [ ] 4. If MISSING, create `~/code/datahub-gateway/.gitignore` with at minimum:
  ```
  .env
  ```

- [ ] 5. Verify `.env` is ignored but `.env.example` is tracked:
  ```bash
  cd ~/code/datahub-gateway
  echo "SERVICE_TOKEN=test" > .env
  git status
  # .env should NOT appear in untracked files
  # .env.example SHOULD appear as a new file
  rm .env
  ```

- [ ] 6. Commit:
  ```bash
  cd ~/code/datahub-gateway
  git add .env.example .gitignore
  git commit -m "feat: add .env.example and ensure .env is gitignored"
  ```

---

## Task 6: CI Pipeline

**Files:** `~/code/datahub-gateway/.github/workflows/gateway.yml`

- [ ] 1. Create the workflows directory:
  ```bash
  mkdir -p ~/code/datahub-gateway/.github/workflows
  ```

- [ ] 2. Create `~/code/datahub-gateway/.github/workflows/gateway.yml`:
  ```yaml
  name: Gateway CI

  on:
    push:
      branches: [main]
    pull_request:

  jobs:
    test:
      name: Go Tests
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v4
        - uses: actions/setup-go@v5
          with:
            go-version: "1.23"
        - name: Run datahub unit tests
          run: go test ./modules/datahub/... ./routers/api/v1/repo/...

    lint:
      name: Go Lint
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v4
        - uses: golangci/golangci-lint-action@v4
          with:
            version: latest

    build:
      name: Docker Build
      runs-on: ubuntu-latest
      needs: [test, lint]
      steps:
        - uses: actions/checkout@v4
        - name: Set up Docker Buildx
          uses: docker/setup-buildx-action@v3
        - name: Build gateway image
          run: docker compose build gateway
  ```

- [ ] 3. Validate YAML syntax:
  ```bash
  python3 -c "import yaml; yaml.safe_load(open('$HOME/code/datahub-gateway/.github/workflows/gateway.yml'))" && echo "YAML valid"
  ```
  Expected output: `YAML valid`

- [ ] 4. Optional — test locally with `act` if installed:
  ```bash
  which act && cd ~/code/datahub-gateway && act --list
  ```
  If `act` is not installed, skip this step; YAML validation above is sufficient.

- [ ] 5. Commit:
  ```bash
  cd ~/code/datahub-gateway
  git add .github/workflows/gateway.yml
  git commit -m "ci: add GitHub Actions workflow for Go test, lint, and Docker build"
  ```

---

## Task 7: Dev Workflow Docs

**Files:** `~/code/datahub-gateway/DEVELOPMENT.md`

> Check if a README.md already covers dev setup. If so, add a Development section to it instead of creating a separate file. If the README is minimal or absent, create DEVELOPMENT.md.

- [ ] 1. Check for existing README:
  ```bash
  ls ~/code/datahub-gateway/README.md 2>/dev/null && echo "EXISTS" || echo "MISSING"
  ```

- [ ] 2. Create `~/code/datahub-gateway/DEVELOPMENT.md` (or append to README.md if it already exists with substantial content):
  ```markdown
  # Development Guide

  ## Prerequisites

  - Go 1.23+
  - Node.js 22+
  - Docker and Docker Compose
  - PostgreSQL 16 (or use the Docker service)

  ---

  ## Local Development (without full Docker)

  Run each component in a separate terminal for fast iteration.

  **Terminal 1 — PostgreSQL (Docker)**
  ```bash
  cd ~/code/datahub-gateway
  docker compose up db
  ```

  **Terminal 2 — datahub-core**
  ```bash
  cd ~/code/datahub
  # First time only:
  pip install -e ".[server]"
  # Set database URL to the Docker Postgres instance
  export DATABASE_URL="postgresql+asyncpg://datahub:datahub@localhost:5432/datahub"
  export DATA_DIR="./data/objects"
  uvicorn dit.server.app:app --reload --port 8000
  ```

  **Terminal 3 — datahub-gateway (Forgejo)**
  ```bash
  cd ~/code/datahub-gateway
  make watch
  ```

  Gateway available at http://localhost:3000
  Core API available at http://localhost:8000

  ---

  ## Full Stack (Docker Compose)

  ```bash
  cd ~/code/datahub-gateway

  # First time: copy and configure environment
  cp .env.example .env
  # Edit .env — set SERVICE_TOKEN and POSTGRES_PASSWORD

  # Build and start all services
  docker compose up --build

  # In background
  docker compose up --build -d

  # View logs
  docker compose logs -f

  # Stop and remove containers (keep volumes)
  docker compose down

  # Stop and remove everything including volumes (full reset)
  docker compose down -v
  ```

  Services:
  - Gateway: http://localhost:3000
  - Core API: http://localhost:8000 (internal only, exposed for debugging)

  ---

  ## Common Commands

  | Command | Description |
  |---------|-------------|
  | `docker compose up db` | Start only PostgreSQL |
  | `docker compose up --build` | Build and start all services |
  | `docker compose down -v` | Full teardown including volumes |
  | `docker compose ps` | Check service health status |
  | `docker compose logs core` | View datahub-core logs |
  | `docker compose logs gateway` | View gateway logs |
  | `go test ./modules/datahub/...` | Run datahub Go unit tests |
  | `make watch` | Start gateway with hot reload |

  ---

  ## Database Access

  ```bash
  # Connect to forgejo database
  docker compose exec db psql -U forgejo forgejo

  # Connect to datahub database
  docker compose exec db psql -U datahub datahub

  # List all databases
  docker compose exec db psql -U forgejo -c "\l"
  ```

  ---

  ## Environment Variables

  See `.env.example` for all required variables.

  | Variable | Description |
  |----------|-------------|
  | `SERVICE_TOKEN` | Shared secret between gateway and core for internal API calls |
  | `POSTGRES_PASSWORD` | Password for the `forgejo` PostgreSQL superuser |
  ```

- [ ] 3. Commit:
  ```bash
  cd ~/code/datahub-gateway
  git add DEVELOPMENT.md  # or README.md if appended there
  git commit -m "docs: add development guide for local and Docker Compose workflows"
  ```

---

## Completion Checklist

- [ ] `scripts/init-db.sql` creates `datahub` user, database, and schema
- [ ] Gateway `Dockerfile` has three stages: Go backend, webpack frontend, alpine final
- [ ] Core `Dockerfile` exists at `~/code/datahub/Dockerfile` with health check
- [ ] `docker-compose.yml` starts all three services with correct `depends_on` conditions
- [ ] `.env.example` committed, `.env` gitignored
- [ ] `.github/workflows/gateway.yml` passes YAML validation
- [ ] `DEVELOPMENT.md` covers both local and Docker Compose workflows
- [ ] `docker compose up --build` runs end-to-end without errors
