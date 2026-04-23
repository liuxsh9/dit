from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, String, DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Repo(Base):
    __tablename__ = "repos"
    __table_args__ = {"schema": "datahub"}

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"Repo(id={self.id}, name={self.name!r})"


class Ref(Base):
    __tablename__ = "refs"
    __table_args__ = {"schema": "datahub"}

    repo_id: Mapped[int] = mapped_column(ForeignKey("datahub.repos.id"), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), primary_key=True)
    target_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"Ref(repo_id={self.repo_id}, name={self.name!r}, target_hash={self.target_hash[:8]}...)"


class Token(Base):
    __tablename__ = "tokens"
    __table_args__ = {"schema": "datahub"}

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    repo_scope: Mapped[Optional[int]] = mapped_column(ForeignKey("datahub.repos.id"), nullable=True)
    permissions: Mapped[str] = mapped_column(String(32), nullable=False, default="push")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"Token(id={self.id}, label={self.label!r}, permissions={self.permissions!r})"


class Webhook(Base):
    __tablename__ = "webhooks"
    __table_args__ = {"schema": "datahub"}

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("datahub.repos.id"), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    secret: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    events: Mapped[str] = mapped_column(String(256), nullable=False)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
