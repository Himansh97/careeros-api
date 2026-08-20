from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable

from app.config import CAREEROS_DIR


DATASET_ROOT = Path(CAREEROS_DIR) / "technical-learning" / "datasets"


def _connect_for_build(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA page_size=4096")
    connection.execute("PRAGMA journal_mode=DELETE")
    return connection


def _build_commerce(path: Path) -> None:
    connection = _connect_for_build(path)
    connection.executescript("""
        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY,
            segment TEXT NOT NULL,
            region TEXT NOT NULL,
            signup_date TEXT NOT NULL
        );
        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            order_date TEXT NOT NULL,
            revenue REAL NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE experiments (
            visitor_id INTEGER PRIMARY KEY,
            variant TEXT NOT NULL,
            converted INTEGER NOT NULL,
            revenue REAL NOT NULL
        );
    """)
    customers = [
        (index, ("self-serve", "mid-market", "enterprise")[(index - 1) % 3],
         ("north", "south", "east", "west")[(index - 1) % 4],
         f"2026-{((index - 1) % 6) + 1:02d}-{((index * 2) % 27) + 1:02d}")
        for index in range(1, 13)
    ]
    orders = []
    for order_id in range(1, 37):
        customer_id = ((order_id * 5) % 12) + 1
        orders.append((
            order_id,
            customer_id,
            f"2026-{((order_id - 1) % 6) + 1:02d}-{((order_id * 3) % 27) + 1:02d}",
            float(45 + (order_id * 37) % 460),
            ("paid", "paid", "refunded", "pending")[order_id % 4],
        ))
    experiments = [
        (index, "control" if index <= 20 else "treatment", 1 if index % 4 == 0 else 0,
         float(60 + index * 3) if index % 4 == 0 else 0.0)
        for index in range(1, 41)
    ]
    connection.executemany("INSERT INTO customers VALUES (?,?,?,?)", customers)
    connection.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", orders)
    connection.executemany("INSERT INTO experiments VALUES (?,?,?,?)", experiments)
    connection.commit()
    connection.execute("VACUUM")
    connection.close()


def _build_lending(path: Path) -> None:
    connection = _connect_for_build(path)
    connection.executescript("""
        CREATE TABLE borrowers (
            borrower_id INTEGER PRIMARY KEY,
            fico_band TEXT NOT NULL,
            state_code TEXT NOT NULL,
            first_time_buyer INTEGER NOT NULL
        );
        CREATE TABLE loans (
            loan_id INTEGER PRIMARY KEY,
            borrower_id INTEGER NOT NULL,
            product TEXT NOT NULL,
            principal REAL NOT NULL,
            originated_at TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE payments (
            payment_id INTEGER PRIMARY KEY,
            loan_id INTEGER NOT NULL,
            due_at TEXT NOT NULL,
            paid_at TEXT,
            amount REAL NOT NULL
        );
    """)
    borrowers = [
        (i, ("580-639", "640-699", "700-759", "760+")[(i - 1) % 4],
         ("TX", "CA", "IL", "NY", "FL")[(i - 1) % 5], i % 3 == 0)
        for i in range(1, 17)
    ]
    loans = [
        (i, ((i * 7) % 16) + 1, ("conventional", "fha", "va")[i % 3],
         float(120000 + i * 17500), f"2025-{((i - 1) % 12) + 1:02d}-01",
         ("current", "current", "delinquent", "paid-off")[i % 4])
        for i in range(1, 25)
    ]
    payments = [
        (i, ((i - 1) % 24) + 1, f"2026-{((i - 1) % 8) + 1:02d}-01",
         None if i % 7 == 0 else f"2026-{((i - 1) % 8) + 1:02d}-{3 + (i % 5):02d}",
         float(900 + (i % 11) * 85))
        for i in range(1, 73)
    ]
    connection.executemany("INSERT INTO borrowers VALUES (?,?,?,?)", borrowers)
    connection.executemany("INSERT INTO loans VALUES (?,?,?,?,?,?)", loans)
    connection.executemany("INSERT INTO payments VALUES (?,?,?,?,?)", payments)
    connection.commit()
    connection.execute("VACUUM")
    connection.close()


_BUILDERS: dict[tuple[str, str], Callable[[Path], None]] = {
    ("commerce", "1"): _build_commerce,
    ("lending", "1"): _build_lending,
}


def ensure_dataset(dataset_id: str, version: str) -> Path:
    matching_versions = {v for dataset, v in _BUILDERS if dataset == dataset_id}
    if not matching_versions:
        raise KeyError(f"unknown dataset: {dataset_id}")
    if version not in matching_versions:
        raise KeyError(f"unknown dataset version: {dataset_id}@{version}")
    DATASET_ROOT.mkdir(parents=True, exist_ok=True)
    path = DATASET_ROOT / f"{dataset_id}-v{version}.sqlite3"
    if not path.exists():
        _BUILDERS[(dataset_id, version)](path)
    return path


def dataset_schema(dataset_id: str, version: str) -> list[dict[str, object]]:
    path = ensure_dataset(dataset_id, version)
    connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        return [
            {
                "table": table,
                "columns": [
                    {"name": row[1], "type": row[2] or "TEXT"}
                    for row in connection.execute(f'PRAGMA table_info("{table}")')
                ],
                "rows": connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0],
            }
            for table in tables
        ]
    finally:
        connection.close()
