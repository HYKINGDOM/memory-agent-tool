from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class MemoryState(StrEnum):
    SESSION_ONLY = "session_only"
    MEMORY_CANDIDATE = "memory_candidate"
    CONFLICT_CANDIDATE = "conflict_candidate"
    PINNED_ACTIVE = "pinned_active"
    DEGRADED = "degraded"
    PROMOTED_TO_SKILL = "promoted_to_skill"


class DurabilityLevel(StrEnum):
    TRANSIENT = "transient"
    SESSION_RELEVANT = "session_relevant"
    PROJECT_DURABLE = "project_durable"
    SKILL_CANDIDATE = "skill_candidate"


class ConflictState(StrEnum):
    NONE = "none"
    SUSPECTED = "suspected"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"


class RuleOverlapState(StrEnum):
    NONE = "none"
    OVERLAPS_AGENTS = "overlaps_agents"
    OVERLAPS_CHECKED_IN_INSTRUCTION = "overlaps_checked_in_instruction"


class PromotionState(StrEnum):
    NONE = "none"
    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    PROMOTED = "promoted"
    RETIRED = "retired"


class ProjectContext(BaseModel):
    repo_identity: str
    workspace: str | None = None
    branch: str | None = None
    namespace: str | None = None
    monorepo_subpath: str | None = None
    tool_name: str = "unknown"
    working_directory: str | None = None
    client_type: str | None = None
    client_session_id: str | None = None


class ResolvedProject(BaseModel):
    project_key: str
    project_scope_metadata: dict[str, Any]


class SessionStartRequest(BaseModel):
    project: ProjectContext
    source_channel: str = "local"


class SessionStartResponse(BaseModel):
    session_id: str
    resolved_project: ResolvedProject


class SessionEndResponse(BaseModel):
    session_id: str
    project_key: str
    status: str
    focused_summary: str
    extracted_memory_ids: list[int] = Field(default_factory=list)


class SessionEvent(BaseModel):
    event_id: str | None = None
    event_kind: str | None = None
    role_or_event_type: str
    content: str
    normalized_summary: str | None = None
    capture_eligible: bool = True
    recalled_from_memory: bool = False
    source_tool: str | None = None
    memory_type: str = "fact"
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestedMemory(BaseModel):
    memory_id: int
    state: MemoryState
    durability_level: DurabilityLevel
    trust_score: float
    conflict_state: ConflictState
    rule_overlap_state: RuleOverlapState
    summary: str
    promoted: bool = False
    details: str


class MemoryIngestRequest(BaseModel):
    project: ProjectContext
    content: str
    memory_type: str = "fact"
    title: str | None = None
    source_session_id: str | None = None
    source_message_id: int | None = None
    recalled_from_memory: bool = False


class MemoryRecallRequest(BaseModel):
    project: ProjectContext
    query: str
    limit: int = Field(default=3, ge=1, le=10)


class FeedbackRequest(BaseModel):
    memory_id: int
    helpful: bool


class SkillPromotionRequest(BaseModel):
    project: ProjectContext
    memory_id: int | None = None
    min_positive_feedback: int = Field(default=2, ge=1)


class RuleSummary(BaseModel):
    path: str
    content: str
    summary: str


class SessionSummary(BaseModel):
    session_id: str
    source_tool: str
    summary: str
    matched_messages: list[str]


class SkillSummary(BaseModel):
    skill_id: int
    name: str
    content: str
    file_path: str
    status: str = "active"
    version: int = 1
    feedback_positive_count: int = 0
    feedback_negative_count: int = 0
    source_memory_count: int = 0
    source_memory_ids: list[int] = Field(default_factory=list)
    last_used_at: datetime | None = None
    last_refreshed_at: datetime | None = None
    relevance_score: float = 0.0
    rationale: str | None = None


class RecallCandidate(BaseModel):
    source: str
    source_id: str
    score: float
    title: str
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


class ContextBundle(BaseModel):
    rules_summary: list[RuleSummary]
    fixed_memory_summary: list[IngestedMemory]
    related_session_summaries: list[SessionSummary]
    recommended_skills: list[SkillSummary]
    provider_context: list[str]
    conflict_hints: list[dict[str, Any]] = Field(default_factory=list)
    source_trace: list[dict[str, Any]] = Field(default_factory=list)
    combined_text: str


class HealthResponse(BaseModel):
    status: str
    database_writable: bool
    schema_version: int


class StatusReport(BaseModel):
    service_health: str
    schema_version: int
    generated_at: datetime
    stats: dict[str, int]
    recent_recall_hits: list[dict[str, Any]]
    recent_conflicts: list[dict[str, Any]]
    recent_degraded: list[dict[str, Any]]
    stale_memory_count: int = 0
    review_candidate_count: int = 0
    recent_consolidated_count: int = 0
    provider_observability: dict[str, Any] = Field(default_factory=dict)
    skill_observability: dict[str, Any] = Field(default_factory=dict)
    project_scope_observability: dict[str, Any] = Field(default_factory=dict)
    recent_e2e_result: dict[str, Any] | None = None
    recent_client_acceptance_result: dict[str, Any] | None = None


class ProviderStatusModel(BaseModel):
    provider_name: str
    status: str
    capabilities: dict[str, Any]
    last_error: str | None = None


class ConflictRecord(BaseModel):
    conflict_id: int
    project_key: str
    existing_memory_id: int
    candidate_memory_id: int
    resolution: str
    reason: str


class SessionSummaryRecord(BaseModel):
    summary_id: int
    project_key: str
    session_id: str
    summary: str


class SkillFeedbackRequest(BaseModel):
    helpful: bool
    accepted: bool = False


class ProjectAliasRequest(BaseModel):
    alias_key: str
    canonical_project_key: str


class ConflictResolutionResult(BaseModel):
    decision: str
    resolution: str


class AppendEventResult(BaseModel):
    message_id: int
    project_key: str
    ingested_memory: IngestedMemory | None = None


class SkillFeedbackResult(BaseModel):
    skill_id: int
    feedback_positive_count: int
    feedback_negative_count: int
    status: str
    accepted: bool


class AliasSummaryResult(BaseModel):
    alias_count: int
    recent_aliases: list[dict[str, Any]]
    recent_alias_usage: list[dict[str, Any]]


class StaleReviewResult(BaseModel):
    project_key: str
    review_candidates: int
    degraded: int


class ConsolidationResult(BaseModel):
    project_key: str
    consolidated: int


class RebuildResult(BaseModel):
    project_key: str | None
    rebuilt: int


class ObservabilitySummaryResult(BaseModel):
    total_skills: int
    candidate_refresh_count: int
    refreshed_skill_count: int
    latest_refresh: dict[str, Any] | None = None
