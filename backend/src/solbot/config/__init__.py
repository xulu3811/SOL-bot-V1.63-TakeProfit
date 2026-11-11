"""Small, explicit configuration surface for the market-data process."""

from dataclasses import dataclass
import os
from pathlib import Path

from solbot.storage.models import validate_symbol


@dataclass(frozen=True)
class Settings:
    db_path: Path | None = None
    stale_after_seconds: int = 20
    close_grace_seconds: int = 15
    symbol: str = "SOLUSDT"
    port: int | None = None

    def __post_init__(self) -> None:
        validate_symbol(self.symbol)
        if self.db_path is None:
            filename = "market.sqlite" if self.symbol == "SOLUSDT" else "sui-market.sqlite"
            object.__setattr__(self, "db_path", Path("/var/lib/sol-bot/database") / filename)
        else:
            if not str(self.db_path).strip() or str(self.db_path) == ":memory:":
                raise ValueError("SOLBOT_DB_PATH must name a persistent SQLite file")
            object.__setattr__(self, "db_path", Path(self.db_path).expanduser())
        if self.port is None:
            object.__setattr__(self, "port", 8000 if self.symbol == "SOLUSDT" else 8001)
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise ValueError("SOLBOT_PORT must be an integer between 1024 and 65535")
        if self.stale_after_seconds <= 0 or self.close_grace_seconds < 0:
            raise ValueError("invalid market health timing")

    @classmethod
    def from_env(cls) -> "Settings":
        configured_path = os.environ.get("SOLBOT_DB_PATH")
        if configured_path is not None and (not configured_path.strip() or configured_path == ":memory:"):
            raise ValueError("SOLBOT_DB_PATH must name a persistent SQLite file")
        configured_port = os.environ.get("SOLBOT_PORT")
        return cls(db_path=Path(configured_path).expanduser() if configured_path else None,
                   symbol=os.environ.get("SOLBOT_SYMBOL", "SOLUSDT"),
                   port=int(configured_port) if configured_port is not None else None)
