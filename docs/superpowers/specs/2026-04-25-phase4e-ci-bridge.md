# Phase 4E: CI Bridge Skeleton

> **Parent:** Phase 4 (Metadata & Advanced Features)
> **Date:** 2026-04-25
> **Depends on:** Phase 4A–4D
> **Blocks:** Phase 5 (CI result → PR block logic, pre-commit hook, full Checks tab)

---

## Overview

Phase 4E adds the foundation for a CI/validation system in three parts:

1. **`dit validate` CLI command** — reads `.ditvalidate.yaml` from the repo root and checks all committed JSONL rows against user-defined rules. This is an explicit, on-demand command in Phase 4E; automatic pre-commit hook integration is Phase 5.
2. **Core validate module** (`core/validate.py`) — the library that loads rules and runs checks, shared by the CLI and the server endpoint.
3. **Server API** — a `POST /validate` endpoint that runs the same checks server-side, plus a simple `ci_checks` table and two endpoints for external CI systems to report and query check results.

Phase 4E is a **skeleton**: it defines the data model, the local validation logic, and the external CI interface. CI job execution, Forgejo webhook triggers, S3 packaging, PR-blocking based on check status, and the full Checks tab UI are all Phase 5.

---

## 1. Validation Rules (`.ditvalidate.yaml`)

The file lives at the root of every data repository (alongside the `.dit/` directory). It is committed alongside training data and versioned like any other file.

### 1.1 Schema

```yaml
# .ditvalidate.yaml
required_fields:
  - instruction
  - response

forbidden_keywords:
  - "OpenAI"
  - "GPT-4"
  - "Anthropic"

max_row_chars: 8192
min_row_chars: 10
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `required_fields` | list of strings | `[]` | Every row in every JSONL file must contain all of these top-level keys. |
| `forbidden_keywords` | list of strings | `[]` | No row may contain any of these substrings (case-insensitive match against the raw JSON string of the row). |
| `max_row_chars` | integer \| null | `null` | Maximum character count of the raw JSON string of a row. `null` means no limit. |
| `min_row_chars` | integer \| null | `null` | Minimum character count of the raw JSON string of a row. `null` means no limit. |

All fields are optional. An empty `.ditvalidate.yaml` (or one with only some fields set) is valid. If the file does not exist at the repo root, `load_rules` returns a default `ValidationRules` with all rules empty/null — validation passes trivially.

### 1.2 Rule evaluation order

Rules are checked in this order per row:

1. `required_fields`
2. `forbidden_keywords`
3. `max_row_chars`
4. `min_row_chars`

All violations are collected (not short-circuited per row), so a single bad row may produce multiple violations.

---

## 2. CLI Command

### 2.1 `dit validate`

```
dit validate [--ref REF] [--format table|json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--ref` | current HEAD | Branch name or full commit hex to validate |
| `--format` | `table` | Output format: `table` (human-readable) or `json` (machine-readable) |

The command:
1. Reads `.ditvalidate.yaml` from the repo root (on-disk if `--ref` is HEAD; from the commit tree otherwise).
2. Runs `validate_commit` against every committed JSONL file in the ref.
3. Prints a summary and exits with code `0` (pass) or `1` (fail).

### 2.2 Pass output

```
$ dit validate
Validating heads/main (commit abc12345)
Rules: required_fields=[instruction, response]  forbidden_keywords=2  max_row_chars=8192

Checked 1700 rows across 2 files.
PASS
```

### 2.3 Fail output

```
$ dit validate
Validating heads/main (commit abc12345)
Rules: required_fields=[instruction, response]  forbidden_keywords=2  max_row_chars=8192

FAIL — 3 violation(s)

File          Row   Rule               Detail
──────────────────────────────────────────────────────────────────────────────
train.jsonl   42    required_fields    missing field: response
train.jsonl   187   forbidden_keywords keyword "OpenAI" found
eval.jsonl    5     max_row_chars      row has 9100 chars (limit 8192)
──────────────────────────────────────────────────────────────────────────────
Checked 1700 rows across 2 files.
```

Exit code is `1` when any violations are found.

### 2.4 JSON output (`--format json`)

The shape mirrors the server API response (Section 4.2):

```json
{
  "status": "fail",
  "violations": [
    {
      "file": "train.jsonl",
      "row_index": 42,
      "row_hash": "3a9f...",
      "rule": "required_fields",
      "detail": "missing field: response"
    },
    {
      "file": "train.jsonl",
      "row_index": 187,
      "row_hash": "b2c1...",
      "rule": "forbidden_keywords",
      "detail": "keyword \"OpenAI\" found"
    }
  ],
  "checked_rows": 1700
}
```

Exit code follows the same `0`/`1` convention even in JSON mode.

---

## 3. Core Validate Module

### 3.1 New file: `src/dit/core/validate.py`

```python
from pathlib import Path

def load_rules(repo_root: Path) -> dict:
    """Read .ditvalidate.yaml from repo_root. Returns a ValidationRules dict.

    If the file does not exist, returns default rules (all empty/null).
    Raises ValueError if the YAML is structurally invalid.

    Returns:
    {
      "required_fields": ["instruction", "response"],
      "forbidden_keywords": ["OpenAI", "GPT-4"],
      "max_row_chars": 8192,
      "min_row_chars": 10,
    }
    """
```

```python
def validate_commit(
    store: "ObjectStore",
    commit_hash: str,
    rules: dict,
) -> dict:
    """Validate all JSONL rows in a commit against the given rules.

    Returns:
    {
      "status": "pass" | "fail",
      "violations": [
        {
          "file": "train.jsonl",
          "row_index": 0,
          "row_hash": "abc...",
          "rule": "required_fields",
          "detail": "missing field: response"
        },
        ...
      ],
      "checked_rows": 1700,
    }

    Raises FileNotFoundError if commit_hash is not found in store.
    All violations are collected; the function never short-circuits early.
    """
```

### 3.2 Implementation details

**`load_rules`:**

1. Construct `config_path = repo_root / ".ditvalidate.yaml"`.
2. If `config_path` does not exist, return the default rules dict with `required_fields=[]`, `forbidden_keywords=[]`, `max_row_chars=None`, `min_row_chars=None`.
3. Load YAML using `yaml.safe_load`. If the result is not a `dict`, raise `ValueError("invalid .ditvalidate.yaml: expected mapping at top level")`.
4. Extract each key with a safe default if missing. Validate types: `required_fields` and `forbidden_keywords` must be lists of strings; `max_row_chars` and `min_row_chars` must be positive integers or `None`.

**`validate_commit`:**

1. Read and deserialize the commit from `store.read("commits", commit_hash)`.
2. Call `flatten_tree(store, commit.tree_hash)` to get the flat path map.
3. Filter entries to `obj_type == "manifest"` (same pattern as `stats.py` and `search.py`).
4. For each manifest entry (sorted by path), read the manifest object and iterate its entries.
5. For each `ManifestEntry`, read row bytes from `store.read("rows", entry.row_hash)`.
6. Parse the row bytes as JSON.
7. Serialize the row back to a compact JSON string for char-count and keyword checks.
8. Run rules in order (see Section 1.2), appending to the violations list on each failure.
9. Increment `checked_rows` for every row examined.
10. Return the result dict after processing all manifests.

**Rule checks (per row):**

- **`required_fields`**: for each field name in `rules["required_fields"]`, check `field not in row_dict`. Violation per missing field: `{"rule": "required_fields", "detail": "missing field: <name>"}`.
- **`forbidden_keywords`**: convert the compact JSON string to lowercase once. For each keyword (case-insensitive), check `keyword.lower() in row_json_lower`. Violation per matched keyword: `{"rule": "forbidden_keywords", "detail": "keyword \"<kw>\" found"}`.
- **`max_row_chars`**: if `rules["max_row_chars"]` is set and `len(row_json) > max_row_chars`. Violation: `{"rule": "max_row_chars", "detail": "row has <N> chars (limit <max>)"}`.
- **`min_row_chars`**: if `rules["min_row_chars"]` is set and `len(row_json) < min_row_chars`. Violation: `{"rule": "min_row_chars", "detail": "row has <N> chars (minimum <min>)"}`.

All violation dicts are completed with `"file"`, `"row_index"`, and `"row_hash"` fields before appending.

### 3.3 Return type

Both functions return plain `dict` — consistent with `stats.py`, `search.py`, and `sidecar.py` conventions. All values are JSON-serializable without extra conversion.

---

## 4. Server API

### 4.1 Validate endpoint

```
POST /api/v1/repos/{repo}/validate
```

**Request body (JSON):**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ref` | string | `"heads/main"` | Branch name or commit hash to validate |

```json
{
  "ref": "heads/main"
}
```

**Authentication:** `require_permission("read")`.

**Response (200):**

```json
{
  "status": "pass",
  "violations": [],
  "checked_rows": 1700
}
```

Or on failure:

```json
{
  "status": "fail",
  "violations": [
    {
      "file": "train.jsonl",
      "row_index": 42,
      "row_hash": "3a9f...",
      "rule": "required_fields",
      "detail": "missing field: response"
    }
  ],
  "checked_rows": 1700
}
```

**Error cases:**

- `404` if the ref cannot be resolved.
- `422` if the request body is malformed.
- `200` with `status: "pass"` and empty `violations` if no rules file exists (not an error).

**Note:** The HTTP status code is always `200` regardless of validation pass/fail — `status` in the body conveys the validation result. This keeps the endpoint consistent with other read endpoints and avoids callers having to special-case `4xx` for legitimate failures.

**Note on `.ditvalidate.yaml` resolution:** The server reads the rules file from the committed tree at the resolved commit. It calls `load_rules` with a synthetic repo root backed by the object store rather than the local filesystem (see implementation note in Section 4.3).

### 4.2 CI check status endpoints

```
POST /api/v1/repos/{repo}/checks
GET  /api/v1/repos/{repo}/checks/{commit}
```

These endpoints form the external CI interface. External CI systems call `POST` to report results and `GET` to query them. The dit PR review UI (Phase 5) will call `GET` to show check badges.

**POST body:**

```json
{
  "commit_hash": "abc1234567890abcd...",
  "check_name": "data-quality-ci",
  "status": "pass",
  "details": {
    "passed": 1700,
    "failed": 0,
    "url": "https://ci.example.com/runs/42"
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `commit_hash` | string | yes | Full commit hex |
| `check_name` | string | yes | Identifier for the CI system or job (max 128 chars) |
| `status` | string | yes | One of: `pending`, `pass`, `fail` |
| `details` | object \| null | no | Arbitrary JSON payload from the CI system |

**POST response (201):**

```json
{
  "id": 7,
  "repo_id": 3,
  "commit_hash": "abc1234567890abcd...",
  "check_name": "data-quality-ci",
  "status": "pass",
  "details": { "passed": 1700, "failed": 0, "url": "https://ci.example.com/runs/42" },
  "created_at": "2026-04-25T10:00:00Z",
  "updated_at": "2026-04-25T10:00:00Z"
}
```

If a row already exists for `(repo_id, commit_hash, check_name)`, it is updated in place (upsert) and the response reflects the updated record.

**GET response (200):**

```json
{
  "commit_hash": "abc1234567890abcd...",
  "checks": [
    {
      "id": 7,
      "check_name": "data-quality-ci",
      "status": "pass",
      "details": { "passed": 1700, "failed": 0, "url": "https://ci.example.com/runs/42" },
      "created_at": "2026-04-25T10:00:00Z",
      "updated_at": "2026-04-25T10:00:00Z"
    }
  ]
}
```

Returns an empty `checks` list (not `404`) if no checks exist for the commit.

**Authentication:**

- `POST /checks` requires `require_permission("write")` — only repo members or CI tokens with write access may report results.
- `GET /checks/{commit}` requires `require_permission("read")`.

### 4.3 New file: `src/dit/server/routes/validate_api.py`

```python
from pydantic import BaseModel
from typing import Any

class ValidateRequest(BaseModel):
    ref: str = "heads/main"

class CheckReportRequest(BaseModel):
    commit_hash: str
    check_name: str
    status: str          # "pending" | "pass" | "fail"
    details: dict[str, Any] | None = None

router = APIRouter(prefix="/api/v1/repos", tags=["validate"])

@router.post("/{repo}/validate")
async def repo_validate_endpoint(
    repo: str,
    body: ValidateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    from dit.core.validate import load_rules, validate_commit
    ...

@router.post("/{repo}/checks", status_code=201)
async def report_check_endpoint(
    repo: str,
    body: CheckReportRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("write")),
):
    ...

@router.get("/{repo}/checks/{commit}")
async def get_checks_endpoint(
    repo: str,
    commit: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    ...
```

**Validate handler implementation notes:**

1. Call `_get_repo(repo, session)` — raises `404` if not found.
2. Resolve `body.ref` to a commit hash via `RefStore`.
3. Build `store = _store_for_repo(request, repo)`.
4. Read the `.ditvalidate.yaml` blob from the commit tree via the store (not from disk). The `load_rules` function accepts an optional `store` + `commit_hash` pair as an alternative to `repo_root: Path`; or a thin adapter reads the blob and writes it to a `tempfile` for `load_rules` to consume. (Implementation may choose either approach — the spec does not mandate.)
5. Call `validate_commit(store, commit_hash, rules)`.
6. Return the result dict.

Register `validate_router` in `src/dit/server/app.py` alongside other routers.

---

## 5. CI Check Model

### 5.1 Database table: `ci_checks`

New SQLAlchemy model alongside existing models (e.g., `Repo`, `PullRequest`):

```python
class CICheck(Base):
    __tablename__ = "ci_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id"), nullable=False, index=True)
    commit_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    check_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # pending|pass|fail
    details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("repo_id", "commit_hash", "check_name", name="uq_ci_check"),
    )
```

The unique constraint on `(repo_id, commit_hash, check_name)` enforces the upsert semantics: a CI system can call `POST /checks` multiple times (e.g., to update a `pending` → `pass`) and the record is updated, not duplicated.

### 5.2 Migration

Add an Alembic migration (new file in `migrations/versions/`) that creates the `ci_checks` table. No data migration needed — table is new.

---

## 6. Gateway + Web UI

### 6.1 Gateway routes

Add three routes to the `dit` group in `routers/api/v1/api.go`:

```go
m.Post("/validate",           repo.DatahubValidate)
m.Post("/checks",             repo.DatahubReportCheck)
m.Get("/checks/{commit}",     repo.DatahubGetChecks)
```

### 6.2 Handlers in `routers/api/v1/repo/dit.go`

```go
func DatahubValidate(ctx *context.APIContext) {
    body, err := io.ReadAll(ctx.Req.Body)
    if err != nil {
        ctx.Error(http.StatusBadRequest, "read body", err)
        return
    }
    data, status, err := dit.DefaultClient().Validate(ctx, ctx.Repo.Repository.Name, body)
    proxyResponse(ctx, data, status, err)
}

func DatahubReportCheck(ctx *context.APIContext) {
    body, err := io.ReadAll(ctx.Req.Body)
    if err != nil {
        ctx.Error(http.StatusBadRequest, "read body", err)
        return
    }
    data, status, err := dit.DefaultClient().ReportCheck(ctx, ctx.Repo.Repository.Name, body)
    proxyResponse(ctx, data, status, err)
}

func DatahubGetChecks(ctx *context.APIContext) {
    data, status, err := dit.DefaultClient().GetChecks(ctx, ctx.Repo.Repository.Name, ctx.Params(":commit"))
    proxyResponse(ctx, data, status, err)
}
```

### 6.3 Client methods in `modules/dit/client.go`

```go
func (c *Client) Validate(ctx context.Context, repoName string, body []byte) ([]byte, int, error) {
    return c.do(ctx, http.MethodPost, "/api/v1/repos/"+repoName+"/validate", body)
}

func (c *Client) ReportCheck(ctx context.Context, repoName string, body []byte) ([]byte, int, error) {
    return c.do(ctx, http.MethodPost, "/api/v1/repos/"+repoName+"/checks", body)
}

func (c *Client) GetChecks(ctx context.Context, repoName, commitHash string) ([]byte, int, error) {
    return c.do(ctx, http.MethodGet, "/api/v1/repos/"+repoName+"/checks/"+commitHash, nil)
}
```

### 6.4 Web UI: Checks badge in DataRepoHome

Add a small inline badge next to the commit hash in `DataRepoHome.vue`. The badge is loaded automatically when `commitHash` is available (unlike Stats, which is lazy).

**Template addition (in the commit info line, alongside branch selector and commit hash):**

```html
<span v-if="checksStatus" class="ui tiny label" :class="checksStatusClass" style="margin-left: 6px;">
  <i :class="checksStatusIcon"></i> {{ checksStatusText }}
</span>
<span v-else-if="checksLoading" class="ui tiny label" style="margin-left: 6px;">
  <i class="spinner loading icon"></i>
</span>
```

**Script additions (`data()`):**

```js
checksLoading: false,
checksData: null,
```

**Script additions (`computed`):**

```js
checksStatus() {
  if (!this.checksData || this.checksData.checks.length === 0) return null;
  const statuses = this.checksData.checks.map(c => c.status);
  if (statuses.includes('fail')) return 'fail';
  if (statuses.includes('pending')) return 'pending';
  return 'pass';
},
checksStatusClass() {
  return {
    'green': this.checksStatus === 'pass',
    'red':   this.checksStatus === 'fail',
    'grey':  this.checksStatus === 'pending',
  };
},
checksStatusIcon() {
  return {
    'pass':    'check icon',
    'fail':    'times icon',
    'pending': 'clock icon',
  }[this.checksStatus] || '';
},
checksStatusText() {
  return { 'pass': 'CI pass', 'fail': 'CI fail', 'pending': 'CI pending' }[this.checksStatus] || '';
},
```

**Script additions (`methods` — called from `loadTree()` after `commitHash` is set):**

```js
async loadChecks() {
  if (!this.commitHash) return;
  this.checksLoading = true;
  try {
    this.checksData = await ditFetch(
      this.owner, this.repo,
      `/checks/${this.commitHash}`,
    );
  } catch {
    this.checksData = null;
  } finally {
    this.checksLoading = false;
  }
},
```

Call `this.loadChecks()` at the end of `loadTree()`, and reset `this.checksData = null` at the start of `loadTree()` (on branch change).

The badge is display-only in Phase 4E. Clicking it has no action. The full Checks tab with per-check detail and deep links to failing rows is Phase 5.

---

## 7. Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| `.ditvalidate.yaml` format | Flat YAML with four top-level keys | Simple to read and write by hand; four rules cover the most common data-quality checks; extensible in Phase 5 |
| Rules file location | Repo root (committed) | Rules travel with the data; a branch can have different rules than main; consistent with `.gitignore` pattern |
| Validation HTTP response | Always 200; `status` in body signals pass/fail | Avoids callers special-casing 4xx for legitimate validation failures; consistent with search and stats endpoints |
| `validate_commit` collects all violations | Collect all, never short-circuit | Users need the full list to fix everything at once; short-circuiting would force repeated runs |
| `forbidden_keywords` matching | Case-insensitive substring on the raw JSON string | Catches variations in casing; operates on the raw string to avoid missing keywords split across fields |
| Char-count rule applies to raw JSON | `len(row_json)` | Simple, deterministic; consistent with what gets stored in the object store |
| `ci_checks` upsert semantics | Unique constraint on `(repo_id, commit_hash, check_name)` | Allows a CI job to update a `pending` → `pass`/`fail` result without duplication; external CI systems often poll and update |
| `details_json` | Arbitrary JSON, opaque to dit-core | External CI systems have varied result formats; dit-core stores and proxies without interpreting |
| `POST /checks` auth | `require_permission("write")` | Only authorized parties should be able to assert check status; read-only tokens cannot tamper with results |
| Web UI checks badge | Inline badge, no tab in Phase 4E | Minimal viable signal (pass/fail/pending) with near-zero effort; full Checks tab deferred to Phase 5 |
| Badge loaded automatically | Yes, not lazy | It is a small GET and conveys critical CI status; unlike Stats it should always be visible when data is present |
| CLI exit code on fail | Exit 1 | Allows `dit validate` to be used in shell scripts and simple CI pipelines without parsing JSON |

---

## 8. Out of Scope

- **Automatic pre-commit hook.** Running `dit validate` automatically on `dit commit` requires hook integration. Deferred to Phase 5.
- **Webhook triggers for CI jobs.** Forgejo's native webhook system handles triggering external CI when a PR is opened or updated. The CI Bridge skeleton provides the interface for CI systems to report back, but does not send outbound webhooks.
- **CI job execution.** External systems run the actual CI jobs. Dit-core only stores and exposes results.
- **S3 packaging for CI.** Packaging incremental data to S3 for external CI consumption is Phase 5.
- **CI result → PR block logic.** Preventing a PR from being merged when CI checks are failing requires Forgejo integration (status check enforcement). Deferred to Phase 5.
- **Full Checks tab UI.** The PR review Checks tab with per-check details, failure counts, and deep links to failing rows in the Files view is Phase 5.
- **`dit validate` against the working directory.** The command always operates on committed data (HEAD or a named ref). Validating uncommitted local changes is out of scope.
- **Validation rule inheritance / override.** Per-branch or per-directory rule overrides are not supported. A single `.ditvalidate.yaml` at the repo root applies to all files in the commit.
- **Custom rule plugins.** The four built-in rule types cover Phase 4E. A plugin mechanism for user-defined rules is deferred.
