# Phase 1: Remote Collaboration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable multi-user remote collaboration via HTTP API server, CLI remote commands (clone/push/pull), PostgreSQL-backed refs, and API token authentication.

**Architecture:** FastAPI server in `src/dit/server/` sharing `dit.core` with CLI. PostgreSQL stores refs/repos/tokens via SQLAlchemy 2.0 async. ObjectStore stays on filesystem. CLI uses sync httpx RemoteClient for remote operations.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, asyncpg, alembic, httpx, pydantic-settings, tomli-w

---

## File Structure

### New files:
- `src/dit/core/config.py` — TOML config reader/writer for .dit/config
- `src/dit/core/remote.py` — Sync httpx RemoteClient
- `src/dit/core/walker.py` — Object walker + ancestor check
- `src/dit/server/__init__.py`
- `src/dit/server/app.py` — FastAPI app with lifespan
- `src/dit/server/config.py` — pydantic-settings ServerSettings
- `src/dit/server/auth.py` — Token auth dependency
- `src/dit/server/database.py` — SQLAlchemy async engine + session
- `src/dit/server/models.py` — ORM models (Repo, Ref, Token)
- `src/dit/server/routes/__init__.py`
- `src/dit/server/routes/repos.py` — Repo CRUD routes
- `src/dit/server/routes/refs.py` — Ref routes + CAS
- `src/dit/server/routes/objects.py` — Object upload/download/batch-exists
- `src/dit/server/routes/tokens.py` — Token admin routes
- `src/dit/server/alembic.ini`
- `src/dit/server/alembic/env.py`
- `src/dit/server/alembic/versions/001_initial.py`

### Modified files:
- `pyproject.toml` — New dependencies
- `src/dit/cli/main.py` — New commands: serve, remote, auth, clone, push, pull, fetch
- `src/dit/core/workspace.py` — Add materialize_file()

### New test files:
- `tests/test_config.py`
- `tests/test_remote.py`
- `tests/test_walker.py`
- `tests/server/__init__.py`
- `tests/server/test_config.py`
- `tests/server/test_models.py`
- `tests/server/test_auth.py`
- `tests/server/test_app.py`
- `tests/server/test_routes_repos.py`
- `tests/server/test_routes_refs.py`
- `tests/server/test_routes_objects.py`
- `tests/server/test_routes_tokens.py`
- `tests/server/conftest.py`
- `tests/test_cli_remote.py`
- `tests/test_cli_push.py`
- `tests/test_cli_clone.py`
- `tests/test_cli_pull.py`
- `tests/test_integration_remote.py`

---

## Task 1: Dependencies & Project Config

Update `pyproject.toml` to add new runtime and server dependencies.

- [ ] Open `pyproject.toml` and apply the following changes:

```toml
[project]
name = "dit"
version = "0.1.0"
description = "Git-like version control for LLM SFT training data"
requires-python = ">=3.12"
dependencies = [
    "typer>=0.15",
    "pyzstd>=0.16",
    "jcs>=0.2",
    "httpx>=0.27",
    "tomli-w>=1.0",
]

[project.optional-dependencies]
server = [
    "fastapi>=0.115",
    "uvicorn>=0.32",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.30",
    "pydantic-settings>=2.0",
    "alembic>=1.14",
]

[project.scripts]
dit = "dit.cli.main:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/dit"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-tmp-files>=0.0.2",
    "pytest-asyncio>=0.24",
    "aiosqlite>=0.20",
    "httpx>=0.27",
]
```

- [ ] Run `uv sync --extra server` — verify it completes without errors.
- [ ] Run `uv run pytest` — verify all existing tests still pass.
- [ ] Commit: `chore: add server deps and dev test deps to pyproject.toml`

---

## Task 2: TOML Config Reader/Writer

Create `src/dit/core/config.py` with functions to read/write `.dit/config`.

- [ ] Create `src/dit/core/config.py`:

```python
import tomllib
from pathlib import Path

import tomli_w


def load_config(dot_dit: Path) -> dict:
    """Load .dit/config TOML, return empty dict if missing."""
    config_path = dot_dit / "config"
    if not config_path.exists():
        return {}
    return tomllib.loads(config_path.read_text(encoding="utf-8"))


def save_config(dot_dit: Path, config: dict) -> None:
    """Write config dict to .dit/config as TOML."""
    config_path = dot_dit / "config"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_bytes(tomli_w.dumps(config).encode("utf-8"))


def get_remote(dot_dit: Path, name: str) -> dict | None:
    """Get remote config {url, token} or None."""
    config = load_config(dot_dit)
    return config.get("remote", {}).get(name)


def set_remote(dot_dit: Path, name: str, url: str, token: str = "") -> None:
    """Set remote URL and optional token."""
    config = load_config(dot_dit)
    config.setdefault("remote", {})[name] = {"url": url, "token": token}
    save_config(dot_dit, config)


def remove_remote(dot_dit: Path, name: str) -> bool:
    """Remove remote, return True if existed."""
    config = load_config(dot_dit)
    remotes = config.get("remote", {})
    if name not in remotes:
        return False
    del remotes[name]
    if not remotes:
        config.pop("remote", None)
    save_config(dot_dit, config)
    return True
```

- [ ] Create `tests/test_config.py`:

```python
from pathlib import Path

import pytest

from dit.core.config import (
    get_remote,
    load_config,
    remove_remote,
    save_config,
    set_remote,
)


def test_load_config_missing(tmp_path: Path) -> None:
    dot = tmp_path / ".dit"
    dot.mkdir()
    assert load_config(dot) == {}


def test_save_and_load_config(tmp_path: Path) -> None:
    dot = tmp_path / ".dit"
    dot.mkdir()
    save_config(dot, {"foo": "bar"})
    assert load_config(dot) == {"foo": "bar"}


def test_set_and_get_remote(tmp_path: Path) -> None:
    dot = tmp_path / ".dit"
    dot.mkdir()
    set_remote(dot, "origin", "http://localhost:8000", "tok123")
    result = get_remote(dot, "origin")
    assert result == {"url": "http://localhost:8000", "token": "tok123"}


def test_get_remote_missing(tmp_path: Path) -> None:
    dot = tmp_path / ".dit"
    dot.mkdir()
    assert get_remote(dot, "origin") is None


def test_set_remote_no_token(tmp_path: Path) -> None:
    dot = tmp_path / ".dit"
    dot.mkdir()
    set_remote(dot, "upstream", "http://example.com")
    result = get_remote(dot, "upstream")
    assert result == {"url": "http://example.com", "token": ""}


def test_remove_remote_existing(tmp_path: Path) -> None:
    dot = tmp_path / ".dit"
    dot.mkdir()
    set_remote(dot, "origin", "http://localhost:8000", "tok")
    assert remove_remote(dot, "origin") is True
    assert get_remote(dot, "origin") is None


def test_remove_remote_missing(tmp_path: Path) -> None:
    dot = tmp_path / ".dit"
    dot.mkdir()
    assert remove_remote(dot, "origin") is False


def test_remove_remote_leaves_others(tmp_path: Path) -> None:
    dot = tmp_path / ".dit"
    dot.mkdir()
    set_remote(dot, "origin", "http://a.com", "t1")
    set_remote(dot, "upstream", "http://b.com", "t2")
    remove_remote(dot, "origin")
    assert get_remote(dot, "upstream") == {"url": "http://b.com", "token": "t2"}
    assert get_remote(dot, "origin") is None
```

- [ ] Run `uv run pytest tests/test_config.py` — all tests pass.
- [ ] Commit: `feat: TOML config reader/writer for .dit/config`

---

## Task 3: Server Settings

Create the server package and its pydantic-settings configuration.

- [ ] Create `src/dit/server/__init__.py` (empty file).

- [ ] Create `src/dit/server/config.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerSettings(BaseSettings):
    database_url: str = "postgresql+asyncpg://localhost/dit"
    data_dir: str = "/data/dit"
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = SettingsConfigDict(env_prefix="DIT_SERVER_")
```

- [ ] Create `tests/server/__init__.py` (empty file).

- [ ] Create `tests/server/test_config.py`:

```python
import pytest

from dit.server.config import ServerSettings


def test_default_values() -> None:
    settings = ServerSettings()
    assert settings.database_url == "postgresql+asyncpg://localhost/dit"
    assert settings.data_dir == "/data/dit"
    assert settings.host == "0.0.0.0"
    assert settings.port == 8000


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIT_SERVER_PORT", "9000")
    monkeypatch.setenv("DIT_SERVER_HOST", "127.0.0.1")
    monkeypatch.setenv("DIT_SERVER_DATA_DIR", "/tmp/dit")
    settings = ServerSettings()
    assert settings.port == 9000
    assert settings.host == "127.0.0.1"
    assert settings.data_dir == "/tmp/dit"


def test_database_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIT_SERVER_DATABASE_URL", "postgresql+asyncpg://user:pass@db/test")
    settings = ServerSettings()
    assert settings.database_url == "postgresql+asyncpg://user:pass@db/test"
```

- [ ] Run `uv run pytest tests/server/test_config.py` — all tests pass.
- [ ] Commit: `feat: server package + pydantic-settings ServerSettings`

---

## Task 4: SQLAlchemy Models + Database

Create the async database engine factory, ORM models, and shared test fixtures.

- [ ] Create `src/dit/server/database.py`:

```python
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio import AsyncEngine


async def create_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, echo=False)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
```

- [ ] Create `src/dit/server/models.py`:

```python
from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Repo(Base):
    __tablename__ = "repos"
    __table_args__ = {"schema": "dit"}

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"Repo(id={self.id!r}, name={self.name!r})"


class Ref(Base):
    __tablename__ = "refs"
    __table_args__ = {"schema": "dit"}

    repo_id: Mapped[int] = mapped_column(
        ForeignKey("dit.repos.id"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(256), primary_key=True)
    target_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    def __repr__(self) -> str:
        return f"Ref(repo_id={self.repo_id!r}, name={self.name!r}, target_hash={self.target_hash!r})"


class Token(Base):
    __tablename__ = "tokens"
    __table_args__ = {"schema": "dit"}

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    repo_scope: Mapped[int | None] = mapped_column(
        ForeignKey("dit.repos.id"), nullable=True
    )
    permissions: Mapped[str] = mapped_column(String(32), nullable=False, default="push")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)

    def __repr__(self) -> str:
        return f"Token(id={self.id!r}, label={self.label!r}, permissions={self.permissions!r})"
```

- [ ] Create `tests/server/conftest.py`:

```python
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from dit.server.database import create_engine, create_session_factory
from dit.server.models import Base


@pytest_asyncio.fixture
async def engine() -> AsyncEngine:
    eng = await create_engine("sqlite+aiosqlite:///:memory:")
    # Create dit schema equivalent for SQLite (schema not supported, use no-schema variant)
    async with eng.begin() as conn:
        # SQLite doesn't support schemas; patch table args for tests
        for table in Base.metadata.tables.values():
            table.schema = None
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncSession:
    factory = create_session_factory(engine)
    async with factory() as s:
        yield s


@pytest_asyncio.fixture
async def client(engine: AsyncEngine) -> AsyncClient:
    from dit.server.app import app
    from dit.server.database import create_session_factory

    factory = create_session_factory(engine)

    async def override_get_session():
        async with factory() as s:
            yield s

    app.dependency_overrides = {}
    # Session override registered in test_app.py as needed
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
```

- [ ] Create `tests/server/test_models.py`:

```python
from datetime import datetime

import pytest

from dit.server.models import Ref, Repo, Token


def test_repo_instantiation() -> None:
    repo = Repo(name="my-dataset")
    assert repo.name == "my-dataset"
    assert repo.id is None


def test_repo_repr() -> None:
    repo = Repo(id=1, name="sft-code")
    assert "sft-code" in repr(repo)


def test_ref_instantiation() -> None:
    ref = Ref(repo_id=1, name="heads/main", target_hash="a" * 64)
    assert ref.name == "heads/main"
    assert ref.target_hash == "a" * 64


def test_ref_repr() -> None:
    ref = Ref(repo_id=1, name="heads/main", target_hash="b" * 64)
    assert "heads/main" in repr(ref)


def test_token_instantiation() -> None:
    token = Token(token_hash="c" * 64, label="alice-laptop", permissions="push")
    assert token.label == "alice-laptop"
    assert token.permissions == "push"
    assert token.repo_scope is None
    assert token.expires_at is None


def test_token_repr() -> None:
    token = Token(id=5, token_hash="d" * 64, label="bob-ci", permissions="read")
    assert "bob-ci" in repr(token)
    assert "read" in repr(token)
```

- [ ] Run `uv run pytest tests/server/test_models.py` — all tests pass.
- [ ] Commit: `feat: SQLAlchemy async engine, session factory, and ORM models`

---

## Task 5: Alembic Setup

Create the Alembic migration infrastructure for the `dit` schema.

- [ ] Create `src/dit/server/alembic.ini`:

```ini
[alembic]
script_location = %(here)s/alembic
prepend_sys_path = .
sqlalchemy.url = postgresql+asyncpg://localhost/dit

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] Create `src/dit/server/alembic/env.py`:

```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from dit.server.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema="dit",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = create_async_engine(config.get_main_option("sqlalchemy.url"))
    async with connectable.connect() as connection:
        await connection.run_sync(
            lambda conn: context.configure(
                connection=conn,
                target_metadata=target_metadata,
                include_schemas=True,
                version_table_schema="dit",
            )
        )
        async with connection.begin():
            await connection.run_sync(lambda conn: context.run_migrations())
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

- [ ] Create `src/dit/server/alembic/script.mako`:

```
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] Create `src/dit/server/alembic/versions/001_initial.py`:

```python
"""Initial schema: dit.repos, dit.refs, dit.tokens

Revision ID: 001
Revises:
Create Date: 2026-04-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS dit")

    op.create_table(
        "repos",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="dit",
    )

    op.create_table(
        "refs",
        sa.Column("repo_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("target_hash", sa.String(64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["dit.repos.id"]),
        sa.PrimaryKeyConstraint("repo_id", "name"),
        schema="dit",
    )

    op.create_table(
        "tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("repo_scope", sa.Integer(), nullable=True),
        sa.Column("permissions", sa.String(32), nullable=False, server_default="push"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["repo_scope"], ["dit.repos.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        schema="dit",
    )


def downgrade() -> None:
    op.drop_table("tokens", schema="dit")
    op.drop_table("refs", schema="dit")
    op.drop_table("repos", schema="dit")
    op.execute("DROP SCHEMA IF EXISTS dit")
```

- [ ] Verify files exist: `src/dit/server/alembic.ini`, `src/dit/server/alembic/env.py`, `src/dit/server/alembic/script.mako`, `src/dit/server/alembic/versions/001_initial.py`.
- [ ] Run `uv run pytest` — existing tests still pass (alembic files have no tests).
- [ ] Commit: `chore: alembic setup with initial dit schema migration`

---

## Task 6: Auth Dependency

Create the FastAPI token authentication dependency.

- [ ] Create `src/dit/server/auth.py`:

```python
import hashlib
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.models import Token

api_key_header = APIKeyHeader(name="Authorization", auto_error=True)


async def get_session() -> AsyncSession:  # pragma: no cover
    raise NotImplementedError("Override in app lifespan")


async def verify_token(
    authorization: str = Depends(api_key_header),
    session: AsyncSession = Depends(get_session),
) -> Token:
    raw = authorization.removeprefix("Bearer ").strip()
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    result = await session.execute(select(Token).where(Token.token_hash == token_hash))
    token = result.scalar_one_or_none()

    if token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if token.expires_at is not None:
        now = datetime.now(tz=timezone.utc)
        expires = token.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if now > expires:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token expired")

    return token
```

- [ ] Create `tests/server/test_auth.py`:

```python
import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from dit.server.auth import verify_token
from dit.server.models import Token


def make_token(**kwargs) -> Token:
    defaults = dict(
        id=1,
        token_hash="x" * 64,
        label="test",
        permissions="push",
        expires_at=None,
        repo_scope=None,
    )
    defaults.update(kwargs)
    t = Token.__new__(Token)
    for k, v in defaults.items():
        object.__setattr__(t, k, v)
    return t


def make_session(token: Token | None) -> AsyncMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = token
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_valid_token() -> None:
    raw = "dit_abc123"
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    token = make_token(token_hash=token_hash)
    session = make_session(token)
    result = await verify_token(f"Bearer {raw}", session)
    assert result is token


@pytest.mark.asyncio
async def test_missing_token_raises_401() -> None:
    session = make_session(None)
    with pytest.raises(HTTPException) as exc_info:
        await verify_token("Bearer ", session)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_invalid_token_raises_401() -> None:
    session = make_session(None)
    with pytest.raises(HTTPException) as exc_info:
        await verify_token("Bearer invalid_token", session)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_expired_token_raises_403() -> None:
    raw = "dit_expired"
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    past = datetime(2000, 1, 1, tzinfo=timezone.utc)
    token = make_token(token_hash=token_hash, expires_at=past)
    session = make_session(token)
    with pytest.raises(HTTPException) as exc_info:
        await verify_token(f"Bearer {raw}", session)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_non_expired_token_passes() -> None:
    raw = "dit_valid_future"
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    future = datetime.now(tz=timezone.utc) + timedelta(days=365)
    token = make_token(token_hash=token_hash, expires_at=future)
    session = make_session(token)
    result = await verify_token(f"Bearer {raw}", session)
    assert result is token
```

- [ ] Add `asyncio_mode = "auto"` to `[tool.pytest.ini_options]` in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
asyncio_mode = "auto"
```

- [ ] Run `uv run pytest tests/server/test_auth.py` — all tests pass.
- [ ] Commit: `feat: API token auth dependency with Bearer token + expiry check`

---

## Task 7: FastAPI App Skeleton

Create the FastAPI application with lifespan, health endpoint, and router stubs.

- [ ] Create `src/dit/server/routes/__init__.py` (empty file).

- [ ] Create `src/dit/server/app.py`:

```python
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI

from dit.core.store import ObjectStore
from dit.server.config import ServerSettings
from dit.server.database import create_engine, create_session_factory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = ServerSettings()
    engine = await create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    store = ObjectStore(Path(settings.data_dir) / "objects")
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.store = store
    yield
    await engine.dispose()


app = FastAPI(title="Dit", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
```

- [ ] Create `tests/server/test_app.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from dit.server.app import app


@pytest.mark.asyncio
async def test_health_returns_200() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

  > Note: The health test bypasses the full lifespan (no real DB needed). Use `AsyncClient` with `ASGITransport` and no lifespan trigger — FastAPI will call lifespan only on context manager entry. Alternatively, mock `ServerSettings` to use aiosqlite URL:

```python
import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch

from dit.server.app import app
from dit.server.config import ServerSettings


@pytest.mark.asyncio
async def test_health_returns_200(tmp_path) -> None:
    settings = ServerSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        data_dir=str(tmp_path),
    )
    with patch("dit.server.app.ServerSettings", return_value=settings):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] Run `uv run pytest tests/server/test_app.py` — test passes.
- [ ] Run `uv run pytest` — full suite passes.
- [ ] Commit: `feat: FastAPI app skeleton with lifespan, ObjectStore wiring, and health endpoint`

---

## Task 8: Repos Routes

**Files:**
- Create: `src/dit/server/routes/repos.py`
- Modify: `src/dit/server/app.py` (add session dep override + register router)
- Modify: `tests/server/conftest.py` (fix client fixture with dep overrides + tmp_path)
- Create: `tests/server/test_routes_repos.py`

- [ ] **Step 1:** Create `src/dit/server/routes/repos.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, verify_token
from dit.server.models import Repo, Token

router = APIRouter(prefix="/api/v1/repos", tags=["repos"])


class RepoCreate(BaseModel):
    name: str


class RepoOut(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


@router.post("", status_code=status.HTTP_201_CREATED, response_model=RepoOut)
async def create_repo(
    body: RepoCreate,
    session: AsyncSession = Depends(get_session),
    _token: Token = Depends(verify_token),
) -> RepoOut:
    existing = await session.execute(select(Repo).where(Repo.name == body.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Repo already exists")
    repo = Repo(name=body.name)
    session.add(repo)
    await session.commit()
    await session.refresh(repo)
    return repo


@router.get("", response_model=list[RepoOut])
async def list_repos(
    session: AsyncSession = Depends(get_session),
    _token: Token = Depends(verify_token),
) -> list[RepoOut]:
    result = await session.execute(select(Repo).order_by(Repo.id))
    return list(result.scalars().all())
```

- [ ] **Step 1b:** Rewrite `src/dit/server/app.py` to wire up the session dependency override and register the repos router:

```python
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI

from dit.core.store import ObjectStore
from dit.server.auth import get_session
from dit.server.config import ServerSettings
from dit.server.database import create_engine, create_session_factory
from dit.server.routes import repos as repos_routes


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = ServerSettings()
    engine = await create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    store = ObjectStore(Path(settings.data_dir) / "objects")
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.store = store
    app.state.data_dir = settings.data_dir

    async def _get_session() -> AsyncGenerator:
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _get_session
    yield
    await engine.dispose()


app = FastAPI(title="Dit", lifespan=lifespan)
app.include_router(repos_routes.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
```

- [ ] **Step 1c:** Rewrite `tests/server/conftest.py` to fix the client fixture with proper dep overrides and `tmp_path`:

```python
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from dit.server.auth import get_session, verify_token
from dit.server.database import create_engine, create_session_factory
from dit.server.models import Base, Token


def _make_admin_token() -> Token:
    t = Token.__new__(Token)
    object.__setattr__(t, "id", 1)
    object.__setattr__(t, "token_hash", "a" * 64)
    object.__setattr__(t, "label", "test-admin")
    object.__setattr__(t, "permissions", "admin")
    object.__setattr__(t, "expires_at", None)
    object.__setattr__(t, "repo_scope", None)
    return t


@pytest_asyncio.fixture
async def engine() -> AsyncEngine:
    eng = await create_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        for table in Base.metadata.tables.values():
            table.schema = None
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncSession:
    factory = create_session_factory(engine)
    async with factory() as s:
        yield s


@pytest_asyncio.fixture
async def client(engine: AsyncEngine, tmp_path) -> AsyncClient:
    from dit.server.app import app

    factory = create_session_factory(engine)

    async def override_get_session():
        async with factory() as s:
            yield s

    app.state.data_dir = str(tmp_path)
    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[verify_token] = _make_admin_token
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
```

- [ ] **Step 1d:** Create `tests/server/test_routes_repos.py`:

```python
import pytest
from httpx import AsyncClient


async def test_create_repo(client: AsyncClient) -> None:
    response = await client.post("/api/v1/repos", json={"name": "sft-code"})
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "sft-code"
    assert "id" in data


async def test_create_repo_duplicate_returns_409(client: AsyncClient) -> None:
    await client.post("/api/v1/repos", json={"name": "dup-repo"})
    response = await client.post("/api/v1/repos", json={"name": "dup-repo"})
    assert response.status_code == 409


async def test_list_repos_empty(client: AsyncClient) -> None:
    response = await client.get("/api/v1/repos")
    assert response.status_code == 200
    assert response.json() == []


async def test_list_repos_after_create(client: AsyncClient) -> None:
    await client.post("/api/v1/repos", json={"name": "alpha"})
    await client.post("/api/v1/repos", json={"name": "beta"})
    response = await client.get("/api/v1/repos")
    assert response.status_code == 200
    names = [r["name"] for r in response.json()]
    assert names == ["alpha", "beta"]
```

- [ ] **Step 2:** Run `uv run pytest tests/server/test_routes_repos.py -v`
- [ ] **Step 3:** Commit: `feat: repos routes POST+GET /api/v1/repos with SQLite test fixtures`

---

## Task 9: Refs Routes

**Files:**
- Create: `src/dit/server/routes/refs.py`
- Modify: `src/dit/server/app.py` (include refs router)
- Create: `tests/server/test_routes_refs.py`

- [ ] **Step 1:** Create `src/dit/server/routes/refs.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, verify_token
from dit.server.models import Ref, Repo, Token

router = APIRouter(prefix="/api/v1/repos", tags=["refs"])


class RefOut(BaseModel):
    name: str
    target_hash: str

    model_config = {"from_attributes": True}


class CASUpdate(BaseModel):
    old: str | None
    new: str


async def _get_repo(repo_name: str, session: AsyncSession) -> Repo:
    result = await session.execute(select(Repo).where(Repo.name == repo_name))
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="Repo not found")
    return repo


@router.get("/{repo}/refs", response_model=list[RefOut])
async def list_refs(
    repo: str,
    session: AsyncSession = Depends(get_session),
    _token: Token = Depends(verify_token),
) -> list[RefOut]:
    repo_obj = await _get_repo(repo, session)
    result = await session.execute(
        select(Ref).where(Ref.repo_id == repo_obj.id).order_by(Ref.name)
    )
    return list(result.scalars().all())


@router.get("/{repo}/refs/{ref_type}/{name}", response_model=RefOut)
async def get_ref(
    repo: str,
    ref_type: str,
    name: str,
    session: AsyncSession = Depends(get_session),
    _token: Token = Depends(verify_token),
) -> RefOut:
    repo_obj = await _get_repo(repo, session)
    full_name = f"{ref_type}/{name}"
    result = await session.execute(
        select(Ref).where(Ref.repo_id == repo_obj.id, Ref.name == full_name)
    )
    ref = result.scalar_one_or_none()
    if ref is None:
        raise HTTPException(status_code=404, detail="Ref not found")
    return ref


@router.post("/{repo}/refs/{ref_type}/{name}", response_model=RefOut)
async def cas_ref(
    repo: str,
    ref_type: str,
    name: str,
    body: CASUpdate,
    session: AsyncSession = Depends(get_session),
    _token: Token = Depends(verify_token),
) -> RefOut:
    repo_obj = await _get_repo(repo, session)
    full_name = f"{ref_type}/{name}"

    result = await session.execute(
        select(Ref).where(Ref.repo_id == repo_obj.id, Ref.name == full_name)
    )
    existing = result.scalar_one_or_none()

    if body.old is None:
        # INSERT: null old means create new ref
        if existing is not None:
            raise HTTPException(status_code=409, detail="Ref already exists; provide old hash for update")
        ref = Ref(repo_id=repo_obj.id, name=full_name, target_hash=body.new)
        session.add(ref)
        await session.commit()
        await session.refresh(ref)
        return ref
    else:
        # UPDATE with CAS check
        if existing is None:
            raise HTTPException(status_code=404, detail="Ref not found")
        if existing.target_hash != body.old:
            raise HTTPException(status_code=409, detail="CAS mismatch: current hash does not match old")
        existing.target_hash = body.new
        await session.commit()
        await session.refresh(existing)
        return existing
```

- [ ] **Step 1b:** Add refs router to `src/dit/server/app.py` (add import and `app.include_router` call after the repos router line):

```python
from dit.server.routes import refs as refs_routes
# ...
app.include_router(refs_routes.router)
```

- [ ] **Step 1c:** Create `tests/server/test_routes_refs.py`:

```python
import pytest
from httpx import AsyncClient

HASH_A = "a" * 64
HASH_B = "b" * 64


async def _create_repo(client: AsyncClient, name: str = "my-repo") -> dict:
    r = await client.post("/api/v1/repos", json={"name": name})
    assert r.status_code == 201
    return r.json()


async def test_list_refs_empty(client: AsyncClient) -> None:
    await _create_repo(client)
    response = await client.get("/api/v1/repos/my-repo/refs")
    assert response.status_code == 200
    assert response.json() == []


async def test_list_refs_repo_not_found(client: AsyncClient) -> None:
    response = await client.get("/api/v1/repos/no-such-repo/refs")
    assert response.status_code == 404


async def test_cas_insert_new_ref(client: AsyncClient) -> None:
    await _create_repo(client)
    response = await client.post(
        "/api/v1/repos/my-repo/refs/heads/main",
        json={"old": None, "new": HASH_A},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "heads/main"
    assert data["target_hash"] == HASH_A


async def test_cas_insert_duplicate_returns_409(client: AsyncClient) -> None:
    await _create_repo(client)
    await client.post("/api/v1/repos/my-repo/refs/heads/main", json={"old": None, "new": HASH_A})
    response = await client.post(
        "/api/v1/repos/my-repo/refs/heads/main",
        json={"old": None, "new": HASH_B},
    )
    assert response.status_code == 409


async def test_cas_update_matching_old(client: AsyncClient) -> None:
    await _create_repo(client)
    await client.post("/api/v1/repos/my-repo/refs/heads/main", json={"old": None, "new": HASH_A})
    response = await client.post(
        "/api/v1/repos/my-repo/refs/heads/main",
        json={"old": HASH_A, "new": HASH_B},
    )
    assert response.status_code == 200
    assert response.json()["target_hash"] == HASH_B


async def test_cas_update_mismatched_old_returns_409(client: AsyncClient) -> None:
    await _create_repo(client)
    await client.post("/api/v1/repos/my-repo/refs/heads/main", json={"old": None, "new": HASH_A})
    response = await client.post(
        "/api/v1/repos/my-repo/refs/heads/main",
        json={"old": HASH_B, "new": "c" * 64},
    )
    assert response.status_code == 409


async def test_get_ref(client: AsyncClient) -> None:
    await _create_repo(client)
    await client.post("/api/v1/repos/my-repo/refs/heads/main", json={"old": None, "new": HASH_A})
    response = await client.get("/api/v1/repos/my-repo/refs/heads/main")
    assert response.status_code == 200
    assert response.json()["target_hash"] == HASH_A


async def test_get_ref_not_found(client: AsyncClient) -> None:
    await _create_repo(client)
    response = await client.get("/api/v1/repos/my-repo/refs/heads/missing")
    assert response.status_code == 404


async def test_list_refs_after_insert(client: AsyncClient) -> None:
    await _create_repo(client)
    await client.post("/api/v1/repos/my-repo/refs/heads/main", json={"old": None, "new": HASH_A})
    await client.post("/api/v1/repos/my-repo/refs/heads/dev", json={"old": None, "new": HASH_B})
    response = await client.get("/api/v1/repos/my-repo/refs")
    assert response.status_code == 200
    names = [r["name"] for r in response.json()]
    assert "heads/dev" in names
    assert "heads/main" in names
```

- [ ] **Step 2:** Run `uv run pytest tests/server/test_routes_refs.py -v`
- [ ] **Step 3:** Commit: `feat: refs routes GET list/single + POST CAS update with 409 on mismatch`

---

## Task 10: Objects Routes

**Files:**
- Create: `src/dit/server/routes/objects.py`
- Modify: `src/dit/server/app.py` (include objects router)
- Create: `tests/server/test_routes_objects.py`

- [ ] **Step 1:** Create `src/dit/server/routes/objects.py`:

```python
import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.core.store import ObjectStore
from dit.server.auth import get_session, verify_token
from dit.server.models import Repo, Token

router = APIRouter(prefix="/api/v1/repos", tags=["objects"])


async def _get_repo(repo_name: str, session: AsyncSession) -> Repo:
    result = await session.execute(select(Repo).where(Repo.name == repo_name))
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="Repo not found")
    return repo


def _store_for_repo(request: Request, repo_name: str) -> ObjectStore:
    data_dir = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


class BatchExistsIn(BaseModel):
    obj_type: str
    hashes: list[str]


class BatchExistsOut(BaseModel):
    exists: dict[str, bool]


@router.get("/{repo}/objects/{obj_type}/{hash}", response_class=Response)
async def download_object(
    repo: str,
    obj_type: str,
    hash: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token: Token = Depends(verify_token),
) -> Response:
    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)
    data = store.read(obj_type, hash)
    if data is None:
        raise HTTPException(status_code=404, detail="Object not found")
    return Response(content=data, media_type="application/octet-stream")


@router.post("/{repo}/objects/{obj_type}/{hash}", status_code=status.HTTP_204_NO_CONTENT)
async def upload_object(
    repo: str,
    obj_type: str,
    hash: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token: Token = Depends(verify_token),
) -> None:
    await _get_repo(repo, session)
    body = await request.body()
    computed = hashlib.sha256(body).hexdigest()
    if computed != hash:
        raise HTTPException(
            status_code=400,
            detail=f"Hash mismatch: path has {hash}, body hashes to {computed}",
        )
    store = _store_for_repo(request, repo)
    store.write(obj_type, body)  # idempotent: ObjectStore.write skips if already exists


@router.post("/{repo}/objects/batch-exists", response_model=BatchExistsOut)
async def batch_exists(
    repo: str,
    body: BatchExistsIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token: Token = Depends(verify_token),
) -> BatchExistsOut:
    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)
    result = store.batch_exists(body.obj_type, body.hashes)
    return BatchExistsOut(exists=result)
```

- [ ] **Step 1b:** Add objects router to `src/dit/server/app.py`:

```python
from dit.server.routes import objects as objects_routes
# ...
app.include_router(objects_routes.router)
```

- [ ] **Step 1c:** Create `tests/server/test_routes_objects.py`:

```python
import hashlib

import pytest
from httpx import AsyncClient

PAYLOAD = b"hello world data row"
PAYLOAD_HASH = hashlib.sha256(PAYLOAD).hexdigest()


async def _create_repo(client: AsyncClient, name: str = "obj-repo") -> None:
    r = await client.post("/api/v1/repos", json={"name": name})
    assert r.status_code == 201


async def test_upload_object(client: AsyncClient) -> None:
    await _create_repo(client)
    response = await client.post(
        f"/api/v1/repos/obj-repo/objects/rows/{PAYLOAD_HASH}",
        content=PAYLOAD,
    )
    assert response.status_code == 204


async def test_upload_idempotent(client: AsyncClient) -> None:
    await _create_repo(client)
    await client.post(f"/api/v1/repos/obj-repo/objects/rows/{PAYLOAD_HASH}", content=PAYLOAD)
    response = await client.post(
        f"/api/v1/repos/obj-repo/objects/rows/{PAYLOAD_HASH}",
        content=PAYLOAD,
    )
    assert response.status_code == 204


async def test_upload_hash_mismatch_returns_400(client: AsyncClient) -> None:
    await _create_repo(client)
    wrong_hash = "0" * 64
    response = await client.post(
        f"/api/v1/repos/obj-repo/objects/rows/{wrong_hash}",
        content=PAYLOAD,
    )
    assert response.status_code == 400


async def test_download_object(client: AsyncClient) -> None:
    await _create_repo(client)
    await client.post(f"/api/v1/repos/obj-repo/objects/rows/{PAYLOAD_HASH}", content=PAYLOAD)
    response = await client.get(f"/api/v1/repos/obj-repo/objects/rows/{PAYLOAD_HASH}")
    assert response.status_code == 200
    assert response.content == PAYLOAD


async def test_download_missing_returns_404(client: AsyncClient) -> None:
    await _create_repo(client)
    response = await client.get(f"/api/v1/repos/obj-repo/objects/rows/{'0' * 64}")
    assert response.status_code == 404


async def test_batch_exists(client: AsyncClient) -> None:
    await _create_repo(client)
    await client.post(f"/api/v1/repos/obj-repo/objects/rows/{PAYLOAD_HASH}", content=PAYLOAD)
    missing_hash = "f" * 64
    response = await client.post(
        "/api/v1/repos/obj-repo/objects/batch-exists",
        json={"obj_type": "rows", "hashes": [PAYLOAD_HASH, missing_hash]},
    )
    assert response.status_code == 200
    data = response.json()["exists"]
    assert data[PAYLOAD_HASH] is True
    assert data[missing_hash] is False


async def test_upload_repo_not_found_returns_404(client: AsyncClient) -> None:
    response = await client.post(
        f"/api/v1/repos/no-such-repo/objects/rows/{PAYLOAD_HASH}",
        content=PAYLOAD,
    )
    assert response.status_code == 404
```

- [ ] **Step 2:** Run `uv run pytest tests/server/test_routes_objects.py -v`
- [ ] **Step 3:** Commit: `feat: objects routes GET download + POST upload (hash-verified, idempotent) + batch-exists`

---

## Task 11: Token Admin Routes

**Files:**
- Create: `src/dit/server/routes/tokens.py`
- Modify: `src/dit/server/app.py` (include tokens router)
- Create: `tests/server/test_routes_tokens.py`

- [ ] **Step 1:** Create `src/dit/server/routes/tokens.py`:

```python
import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, verify_token
from dit.server.models import Token

router = APIRouter(prefix="/api/v1/admin/tokens", tags=["tokens"])


def _require_admin(token: Token = Depends(verify_token)) -> Token:
    if token.permissions != "admin":
        raise HTTPException(status_code=403, detail="Admin permission required")
    return token


class TokenCreate(BaseModel):
    label: str
    permissions: str = "push"
    repo_scope: int | None = None


class TokenCreated(BaseModel):
    id: int
    label: str
    permissions: str
    token: str  # raw token — returned only on creation


class TokenRevoked(BaseModel):
    id: int
    deleted: bool


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TokenCreated)
async def create_token(
    body: TokenCreate,
    session: AsyncSession = Depends(get_session),
    _admin: Token = Depends(_require_admin),
) -> TokenCreated:
    raw = "dit_" + secrets.token_hex(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    token = Token(
        token_hash=token_hash,
        label=body.label,
        permissions=body.permissions,
        repo_scope=body.repo_scope,
    )
    session.add(token)
    await session.commit()
    await session.refresh(token)
    return TokenCreated(
        id=token.id,
        label=token.label,
        permissions=token.permissions,
        token=raw,
    )


@router.delete("/{token_id}", response_model=TokenRevoked)
async def revoke_token(
    token_id: int,
    session: AsyncSession = Depends(get_session),
    _admin: Token = Depends(_require_admin),
) -> TokenRevoked:
    result = await session.execute(select(Token).where(Token.id == token_id))
    token = result.scalar_one_or_none()
    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")
    await session.delete(token)
    await session.commit()
    return TokenRevoked(id=token_id, deleted=True)
```

- [ ] **Step 1b:** Add tokens router to `src/dit/server/app.py`:

```python
from dit.server.routes import tokens as tokens_routes
# ...
app.include_router(tokens_routes.router)
```

- [ ] **Step 1c:** Create `tests/server/test_routes_tokens.py`:

```python
import hashlib

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.models import Token


async def _seed_admin_token(session: AsyncSession) -> str:
    """Insert a real admin token into DB and return the raw value."""
    raw = "dit_test_admin_secret"
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    t = Token(token_hash=token_hash, label="seeded-admin", permissions="admin")
    session.add(t)
    await session.commit()
    return raw


async def test_create_token(client: AsyncClient) -> None:
    """conftest client fixture overrides verify_token with admin stub."""
    response = await client.post(
        "/api/v1/admin/tokens",
        json={"label": "ci-bot", "permissions": "push"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["label"] == "ci-bot"
    assert data["permissions"] == "push"
    assert data["token"].startswith("dit_")
    assert "id" in data


async def test_create_token_with_scope(client: AsyncClient) -> None:
    repo_resp = await client.post("/api/v1/repos", json={"name": "scoped-repo"})
    repo_id = repo_resp.json()["id"]
    response = await client.post(
        "/api/v1/admin/tokens",
        json={"label": "scoped-bot", "permissions": "read", "repo_scope": repo_id},
    )
    assert response.status_code == 201
    assert response.json()["permissions"] == "read"


async def test_revoke_token(client: AsyncClient, session: AsyncSession) -> None:
    create_resp = await client.post(
        "/api/v1/admin/tokens",
        json={"label": "to-revoke", "permissions": "push"},
    )
    token_id = create_resp.json()["id"]
    response = await client.delete(f"/api/v1/admin/tokens/{token_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == token_id
    assert data["deleted"] is True


async def test_revoke_missing_token_returns_404(client: AsyncClient) -> None:
    response = await client.delete("/api/v1/admin/tokens/99999")
    assert response.status_code == 404


async def test_token_raw_value_is_unique(client: AsyncClient) -> None:
    r1 = await client.post("/api/v1/admin/tokens", json={"label": "t1", "permissions": "push"})
    r2 = await client.post("/api/v1/admin/tokens", json={"label": "t2", "permissions": "push"})
    assert r1.json()["token"] != r2.json()["token"]
```

- [ ] **Step 2:** Run `uv run pytest tests/server/test_routes_tokens.py -v`
- [ ] **Step 3:** Commit: `feat: token admin routes POST create (raw token returned once) + DELETE revoke`

---

## Task 12: CLI serve

**Files:**
- Modify: `src/dit/cli/main.py` (add `serve` command with lazy import)
- Test: verify command appears in `dit --help`

- [ ] **Step 1:** Add the `serve` command to `src/dit/cli/main.py` (append before `_get_author`):

```python
@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Host to bind"),
    port: int = typer.Option(8000, help="Port to listen on"),
):
    """Start the Dit HTTP API server."""
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        typer.echo(
            "Server dependencies not installed. Run: uv sync --extra server",
            err=True,
        )
        raise typer.Exit(1)

    from dit.server.app import app as fastapi_app

    import uvicorn as _uvicorn

    _uvicorn.run(fastapi_app, host=host, port=port)
```

- [ ] **Step 2:** Run `uv run dit --help` and confirm `serve` appears in the command list.
- [ ] **Step 2b:** Run `uv run pytest` — full suite still passes (serve is not invoked in tests).
- [ ] **Step 3:** Commit: `feat: CLI serve command with lazy uvicorn import and install hint`

---

## Task 13: CLI remote

**Files:**
- Modify: `src/dit/cli/main.py` (add `remote` sub-typer with add/remove/list subcommands)
- Create: `tests/test_cli_remote.py`

- [ ] **Step 1:** Add the `remote` sub-typer to `src/dit/cli/main.py`. Add after the `serve` command (before `_get_author`):

```python
remote_app = typer.Typer(name="remote", help="Manage remote repositories.")
app.add_typer(remote_app)


@remote_app.command("add")
def remote_add(
    name: str = typer.Argument(..., help="Remote name (e.g. origin)"),
    url: str = typer.Argument(..., help="Remote URL"),
    token: str = typer.Option("", help="Auth token for this remote"),
):
    """Add a remote."""
    from dit.core.config import set_remote

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    set_remote(dot, name, url, token)
    typer.echo(f"Remote '{name}' added: {url}")


@remote_app.command("remove")
def remote_remove(
    name: str = typer.Argument(..., help="Remote name to remove"),
):
    """Remove a remote."""
    from dit.core.config import remove_remote

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    if not remove_remote(dot, name):
        typer.echo(f"fatal: No remote '{name}' found", err=True)
        raise typer.Exit(1)
    typer.echo(f"Remote '{name}' removed.")


@remote_app.command("list")
def remote_list():
    """List configured remotes."""
    from dit.core.config import load_config

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    config = load_config(dot)
    remotes = config.get("remote", {})
    if not remotes:
        typer.echo("No remotes configured.")
        return
    for rname, rcfg in remotes.items():
        typer.echo(f"{rname}\t{rcfg.get('url', '')}")
```

- [ ] **Step 1b:** Create `tests/test_cli_remote.py`:

```python
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dit.cli.main import app

runner = CliRunner()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"], catch_exceptions=False)
    assert result.exit_code == 0
    return tmp_path


def test_remote_add(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["remote", "add", "origin", "http://localhost:8000"])
    assert result.exit_code == 0
    assert "origin" in result.output


def test_remote_list_empty(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["remote", "list"])
    assert result.exit_code == 0
    assert "No remotes configured" in result.output


def test_remote_list_shows_added(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["remote", "add", "origin", "http://server:8000"])
    result = runner.invoke(app, ["remote", "list"])
    assert result.exit_code == 0
    assert "origin" in result.output
    assert "http://server:8000" in result.output


def test_remote_remove(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["remote", "add", "origin", "http://server:8000"])
    result = runner.invoke(app, ["remote", "remove", "origin"])
    assert result.exit_code == 0
    list_result = runner.invoke(app, ["remote", "list"])
    assert "No remotes configured" in list_result.output


def test_remote_remove_missing_exits_nonzero(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["remote", "remove", "no-such-remote"])
    assert result.exit_code != 0


def test_remote_add_with_token(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        ["remote", "add", "origin", "http://server:8000", "--token", "dit_abc123"],
    )
    assert result.exit_code == 0
    from dit.core.config import get_remote

    dot = tmp_path / ".dit"
    cfg = get_remote(dot, "origin")
    assert cfg is not None
    assert cfg["token"] == "dit_abc123"
    assert cfg["url"] == "http://server:8000"
```

- [ ] **Step 2:** Run `uv run pytest tests/test_cli_remote.py -v`
- [ ] **Step 3:** Commit: `feat: CLI remote add/remove/list subcommands backed by TOML config`

---

## Task 14: CLI auth

**Files:**
- Modify: `src/dit/cli/main.py` (add `auth` sub-typer with `set-token` subcommand)
- Modify: `tests/test_cli_remote.py` (add auth tests)

- [ ] **Step 1:** Add the `auth` sub-typer to `src/dit/cli/main.py` (after the `remote_app` block, before `_get_author`):

```python
auth_app = typer.Typer(name="auth", help="Manage authentication credentials.")
app.add_typer(auth_app)


@auth_app.command("set-token")
def auth_set_token(
    token: str = typer.Argument(..., help="Raw API token to store"),
    remote: str = typer.Option("origin", help="Remote name to associate the token with"),
):
    """Store an API token for a remote in .dit/config."""
    from dit.core.config import get_remote, set_remote

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    existing = get_remote(dot, remote)
    if existing is None:
        typer.echo(f"fatal: Remote '{remote}' not found. Add it first with: dit remote add", err=True)
        raise typer.Exit(1)
    set_remote(dot, remote, existing["url"], token)
    typer.echo(f"Token stored for remote '{remote}'.")
```

- [ ] **Step 1b:** Append auth tests to `tests/test_cli_remote.py`:

```python
def test_auth_set_token(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["remote", "add", "origin", "http://server:8000"])
    result = runner.invoke(app, ["auth", "set-token", "dit_newsecret123"])
    assert result.exit_code == 0
    assert "Token stored" in result.output
    from dit.core.config import get_remote

    dot = tmp_path / ".dit"
    cfg = get_remote(dot, "origin")
    assert cfg is not None
    assert cfg["token"] == "dit_newsecret123"
    assert cfg["url"] == "http://server:8000"  # URL preserved


def test_auth_set_token_no_remote_exits_nonzero(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["auth", "set-token", "dit_abc", "--remote", "no-such-remote"])
    assert result.exit_code != 0


def test_auth_set_token_custom_remote(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["remote", "add", "upstream", "http://upstream:9000"])
    result = runner.invoke(
        app, ["auth", "set-token", "dit_upstreamtok", "--remote", "upstream"]
    )
    assert result.exit_code == 0
    from dit.core.config import get_remote

    dot = tmp_path / ".dit"
    cfg = get_remote(dot, "upstream")
    assert cfg["token"] == "dit_upstreamtok"
```

- [ ] **Step 2:** Run `uv run pytest tests/test_cli_remote.py -v`
- [ ] **Step 2b:** Run `uv run pytest` — full suite passes.
- [ ] **Step 3:** Commit: `feat: CLI auth set-token command — update token for named remote in .dit/config`

---

## Task 15: RemoteClient

Create `src/dit/core/remote.py` — a synchronous httpx.Client wrapper for all server API calls.

- [ ] Create `src/dit/core/remote.py`:

```python
from __future__ import annotations

import httpx


class RemoteClient:
    """Synchronous HTTP client for the Dit server API."""

    def __init__(self, base_url: str, token: str = "", repo: str = "") -> None:
        self.client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
        )
        self.repo = repo

    def create_repo(self, name: str) -> dict:
        """Create a new repository on the server. Returns repo dict."""
        response = self.client.post("/api/v1/repos", json={"name": name})
        response.raise_for_status()
        return response.json()

    def list_repos(self) -> list[dict]:
        """List all repositories on the server."""
        response = self.client.get("/api/v1/repos")
        response.raise_for_status()
        return response.json()

    def get_ref(self, ref_type: str, name: str) -> str | None:
        """Get target hash for a ref, or None if not found."""
        response = self.client.get(f"/api/v1/repos/{self.repo}/refs/{ref_type}/{name}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()["target_hash"]

    def list_refs(self) -> list[dict]:
        """List all refs for the current repo."""
        response = self.client.get(f"/api/v1/repos/{self.repo}/refs")
        response.raise_for_status()
        return response.json()

    def cas_ref(
        self, ref_type: str, name: str, old: str | None, new: str
    ) -> bool:
        """Compare-and-swap a ref. Returns True on success, False on CAS mismatch (409)."""
        response = self.client.post(
            f"/api/v1/repos/{self.repo}/refs/{ref_type}/{name}",
            json={"old": old, "new": new},
        )
        if response.status_code == 409:
            return False
        response.raise_for_status()
        return True

    def upload_object(self, obj_type: str, hash_hex: str, data: bytes) -> None:
        """Upload a raw object to the server. Idempotent."""
        response = self.client.post(
            f"/api/v1/repos/{self.repo}/objects/{obj_type}/{hash_hex}",
            content=data,
        )
        response.raise_for_status()

    def download_object(self, obj_type: str, hash_hex: str) -> bytes | None:
        """Download a raw object. Returns None if not found."""
        response = self.client.get(
            f"/api/v1/repos/{self.repo}/objects/{obj_type}/{hash_hex}"
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    def batch_exists(self, obj_type: str, hashes: list[str]) -> dict[str, bool]:
        """Check which hashes already exist on the server."""
        response = self.client.post(
            f"/api/v1/repos/{self.repo}/objects/batch-exists",
            json={"obj_type": obj_type, "hashes": hashes},
        )
        response.raise_for_status()
        return response.json()["exists"]
```

- [ ] Create `tests/test_remote.py`:

```python
"""Tests for RemoteClient using httpx.MockTransport."""
from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from dit.core.remote import RemoteClient


def _json_response(data, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        headers={"Content-Type": "application/json"},
        content=json.dumps(data).encode(),
    )


def _make_client(handler) -> RemoteClient:
    transport = httpx.MockTransport(handler)
    rc = RemoteClient.__new__(RemoteClient)
    rc.client = httpx.Client(transport=transport, base_url="http://test")
    rc.repo = "my-repo"
    return rc


def test_create_repo() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/repos"
        body = json.loads(request.content)
        assert body["name"] == "my-repo"
        return _json_response({"id": 1, "name": "my-repo"}, 201)

    rc = _make_client(handler)
    result = rc.create_repo("my-repo")
    assert result["name"] == "my-repo"
    assert result["id"] == 1


def test_list_repos() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/repos"
        return _json_response([{"id": 1, "name": "r1"}, {"id": 2, "name": "r2"}])

    rc = _make_client(handler)
    repos = rc.list_repos()
    assert len(repos) == 2
    assert repos[0]["name"] == "r1"


def test_get_ref_found() -> None:
    hash_val = "a" * 64

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/repos/my-repo/refs/heads/main"
        return _json_response({"name": "heads/main", "target_hash": hash_val})

    rc = _make_client(handler)
    result = rc.get_ref("heads", "main")
    assert result == hash_val


def test_get_ref_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b'{"detail":"not found"}')

    rc = _make_client(handler)
    result = rc.get_ref("heads", "main")
    assert result is None


def test_list_refs() -> None:
    refs = [{"name": "heads/main", "target_hash": "a" * 64}]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/repos/my-repo/refs"
        return _json_response(refs)

    rc = _make_client(handler)
    result = rc.list_refs()
    assert result == refs


def test_cas_ref_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/repos/my-repo/refs/heads/main"
        body = json.loads(request.content)
        assert body["old"] is None
        assert body["new"] == "b" * 64
        return _json_response({"name": "heads/main", "target_hash": "b" * 64})

    rc = _make_client(handler)
    ok = rc.cas_ref("heads", "main", old=None, new="b" * 64)
    assert ok is True


def test_cas_ref_mismatch_returns_false() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, content=b'{"detail":"CAS mismatch"}')

    rc = _make_client(handler)
    ok = rc.cas_ref("heads", "main", old="a" * 64, new="b" * 64)
    assert ok is False


def test_upload_object() -> None:
    payload = b"row data"
    hash_hex = hashlib.sha256(payload).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/repos/my-repo/objects/rows/{hash_hex}"
        assert request.method == "POST"
        assert request.content == payload
        return httpx.Response(204)

    rc = _make_client(handler)
    rc.upload_object("rows", hash_hex, payload)  # no exception


def test_download_object() -> None:
    payload = b"row data"
    hash_hex = hashlib.sha256(payload).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/repos/my-repo/objects/rows/{hash_hex}"
        assert request.method == "GET"
        return httpx.Response(200, content=payload)

    rc = _make_client(handler)
    result = rc.download_object("rows", hash_hex)
    assert result == payload


def test_download_object_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b'{"detail":"not found"}')

    rc = _make_client(handler)
    result = rc.download_object("rows", "0" * 64)
    assert result is None


def test_batch_exists() -> None:
    h1, h2 = "a" * 64, "b" * 64

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["obj_type"] == "rows"
        assert set(body["hashes"]) == {h1, h2}
        return _json_response({"exists": {h1: True, h2: False}})

    rc = _make_client(handler)
    result = rc.batch_exists("rows", [h1, h2])
    assert result[h1] is True
    assert result[h2] is False
```

- [ ] Run `uv run pytest tests/test_remote.py -v` — all tests pass.
- [ ] Commit: `feat: sync RemoteClient (httpx) for server API with MockTransport tests`

---

## Task 16: Workspace Materialize

Add `materialize_file` to `src/dit/core/workspace.py` — reconstruct a JSONL file from manifest entries.

- [ ] Modify `src/dit/core/workspace.py` — append the function after `build_manifest_for_file`:

```python
import json
from pathlib import Path

from dit.core.objects import Manifest
from dit.core.store import ObjectStore


def materialize_file(
    repo_root: Path, rel_path: str, manifest: Manifest, store: ObjectStore
) -> None:
    """Reconstruct a JSONL file from manifest entries by reading rows from store.

    Reads each row's canonical bytes from the object store, deserializes to dict,
    and writes the result as a JSONL file at repo_root / rel_path.
    """
    dest = repo_root / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for entry in manifest.entries:
        data = store.read("rows", entry.row_hash)
        if data is None:
            raise KeyError(f"Row {entry.row_hash} not found in store")
        rows.append(json.loads(data))
    with open(dest, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
```

  > Note: the existing `workspace.py` does not yet import `json` or `ObjectStore` at the top — add those imports. The full updated file should look like:

```python
import json
from pathlib import Path

from dit.core.hash import canonical_json, row_hash, query_fingerprint
from dit.core.objects import Manifest, ManifestEntry
from dit.core.store import ObjectStore
from dit.utils.jsonl import read_rows


def find_jsonl_files(root: Path) -> list[Path]:
    results = []
    for p in sorted(root.rglob("*.jsonl")):
        if ".dit" in p.parts:
            continue
        results.append(p)
    return results


def build_manifest_for_file(path: Path) -> tuple[Manifest, dict[str, bytes]]:
    """Build a Manifest for a JSONL file.

    Returns (manifest, row_data) where row_data maps row_hash -> canonical bytes.
    """
    entries = []
    row_data: dict[str, bytes] = {}
    for row in read_rows(path):
        canon = canonical_json(row)
        rh = row_hash(row)
        qfp = query_fingerprint(row)
        entries.append(ManifestEntry(row_hash=rh, query_fingerprint=qfp))
        row_data[rh] = canon
    return Manifest(entries=entries), row_data


def materialize_file(
    repo_root: Path, rel_path: str, manifest: Manifest, store: ObjectStore
) -> None:
    """Reconstruct a JSONL file from manifest entries by reading rows from store.

    Reads each row's canonical bytes from the object store, deserializes to dict,
    and writes the result as a JSONL file at repo_root / rel_path.
    """
    dest = repo_root / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for entry in manifest.entries:
        data = store.read("rows", entry.row_hash)
        if data is None:
            raise KeyError(f"Row {entry.row_hash} not found in store")
        rows.append(json.loads(data))
    with open(dest, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
```

- [ ] Extend `tests/test_workspace.py` — add tests for `materialize_file`:

```python
import json
from pathlib import Path

import pytest

from dit.core.objects import serialize_manifest
from dit.core.store import ObjectStore
from dit.core.workspace import build_manifest_for_file, materialize_file
from dit.utils.jsonl import write_rows


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_rows(path, rows)


def test_materialize_roundtrip(tmp_path: Path) -> None:
    """Build manifest from file, store rows, materialize to new path, compare content."""
    rows = [
        {"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]},
        {"messages": [{"role": "user", "content": "bye"}, {"role": "assistant", "content": "bye!"}]},
    ]
    src = tmp_path / "data.jsonl"
    _write_jsonl(src, rows)

    store = ObjectStore(tmp_path / ".dit" / "objects")
    manifest, row_data = build_manifest_for_file(src)
    for rh, data in row_data.items():
        store.write("rows", data)

    dest_root = tmp_path / "clone"
    materialize_file(dest_root, "data.jsonl", manifest, store)

    dest = dest_root / "data.jsonl"
    assert dest.exists()
    materialized = [json.loads(line) for line in dest.read_text().splitlines() if line.strip()]
    assert len(materialized) == len(rows)
    # Content should be semantically equivalent (canonical JSON may differ in key order)
    for original, materialized_row in zip(rows, materialized):
        assert materialized_row == original


def test_materialize_missing_row_raises(tmp_path: Path) -> None:
    """Materializing with a missing row raises KeyError."""
    rows = [{"messages": [{"role": "user", "content": "x"}]}]
    src = tmp_path / "data.jsonl"
    _write_jsonl(src, rows)

    store = ObjectStore(tmp_path / ".dit" / "objects")
    manifest, _row_data = build_manifest_for_file(src)
    # Do NOT write rows to store — materialize should raise
    with pytest.raises(KeyError):
        materialize_file(tmp_path / "clone", "data.jsonl", manifest, store)


def test_materialize_creates_parent_dirs(tmp_path: Path) -> None:
    """materialize_file creates intermediate directories."""
    rows = [{"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]}]
    src = tmp_path / "data.jsonl"
    _write_jsonl(src, rows)

    store = ObjectStore(tmp_path / ".dit" / "objects")
    manifest, row_data = build_manifest_for_file(src)
    for rh, data in row_data.items():
        store.write("rows", data)

    materialize_file(tmp_path / "clone", "nested/deep/data.jsonl", manifest, store)
    assert (tmp_path / "clone" / "nested" / "deep" / "data.jsonl").exists()
```

- [ ] Run `uv run pytest tests/test_workspace.py -v` — all tests pass.
- [ ] Commit: `feat: workspace materialize_file — reconstruct JSONL from manifest + store`

---

## Task 17: Object Walker

Create `src/dit/core/walker.py` — walk all objects reachable from a commit hash.

- [ ] Create `src/dit/core/walker.py`:

```python
from __future__ import annotations

from dit.core.objects import deserialize_commit, deserialize_manifest, deserialize_tree
from dit.core.store import ObjectStore


def walk_commit_objects(
    store: ObjectStore, commit_hash: str
) -> dict[str, set[str]]:
    """Collect all object hashes reachable from a commit, grouped by type.

    Returns:
        {
            "commits": {hash, ...},
            "trees": {hash, ...},
            "manifests": {hash, ...},
            "rows": {hash, ...},
        }

    Walks: commit -> tree -> manifests -> rows; also walks parent commits recursively.
    Stops if a commit hash has already been visited (handles DAGs safely).
    """
    result: dict[str, set[str]] = {
        "commits": set(),
        "trees": set(),
        "manifests": set(),
        "rows": set(),
    }
    _walk_commit(store, commit_hash, result)
    return result


def _walk_commit(
    store: ObjectStore, commit_hash: str, result: dict[str, set[str]]
) -> None:
    if commit_hash in result["commits"]:
        return
    result["commits"].add(commit_hash)

    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        return
    commit = deserialize_commit(commit_data)

    _walk_tree(store, commit.tree_hash, result)

    for parent_hash in commit.parent_hashes:
        _walk_commit(store, parent_hash, result)


def _walk_tree(
    store: ObjectStore, tree_hash: str, result: dict[str, set[str]]
) -> None:
    if tree_hash in result["trees"]:
        return
    result["trees"].add(tree_hash)

    tree_data = store.read("trees", tree_hash)
    if tree_data is None:
        return
    tree = deserialize_tree(tree_data)

    for entry in tree.entries:
        if entry.obj_type == "manifest":
            _walk_manifest(store, entry.obj_hash, result)
        elif entry.obj_type == "tree":
            _walk_tree(store, entry.obj_hash, result)


def _walk_manifest(
    store: ObjectStore, manifest_hash: str, result: dict[str, set[str]]
) -> None:
    if manifest_hash in result["manifests"]:
        return
    result["manifests"].add(manifest_hash)

    manifest_data = store.read("manifests", manifest_hash)
    if manifest_data is None:
        return
    manifest = deserialize_manifest(manifest_data)

    for entry in manifest.entries:
        result["rows"].add(entry.row_hash)


def is_ancestor(
    store: ObjectStore, ancestor_hash: str, descendant_hash: str
) -> bool:
    """Check if ancestor_hash is in the parent chain of descendant_hash.

    Returns True if ancestor_hash == descendant_hash (a commit is its own ancestor).
    """
    if ancestor_hash == descendant_hash:
        return True
    visited: set[str] = set()
    return _is_ancestor_dfs(store, ancestor_hash, descendant_hash, visited)


def _is_ancestor_dfs(
    store: ObjectStore,
    ancestor_hash: str,
    current_hash: str,
    visited: set[str],
) -> bool:
    if current_hash in visited:
        return False
    visited.add(current_hash)

    commit_data = store.read("commits", current_hash)
    if commit_data is None:
        return False
    commit = deserialize_commit(commit_data)

    for parent_hash in commit.parent_hashes:
        if parent_hash == ancestor_hash:
            return True
        if _is_ancestor_dfs(store, ancestor_hash, parent_hash, visited):
            return True
    return False
```

- [ ] Create `tests/test_walker.py`:

```python
"""Tests for walk_commit_objects and is_ancestor."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from dit.core.objects import (
    Commit,
    Manifest,
    ManifestEntry,
    Tree,
    TreeEntry,
    serialize_commit,
    serialize_manifest,
    serialize_tree,
)
from dit.core.store import ObjectStore
from dit.core.walker import is_ancestor, walk_commit_objects


def _make_store(tmp_path: Path) -> ObjectStore:
    return ObjectStore(tmp_path / "objects")


def _store_manifest(store: ObjectStore, row_hashes: list[str]) -> str:
    entries = [ManifestEntry(row_hash=rh, query_fingerprint=None) for rh in row_hashes]
    manifest = Manifest(entries=entries)
    data = serialize_manifest(manifest)
    return store.write("manifests", data)


def _store_row(store: ObjectStore, content: str) -> str:
    data = content.encode("utf-8")
    return store.write("rows", data)


def _store_tree(store: ObjectStore, entries: list[TreeEntry]) -> str:
    tree = Tree(entries=entries)
    data = serialize_tree(tree)
    return store.write("trees", data)


def _store_commit(
    store: ObjectStore,
    tree_hash: str,
    parent_hashes: list[str],
    message: str = "test",
) -> str:
    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=parent_hashes,
        author="tester",
        message=message,
        timestamp=int(time.time()),
    )
    data = serialize_commit(commit)
    return store.write("commits", data)


def test_walk_single_commit(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    row_hash = _store_row(store, '{"a":1}')
    manifest_hash = _store_manifest(store, [row_hash])
    tree_hash = _store_tree(store, [TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=manifest_hash)])
    commit_hash = _store_commit(store, tree_hash, [])

    result = walk_commit_objects(store, commit_hash)

    assert commit_hash in result["commits"]
    assert tree_hash in result["trees"]
    assert manifest_hash in result["manifests"]
    assert row_hash in result["rows"]


def test_walk_two_commits_shares_history(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    row1 = _store_row(store, '{"a":1}')
    mhash1 = _store_manifest(store, [row1])
    thash1 = _store_tree(store, [TreeEntry("data.jsonl", "manifest", mhash1)])
    commit1 = _store_commit(store, thash1, [])

    row2 = _store_row(store, '{"b":2}')
    mhash2 = _store_manifest(store, [row2])
    thash2 = _store_tree(store, [TreeEntry("data.jsonl", "manifest", mhash2)])
    commit2 = _store_commit(store, thash2, [commit1])

    result = walk_commit_objects(store, commit2)

    assert result["commits"] == {commit1, commit2}
    assert thash1 in result["trees"] and thash2 in result["trees"]
    assert mhash1 in result["manifests"] and mhash2 in result["manifests"]
    assert row1 in result["rows"] and row2 in result["rows"]


def test_walk_deduplicates_shared_objects(tmp_path: Path) -> None:
    """Two commits sharing a manifest: manifest and rows appear only once each."""
    store = _make_store(tmp_path)

    row = _store_row(store, '{"shared":true}')
    mhash = _store_manifest(store, [row])
    thash1 = _store_tree(store, [TreeEntry("x.jsonl", "manifest", mhash)])
    thash2 = _store_tree(store, [TreeEntry("x.jsonl", "manifest", mhash)])
    c1 = _store_commit(store, thash1, [])
    c2 = _store_commit(store, thash2, [c1])

    result = walk_commit_objects(store, c2)
    assert len(result["manifests"]) == 1
    assert len(result["rows"]) == 1


def test_is_ancestor_linear_chain(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    thash = _store_tree(store, [])
    ca = _store_commit(store, thash, [])
    cb = _store_commit(store, thash, [ca])
    cc = _store_commit(store, thash, [cb])

    assert is_ancestor(store, ca, cc) is True
    assert is_ancestor(store, cb, cc) is True
    assert is_ancestor(store, ca, cb) is True


def test_is_ancestor_same_hash(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    thash = _store_tree(store, [])
    ca = _store_commit(store, thash, [])
    assert is_ancestor(store, ca, ca) is True


def test_is_ancestor_non_ancestor(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    thash = _store_tree(store, [])
    ca = _store_commit(store, thash, [])
    cb = _store_commit(store, thash, [ca])
    cc = _store_commit(store, thash, [ca])  # sibling of cb
    assert is_ancestor(store, cb, cc) is False
    assert is_ancestor(store, cc, cb) is False


def test_is_ancestor_descendant_not_ancestor(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    thash = _store_tree(store, [])
    ca = _store_commit(store, thash, [])
    cb = _store_commit(store, thash, [ca])
    # cb is NOT ancestor of ca
    assert is_ancestor(store, cb, ca) is False
```

- [ ] Run `uv run pytest tests/test_walker.py -v` — all tests pass.
- [ ] Commit: `feat: object walker — walk_commit_objects + is_ancestor for push/fetch`

---

## Task 18: Ancestor Check

`is_ancestor` is already implemented in Task 17 as part of `src/dit/core/walker.py`. The tests are also included in `tests/test_walker.py`.

This task is a checkpoint to confirm `is_ancestor` is complete and tested:

- [ ] Verify `is_ancestor` is exported from `src/dit/core/walker.py`.
- [ ] Run `uv run pytest tests/test_walker.py::test_is_ancestor_linear_chain tests/test_walker.py::test_is_ancestor_same_hash tests/test_walker.py::test_is_ancestor_non_ancestor tests/test_walker.py::test_is_ancestor_descendant_not_ancestor -v` — all pass.
- [ ] Commit: `test: is_ancestor — linear chain, same hash, non-ancestor, reverse cases`

---

## Task 19: CLI push

Add the `push` command to `src/dit/cli/main.py`.

**Flow:**
1. Read local HEAD commit hash.
2. Get remote ref hash via RemoteClient.get_ref.
3. If remote has commits, verify local is a descendant (is_ancestor check).
4. Walk objects reachable from local HEAD; if remote has a head, subtract objects reachable from remote head (walk since remote).
5. batch_exists to filter already-uploaded objects.
6. Upload new objects (rows first, then manifests, trees, commits — dependency order).
7. cas_ref to update remote.
8. Print result.

- [ ] Add the following helper and command to `src/dit/cli/main.py` (before `_get_author`):

```python
def _build_remote_client(dot: Path, remote_name: str = "origin") -> "RemoteClient":
    """Load remote config and construct a RemoteClient. Exits on error."""
    from dit.core.config import get_remote
    from dit.core.remote import RemoteClient

    cfg = get_remote(dot, remote_name)
    if cfg is None:
        typer.echo(f"fatal: remote '{remote_name}' not configured", err=True)
        raise typer.Exit(1)
    url: str = cfg["url"]
    token: str = cfg.get("token", "")
    # Parse repo name from URL: http://host:port/repo-name
    repo_name = url.rstrip("/").rsplit("/", 1)[-1]
    base_url = url.rstrip("/").rsplit("/", 1)[0]
    return RemoteClient(base_url=base_url, token=token, repo=repo_name)


@app.command()
def push(
    remote: str = typer.Option("origin", help="Remote name"),
    branch: str = typer.Option("main", help="Branch name to push"),
):
    """Push local commits to the remote server."""
    from dit.core.config import get_remote
    from dit.core.remote import RemoteClient
    from dit.core.walker import walk_commit_objects, is_ancestor

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    local_hash = refs.get_branch(branch)
    if local_hash is None:
        typer.echo(f"fatal: branch '{branch}' does not exist locally", err=True)
        raise typer.Exit(1)

    rc = _build_remote_client(dot, remote)

    remote_hash = rc.get_ref("heads", branch)

    if remote_hash is not None:
        if not is_ancestor(store, remote_hash, local_hash):
            typer.echo(
                "error: push rejected — local branch is not a descendant of remote.\n"
                "  Pull first: dit pull",
                err=True,
            )
            raise typer.Exit(1)

    # Walk objects reachable from local HEAD
    local_objects = walk_commit_objects(store, local_hash)

    # Subtract objects already reachable from remote HEAD to avoid re-uploading history
    if remote_hash is not None:
        remote_objects = walk_commit_objects(store, remote_hash)
        new_objects: dict[str, set[str]] = {
            obj_type: local_objects[obj_type] - remote_objects[obj_type]
            for obj_type in local_objects
        }
    else:
        new_objects = local_objects

    # Check which objects already exist on server (idempotency)
    upload_order = ["rows", "manifests", "trees", "commits"]
    to_upload: dict[str, list[str]] = {}
    for obj_type in upload_order:
        hashes = list(new_objects.get(obj_type, set()))
        if not hashes:
            to_upload[obj_type] = []
            continue
        exists = rc.batch_exists(obj_type, hashes)
        to_upload[obj_type] = [h for h in hashes if not exists.get(h, False)]

    # Upload in dependency order: rows -> manifests -> trees -> commits
    total = sum(len(v) for v in to_upload.values())
    uploaded = 0
    for obj_type in upload_order:
        for hash_hex in to_upload[obj_type]:
            data = store.read(obj_type, hash_hex)
            if data is None:
                typer.echo(f"warning: local object {obj_type}/{hash_hex} missing in store", err=True)
                continue
            rc.upload_object(obj_type, hash_hex, data)
            uploaded += 1

    # CAS update the remote ref
    ok = rc.cas_ref("heads", branch, old=remote_hash, new=local_hash)
    if not ok:
        typer.echo(
            "error: remote ref was updated by another push — pull and retry",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(f"Pushed {uploaded} new objects to {remote}/{branch} ({local_hash[:8]})")
```

- [ ] Create `tests/test_cli_push.py`:

```python
"""Integration test for `dit push` using a real FastAPI app via ASGITransport."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import httpx
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.config import set_remote


runner = CliRunner()


def _make_sync_client_from_asgi(asgi_app, token: str = "dit_admin") -> httpx.Client:
    """Return a sync httpx.Client backed by ASGITransport (no real server needed)."""
    transport = httpx.ASGITransport(app=asgi_app)
    return httpx.Client(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.fixture
def server_app(tmp_path):
    """Configure the FastAPI app to use in-memory SQLite and tmp_path for objects."""
    from unittest.mock import patch
    from dit.server.app import app as fastapi_app
    from dit.server.config import ServerSettings
    from dit.server.auth import get_session, verify_token
    from dit.server.database import create_engine, create_session_factory
    from dit.server.models import Base
    import asyncio

    settings = ServerSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        data_dir=str(tmp_path / "server"),
    )

    async def _setup():
        engine = await create_engine(settings.database_url)
        async with engine.begin() as conn:
            for table in Base.metadata.tables.values():
                table.schema = None
            await conn.run_sync(Base.metadata.create_all)
        factory = create_session_factory(engine)

        async def override_session():
            async with factory() as s:
                yield s

        from dit.server.models import Token

        def override_token():
            t = Token.__new__(Token)
            object.__setattr__(t, "id", 1)
            object.__setattr__(t, "token_hash", "x" * 64)
            object.__setattr__(t, "label", "test-admin")
            object.__setattr__(t, "permissions", "admin")
            object.__setattr__(t, "expires_at", None)
            object.__setattr__(t, "repo_scope", None)
            return t

        fastapi_app.state.data_dir = str(tmp_path / "server")
        fastapi_app.dependency_overrides[get_session] = override_session
        fastapi_app.dependency_overrides[verify_token] = override_token
        return fastapi_app, engine

    fastapi_app, engine = asyncio.get_event_loop().run_until_complete(_setup())
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()
    asyncio.get_event_loop().run_until_complete(engine.dispose())


@pytest.fixture
def local_repo(tmp_path: Path) -> Path:
    """Initialize a local dit repo with one committed JSONL file."""
    repo = tmp_path / "client"
    repo.mkdir()
    result = runner.invoke(app, ["init"], catch_exceptions=False, env={"HOME": str(tmp_path)})
    assert result.exit_code == 0, result.output

    jsonl = repo / "train.jsonl"
    jsonl.write_text(
        json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]}) + "\n"
    )

    import os
    old_cwd = os.getcwd()
    os.chdir(repo)
    try:
        r = runner.invoke(app, ["add", "train.jsonl"], catch_exceptions=False)
        assert r.exit_code == 0, r.output
        r = runner.invoke(app, ["commit", "-m", "initial"], catch_exceptions=False)
        assert r.exit_code == 0, r.output
    finally:
        os.chdir(old_cwd)

    return repo


def test_push_creates_objects_on_server(server_app, local_repo: Path, tmp_path: Path, monkeypatch) -> None:
    """Push a commit to the server and verify objects exist."""
    monkeypatch.chdir(local_repo)

    # Create repo on server
    sync_client = _make_sync_client_from_asgi(server_app)
    resp = sync_client.post("/api/v1/repos", json={"name": "train"})
    assert resp.status_code == 201

    # Configure remote in local repo — URL format: http://host/repo-name
    dot = local_repo / ".dit"
    set_remote(dot, "origin", "http://test/train", token="dit_admin")

    # Patch RemoteClient to use ASGITransport instead of real HTTP
    import dit.core.remote as remote_mod
    original_client_cls = remote_mod.RemoteClient

    class PatchedRemoteClient(original_client_cls):
        def __init__(self, base_url: str, token: str = "", repo: str = "") -> None:
            import httpx
            self.client = httpx.Client(
                transport=httpx.ASGITransport(app=server_app),
                base_url=base_url,
                headers={"Authorization": f"Bearer {token}"},
            )
            self.repo = repo

    monkeypatch.setattr(remote_mod, "RemoteClient", PatchedRemoteClient)

    result = runner.invoke(app, ["push"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "Pushed" in result.output

    # Verify remote ref was set
    resp = sync_client.get("/api/v1/repos/train/refs/heads/main")
    assert resp.status_code == 200
    assert len(resp.json()["target_hash"]) == 64


def test_push_idempotent(server_app, local_repo: Path, tmp_path: Path, monkeypatch) -> None:
    """Pushing twice should succeed both times (second push uploads 0 new objects)."""
    monkeypatch.chdir(local_repo)

    sync_client = _make_sync_client_from_asgi(server_app)
    sync_client.post("/api/v1/repos", json={"name": "train"})

    dot = local_repo / ".dit"
    set_remote(dot, "origin", "http://test/train", token="dit_admin")

    import dit.core.remote as remote_mod

    class PatchedRemoteClient(remote_mod.RemoteClient):
        def __init__(self, base_url: str, token: str = "", repo: str = "") -> None:
            import httpx
            self.client = httpx.Client(
                transport=httpx.ASGITransport(app=server_app),
                base_url=base_url,
                headers={"Authorization": f"Bearer {token}"},
            )
            self.repo = repo

    monkeypatch.setattr(remote_mod, "RemoteClient", PatchedRemoteClient)

    r1 = runner.invoke(app, ["push"], catch_exceptions=False)
    assert r1.exit_code == 0
    r2 = runner.invoke(app, ["push"], catch_exceptions=False)
    assert r2.exit_code == 0
    assert "0 new objects" in r2.output or "Pushed 0" in r2.output
```

- [ ] Run `uv run pytest tests/test_cli_push.py -v` — all tests pass.
- [ ] Commit: `feat: CLI push — upload new objects + CAS ref update with fast-forward check`

---

## Task 20: CLI clone

Add the `clone` command to `src/dit/cli/main.py`.

**Flow:**
1. Parse URL to extract server base + repo name (`http://host:port/repo-name`).
2. Get remote `heads/main` ref.
3. Download all commits (walk parent chain), trees, manifests.
4. Store in local `.dit/objects/`.
5. Set up local refs (`heads/main`), `HEAD`, remote config.
6. Download all rows referenced by manifests.
7. Materialize all JSONL files.

- [ ] Add the following to `src/dit/cli/main.py` (before `_get_author`):

```python
@app.command()
def clone(
    url: str = typer.Argument(..., help="Remote URL (http://host:port/repo-name)"),
    dest: str = typer.Argument("", help="Destination directory (default: repo name)"),
    token: str = typer.Option("", help="Auth token"),
    branch: str = typer.Option("main", help="Branch to clone"),
):
    """Clone a remote repository into a new directory."""
    from dit.core.config import set_remote
    from dit.core.remote import RemoteClient
    from dit.core.workspace import materialize_file
    from dit.core.objects import deserialize_commit, deserialize_tree, deserialize_manifest

    # Parse URL: last path segment is repo name
    clean_url = url.rstrip("/")
    repo_name = clean_url.rsplit("/", 1)[-1]
    base_url = clean_url.rsplit("/", 1)[0]

    dest_dir = Path(dest) if dest else Path.cwd() / repo_name
    if dest_dir.exists() and any(dest_dir.iterdir()):
        typer.echo(f"fatal: destination '{dest_dir}' already exists and is not empty", err=True)
        raise typer.Exit(1)
    dest_dir.mkdir(parents=True, exist_ok=True)

    dot = dest_dir / ".dit"
    dot.mkdir()
    (dot / "objects").mkdir()
    refs = RefStore(dot)
    refs.init()
    store = ObjectStore(dot / "objects")

    rc = RemoteClient(base_url=base_url, token=token, repo=repo_name)

    remote_hash = rc.get_ref("heads", branch)
    if remote_hash is None:
        typer.echo(f"fatal: remote branch '{branch}' not found", err=True)
        raise typer.Exit(1)

    typer.echo(f"Cloning {url} -> {dest_dir}")

    # Download commits (walk parent chain), trees, manifests
    commits_to_fetch: list[str] = []
    queue = [remote_hash]
    visited: set[str] = set()

    while queue:
        chash = queue.pop()
        if chash in visited:
            continue
        visited.add(chash)
        data = rc.download_object("commits", chash)
        if data is None:
            typer.echo(f"warning: commit {chash} not found on remote", err=True)
            continue
        store.write("commits", data)
        commits_to_fetch.append(chash)
        commit = deserialize_commit(data)
        queue.extend(commit.parent_hashes)

    # Download trees and manifests
    manifest_hashes: set[str] = set()
    for chash in commits_to_fetch:
        commit_data = store.read("commits", chash)
        commit = deserialize_commit(commit_data)

        tree_data = rc.download_object("trees", commit.tree_hash)
        if tree_data:
            store.write("trees", tree_data)
            tree = deserialize_tree(tree_data)
            for entry in tree.entries:
                if entry.obj_type == "manifest":
                    m_data = rc.download_object("manifests", entry.obj_hash)
                    if m_data:
                        store.write("manifests", m_data)
                        manifest_hashes.add(entry.obj_hash)

    # Download rows referenced by all manifests
    for mhash in manifest_hashes:
        m_data = store.read("manifests", mhash)
        if m_data is None:
            continue
        manifest = deserialize_manifest(m_data)
        for entry in manifest.entries:
            if not store.exists("rows", entry.row_hash):
                row_data = rc.download_object("rows", entry.row_hash)
                if row_data:
                    store.write("rows", row_data)

    # Set up refs and remote config
    refs.set_branch(branch, remote_hash)
    refs.head_file.write_text(f"ref:{branch}\n")
    set_remote(dot, "origin", url, token)

    # Materialize all JSONL files from HEAD commit
    head_commit_data = store.read("commits", remote_hash)
    head_commit = deserialize_commit(head_commit_data)
    tree_data = store.read("trees", head_commit.tree_hash)
    tree = deserialize_tree(tree_data)
    for entry in tree.entries:
        if entry.obj_type == "manifest":
            m_data = store.read("manifests", entry.obj_hash)
            manifest = deserialize_manifest(m_data)
            materialize_file(dest_dir, entry.name, manifest, store)
            typer.echo(f"  {entry.name}")

    typer.echo(f"Clone complete. {len(commits_to_fetch)} commit(s).")
```

- [ ] Create `tests/test_cli_clone.py`:

```python
"""Integration test for `dit clone` using ASGITransport."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.config import set_remote

runner = CliRunner()


@pytest.fixture
def server_app(tmp_path):
    from dit.server.app import app as fastapi_app
    from dit.server.auth import get_session, verify_token
    from dit.server.database import create_engine, create_session_factory
    from dit.server.models import Base, Token

    async def _setup():
        engine = await create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            for table in Base.metadata.tables.values():
                table.schema = None
            await conn.run_sync(Base.metadata.create_all)
        factory = create_session_factory(engine)

        async def override_session():
            async with factory() as s:
                yield s

        def override_token():
            t = Token.__new__(Token)
            object.__setattr__(t, "id", 1)
            object.__setattr__(t, "token_hash", "x" * 64)
            object.__setattr__(t, "label", "test-admin")
            object.__setattr__(t, "permissions", "admin")
            object.__setattr__(t, "expires_at", None)
            object.__setattr__(t, "repo_scope", None)
            return t

        fastapi_app.state.data_dir = str(tmp_path / "server")
        fastapi_app.dependency_overrides[get_session] = override_session
        fastapi_app.dependency_overrides[verify_token] = override_token
        return fastapi_app, engine

    fastapi_app, engine = asyncio.get_event_loop().run_until_complete(_setup())
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()
    asyncio.get_event_loop().run_until_complete(engine.dispose())


def _patch_remote_client(monkeypatch, server_app):
    import dit.core.remote as remote_mod

    class PatchedRemoteClient(remote_mod.RemoteClient):
        def __init__(self, base_url: str, token: str = "", repo: str = "") -> None:
            self.client = httpx.Client(
                transport=httpx.ASGITransport(app=server_app),
                base_url=base_url,
                headers={"Authorization": f"Bearer {token}"},
            )
            self.repo = repo

    monkeypatch.setattr(remote_mod, "RemoteClient", PatchedRemoteClient)


def _push_to_server(server_app, local_repo: Path, tmp_path: Path, monkeypatch) -> None:
    """Helper: push local_repo to server."""
    sync_client = httpx.Client(
        transport=httpx.ASGITransport(app=server_app),
        base_url="http://test",
        headers={"Authorization": "Bearer dit_admin"},
    )
    resp = sync_client.post("/api/v1/repos", json={"name": "dataset"})
    assert resp.status_code == 201

    dot = local_repo / ".dit"
    set_remote(dot, "origin", "http://test/dataset", token="dit_admin")

    monkeypatch.chdir(local_repo)
    result = runner.invoke(app, ["push"], catch_exceptions=False)
    assert result.exit_code == 0, result.output


def test_clone_creates_jsonl_files(server_app, tmp_path: Path, monkeypatch) -> None:
    """Push data to server, clone to new dir, verify JSONL files match."""
    # Set up source repo
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.chdir(src)
    runner.invoke(app, ["init"], catch_exceptions=False)

    rows = [
        {"messages": [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]},
        {"messages": [{"role": "user", "content": "q2"}, {"role": "assistant", "content": "a2"}]},
    ]
    jsonl = src / "train.jsonl"
    with open(jsonl, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    runner.invoke(app, ["add", "train.jsonl"], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", "init"], catch_exceptions=False)

    _patch_remote_client(monkeypatch, server_app)
    _push_to_server(server_app, src, tmp_path, monkeypatch)

    # Clone to new directory
    clone_dir = tmp_path / "clone"
    result = runner.invoke(
        app,
        ["clone", "http://test/dataset", str(clone_dir), "--token", "dit_admin"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "Clone complete" in result.output

    cloned_jsonl = clone_dir / "train.jsonl"
    assert cloned_jsonl.exists()
    cloned_rows = [json.loads(line) for line in cloned_jsonl.read_text().splitlines() if line.strip()]
    assert len(cloned_rows) == 2
    assert cloned_rows[0]["messages"][0]["content"] == "q1"
    assert cloned_rows[1]["messages"][0]["content"] == "q2"


def test_clone_sets_up_remote_config(server_app, tmp_path: Path, monkeypatch) -> None:
    """Cloned repo has 'origin' remote configured."""
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.chdir(src)
    runner.invoke(app, ["init"], catch_exceptions=False)

    jsonl = src / "data.jsonl"
    jsonl.write_text(json.dumps({"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]}) + "\n")
    runner.invoke(app, ["add", "data.jsonl"], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", "first"], catch_exceptions=False)

    _patch_remote_client(monkeypatch, server_app)
    _push_to_server(server_app, src, tmp_path, monkeypatch)

    clone_dir = tmp_path / "cloned"
    runner.invoke(
        app,
        ["clone", "http://test/dataset", str(clone_dir), "--token", "dit_admin"],
        catch_exceptions=False,
    )

    from dit.core.config import get_remote
    cfg = get_remote(clone_dir / ".dit", "origin")
    assert cfg is not None
    assert "dataset" in cfg["url"]
```

- [ ] Run `uv run pytest tests/test_cli_clone.py -v` — all tests pass.
- [ ] Commit: `feat: CLI clone — download commits/objects + materialize JSONL files`

---

## Task 21: CLI fetch + pull

Add `fetch` and `pull` commands to `src/dit/cli/main.py`.

**fetch**: download missing commits/trees/manifests/rows from remote (compare local vs remote ref, download the gap). Does NOT update local refs or materialize files.

**pull**: fetch + verify fast-forward + update local ref + materialize changed files. If not fast-forward, error.

- [ ] Add the following to `src/dit/cli/main.py` (before `_get_author`):

```python
def _fetch_objects_since(
    rc: "RemoteClient",
    store: ObjectStore,
    remote_hash: str,
    stop_at: str | None,
) -> tuple[int, set[str]]:
    """Download commits/trees/manifests/rows from remote_hash back to stop_at.

    Returns (count_downloaded, set_of_manifest_hashes_fetched).
    Stops walking when a commit hash matches stop_at (exclusive — stop_at itself
    is already local).
    """
    from dit.core.objects import deserialize_commit, deserialize_tree, deserialize_manifest

    downloaded = 0
    manifest_hashes: set[str] = set()
    queue = [remote_hash]
    visited: set[str] = set()

    while queue:
        chash = queue.pop()
        if chash in visited:
            continue
        if chash == stop_at:
            continue
        visited.add(chash)

        if store.exists("commits", chash):
            # Already have this commit and everything below it
            continue

        data = rc.download_object("commits", chash)
        if data is None:
            continue
        store.write("commits", data)
        downloaded += 1
        commit = deserialize_commit(data)

        # Tree
        if not store.exists("trees", commit.tree_hash):
            tree_data = rc.download_object("trees", commit.tree_hash)
            if tree_data:
                store.write("trees", tree_data)
                downloaded += 1
                tree = deserialize_tree(tree_data)
                for entry in tree.entries:
                    if entry.obj_type == "manifest" and not store.exists("manifests", entry.obj_hash):
                        m_data = rc.download_object("manifests", entry.obj_hash)
                        if m_data:
                            store.write("manifests", m_data)
                            downloaded += 1
                            manifest_hashes.add(entry.obj_hash)
                            m = deserialize_manifest(m_data)
                            for me in m.entries:
                                if not store.exists("rows", me.row_hash):
                                    row_data = rc.download_object("rows", me.row_hash)
                                    if row_data:
                                        store.write("rows", row_data)
                                        downloaded += 1

        queue.extend(commit.parent_hashes)

    return downloaded, manifest_hashes


@app.command()
def fetch(
    remote: str = typer.Option("origin", help="Remote name"),
    branch: str = typer.Option("main", help="Branch to fetch"),
):
    """Download new objects from the remote (does not update local branch)."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    rc = _build_remote_client(dot, remote)
    remote_hash = rc.get_ref("heads", branch)
    if remote_hash is None:
        typer.echo(f"  remote branch '{branch}' not found")
        return

    local_hash = refs.get_branch(branch)
    if local_hash == remote_hash:
        typer.echo("Already up to date.")
        return

    count, _ = _fetch_objects_since(rc, store, remote_hash, stop_at=local_hash)
    typer.echo(f"Fetched {count} new objects from {remote}/{branch}")


@app.command()
def pull(
    remote: str = typer.Option("origin", help="Remote name"),
    branch: str = typer.Option("main", help="Branch to pull"),
):
    """Fetch from remote + fast-forward local branch + materialize changed files."""
    from dit.core.objects import deserialize_commit, deserialize_tree, deserialize_manifest
    from dit.core.walker import is_ancestor
    from dit.core.workspace import materialize_file

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    rc = _build_remote_client(dot, remote)
    remote_hash = rc.get_ref("heads", branch)
    if remote_hash is None:
        typer.echo(f"  remote branch '{branch}' not found")
        return

    local_hash = refs.get_branch(branch)
    if local_hash == remote_hash:
        typer.echo("Already up to date.")
        return

    # Fetch missing objects
    count, _ = _fetch_objects_since(rc, store, remote_hash, stop_at=local_hash)

    # Verify fast-forward: local must be an ancestor of remote
    if local_hash is not None and not is_ancestor(store, local_hash, remote_hash):
        typer.echo(
            "error: pull would not be a fast-forward.\n"
            "  Local and remote have diverged. Resolve manually.",
            err=True,
        )
        raise typer.Exit(1)

    # Update local branch ref
    refs.set_branch(branch, remote_hash)

    # Materialize files from new HEAD
    head_commit_data = store.read("commits", remote_hash)
    head_commit = deserialize_commit(head_commit_data)
    tree_data = store.read("trees", head_commit.tree_hash)
    tree = deserialize_tree(tree_data)
    for entry in tree.entries:
        if entry.obj_type == "manifest":
            m_data = store.read("manifests", entry.obj_hash)
            manifest = deserialize_manifest(m_data)
            materialize_file(repo_root, entry.name, manifest, store)

    typer.echo(f"Pulled {count} new objects. Now at {remote_hash[:8]}.")
```

- [ ] Create `tests/test_cli_pull.py`:

```python
"""Integration tests for `dit fetch` and `dit pull` commands."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.config import set_remote

runner = CliRunner()


@pytest.fixture
def server_app(tmp_path):
    from dit.server.app import app as fastapi_app
    from dit.server.auth import get_session, verify_token
    from dit.server.database import create_engine, create_session_factory
    from dit.server.models import Base, Token

    async def _setup():
        engine = await create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            for table in Base.metadata.tables.values():
                table.schema = None
            await conn.run_sync(Base.metadata.create_all)
        factory = create_session_factory(engine)

        async def override_session():
            async with factory() as s:
                yield s

        def override_token():
            t = Token.__new__(Token)
            object.__setattr__(t, "id", 1)
            object.__setattr__(t, "token_hash", "x" * 64)
            object.__setattr__(t, "label", "test-admin")
            object.__setattr__(t, "permissions", "admin")
            object.__setattr__(t, "expires_at", None)
            object.__setattr__(t, "repo_scope", None)
            return t

        fastapi_app.state.data_dir = str(tmp_path / "server")
        fastapi_app.dependency_overrides[get_session] = override_session
        fastapi_app.dependency_overrides[verify_token] = override_token
        return fastapi_app, engine

    fastapi_app, engine = asyncio.get_event_loop().run_until_complete(_setup())
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()
    asyncio.get_event_loop().run_until_complete(engine.dispose())


def _patch_remote_client(monkeypatch, server_app):
    import dit.core.remote as remote_mod

    class PatchedRemoteClient(remote_mod.RemoteClient):
        def __init__(self, base_url: str, token: str = "", repo: str = "") -> None:
            self.client = httpx.Client(
                transport=httpx.ASGITransport(app=server_app),
                base_url=base_url,
                headers={"Authorization": f"Bearer {token}"},
            )
            self.repo = repo

    monkeypatch.setattr(remote_mod, "RemoteClient", PatchedRemoteClient)


def _init_and_commit(repo: Path, filename: str, rows: list[dict], message: str) -> None:
    import os
    old = os.getcwd()
    os.chdir(repo)
    try:
        jsonl = repo / filename
        with open(jsonl, "w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        r = runner.invoke(app, ["add", filename], catch_exceptions=False)
        assert r.exit_code == 0, r.output
        r = runner.invoke(app, ["commit", "-m", message], catch_exceptions=False)
        assert r.exit_code == 0, r.output
    finally:
        os.chdir(old)


def test_pull_updates_local_data(server_app, tmp_path: Path, monkeypatch) -> None:
    """Push v1, clone, push v2 from another client, pull from clone — verify updated data."""
    _patch_remote_client(monkeypatch, server_app)

    # Create server repo
    sync_client = httpx.Client(
        transport=httpx.ASGITransport(app=server_app),
        base_url="http://test",
        headers={"Authorization": "Bearer dit_admin"},
    )
    sync_client.post("/api/v1/repos", json={"name": "shared"})

    # Client A: init + commit v1 + push
    client_a = tmp_path / "client_a"
    client_a.mkdir()
    monkeypatch.chdir(client_a)
    runner.invoke(app, ["init"], catch_exceptions=False)
    _init_and_commit(
        client_a,
        "data.jsonl",
        [{"messages": [{"role": "user", "content": "v1"}, {"role": "assistant", "content": "r1"}]}],
        "v1",
    )
    set_remote(client_a / ".dit", "origin", "http://test/shared", token="dit_admin")
    monkeypatch.chdir(client_a)
    r = runner.invoke(app, ["push"], catch_exceptions=False)
    assert r.exit_code == 0, r.output

    # Clone to client_b
    client_b = tmp_path / "client_b"
    r = runner.invoke(
        app,
        ["clone", "http://test/shared", str(client_b), "--token", "dit_admin"],
        catch_exceptions=False,
    )
    assert r.exit_code == 0, r.output

    # Verify client_b has v1 data
    b_data = (client_b / "data.jsonl").read_text()
    assert "v1" in b_data

    # Client A: add v2 commit + push
    _init_and_commit(
        client_a,
        "data.jsonl",
        [
            {"messages": [{"role": "user", "content": "v1"}, {"role": "assistant", "content": "r1"}]},
            {"messages": [{"role": "user", "content": "v2"}, {"role": "assistant", "content": "r2"}]},
        ],
        "v2",
    )
    monkeypatch.chdir(client_a)
    r = runner.invoke(app, ["push"], catch_exceptions=False)
    assert r.exit_code == 0, r.output

    # Client B: pull
    monkeypatch.chdir(client_b)
    r = runner.invoke(app, ["pull"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert "Pulled" in r.output

    # Verify client_b now has v2 data
    b_data = (client_b / "data.jsonl").read_text()
    lines = [line for line in b_data.splitlines() if line.strip()]
    assert len(lines) == 2
    assert "v2" in b_data


def test_pull_already_up_to_date(server_app, tmp_path: Path, monkeypatch) -> None:
    """Pull with nothing new should report up-to-date."""
    _patch_remote_client(monkeypatch, server_app)

    sync_client = httpx.Client(
        transport=httpx.ASGITransport(app=server_app),
        base_url="http://test",
        headers={"Authorization": "Bearer dit_admin"},
    )
    sync_client.post("/api/v1/repos", json={"name": "stable"})

    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.chdir(src)
    runner.invoke(app, ["init"], catch_exceptions=False)
    _init_and_commit(
        src,
        "x.jsonl",
        [{"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]}],
        "init",
    )
    set_remote(src / ".dit", "origin", "http://test/stable", token="dit_admin")
    monkeypatch.chdir(src)
    runner.invoke(app, ["push"], catch_exceptions=False)

    clone_dir = tmp_path / "clone"
    runner.invoke(
        app,
        ["clone", "http://test/stable", str(clone_dir), "--token", "dit_admin"],
        catch_exceptions=False,
    )

    monkeypatch.chdir(clone_dir)
    r = runner.invoke(app, ["pull"], catch_exceptions=False)
    assert r.exit_code == 0
    assert "up to date" in r.output.lower()
```

- [ ] Run `uv run pytest tests/test_cli_pull.py -v` — all tests pass.
- [ ] Commit: `feat: CLI fetch + pull — download gap objects + fast-forward materialize`

---

## Task 22: Integration Test

Create `tests/test_integration_remote.py` — full end-to-end workflow test.

- [ ] Create `tests/test_integration_remote.py`:

```python
"""Full remote collaboration integration test.

Scenario:
  1. Start test server (ASGITransport + in-memory SQLite)
  2. Create repo on server
  3. Client A: init local, add JSONL, commit, push
  4. Client B: clone from server, verify data matches
  5. Client B: modify data, add, commit, push
  6. Client A: pull, verify updated data
  7. Verify both clients have identical working directory content
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.config import set_remote

runner = CliRunner()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def server_app(tmp_path):
    from dit.server.app import app as fastapi_app
    from dit.server.auth import get_session, verify_token
    from dit.server.database import create_engine, create_session_factory
    from dit.server.models import Base, Token

    async def _setup():
        engine = await create_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            for table in Base.metadata.tables.values():
                table.schema = None
            await conn.run_sync(Base.metadata.create_all)
        factory = create_session_factory(engine)

        async def override_session():
            async with factory() as s:
                yield s

        def override_token():
            t = Token.__new__(Token)
            object.__setattr__(t, "id", 1)
            object.__setattr__(t, "token_hash", "x" * 64)
            object.__setattr__(t, "label", "test-admin")
            object.__setattr__(t, "permissions", "admin")
            object.__setattr__(t, "expires_at", None)
            object.__setattr__(t, "repo_scope", None)
            return t

        fastapi_app.state.data_dir = str(tmp_path / "server")
        fastapi_app.dependency_overrides[get_session] = override_session
        fastapi_app.dependency_overrides[verify_token] = override_token
        return fastapi_app, engine

    fastapi_app, engine = asyncio.get_event_loop().run_until_complete(_setup())
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()
    asyncio.get_event_loop().run_until_complete(engine.dispose())


def _patch_remote_client(monkeypatch, server_app) -> None:
    import dit.core.remote as remote_mod

    class PatchedRemoteClient(remote_mod.RemoteClient):
        def __init__(self, base_url: str, token: str = "", repo: str = "") -> None:
            self.client = httpx.Client(
                transport=httpx.ASGITransport(app=server_app),
                base_url=base_url,
                headers={"Authorization": f"Bearer {token}"},
            )
            self.repo = repo

    monkeypatch.setattr(remote_mod, "RemoteClient", PatchedRemoteClient)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chdir_invoke(repo: Path, cmd: list[str]) -> None:
    old = os.getcwd()
    os.chdir(repo)
    try:
        r = runner.invoke(app, cmd, catch_exceptions=False)
        assert r.exit_code == 0, f"Command {cmd} failed:\n{r.output}"
    finally:
        os.chdir(old)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


V1_ROWS = [
    {"messages": [{"role": "user", "content": "What is 2+2?"}, {"role": "assistant", "content": "4"}]},
    {"messages": [{"role": "user", "content": "Name a color."}, {"role": "assistant", "content": "Blue"}]},
]

V2_ROWS = V1_ROWS + [
    {"messages": [{"role": "user", "content": "Capital of France?"}, {"role": "assistant", "content": "Paris"}]},
]


# ---------------------------------------------------------------------------
# Full integration test
# ---------------------------------------------------------------------------


def test_full_remote_collaboration_workflow(
    server_app, tmp_path: Path, monkeypatch
) -> None:
    """Complete push/clone/push/pull round-trip between two clients."""
    _patch_remote_client(monkeypatch, server_app)

    SERVER_REPO_URL = "http://test/sft-data"
    TOKEN = "dit_admin"

    # Step 1: Create repo on server
    sync_client = httpx.Client(
        transport=httpx.ASGITransport(app=server_app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    resp = sync_client.post("/api/v1/repos", json={"name": "sft-data"})
    assert resp.status_code == 201

    # Step 2: Client A — init + add + commit v1 + push
    client_a = tmp_path / "client_a"
    client_a.mkdir()
    _chdir_invoke(client_a, ["init"])

    _write_jsonl(client_a / "train.jsonl", V1_ROWS)
    _chdir_invoke(client_a, ["add", "train.jsonl"])
    _chdir_invoke(client_a, ["commit", "-m", "v1: initial training data"])

    set_remote(client_a / ".dit", "origin", SERVER_REPO_URL, token=TOKEN)
    _chdir_invoke(client_a, ["push"])

    # Step 3: Client B — clone + verify v1 data
    client_b = tmp_path / "client_b"
    r = runner.invoke(
        app,
        ["clone", SERVER_REPO_URL, str(client_b), "--token", TOKEN],
        catch_exceptions=False,
    )
    assert r.exit_code == 0, r.output

    b_rows_v1 = _read_jsonl(client_b / "train.jsonl")
    assert len(b_rows_v1) == 2
    assert b_rows_v1[0]["messages"][0]["content"] == "What is 2+2?"
    assert b_rows_v1[1]["messages"][0]["content"] == "Name a color."

    # Step 4: Client B — add row, commit v2, push
    _write_jsonl(client_b / "train.jsonl", V2_ROWS)
    _chdir_invoke(client_b, ["add", "train.jsonl"])
    _chdir_invoke(client_b, ["commit", "-m", "v2: add geography question"])
    _chdir_invoke(client_b, ["push"])

    # Step 5: Client A — pull + verify v2
    _chdir_invoke(client_a, ["pull"])

    a_rows_v2 = _read_jsonl(client_a / "train.jsonl")
    assert len(a_rows_v2) == 3
    assert a_rows_v2[2]["messages"][0]["content"] == "Capital of France?"

    # Step 6: Verify both clients have identical JSONL content
    b_rows_v2 = _read_jsonl(client_b / "train.jsonl")
    assert a_rows_v2 == b_rows_v2

    # Step 7: Verify server ref points to latest commit
    resp = sync_client.get("/api/v1/repos/sft-data/refs/heads/main")
    assert resp.status_code == 200
    final_hash = resp.json()["target_hash"]
    assert len(final_hash) == 64


def test_diverged_push_rejected(server_app, tmp_path: Path, monkeypatch) -> None:
    """Two clients both push from same base — second push should be rejected."""
    _patch_remote_client(monkeypatch, server_app)

    TOKEN = "dit_admin"
    sync_client = httpx.Client(
        transport=httpx.ASGITransport(app=server_app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    sync_client.post("/api/v1/repos", json={"name": "conflict-repo"})

    # Client A — push v1
    client_a = tmp_path / "a"
    client_a.mkdir()
    _chdir_invoke(client_a, ["init"])
    _write_jsonl(client_a / "data.jsonl", V1_ROWS)
    _chdir_invoke(client_a, ["add", "data.jsonl"])
    _chdir_invoke(client_a, ["commit", "-m", "v1"])
    set_remote(client_a / ".dit", "origin", "http://test/conflict-repo", token=TOKEN)
    _chdir_invoke(client_a, ["push"])

    # Client B — clone + commit something independent (diverge)
    client_b = tmp_path / "b"
    runner.invoke(app, ["clone", "http://test/conflict-repo", str(client_b), "--token", TOKEN], catch_exceptions=False)

    # Meanwhile, Client A adds another commit (advances remote)
    _write_jsonl(client_a / "data.jsonl", V2_ROWS)
    _chdir_invoke(client_a, ["add", "data.jsonl"])
    _chdir_invoke(client_a, ["commit", "-m", "v2-a"])
    _chdir_invoke(client_a, ["push"])

    # Client B also adds independent commit (not descended from A's v2)
    _write_jsonl(
        client_b / "data.jsonl",
        V1_ROWS + [{"messages": [{"role": "user", "content": "diverged"}, {"role": "assistant", "content": "yes"}]}],
    )
    _chdir_invoke(client_b, ["add", "data.jsonl"])
    _chdir_invoke(client_b, ["commit", "-m", "diverged"])

    # Client B push should fail — remote has moved past client B's base
    old = os.getcwd()
    os.chdir(client_b)
    try:
        r = runner.invoke(app, ["push"], catch_exceptions=False)
    finally:
        os.chdir(old)
    assert r.exit_code != 0
    assert "descendant" in r.output or "rejected" in r.output or "not a descendant" in r.output
```

- [ ] Run `uv run pytest tests/test_integration_remote.py -v` — all tests pass.
- [ ] Run `uv run pytest` — full suite passes.
- [ ] Commit: `test: full remote collaboration integration test — push/clone/push/pull/diverge`
