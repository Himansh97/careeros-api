from __future__ import annotations

from contextlib import contextmanager
import sqlite3
from typing import Iterator

from app import store as app_store


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    with app_store.connect() as database:
        yield database
