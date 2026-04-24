# Phase 3D: Go Backend — Data Model, Config, Proxy Client, API Routes

> **Parent spec:** 2026-04-24-phase3-web-ui-design.md  
> **Date:** 2026-04-24  
> **Base:** Forgejo v15.0 LTS

---

## Overview

This sub-project adds the Go-side plumbing to Forgejo: a new `IsDataRepo` boolean flag, config section, HTTP proxy client, and 13 API routes that forward to datahub-core.

---

## 1. Data Model — `IsDataRepo` Flag

### 1.1 Repository struct (`models/repo/repo.go`)

Add field following the `IsFork`/`IsTemplate` pattern:

```go
IsDataRepo bool `xorm:"INDEX NOT NULL DEFAULT false"`
```

### 1.2 CreateRepoOptions (`services/repository/create.go`)

Add to the options struct (line ~38):

```go
IsDataRepo bool
```

### 1.3 CreateRepositoryDirectly changes

In the repo construction (line ~222):

```go
IsDataRepo:    opts.IsDataRepo,
IsEmpty:       !opts.AutoInit || opts.IsDataRepo,
IsFsckEnabled: !opts.IsMirror && !opts.IsDataRepo,
```

Inside the DB transaction, after the mirror early-return (line ~253):

```go
if opts.IsDataRepo {
    if err := datahub.DefaultClient().CreateRepo(ctx, repo.Name); err != nil {
        return fmt.Errorf("datahub create repo: %w", err)
    }
    return nil
}
```

This skips: filesystem check, `initRepository`, `CheckDaemonExportOK`, `git update-server-info`. On failure, the transaction rolls back and existing rollback logic calls `DeleteRepositoryDirectly`.

### 1.4 DeleteRepositoryDirectly (`services/repository/delete.go`)

Add before filesystem removal:

```go
if repo.IsDataRepo {
    if err := datahub.DefaultClient().DeleteRepo(ctx, repo.Name); err != nil {
        log.Error("Failed to delete datahub-core repo %s: %v", repo.Name, err)
    }
}
```

Deletion semantics: **fail-open** (log error, continue Forgejo cleanup).

### 1.5 Code indexer filter (`modules/indexer/code/indexer.go`)

Add `IsDataRepo` to the repo eligibility filter alongside `IsMirror`/`IsFork`/`IsTemplate`. Data repos have no git objects to index.

### 1.6 Migration

```sql
ALTER TABLE repository ADD COLUMN is_data_repo BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX idx_repository_is_data_repo ON repository(is_data_repo);
```

---

## 2. Config Module — `[datahub]` Section

### 2.1 `modules/setting/datahub.go`

```go
package setting

var DataHub = struct {
    Enabled      bool   `ini:"ENABLED"`
    CoreURL      string `ini:"CORE_URL"`
    ServiceToken string `ini:"SERVICE_TOKEN"`
}{
    Enabled:      false,
    CoreURL:      "http://localhost:8000",
    ServiceToken: "",
}

func loadDatahubFrom(rootCfg ConfigProvider) {
    mustMapSetting(rootCfg, "datahub", &DataHub)
}
```

### 2.2 Registration

Add `loadDatahubFrom(rootCfg)` in `modules/setting/setting.go` `LoadCommonSettings()` (lines ~127-158). Without this, all config values silently stay zero — `Enabled` is false, `CoreURL` is empty — no startup error.

### 2.3 `app.ini` example

```ini
[datahub]
ENABLED = true
CORE_URL = http://datahub-core:8000
SERVICE_TOKEN = your-secret-token
```

---

## 3. Proxy Client — `modules/datahub/client.go`

### 3.1 Client struct

```go
type Client struct {
    baseURL      string
    serviceToken string
    httpClient   *http.Client
}

func DefaultClient() *Client
```

Singleton initialized from `setting.DataHub` values. Auth: `Authorization: Bearer {serviceToken}` header.

### 3.2 Methods

All accept `repoName string` as first arg. Most return `([]byte, int, error)` for transparent proxying.

| Method | HTTP | Core Path |
|--------|------|-----------|
| `CreateRepo` | POST | `/api/v1/repos` |
| `DeleteRepo` | DELETE | `/api/v1/repos/{repo}` |
| `ListRefs` | GET | `/api/v1/repos/{repo}/refs` |
| `GetRef` | GET | `/api/v1/repos/{repo}/refs/{type}/{name}` |
| `UpdateRef` | POST | `/api/v1/repos/{repo}/refs/{type}/{name}` |
| `GetObject` | GET | `/api/v1/repos/{repo}/objects/{hash}` |
| `PushObjects` | POST | `/api/v1/repos/{repo}/objects/batch` |
| `GetTree` | GET | `/api/v1/repos/{repo}/tree/{hash}` |
| `GetDiff` | GET | `/api/v1/repos/{repo}/diff/{old}/{new}` |
| `GetLog` | GET | `/api/v1/repos/{repo}/log/{ref}` |
| `ListPulls` | GET | `/api/v1/repos/{repo}/pulls` |
| `CreatePull` | POST | `/api/v1/repos/{repo}/pulls` |
| `GetPull` | GET | `/api/v1/repos/{repo}/pulls/{id}` |
| `MergePull` | POST | `/api/v1/repos/{repo}/pulls/{id}/merge` |
| `GetManifest` | GET | `/api/v1/repos/{repo}/manifest/{hash}` |

---

## 4. API Routes — `routers/api/v1/repo/datahub.go`

### 4.1 Route registration (`routers/api/v1/api.go`)

Inside the existing `/{username}/{reponame}` group:

```go
m.Group("/datahub", func() {
    m.Get("/refs", repo.DatahubListRefs)
    m.Get("/refs/{ref_type}/{name}", repo.DatahubGetRef)
    m.Post("/refs/{ref_type}/{name}", repo.DatahubUpdateRef)
    m.Get("/objects/{hash}", repo.DatahubGetObject)
    m.Post("/objects/batch", repo.DatahubPushObjects)
    m.Get("/tree/{hash}", repo.DatahubGetTree)
    m.Get("/diff/{old}/{new}", repo.DatahubGetDiff)
    m.Get("/log/{ref}", repo.DatahubGetLog)
    m.Get("/pulls", repo.DatahubListPulls)
    m.Post("/pulls", repo.DatahubCreatePull)
    m.Get("/pulls/{id}", repo.DatahubGetPull)
    m.Post("/pulls/{id}/merge", repo.DatahubMergePull)
    m.Get("/manifest/{hash}", repo.DatahubGetManifest)
}, repoAssignment())
```

### 4.2 Authorization strategy

- **Skip `reqRepoReader`** — no existing unit type fits data repos; adding `unit.TypeDataRepo` requires DB migration + model changes
- `repoAssignment()` resolves the repo and checks basic access
- Each handler checks `ctx.Repo.Repository.IsDataRepo` — returns 404 if false
- **Never use `context.ReferencesGitRepo()`** — opens git dir, crashes for data repos
- Forgejo repo-level permissions (read/write/admin) mapped to datahub-core roles in proxy layer

### 4.3 Handler pattern

```go
func DatahubListRefs(ctx *context.APIContext) {
    if !ctx.Repo.Repository.IsDataRepo {
        ctx.NotFound()
        return
    }
    data, status, err := datahub.DefaultClient().ListRefs(ctx, ctx.Repo.Repository.Name)
    if err != nil {
        ctx.Error(http.StatusBadGateway, "datahub proxy", err)
        return
    }
    ctx.Resp.Header().Set("Content-Type", "application/json")
    ctx.Resp.WriteHeader(status)
    ctx.Resp.Write(data)
}
```

---

## 5. Additional Forgejo Patches

### 5.1 Repo settings page (`routers/web/repo/repo.go`)

Guard git-specific operations (git config reads, update-server-info) when `IsDataRepo`. Settings page still renders — data repos need settings for webhooks, permissions, etc.

### 5.2 Repo API PATCH (`routers/api/v1/repo/repo.go`)

Data repos have `IsEmpty = true`. The `Edit` handler (line ~806) opens git when `!repo.IsEmpty`, so this is safe. Add guard to prevent setting `IsEmpty = false` on a data repo via API.

### 5.3 Repository list views

Show a "dataset" icon/badge next to data repos in list views (org, user, explore), similar to mirror icon.

---

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Data repo flag | `IsDataRepo bool` not type enum | Follows Forgejo's boolean flag pattern |
| Unit type | Skip `reqRepoReader` | Avoids new unit type migration |
| Deletion | Fail-open | Don't block Forgejo cleanup on external service |
| Creation rollback | Inside DB transaction | Consistent with mirror pattern |
| `IsEmpty` | Always `true` for data repos | Prevents git-opening middleware crash |
| `ReferencesGitRepo` | Never on datahub routes | No git directory exists |
