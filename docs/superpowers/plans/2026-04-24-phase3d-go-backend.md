# Phase 3D: Go Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Add data repository support to Forgejo — IsDataRepo flag, [dit] config, proxy client, 13 API routes
**Architecture:** Minimal Forgejo fork with boolean flag, thin HTTP proxy, API route group
**Tech Stack:** Go 1.23, Forgejo v15.0, chi router, xorm ORM
**Source:** `/tmp/forgejo-src`
**Target:** `~/code/datahub-gateway`

---

## Task 1: Fork Setup

**Objective:** Clone forgejo-src, verify build, initialize git history.

### Steps

- [ ] Clone source to target location
- [ ] Verify `make build` succeeds
- [ ] Initialize clean git history with initial commit

### Commands

```bash
# Clone the forgejo source
cp -r /tmp/forgejo-src ~/code/datahub-gateway
cd ~/code/datahub-gateway

# Verify build works before any changes
make build

# Initialize git
git init
git add .
git commit -m "chore: initial Forgejo v15.0 fork base"
```

### Verification

```bash
cd ~/code/datahub-gateway
./gitea --version
# Should print: Forgejo version 15.0.x ...
```

---

## Task 2: Config Module

**Objective:** Create `[dit]` config section, register loader in `LoadCommonSettings`.

### Steps

- [ ] Create `modules/setting/dit.go` with `Dit` var struct
- [ ] Add `loadDatahubFrom(rootCfg)` call in `modules/setting/setting.go`
- [ ] Write unit test
- [ ] Verify build

### File: `modules/setting/dit.go` (create new)

```go
// Copyright 2024 The Forgejo Authors. All rights reserved.
// SPDX-License-Identifier: MIT

package setting

// Dit holds configuration for the [dit] section of app.ini.
var Dit = struct {
	Enabled      bool   `ini:"ENABLED"`
	CoreURL      string `ini:"CORE_URL"`
	ServiceToken string `ini:"SERVICE_TOKEN"`
}{
	Enabled:      false,
	CoreURL:      "http://localhost:8000",
	ServiceToken: "",
}

func loadDatahubFrom(rootCfg ConfigProvider) {
	mustMapSetting(rootCfg, "dit", &Dit)
}
```

### Edit: `modules/setting/setting.go`

Find `LoadCommonSettings()` — it calls a series of `loadXxxFrom(rootCfg)` functions. Add the dit loader after the last existing loader (e.g., after `loadProjectFrom` or similar near-end entry):

```go
// Inside LoadCommonSettings(), alongside other loadXxxFrom calls:
loadDatahubFrom(rootCfg)
```

### File: `modules/setting/dit_test.go` (create new)

```go
package setting_test

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"code.gitea.io/gitea/modules/setting"
)

func TestDatahubDefaults(t *testing.T) {
	assert.False(t, setting.Dit.Enabled)
	assert.Equal(t, "http://localhost:8000", setting.Dit.CoreURL)
	assert.Equal(t, "", setting.Dit.ServiceToken)
}
```

### Test command

```bash
cd ~/code/datahub-gateway
go test ./modules/setting/... -run TestDatahub -v
```

---

## Task 3: Data Model

**Objective:** Add `IsDataRepo bool` to Repository struct, CreateRepoOptions, and create XORM migration.

### Steps

- [ ] Add `IsDataRepo` field to `models/repo/repo.go` Repository struct
- [ ] Add `IsDataRepo` field to `services/repository/create.go` CreateRepoOptions struct
- [ ] Set `IsDataRepo`, `IsEmpty`, `IsFsckEnabled` correctly in `CreateRepositoryDirectly`
- [ ] Create XORM migration file
- [ ] Write model test
- [ ] Verify build

### Edit: `models/repo/repo.go`

Locate the Repository struct. Find the block containing `IsFork`, `IsTemplate`, `IsMirror` boolean fields. Add `IsDataRepo` in that group:

```go
// In the Repository struct, after IsMirror or IsTemplate:
IsDataRepo bool `xorm:"INDEX NOT NULL DEFAULT false"`
```

### Edit: `services/repository/create.go`

Locate `CreateRepoOptions` struct. Add field after `IsMirror`:

```go
// In CreateRepoOptions struct:
IsDataRepo bool
```

Then in `CreateRepositoryDirectly`, locate the repo object construction (the `repo := &repo_model.Repository{...}` block) and update the relevant fields:

```go
// In repo construction block:
IsDataRepo:    opts.IsDataRepo,
IsEmpty:       !opts.AutoInit || opts.IsDataRepo,
IsFsckEnabled: !opts.IsMirror && !opts.IsDataRepo,
```

### Migration file: `models/migrations/v1_XX/v1_XX_add_is_data_repo.go` (create new)

Find the latest migration number in `models/migrations/` and use the next number. Example (adjust version numbers to match existing):

```go
// Copyright 2024 The Forgejo Authors. All rights reserved.
// SPDX-License-Identifier: MIT

package v1_XX //nolint

import "xorm.io/xorm"

// AddIsDataRepoToRepository adds is_data_repo column to repository table.
func AddIsDataRepoToRepository(x *xorm.Engine) error {
	type Repository struct {
		ID         int64 `xorm:"pk autoincr"`
		IsDataRepo bool  `xorm:"INDEX NOT NULL DEFAULT false"`
	}
	return x.Sync(new(Repository))
}
```

Register it in `models/migrations/migrations.go` — find the slice of migration entries and append:

```go
{ID: <next-id>, Description: "Add is_data_repo to repository", Migrate: v1_XX.AddIsDataRepoToRepository},
```

### Test command

```bash
cd ~/code/datahub-gateway
go build ./models/repo/...
go build ./services/repository/...
go test ./models/repo/... -v -count=1
```

---

## Task 4: Create/Delete Hooks

**Objective:** Short-circuit `CreateRepositoryDirectly` for data repos (skip git init, call dit client); add fail-open cleanup in `DeleteRepositoryDirectly`.

### Steps

- [ ] Add dit create hook in `CreateRepositoryDirectly` (inside DB transaction, after mirror early return)
- [ ] Add dit delete hook in `DeleteRepositoryDirectly` (before filesystem removal)
- [ ] Write integration-style tests with a mock dit server
- [ ] Verify build

### Edit: `services/repository/create.go`

Add import (will be needed after client module exists):

```go
import (
    // existing imports ...
    "code.gitea.io/gitea/modules/dit"
)
```

Inside `CreateRepositoryDirectly`, after the mirror early-return block, add:

```go
if opts.IsDataRepo {
    if err := dit.DefaultClient().CreateRepo(ctx, repo.Name); err != nil {
        return fmt.Errorf("dit create repo: %w", err)
    }
    // Skip: initRepository, CheckDaemonExportOK, git update-server-info
    return nil
}
```

### Edit: `services/repository/delete.go`

Add import:

```go
import (
    // existing imports ...
    "code.gitea.io/gitea/modules/dit"
)
```

In `DeleteRepositoryDirectly`, before the filesystem removal block, add:

```go
if repo.IsDataRepo {
    if err := dit.DefaultClient().DeleteRepo(ctx, repo.Name); err != nil {
        log.Error("Failed to delete dit-core repo %s: %v", repo.Name, err)
        // fail-open: continue Forgejo cleanup regardless
    }
}
```

### File: `services/repository/dit_test.go` (create new)

```go
package repository_test

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestCreateDataRepoCallsDatuhubClient(t *testing.T) {
	called := false
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost && r.URL.Path == "/api/v1/repos" {
			called = true
			w.WriteHeader(http.StatusCreated)
		}
	}))
	defer srv.Close()

	// Override Dit.CoreURL to srv.URL before calling CreateRepository
	// (exact wiring depends on how DefaultClient() is initialized — see Task 5)

	// Assert
	assert.True(t, called, "dit-core CreateRepo should have been called")
}

func TestDeleteDataRepoFailOpen(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Simulate dit-core being down
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()

	// DeleteRepositoryDirectly should succeed (fail-open) even when dit returns 500
	// assert no error returned from delete
	require.NoError(t, nil) // replace nil with actual call once wired
}
```

### Test command

```bash
cd ~/code/datahub-gateway
go build ./services/repository/...
go test ./services/repository/... -run TestDataRepo -v
```

---

## Task 5: Proxy Client

**Objective:** Create `modules/dit/client.go` with `Client` struct, singleton `DefaultClient()`, and 15 proxy methods.

### Steps

- [ ] Create `modules/dit/` directory
- [ ] Implement `client.go` with Client struct and DefaultClient singleton
- [ ] Implement all 15 methods
- [ ] Write tests using `httptest`
- [ ] Verify build

### File: `modules/dit/client.go` (create new)

```go
// Copyright 2024 The Forgejo Authors. All rights reserved.
// SPDX-License-Identifier: MIT

package dit

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"

	"code.gitea.io/gitea/modules/setting"
)

// Client is a thin HTTP proxy client for dit-core.
type Client struct {
	baseURL      string
	serviceToken string
	httpClient   *http.Client
}

var (
	defaultClient     *Client
	defaultClientOnce sync.Once
)

// DefaultClient returns the singleton Client initialized from setting.Dit.
func DefaultClient() *Client {
	defaultClientOnce.Do(func() {
		defaultClient = &Client{
			baseURL:      strings.TrimRight(setting.Dit.CoreURL, "/"),
			serviceToken: setting.Dit.ServiceToken,
			httpClient:   &http.Client{},
		}
	})
	return defaultClient
}

// ResetDefaultClient resets the singleton (for testing).
func ResetDefaultClient() {
	defaultClientOnce = sync.Once{}
	defaultClient = nil
}

func (c *Client) do(ctx context.Context, method, path string, body []byte) ([]byte, int, error) {
	var bodyReader io.Reader
	if body != nil {
		bodyReader = bytes.NewReader(body)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, bodyReader)
	if err != nil {
		return nil, 0, fmt.Errorf("build request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+c.serviceToken)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, 0, fmt.Errorf("http request: %w", err)
	}
	defer resp.Body.Close()

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, resp.StatusCode, fmt.Errorf("read response: %w", err)
	}
	return data, resp.StatusCode, nil
}

// CreateRepo registers a new data repo in dit-core.
// Returns error if the response status is not 2xx.
func (c *Client) CreateRepo(ctx context.Context, repoName string) error {
	payload := []byte(fmt.Sprintf(`{"name":%q}`, repoName))
	_, status, err := c.do(ctx, http.MethodPost, "/api/v1/repos", payload)
	if err != nil {
		return err
	}
	if status < 200 || status >= 300 {
		return fmt.Errorf("dit-core returned status %d for CreateRepo", status)
	}
	return nil
}

// DeleteRepo removes a data repo from dit-core.
// Returns error if the response status is not 2xx or 404.
func (c *Client) DeleteRepo(ctx context.Context, repoName string) error {
	_, status, err := c.do(ctx, http.MethodDelete, "/api/v1/repos/"+repoName, nil)
	if err != nil {
		return err
	}
	if status == http.StatusNotFound {
		return nil // already gone, treat as success
	}
	if status < 200 || status >= 300 {
		return fmt.Errorf("dit-core returned status %d for DeleteRepo", status)
	}
	return nil
}

// ListRefs proxies GET /api/v1/repos/{repo}/refs
func (c *Client) ListRefs(ctx context.Context, repoName string) ([]byte, int, error) {
	return c.do(ctx, http.MethodGet, "/api/v1/repos/"+repoName+"/refs", nil)
}

// GetRef proxies GET /api/v1/repos/{repo}/refs/{type}/{name}
func (c *Client) GetRef(ctx context.Context, repoName, refType, name string) ([]byte, int, error) {
	return c.do(ctx, http.MethodGet, "/api/v1/repos/"+repoName+"/refs/"+refType+"/"+name, nil)
}

// UpdateRef proxies POST /api/v1/repos/{repo}/refs/{type}/{name}
func (c *Client) UpdateRef(ctx context.Context, repoName, refType, name string, body []byte) ([]byte, int, error) {
	return c.do(ctx, http.MethodPost, "/api/v1/repos/"+repoName+"/refs/"+refType+"/"+name, body)
}

// GetObject proxies GET /api/v1/repos/{repo}/objects/{hash}
func (c *Client) GetObject(ctx context.Context, repoName, hash string) ([]byte, int, error) {
	return c.do(ctx, http.MethodGet, "/api/v1/repos/"+repoName+"/objects/"+hash, nil)
}

// PushObjects proxies POST /api/v1/repos/{repo}/objects/batch
func (c *Client) PushObjects(ctx context.Context, repoName string, body []byte) ([]byte, int, error) {
	return c.do(ctx, http.MethodPost, "/api/v1/repos/"+repoName+"/objects/batch", body)
}

// GetTree proxies GET /api/v1/repos/{repo}/tree/{hash}
func (c *Client) GetTree(ctx context.Context, repoName, hash string) ([]byte, int, error) {
	return c.do(ctx, http.MethodGet, "/api/v1/repos/"+repoName+"/tree/"+hash, nil)
}

// GetDiff proxies GET /api/v1/repos/{repo}/diff/{old}/{new}
func (c *Client) GetDiff(ctx context.Context, repoName, oldHash, newHash string) ([]byte, int, error) {
	return c.do(ctx, http.MethodGet, "/api/v1/repos/"+repoName+"/diff/"+oldHash+"/"+newHash, nil)
}

// GetLog proxies GET /api/v1/repos/{repo}/log/{ref}
func (c *Client) GetLog(ctx context.Context, repoName, ref string) ([]byte, int, error) {
	return c.do(ctx, http.MethodGet, "/api/v1/repos/"+repoName+"/log/"+ref, nil)
}

// ListPulls proxies GET /api/v1/repos/{repo}/pulls
func (c *Client) ListPulls(ctx context.Context, repoName string) ([]byte, int, error) {
	return c.do(ctx, http.MethodGet, "/api/v1/repos/"+repoName+"/pulls", nil)
}

// CreatePull proxies POST /api/v1/repos/{repo}/pulls
func (c *Client) CreatePull(ctx context.Context, repoName string, body []byte) ([]byte, int, error) {
	return c.do(ctx, http.MethodPost, "/api/v1/repos/"+repoName+"/pulls", body)
}

// GetPull proxies GET /api/v1/repos/{repo}/pulls/{id}
func (c *Client) GetPull(ctx context.Context, repoName, id string) ([]byte, int, error) {
	return c.do(ctx, http.MethodGet, "/api/v1/repos/"+repoName+"/pulls/"+id, nil)
}

// MergePull proxies POST /api/v1/repos/{repo}/pulls/{id}/merge
func (c *Client) MergePull(ctx context.Context, repoName, id string, body []byte) ([]byte, int, error) {
	return c.do(ctx, http.MethodPost, "/api/v1/repos/"+repoName+"/pulls/"+id+"/merge", body)
}

// GetManifest proxies GET /api/v1/repos/{repo}/manifest/{hash}
func (c *Client) GetManifest(ctx context.Context, repoName, hash string) ([]byte, int, error) {
	return c.do(ctx, http.MethodGet, "/api/v1/repos/"+repoName+"/manifest/"+hash, nil)
}
```

### File: `modules/dit/client_test.go` (create new)

```go
package dit_test

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"code.gitea.io/gitea/modules/dit"
	"code.gitea.io/gitea/modules/setting"
)

func newTestClient(t *testing.T, handler http.Handler) *dit.Client {
	t.Helper()
	srv := httptest.NewServer(handler)
	t.Cleanup(srv.Close)
	// Override setting and reset singleton for test isolation
	setting.Dit.CoreURL = srv.URL
	setting.Dit.ServiceToken = "test-token"
	dit.ResetDefaultClient()
	return dit.DefaultClient()
}

func TestListRefs(t *testing.T) {
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, http.MethodGet, r.Method)
		assert.Equal(t, "/api/v1/repos/myrepo/refs", r.URL.Path)
		assert.Equal(t, "Bearer test-token", r.Header.Get("Authorization"))
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`[]`))
	}))

	data, status, err := client.ListRefs(context.Background(), "myrepo")
	require.NoError(t, err)
	assert.Equal(t, http.StatusOK, status)
	assert.Equal(t, []byte(`[]`), data)
}

func TestCreateRepo(t *testing.T) {
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, http.MethodPost, r.Method)
		assert.Equal(t, "/api/v1/repos", r.URL.Path)
		w.WriteHeader(http.StatusCreated)
	}))

	err := client.CreateRepo(context.Background(), "newrepo")
	require.NoError(t, err)
}

func TestDeleteRepoNotFound(t *testing.T) {
	// 404 should be treated as success (already gone)
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	}))

	err := client.DeleteRepo(context.Background(), "gone-repo")
	require.NoError(t, err)
}

func TestDeleteRepoServerError(t *testing.T) {
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))

	err := client.DeleteRepo(context.Background(), "broken-repo")
	require.Error(t, err)
}
```

### Test command

```bash
cd ~/code/datahub-gateway
go test ./modules/dit/... -v -count=1
```

---

## Task 6: API Routes

**Objective:** Create 13 dit handler functions and register the `/dit` route group in api.go.

### Steps

- [ ] Create `routers/api/v1/repo/dit.go` with all 13 handlers
- [ ] Register route group in `routers/api/v1/api.go`
- [ ] Confirm no `reqRepoReader` or `ReferencesGitRepo` middleware is used
- [ ] Write handler tests
- [ ] Verify build

### File: `routers/api/v1/repo/dit.go` (create new)

```go
// Copyright 2024 The Forgejo Authors. All rights reserved.
// SPDX-License-Identifier: MIT

package repo

import (
	"net/http"

	"code.gitea.io/gitea/modules/dit"
	"code.gitea.io/gitea/services/context"
)

// proxyToDatahub checks IsDataRepo, proxies the request to dit-core,
// and forwards the raw response (status code + body).
func proxyToDatahub(ctx *context.APIContext, fn func() ([]byte, int, error)) {
	if !ctx.Repo.Repository.IsDataRepo {
		ctx.NotFound()
		return
	}
	data, status, err := fn()
	if err != nil {
		ctx.Error(http.StatusBadGateway, "dit proxy", err)
		return
	}
	ctx.Resp.Header().Set("Content-Type", "application/json")
	ctx.Resp.WriteHeader(status)
	_, _ = ctx.Resp.Write(data)
}

// DatahubListRefs godoc
// @Summary List refs for a data repository
// @Router /repos/{owner}/{repo}/dit/refs [get]
func DatahubListRefs(ctx *context.APIContext) {
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		return dit.DefaultClient().ListRefs(ctx, ctx.Repo.Repository.Name)
	})
}

// DatahubGetRef godoc
// @Router /repos/{owner}/{repo}/dit/refs/{ref_type}/{name} [get]
func DatahubGetRef(ctx *context.APIContext) {
	refType := ctx.Params(":ref_type")
	name := ctx.Params(":name")
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		return dit.DefaultClient().GetRef(ctx, ctx.Repo.Repository.Name, refType, name)
	})
}

// DatahubUpdateRef godoc
// @Router /repos/{owner}/{repo}/dit/refs/{ref_type}/{name} [post]
func DatahubUpdateRef(ctx *context.APIContext) {
	refType := ctx.Params(":ref_type")
	name := ctx.Params(":name")
	body := ctx.Req.Body
	defer body.Close()
	var bodyBytes []byte
	if body != nil {
		import_io_ioutil := func() ([]byte, error) {
			import "io"
			return io.ReadAll(body)
		}
		// read body inline
	}
	// Simplified — read body bytes then proxy:
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		return dit.DefaultClient().UpdateRef(ctx, ctx.Repo.Repository.Name, refType, name, bodyBytes)
	})
}

// DatahubGetObject godoc
// @Router /repos/{owner}/{repo}/dit/objects/{hash} [get]
func DatahubGetObject(ctx *context.APIContext) {
	hash := ctx.Params(":hash")
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		return dit.DefaultClient().GetObject(ctx, ctx.Repo.Repository.Name, hash)
	})
}

// DatahubPushObjects godoc
// @Router /repos/{owner}/{repo}/dit/objects/batch [post]
func DatahubPushObjects(ctx *context.APIContext) {
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		body, _ := io.ReadAll(ctx.Req.Body)
		return dit.DefaultClient().PushObjects(ctx, ctx.Repo.Repository.Name, body)
	})
}

// DatahubGetTree godoc
// @Router /repos/{owner}/{repo}/dit/tree/{hash} [get]
func DatahubGetTree(ctx *context.APIContext) {
	hash := ctx.Params(":hash")
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		return dit.DefaultClient().GetTree(ctx, ctx.Repo.Repository.Name, hash)
	})
}

// DatahubGetDiff godoc
// @Router /repos/{owner}/{repo}/dit/diff/{old}/{new} [get]
func DatahubGetDiff(ctx *context.APIContext) {
	old := ctx.Params(":old")
	new := ctx.Params(":new")
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		return dit.DefaultClient().GetDiff(ctx, ctx.Repo.Repository.Name, old, new)
	})
}

// DatahubGetLog godoc
// @Router /repos/{owner}/{repo}/dit/log/{ref} [get]
func DatahubGetLog(ctx *context.APIContext) {
	ref := ctx.Params(":ref")
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		return dit.DefaultClient().GetLog(ctx, ctx.Repo.Repository.Name, ref)
	})
}

// DatahubListPulls godoc
// @Router /repos/{owner}/{repo}/dit/pulls [get]
func DatahubListPulls(ctx *context.APIContext) {
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		return dit.DefaultClient().ListPulls(ctx, ctx.Repo.Repository.Name)
	})
}

// DatahubCreatePull godoc
// @Router /repos/{owner}/{repo}/dit/pulls [post]
func DatahubCreatePull(ctx *context.APIContext) {
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		body, _ := io.ReadAll(ctx.Req.Body)
		return dit.DefaultClient().CreatePull(ctx, ctx.Repo.Repository.Name, body)
	})
}

// DatahubGetPull godoc
// @Router /repos/{owner}/{repo}/dit/pulls/{id} [get]
func DatahubGetPull(ctx *context.APIContext) {
	id := ctx.Params(":id")
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		return dit.DefaultClient().GetPull(ctx, ctx.Repo.Repository.Name, id)
	})
}

// DatahubMergePull godoc
// @Router /repos/{owner}/{repo}/dit/pulls/{id}/merge [post]
func DatahubMergePull(ctx *context.APIContext) {
	id := ctx.Params(":id")
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		body, _ := io.ReadAll(ctx.Req.Body)
		return dit.DefaultClient().MergePull(ctx, ctx.Repo.Repository.Name, id, body)
	})
}

// DatahubGetManifest godoc
// @Router /repos/{owner}/{repo}/dit/manifest/{hash} [get]
func DatahubGetManifest(ctx *context.APIContext) {
	hash := ctx.Params(":hash")
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		return dit.DefaultClient().GetManifest(ctx, ctx.Repo.Repository.Name, hash)
	})
}
```

> **Note:** The `DatahubUpdateRef` and other POST handlers have inline body-reading patterns sketched above. In the actual implementation, consolidate body reads using `io.ReadAll(ctx.Req.Body)` at handler top. The pattern shown for `DatahubPushObjects` and `DatahubCreatePull` is canonical.

### Edit: `routers/api/v1/api.go`

Search for the `/{username}/{reponame}` group. Inside it, add the dit group after the existing API groups (e.g., after `issues`, `releases`, etc.):

```go
// Inside /{username}/{reponame} group, after other sub-groups:
m.Group("/dit", func() {
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

**Critical:** Do NOT add `reqRepoReader` or `context.ReferencesGitRepo()` to this group. `repoAssignment()` is sufficient — it resolves the repo and checks basic access without opening any git directory.

### File: `routers/api/v1/repo/dit_test.go` (create new)

```go
package repo_test

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestDatahubListRefsNotDataRepo(t *testing.T) {
	// If IsDataRepo == false, handler must return 404
	// Wire up a fake APIContext with IsDataRepo=false and assert 404
	w := httptest.NewRecorder()
	assert.Equal(t, http.StatusNotFound, w.Code)
}

func TestDatahubListRefsProxies(t *testing.T) {
	// Mock dit-core, assert response is forwarded transparently
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`[{"name":"main"}]`))
	}))
	defer upstream.Close()
	// Assert response body == `[{"name":"main"}]` and status == 200
}
```

### Test command

```bash
cd ~/code/datahub-gateway
go build ./routers/api/v1/...
go test ./routers/api/v1/repo/... -run TestDatahub -v
```

---

## Task 7: Indexer Filter

**Objective:** Exclude data repos from the code indexer (they have no git objects to index).

### Steps

- [ ] Locate eligibility check in `modules/indexer/code/`
- [ ] Add `IsDataRepo` exclusion alongside existing `IsMirror`/`IsFork`/`IsTemplate` guards
- [ ] Write test confirming data repos are excluded
- [ ] Verify build

### Where to look

```bash
grep -rn "IsMirror\|IsTemplate\|IsEmpty" ~/code/datahub-gateway/modules/indexer/code/
```

The indexer eligibility function typically looks like:

```go
func isRepoIndexable(repo *repo_model.Repository) bool {
    return !repo.IsPrivate && !repo.IsArchived && !repo.IsEmpty && !repo.IsMirror
}
```

Or there may be a filter in `indexer.go` before calling `index()`.

### Edit: wherever `isRepoIndexable` or equivalent guard lives

Add `IsDataRepo` exclusion:

```go
func isRepoIndexable(repo *repo_model.Repository) bool {
    if repo.IsDataRepo {
        return false // data repos have no git objects
    }
    return !repo.IsPrivate && !repo.IsArchived && !repo.IsEmpty && !repo.IsMirror
}
```

### Test: add to existing indexer tests or create `modules/indexer/code/dit_test.go`

```go
func TestDataRepoExcludedFromIndex(t *testing.T) {
	repo := &repo_model.Repository{
		IsDataRepo: true,
		IsEmpty:    true,
	}
	assert.False(t, isRepoIndexable(repo), "data repos must not be indexed")
}
```

### Test command

```bash
cd ~/code/datahub-gateway
go test ./modules/indexer/code/... -run TestDataRepo -v
```

---

## Task 8: Repo Settings Guard

**Objective:** Guard git-specific operations in web repo settings and API PATCH for data repos.

### Steps

- [ ] Guard git-specific ops in `routers/web/repo/repo.go` when `IsDataRepo`
- [ ] Guard `IsEmpty=false` prevention in `routers/api/v1/repo/repo.go` Edit handler
- [ ] Write tests
- [ ] Verify build and full `make build`

### Edit: `routers/web/repo/repo.go`

Find operations that call git commands (e.g., `git config`, `update-server-info`, `CheckDaemonExportOK`). Wrap with IsDataRepo guard:

```go
// Example guard pattern — use wherever git ops are called:
if !ctx.Repo.Repository.IsDataRepo {
    // git-specific operation here
    if err := gitRepo.Config(...); err != nil {
        // ...
    }
}
```

The settings page itself should still render for data repos — only the git-calling code paths need guarding.

### Edit: `routers/api/v1/repo/repo.go`

Find the `Edit` handler (around line 806 per spec). Add guard before the block that sets `IsEmpty = false`:

```go
// In Edit handler, before updating IsEmpty:
if ctx.Repo.Repository.IsDataRepo && opts.HasArg("has_projects") {
    // example: prevent clearing empty flag on data repos
}

// More specifically, find where IsEmpty is set from API input:
if !repo.IsDataRepo {
    // only update IsEmpty for non-data repos
    if opts.AutoInit != nil {
        repo.IsEmpty = !*opts.AutoInit
    }
}
```

The key invariant: **data repos must always have `IsEmpty = true`** to prevent git-opening middleware from crashing.

### Test: `routers/api/v1/repo/dit_settings_test.go` (create new)

```go
func TestEditHandlerCannotClearIsEmptyOnDataRepo(t *testing.T) {
	// Send PATCH /api/v1/repos/{owner}/{repo} with {"auto_init": true}
	// on a repo where IsDataRepo=true
	// Assert response is 422 or the field is ignored
}
```

### Final build verification

```bash
cd ~/code/datahub-gateway
make build
./gitea --version
# Run full test suite:
go test ./... 2>&1 | tail -20
```

---

## Implementation Order

Tasks must be done in this order due to dependencies:

```
Task 1 (fork setup)
  └── Task 2 (config)
        └── Task 5 (proxy client — needs setting.Dit)
              └── Task 4 (create/delete hooks — needs DefaultClient())
              └── Task 6 (API routes — needs DefaultClient())
  └── Task 3 (data model — needed by Tasks 4, 6, 7, 8)
        └── Task 7 (indexer filter)
        └── Task 8 (settings guard)
```

Tasks 7 and 8 are independent once Task 3 is done and can be done in parallel.

---

## Key Constraints (Do Not Violate)

| Constraint | Detail |
|------------|--------|
| Never call `ReferencesGitRepo()` on dit routes | Opens git dir, crashes — no git dir exists for data repos |
| Never add `reqRepoReader` to dit route group | No matching unit type exists; causes 403 for all requests |
| `IsEmpty` must always be `true` for data repos | Prevents git middleware crash; enforce in both create path and API PATCH |
| Deletion is fail-open | Do not block Forgejo DB/filesystem cleanup on dit-core errors |
| CreateRepo is inside DB transaction | On error, transaction rolls back and existing rollback calls DeleteRepositoryDirectly |
| Migration uses `x.Sync()` not raw SQL | Follows Forgejo migration pattern for cross-DB compatibility |
