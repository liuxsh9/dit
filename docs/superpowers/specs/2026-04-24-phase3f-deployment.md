# Phase 3F: Deployment — Docker Compose & CI

> **Parent spec:** 2026-04-24-phase3-web-ui-design.md  
> **Date:** 2026-04-24  
> **Depends on:** Phase 3D + 3E

---

## Overview

Production-ready Docker Compose setup for datahub-gateway (Forgejo fork) + datahub-core (FastAPI) + PostgreSQL. Includes health checks, volume management, and database initialization.

---

## 1. Docker Compose

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
      POSTGRES_PASSWORD: forgejo
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

---

## 2. Database Initialization

### `scripts/init-db.sql`

PostgreSQL runs scripts in `/docker-entrypoint-initdb.d/` on first start. This creates both databases and the datahub schema.

```sql
-- Forgejo uses the default 'forgejo' database (created by POSTGRES_DB env)

-- Create datahub database and user
CREATE USER datahub WITH PASSWORD 'datahub';
CREATE DATABASE datahub OWNER datahub;

-- Connect to datahub database and create schema
\c datahub
CREATE SCHEMA IF NOT EXISTS datahub AUTHORIZATION datahub;
```

Note: datahub-core runs its own Alembic migrations on startup to create tables within the `datahub` schema.

---

## 3. Environment Configuration

### `.env` file (not committed)

```env
SERVICE_TOKEN=change-me-in-production
POSTGRES_PASSWORD=forgejo
```

### `.env.example` (committed)

```env
SERVICE_TOKEN=
POSTGRES_PASSWORD=
```

---

## 4. Gateway Dockerfile

The Forgejo fork builds like standard Forgejo with one addition: the `[datahub]` config section.

```dockerfile
FROM golang:1.23-alpine AS backend
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o forgejo ./cmd

FROM node:22-alpine AS frontend
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY web_src/ web_src/
COPY webpack.config.js .
RUN npx webpack

FROM alpine:3.20
RUN apk add --no-cache git curl
COPY --from=backend /src/forgejo /usr/local/bin/forgejo
COPY --from=frontend /src/public/ /usr/local/share/forgejo/public/
COPY templates/ /usr/local/share/forgejo/templates/

EXPOSE 3000
ENTRYPOINT ["forgejo"]
CMD ["web"]
```

---

## 5. Health Checks

| Service | Endpoint | Method |
|---------|----------|--------|
| gateway | `http://localhost:3000/api/v1/version` | GET (Forgejo built-in) |
| core | `http://localhost:8000/health` | GET (returns `{"status":"ok"}`) |
| db | `pg_isready -U forgejo` | CLI |

---

## 6. Development Workflow

### Local development (without Docker)

```bash
# Terminal 1: PostgreSQL (via Docker)
docker compose up db

# Terminal 2: datahub-core
cd ../datahub
uvicorn dit.server.app:app --reload --port 8000

# Terminal 3: Forgejo gateway
cd ../datahub-gateway
make watch  # Forgejo's built-in dev mode with hot reload
```

### Full stack (Docker)

```bash
docker compose up --build
# Gateway: http://localhost:3000
# Core API: http://localhost:8000 (internal, for debugging)
```

---

## 7. CI Pipeline (GitHub Actions)

### Gateway CI (`.github/workflows/gateway.yml`)

```yaml
name: Gateway CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version: "1.23"
      - run: go test ./modules/datahub/... ./routers/api/v1/repo/...
  
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: golangci/golangci-lint-action@v4

  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker compose build gateway
```

---

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Database | Single PostgreSQL instance, two databases | Simpler ops; Forgejo and datahub-core schemas don't overlap |
| Init strategy | SQL script in docker-entrypoint-initdb.d | Standard PostgreSQL pattern, runs once on first start |
| Service communication | Docker network, no external ports for core | Core is internal-only; gateway proxies all requests |
| Dev workflow | Components run independently | Faster iteration than full Docker rebuild |
