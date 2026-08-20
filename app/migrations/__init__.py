"""Immutable, numbered CareerOS database migrations."""

from .registry import Migration, load_migrations

__all__ = ["Migration", "load_migrations"]
