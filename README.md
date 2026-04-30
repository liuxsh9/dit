# Dit

Git-like version control for LLM SFT training data.

Dit tracks JSONL training datasets at the row level — each JSON object gets its own content-addressable hash, enabling precise diffs, deduplication, blame, and three-way merges on individual training examples rather than opaque file-level changes.

## Why Dit?

Training data for LLMs is typically stored as large JSONL files. Standard git handles these poorly:

- A single changed row shows as a full-line diff with no semantic context
- Merging concurrent edits to the same file almost always conflicts
- There's no way to ask "who added this training example?" or "are there duplicates?"
- File-level tracking can't detect when the same question gets a refreshed answer

Dit solves these by decomposing JSONL files into row-level objects with content-addressable storage, then layering git-like semantics (commits, branches, merges, remotes) on top.

## Features

- **Row-level versioning** — each JSON object is individually hashed (SHA-256 over RFC 8785 canonical JSON)
- **Semantic diffs** — added, removed, and "refreshed" rows (same question, different answer) detected via query fingerprinting
- **Three-way merge** — concurrent edits to the same dataset merge automatically at the row level
- **Deduplication detection** — find exact duplicates and response refreshes across your entire dataset
- **Blame** — trace every row back to the commit that introduced it
- **Validation** — define rules (required fields, forbidden keywords, length limits) in `.ditvalidate.yaml`
- **Sidecar metadata** — per-row char counts, token estimates, field counts, and language detection
- **Search** — full-text search across all rows with field-path filtering
- **Export** — export any commit to JSONL or CSV with optional metadata
- **Sparse clone** — clone only the directory structure, then fetch individual files on demand — essential for large datasets (tens/hundreds of GB)
- **Remote collaboration** — push/pull/clone with a self-hosted Dit server
- **Pull requests** — server-side PRs with inline comments, approvals, and branch protection
- **Zstd compression** — all objects stored compressed for efficient disk usage
- **Garbage collection** — reclaim space from unreachable objects with configurable grace periods

## Quick Start

### Installation

Requires Python >= 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
# Install from GitHub (recommended for CLI users)
uv pip install git+https://github.com/liuxsh9/dit.git

# Or clone and install locally
git clone https://github.com/liuxsh9/dit.git
cd dit
uv pip install .

# With server dependencies (for self-hosting)
uv pip install ".[server]"
```

### Development Setup

```bash
git clone https://github.com/liuxsh9/dit.git
cd dit
uv sync --group dev --extra server
uv run dit --help
```

### Basic Workflow

```bash
# Initialize a repository
dit init

# Create some training data
cat > train.jsonl << 'EOF'
{"messages":[{"role":"user","content":"What is an LRU cache?"},{"role":"assistant","content":"An LRU cache evicts the least recently used item when full..."}]}
{"messages":[{"role":"user","content":"Explain quicksort"},{"role":"assistant","content":"Quicksort is a divide-and-conquer algorithm..."}]}
EOF

# Stage and commit
dit add train.jsonl
dit commit -m "initial training data"

# Check status and history
dit status
dit log --oneline
```

### Branching and Merging

```bash
# Create a feature branch
dit branch improve-answers
dit checkout improve-answers

# Edit data, then commit
dit add train.jsonl
dit commit -m "improve quicksort explanation"

# Merge back (three-way merge at row level)
dit checkout main
dit merge improve-answers
```

### Data Quality

```bash
# Compute metadata (char counts, token estimates, language)
dit meta compute
dit meta show train.jsonl
dit stats

# Detect duplicates
dit dedup

# Validate against rules
cat > .ditvalidate.yaml << 'EOF'
rules:
  - required_fields: ["messages"]
  - min_row_chars: 50
  - forbidden_keywords: ["TODO", "FIXME"]
EOF
dit validate

# Search for specific content
dit search "quicksort" --field messages.content

# Blame: who added each row
dit blame train.jsonl
```

### Remote Collaboration

```bash
# Add a remote (use the gateway URL, not dit-core directly)
dit remote add origin http://your-server:3000/<owner>/<repo-name>.dit

# Tokens are created by the server admin via the gateway web UI
# or the bootstrap API. Ask your admin for a token.
dit auth set-token <your-token> --remote origin

# Push and pull
dit push
dit pull

# Clone on another machine (full clone)
dit clone http://your-server:3000/<owner>/<repo-name>.dit --token <token>

# Sparse clone (recommended for large datasets)
dit clone --sparse http://your-server:3000/<owner>/<repo-name>.dit --token <token>

# Then fetch only the files you need
dit sparse-checkout add train/sft.jsonl
dit sparse-checkout add eval/

# List all files with fetch status
dit sparse-checkout list

# Remove a file from working copy (keeps it in history)
dit sparse-checkout remove train/sft.jsonl

# Convert back to full clone when needed
dit sparse-checkout disable
```

### Export

```bash
# Export to JSONL (default)
dit export --ref main --output ./export/

# Export to CSV with metadata
dit export --ref main --format csv --include-meta --output ./export/
```

### Maintenance

```bash
# Verify object store integrity
dit fsck

# Garbage collection (remove unreachable objects older than 24h)
dit gc --grace 24

# Dry run first
dit gc --dry-run
```

## Architecture

### Object Model

Dit uses a content-addressable object store inspired by git, with types specialized for training data:

```
Commit ──→ Tree ──→ Manifest ──→ Row (JSON object)
              │         │
              │         └──→ Sidecar (per-row metadata)
              │
              └──→ Tree (subdirectory)
              └──→ Blob (non-JSONL file)
```

| Object | Description |
|--------|-------------|
| **Row** | A single JSON object, canonicalized via RFC 8785 before hashing |
| **Manifest** | Ordered list of `(row_hash, query_fingerprint)` — represents one JSONL file |
| **Tree** | Directory listing of manifests, blobs, and sub-trees |
| **Commit** | Snapshot pointer with parent(s), author, message, timestamp |
| **Sidecar** | Per-row metadata: char count, token estimate, field count, language |
| **Blob** | Non-JSONL files stored with length-prefixed envelope |

All objects are zstd-compressed and stored at `.dit/objects/<type>/<xx>/<yy>/<hash>` (sharded by first 4 hex chars). Writes are atomic via tmp + rename.

### Query Fingerprinting

Each manifest entry includes a `query_fingerprint` — the SHA-256 of concatenated user-role message contents. This enables detecting "response refreshes" (same question, new answer) as distinct from "added" or "removed" rows during diffs and merges.

## Server

Dit includes a self-hosted collaboration server with a REST API, role-based access control, pull requests, and webhooks.

### Deployment

Dit is designed to run alongside [datahub-gateway](https://github.com/liuxsh9/datahub-gateway) (a Forgejo-based web UI). The recommended production stack uses Docker Compose:

```bash
# Clone and configure
git clone https://github.com/liuxsh9/datahub-gateway.git
cd datahub-gateway
cp .env.example .env
# Edit .env: set SERVICE_TOKEN, POSTGRES_PASSWORD, DIT_DB_PASSWORD
docker compose up -d
```

This starts 3 services: gateway (web UI on port 3000), dit-core, and PostgreSQL. For HTTPS with automatic TLS certificates, add `DOMAIN=your.domain.com` to `.env` and use `docker compose --profile tls up -d`.

For standalone dit-core deployment:

```bash
docker build -t dit-core .
docker run -p 8000:8000 \
  -e DIT_SERVER_DATABASE_URL=postgresql+asyncpg://user:pass@db:5432/dit \
  -e DIT_SERVER_DATA_DIR=/data/dit \
  -e DIT_SERVER_SERVICE_TOKEN=your-secret \
  -v dit-data:/data/dit \
  dit-core
```

The container runs as non-root user `dit`, uses gunicorn with uvicorn workers, and auto-runs database migrations on startup.

See [docs/deployment.md](docs/deployment.md) for the full deployment checklist.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DIT_SERVER_DATABASE_URL` | `postgresql+asyncpg://localhost/dit` | PostgreSQL connection string |
| `DIT_SERVER_DATA_DIR` | `/data/dit` | Object storage directory |
| `DIT_SERVER_HOST` | `0.0.0.0` | Bind host |
| `DIT_SERVER_PORT` | `8000` | Bind port |
| `DIT_SERVER_SERVICE_TOKEN` | — | Shared secret for service-to-service auth |
| `DIT_SERVER_AUTO_MIGRATE` | `1` | Run Alembic migrations on startup |
| `DIT_SERVER_WORKERS` | `2` | Gunicorn worker count |
| `DIT_SERVER_RATE_LIMIT` | (disabled) | Rate limit, e.g. `100/minute` |

### API Highlights

- **Repositories** — CRUD, multi-repo support
- **Objects** — batch upload/download with CAS (compare-and-swap) ref updates
- **Pull Requests** — create, review, approve, merge with branch protection rules
- **Inline Comments** — file-level, row-level, and field-level review comments
- **Webhooks** — HMAC-SHA256 signed payloads for `ref_update`, `branch_create`, `branch_delete`
- **Auth** — Bearer tokens with role hierarchy: reader < reviewer < committer < maintainer < admin < owner

### Monitoring

Prometheus metrics at `GET /metrics`:

- `dit_http_requests_total` (counter, by method/path/status)
- `dit_http_request_duration_seconds` (histogram)
- `dit_http_requests_in_progress` (gauge)

Health check at `GET /health`.

### Backup and Restore

```bash
# Backup (objects + database)
./scripts/backup.sh

# Restore
./scripts/restore.sh <backup-file>
```

## CLI Reference

| Command | Description |
|---------|-------------|
| `dit init` | Initialize a new repository |
| `dit add <paths>` | Stage files for commit |
| `dit commit -m <msg>` | Create a commit |
| `dit status` | Show working directory status |
| `dit diff` | Show changes vs HEAD |
| `dit log` | Show commit history |
| `dit branch [name]` | List or create branches |
| `dit checkout <branch>` | Switch branches |
| `dit merge <branch>` | Three-way merge |
| `dit cherry-pick <hash>` | Apply a single commit |
| `dit push` | Push to remote |
| `dit pull` | Pull from remote |
| `dit clone <url>` | Clone a remote repository |
| `dit clone --sparse <url>` | Sparse clone (directory structure only) |
| `dit sparse-checkout add/remove/list/disable` | Manage sparse working copy |
| `dit remote add/remove/list` | Manage remotes |
| `dit meta compute/show/diff` | Sidecar metadata |
| `dit stats [path]` | Repository statistics |
| `dit search <query>` | Search rows |
| `dit blame <file>` | Row-level blame |
| `dit dedup` | Detect duplicates |
| `dit validate` | Validate against rules |
| `dit export` | Export to JSONL/CSV |
| `dit gc` | Garbage collection |
| `dit fsck` | Integrity check |
| `dit serve` | Start the API server |

## Running Tests

```bash
uv sync --group dev --extra server
uv run pytest
```

1108 tests covering CLI, core logic, and server routes.

## Project Structure

```
src/dit/
  cli/main.py          # CLI commands (Typer)
  core/                # Core logic (objects, store, merge, diff, ...)
  server/              # FastAPI server (routes, auth, models, migrations)
  utils/jsonl.py       # JSONL read/write helpers
tests/                 # 90+ test files
scripts/               # Deployment, backup, restore
Dockerfile             # Production container image
```

## Tech Stack

- **CLI**: Typer
- **Hashing**: SHA-256 + RFC 8785 (JCS) canonical JSON
- **Compression**: Zstandard (pyzstd)
- **HTTP Client**: httpx
- **Server**: FastAPI + Gunicorn/Uvicorn
- **Database**: PostgreSQL (asyncpg) + SQLAlchemy + Alembic
- **Auth**: Bearer tokens, role-based, HMAC-SHA256 webhooks
- **Metrics**: Prometheus
- **Build**: Hatchling
- **Package Manager**: uv
