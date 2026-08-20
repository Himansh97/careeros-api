"""Load migration files and bind their content to stable checksums."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

_VERSIONS = Path(__file__).resolve().parent / "versions"


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str

    @classmethod
    def build(cls, version: int, name: str, sql: str) -> "Migration":
        return cls(version, name, sql, sha256(sql.encode("utf-8")).hexdigest())


def load_migrations() -> tuple[Migration, ...]:
    migrations: list[Migration] = []
    for path in sorted(_VERSIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        version_text, name = path.stem.split("_", 1)
        migrations.append(
            Migration.build(int(version_text), name, path.read_text(encoding="utf-8"))
        )
    return tuple(migrations)
