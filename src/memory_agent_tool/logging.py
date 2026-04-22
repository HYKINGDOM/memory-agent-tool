from __future__ import annotations

import logging
import sys
from typing import Any


LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str | int = logging.INFO, *, json_mode: bool = False) -> None:
    root = logging.getLogger("memory_agent_tool")
    if root.handlers:
        return
    root.setLevel(level if isinstance(level, int) else getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter() if json_mode else _TextFormatter())
    root.addHandler(handler)


class _TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(fmt=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    def format(self, record: logging.LogRecord) -> str:
        if hasattr(record, "structured_data"):
            base = super().format(record)
            pairs = " ".join(f"{k}={v}" for k, v in record.structured_data.items())
            return f"{base} | {pairs}" if pairs else base
        return super().format(record)


class _JsonFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(datefmt=LOG_DATE_FORMAT)

    def format(self, record: logging.LogRecord) -> str:
        import json as _json

        payload: dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "structured_data"):
            payload["data"] = record.structured_data
        if record.exc_info and record.exc_info[1] is not None:
            payload["error"] = str(record.exc_info[1])
        return _json.dumps(payload, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"memory_agent_tool.{name}")


def log_structured(logger: logging.Logger, level: int, msg: str, **data: Any) -> None:
    record = logger.makeRecord(
        name=logger.name,
        level=level,
        fn="",
        lno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )
    record.structured_data = data  # type: ignore[attr-defined]
    logger.handle(record)
