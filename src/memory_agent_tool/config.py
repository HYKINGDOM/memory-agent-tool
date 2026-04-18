from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AppSettings:
    root_dir: Path
    data_dir: Path
    runtime_dir: Path
    skills_dir: Path
    db_path: Path
    host: str = "127.0.0.1"
    port: int = 8765
    pinned_memory_char_budget: int = 2400

    @classmethod
    def from_env(cls, cwd: Path | None = None) -> "AppSettings":
        root = Path(
            os.environ.get("MEMORY_AGENT_TOOL_HOME") or cwd or Path.cwd()
        ).resolve()
        data_dir = root / ".memory-agent-tool"
        runtime_dir = data_dir / "runtime"
        skills_dir = data_dir / "skills"
        db_path = data_dir / "state.db"
        return cls(
            root_dir=root,
            data_dir=data_dir,
            runtime_dir=runtime_dir,
            skills_dir=skills_dir,
            db_path=db_path,
            host=os.environ.get("MEMORY_AGENT_TOOL_HOST", "127.0.0.1"),
            port=int(os.environ.get("MEMORY_AGENT_TOOL_PORT", "8765")),
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
