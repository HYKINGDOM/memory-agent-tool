from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class TrustConfig:
    positive_delta: float = 0.15
    negative_delta: float = -0.20
    initial_trust: float = 0.6
    auto_promote_threshold: float = 0.75
    degrade_threshold: float = 0.4
    low_trust_threshold: float = 0.2
    stale_freshness_threshold: float = 0.2
    stale_trust_threshold: float = 0.55
    min_positive_feedback: int = 2
    min_negative_for_refresh: int = 2
    supersede_trust_gap: float = 0.1
    keep_existing_trust_gap: float = 0.15

    @classmethod
    def from_env(cls) -> "TrustConfig":
        return cls(
            positive_delta=float(os.environ.get("MEMORY_TRUST_POSITIVE_DELTA", "0.15")),
            negative_delta=float(os.environ.get("MEMORY_TRUST_NEGATIVE_DELTA", "-0.20")),
            initial_trust=float(os.environ.get("MEMORY_TRUST_INITIAL", "0.6")),
            auto_promote_threshold=float(os.environ.get("MEMORY_TRUST_AUTO_PROMOTE", "0.75")),
            degrade_threshold=float(os.environ.get("MEMORY_TRUST_DEGRADE", "0.4")),
            low_trust_threshold=float(os.environ.get("MEMORY_TRUST_LOW", "0.2")),
            stale_freshness_threshold=float(os.environ.get("MEMORY_STALE_FRESHNESS", "0.2")),
            stale_trust_threshold=float(os.environ.get("MEMORY_STALE_TRUST", "0.55")),
            min_positive_feedback=int(os.environ.get("MEMORY_MIN_POSITIVE_FEEDBACK", "2")),
            min_negative_for_refresh=int(os.environ.get("MEMORY_MIN_NEGATIVE_REFRESH", "2")),
            supersede_trust_gap=float(os.environ.get("MEMORY_SUPERSEDE_TRUST_GAP", "0.1")),
            keep_existing_trust_gap=float(os.environ.get("MEMORY_KEEP_EXISTING_TRUST_GAP", "0.15")),
        )


@dataclass(slots=True)
class ScoringConfig:
    strategy: str = "composite"
    text_weight: float = 0.55
    trust_weight: float = 0.25
    freshness_weight: float = 0.15
    state_bonus: float = 0.2
    state_penalty: float = -0.3
    conflict_penalties: dict[str, float] = field(default_factory=lambda: {
        "none": 0.0,
        "confirmed": -0.1,
        "suspected": -0.3,
        "superseded": -0.7,
    })
    default_conflict_penalty: float = -0.2
    min_score_threshold: float = 0.05

    @classmethod
    def from_env(cls) -> "ScoringConfig":
        return cls(
            strategy=os.environ.get("MEMORY_SCORING_STRATEGY", "composite"),
            text_weight=float(os.environ.get("MEMORY_SCORING_TEXT_WEIGHT", "0.55")),
            trust_weight=float(os.environ.get("MEMORY_SCORING_TRUST_WEIGHT", "0.25")),
            freshness_weight=float(os.environ.get("MEMORY_SCORING_FRESHNESS_WEIGHT", "0.15")),
        )


_DEFAULT_DURABLE_MARKERS = (
    "project",
    "repository",
    "always",
    "use ",
    "run ",
    "avoid",
    "build",
    "test",
    "backend",
    "framework",
    "command",
    "convention",
    "procedure",
    "fixed",
)

_DEFAULT_TRANSIENT_MARKERS = (
    "traceback",
    "debug",
    "stack trace",
)


@dataclass(slots=True)
class DurabilityConfig:
    durable_markers: tuple[str, ...] = _DEFAULT_DURABLE_MARKERS
    transient_markers: tuple[str, ...] = _DEFAULT_TRANSIENT_MARKERS
    durable_content_length_threshold: int = 500

    @classmethod
    def from_env(cls) -> "DurabilityConfig":
        durable_raw = os.environ.get("MEMORY_DURABLE_MARKERS", "")
        transient_raw = os.environ.get("MEMORY_TRANSIENT_MARKERS", "")
        return cls(
            durable_markers=tuple(m.strip() for m in durable_raw.split(",") if m.strip()) or _DEFAULT_DURABLE_MARKERS,
            transient_markers=tuple(m.strip() for m in transient_raw.split(",") if m.strip()) or _DEFAULT_TRANSIENT_MARKERS,
        )


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
    trust: TrustConfig = field(default_factory=TrustConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    durability: DurabilityConfig = field(default_factory=DurabilityConfig)
    log_level: str = "INFO"
    log_json: bool = False

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
            trust=TrustConfig.from_env(),
            scoring=ScoringConfig.from_env(),
            durability=DurabilityConfig.from_env(),
            log_level=os.environ.get("MEMORY_AGENT_TOOL_LOG_LEVEL", "INFO"),
            log_json=os.environ.get("MEMORY_AGENT_TOOL_LOG_JSON", "").lower() in {"1", "true", "yes"},
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
