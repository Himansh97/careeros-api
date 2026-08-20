from __future__ import annotations

import re


class QueryRefused(ValueError):
    """A statement is outside the lab's read-only SQL surface."""


_FORBIDDEN = re.compile(
    r"\b(attach|detach|pragma|insert|update|delete|drop|alter|create|replace|"
    r"vacuum|reindex|analyze|begin|commit|rollback|savepoint|load_extension)\b",
    re.IGNORECASE,
)


def _strip_comments(sql: str) -> str:
    without_blocks = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", without_blocks)


def guard_sql(sql: str) -> str:
    if not (sql or "").strip():
        raise QueryRefused("Write a query first.")
    statement = _strip_comments(sql).strip()
    if statement.endswith(";"):
        statement = statement[:-1].strip()
    if not statement:
        raise QueryRefused("The query contains only comments.")
    if ";" in statement:
        raise QueryRefused("Run one statement at a time.")
    if not re.match(r"^(select|with)\b", statement, re.IGNORECASE):
        raise QueryRefused("Only SELECT and WITH queries are supported.")
    forbidden = _FORBIDDEN.search(statement)
    if forbidden:
        raise QueryRefused(f"{forbidden.group(1).upper()} is not allowed.")
    return statement
