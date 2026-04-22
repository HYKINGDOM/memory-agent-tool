from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from memory_agent_tool.config import TrustConfig
from memory_agent_tool.logging import get_logger
from memory_agent_tool.scoring import normalize_text as _normalize_text

logger = get_logger("providers")


def _summarize_text(value: str, limit: int = 180) -> str:
    collapsed = " ".join((value or "").split())
    return collapsed[:limit]


def _sanitize_context(text: str) -> str:
    return re.sub(r"</?\s*memory-context\s*>", "", text or "", flags=re.IGNORECASE)


@dataclass(slots=True)
class ProviderStatus:
    provider_name: str
    status: str
    capabilities: dict[str, Any]
    last_error: str | None = None


class ProjectMemoryProvider(ABC):
    def __init__(self) -> None:
        self.db = None

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier."""

    @abstractmethod
    def is_available(self) -> bool:
        """Whether the provider can run in the current environment."""

    @abstractmethod
    def initialize(self, **kwargs: Any) -> None:
        """Initialize provider runtime state."""

    def prefetch(self, project_key: str, query: str) -> list[str]:
        return []

    def sync_turn(self, project_key: str, payload: dict[str, Any]) -> None:
        """Mirror event writes to the provider."""

    def on_memory_write(self, project_key: str, payload: dict[str, Any]) -> None:
        """React to memory writes."""

    def on_session_end(self, project_key: str, session_id: str) -> dict[str, Any] | None:
        """Receive end-of-session hook."""
        return None

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return []

    def capabilities(self) -> dict[str, Any]:
        return {}

    def save_config(self, values: dict[str, Any]) -> None:
        """Persist provider config if needed."""

    def load_config(self) -> dict[str, Any]:
        return {}

    def bind_runtime(self, db) -> None:
        self.db = db


class _ProviderLiteBase(ProjectMemoryProvider):
    provider_kind = "provider_lite"

    def initialize(self, **kwargs: Any) -> None:
        self._metadata = kwargs
        self.bind_runtime(kwargs["db"])

    def _record_event(
        self,
        project_key: str,
        event_type: str,
        payload: dict[str, Any],
        session_id: str | None = None,
    ) -> None:
        assert self.db is not None
        self.db.execute(
            """
            INSERT INTO provider_events(provider_name, project_key, session_id, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                self.name,
                project_key,
                session_id,
                event_type,
                json.dumps(payload, ensure_ascii=False),
                self.db.now(),
            ),
        )


class LocalBuiltinProvider(_ProviderLiteBase):
    @property
    def name(self) -> str:
        return "local_builtin"

    def is_available(self) -> bool:
        return True

    def capabilities(self) -> dict[str, Any]:
        return {"bounded_memory": True, "session_archive": True}


class HolographicLikeAdapter(_ProviderLiteBase):
    def __init__(self, trust: TrustConfig | None = None) -> None:
        super().__init__()
        self.trust = trust or TrustConfig()

    @property
    def name(self) -> str:
        return "holographic_like"

    def is_available(self) -> bool:
        return True

    def capabilities(self) -> dict[str, Any]:
        return {
            "conflict_detection": True,
            "trust_scoring": True,
            "feedback": True,
            "provider_lite": True,
        }

    def sync_turn(self, project_key: str, payload: dict[str, Any]) -> None:
        self._record_event(project_key, "sync_turn", payload, payload.get("session_id"))

    def on_memory_write(self, project_key: str, payload: dict[str, Any]) -> None:
        self._record_event(project_key, "memory_write", payload, payload.get("source_session_id"))

    def prefetch(self, project_key: str, query: str) -> list[str]:
        assert self.db is not None
        normalized_query = _normalize_text(query)
        if not normalized_query:
            return []
        rows = self.db.fetchall(
            """
            SELECT memory_id, summary, trust_score, conflict_state
            FROM memory_items
            WHERE project_key = ?
              AND conflict_state IN ('suspected', 'confirmed')
            ORDER BY updated_at DESC
            LIMIT 5
            """,
            (project_key,),
        )
        results = []
        for row in rows:
            haystack = _normalize_text(row["summary"])
            if any(token in haystack for token in normalized_query.split()):
                results.append(
                    "Conflict watch: "
                    f"memory #{row['memory_id']} trust={row['trust_score']:.2f} "
                    f"state={row['conflict_state']} {row['summary']}"
                )
        if results:
            self._record_event(project_key, "prefetch", {"query": query, "results": results[:3]})
        return results[:3]

    def check_conflict(
        self,
        *,
        current_trust: float,
        candidate_trust: float,
        existing_updated_at: float,
        candidate_updated_at: float,
    ) -> str:
        if candidate_trust >= current_trust + self.trust.supersede_trust_gap:
            return "supersede"
        if candidate_updated_at > existing_updated_at and candidate_trust >= current_trust:
            return "supersede"
        if current_trust >= candidate_trust + self.trust.keep_existing_trust_gap:
            return "keep_existing"
        return "suspected"

    def adjust_trust(self, helpful: bool, current: float) -> float:
        delta = self.trust.positive_delta if helpful else self.trust.negative_delta
        return max(0.0, min(1.0, round(current + delta, 2)))

    def on_session_end(self, project_key: str, session_id: str) -> dict[str, Any] | None:
        assert self.db is not None
        row = self.db.fetchone(
            """
            SELECT COUNT(*) AS conflict_count
            FROM memory_conflicts
            WHERE project_key = ?
              AND resolution IN ('suspected', 'confirmed')
            """,
            (project_key,),
        )
        payload = {"session_id": session_id, "open_conflicts": int(row["conflict_count"]) if row else 0}
        self._record_event(project_key, "session_end", payload, session_id)
        return payload


class SupermemoryLikeAdapter(_ProviderLiteBase):
    @property
    def name(self) -> str:
        return "supermemory_like"

    def is_available(self) -> bool:
        return True

    def capabilities(self) -> dict[str, Any]:
        return {
            "container_recall": True,
            "context_fencing": True,
            "trivial_filtering": True,
            "provider_lite": True,
        }

    def _scope_for(self, project_key: str) -> dict[str, Any]:
        assert self.db is not None
        row = self.db.fetchone(
            """
            SELECT repo_identity, namespace, workspace, branch, monorepo_subpath, scope_components_json
            FROM projects
            WHERE project_key = ?
            """,
            (project_key,),
        )
        if row is None:
            return {"container_tag": project_key}
        scope_components = json.loads(row["scope_components_json"] or "[]")
        container_parts = [component for component in scope_components if component]
        return {
            "container_tag": "::".join(container_parts) or project_key,
            "repo_identity": row["repo_identity"],
            "namespace": row["namespace"],
            "workspace": row["workspace"],
            "branch": row["branch"],
            "monorepo_subpath": row["monorepo_subpath"],
        }

    def _is_trivial(self, content: str) -> bool:
        normalized = _normalize_text(content)
        return normalized in {"ok", "thanks", "thank you", "got it", "done"} or len(normalized) < 10

    def sync_turn(self, project_key: str, payload: dict[str, Any]) -> None:
        if self._is_trivial(str(payload.get("content") or "")):
            return
        payload = dict(payload)
        payload["scope"] = self._scope_for(project_key)
        payload["content"] = _sanitize_context(str(payload.get("content") or ""))
        self._record_event(project_key, "sync_turn", payload, payload.get("session_id"))

    def on_memory_write(self, project_key: str, payload: dict[str, Any]) -> None:
        fenced_payload = dict(payload)
        fenced_payload["scope"] = self._scope_for(project_key)
        fenced_payload["content"] = _sanitize_context(str(payload.get("content") or ""))
        fenced_payload["recall_capture_guard"] = bool(payload.get("recall_capture_guard"))
        self._record_event(project_key, "memory_write", fenced_payload, payload.get("source_session_id"))

    def prefetch(self, project_key: str, query: str) -> list[str]:
        assert self.db is not None
        normalized_query = _normalize_text(query)
        if not normalized_query:
            return []
        scope = self._scope_for(project_key)
        event_rows = self.db.fetchall(
            """
            SELECT payload_json
            FROM provider_events
            WHERE provider_name = ?
              AND project_key = ?
              AND event_type IN ('memory_write', 'session_end')
            ORDER BY created_at DESC
            LIMIT 12
            """,
            (self.name, project_key),
        )
        results: list[str] = []
        for row in event_rows:
            payload = json.loads(row["payload_json"])
            haystack = _normalize_text(json.dumps(payload, ensure_ascii=False))
            if any(token in haystack for token in normalized_query.split()):
                summary = payload.get("focused_summary") or payload.get("content") or payload.get("summary")
                if summary:
                    results.append(
                        f"[container:{scope['container_tag']}] {_summarize_text(_sanitize_context(str(summary)))}"
                    )
        if results:
            self._record_event(project_key, "prefetch", {"query": query, "results": results[:4]})
        return results[:4]

    def on_session_end(self, project_key: str, session_id: str) -> dict[str, Any] | None:
        assert self.db is not None
        scope = self._scope_for(project_key)
        row = self.db.fetchone(
            """
            SELECT summary
            FROM session_summaries
            WHERE project_key = ? AND session_id = ?
            """,
            (project_key, session_id),
        )
        payload = {
            "session_id": session_id,
            "scope": scope,
            "focused_summary": _sanitize_context(row["summary"]) if row else "",
        }
        self._record_event(project_key, "session_end", payload, session_id)
        return payload


class ProviderManager:
    def __init__(self, db, trust: TrustConfig | None = None) -> None:
        self.db = db
        self._trust = trust or TrustConfig()
        self._providers: list[ProjectMemoryProvider] = [
            LocalBuiltinProvider(),
            HolographicLikeAdapter(trust=self._trust),
            SupermemoryLikeAdapter(),
        ]
        self._statuses: dict[str, ProviderStatus] = {}
        self._config: dict[str, Any] = {
            "enabled_providers": [provider.name for provider in self._providers],
            "provider_order": [provider.name for provider in self._providers],
            "allow_project_override": False,
            "include_provider_context_in_combined_text": True,
            "forced_failures": [],
        }

    def initialize(self, **kwargs: Any) -> None:
        runtime_kwargs = {**kwargs, "db": self.db}
        for provider in self._providers:
            if not provider.is_available():
                status = ProviderStatus(provider.name, "unavailable", provider.capabilities())
                self._statuses[provider.name] = status
                self._record_status(status)
                continue
            try:
                provider.initialize(**runtime_kwargs)
                status = ProviderStatus(provider.name, "ready", provider.capabilities())
            except Exception as exc:  # pragma: no cover - defensive
                status = ProviderStatus(
                    provider.name,
                    "error",
                    provider.capabilities(),
                    last_error=str(exc),
                )
            self._statuses[provider.name] = status
            self._record_status(status)

    def _record_status(self, status: ProviderStatus) -> None:
        self.db.execute(
            """
            INSERT INTO provider_runs(provider_name, status, capabilities_json, last_error, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(provider_name) DO UPDATE SET
                status = excluded.status,
                capabilities_json = excluded.capabilities_json,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            """,
            (
                status.provider_name,
                status.status,
                json.dumps(status.capabilities, ensure_ascii=False, sort_keys=True),
                status.last_error,
                self.db.now(),
            ),
        )

    def prefetch(self, project_key: str, query: str) -> list[str]:
        scored_results: list[tuple[str, str]] = []
        for provider in self._iter_enabled_providers():
            if provider.name in self._config.get("forced_failures", []):
                self._statuses[provider.name] = ProviderStatus(provider.name, "degraded", provider.capabilities(), "forced failure")
                self._record_status(self._statuses[provider.name])
                continue
            if self._statuses.get(provider.name, ProviderStatus(provider.name, "unknown", {})).status not in {"ready", "degraded"}:
                continue
            try:
                for snippet in provider.prefetch(project_key, query):
                    scored_results.append((provider.name, snippet))
            except Exception as exc:  # pragma: no cover - defensive
                self._statuses[provider.name] = ProviderStatus(provider.name, "degraded", provider.capabilities(), str(exc))
                self._record_status(self._statuses[provider.name])
        ordered = [snippet for _, snippet in scored_results]
        return ordered

    def sync_turn(self, project_key: str, payload: dict[str, Any]) -> None:
        for provider in self._iter_enabled_providers():
            if self._statuses.get(provider.name, ProviderStatus(provider.name, "unknown", {})).status in {"ready", "degraded"}:
                provider.sync_turn(project_key, payload)

    def on_memory_write(self, project_key: str, payload: dict[str, Any]) -> None:
        for provider in self._iter_enabled_providers():
            if self._statuses.get(provider.name, ProviderStatus(provider.name, "unknown", {})).status in {"ready", "degraded"}:
                provider.on_memory_write(project_key, payload)

    def on_session_end(self, project_key: str, session_id: str) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for provider in self._iter_enabled_providers():
            if self._statuses.get(provider.name, ProviderStatus(provider.name, "unknown", {})).status not in {"ready", "degraded"}:
                continue
            payload = provider.on_session_end(project_key, session_id)
            if payload is not None:
                results[provider.name] = payload
        return results

    def status(self) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "status": status.status,
                "capabilities": status.capabilities,
                "last_error": status.last_error,
            }
            for name, status in self._statuses.items()
        }

    def _iter_enabled_providers(self) -> list[ProjectMemoryProvider]:
        enabled = set(self._config.get("enabled_providers", []))
        order = self._config.get("provider_order", [])
        ranked = {name: index for index, name in enumerate(order)}
        return sorted(
            [provider for provider in self._providers if provider.name in enabled],
            key=lambda provider: ranked.get(provider.name, 999),
        )

    def configure(self, config: dict[str, Any]) -> dict[str, Any]:
        self._config.update(config)
        return self.runtime_policy()

    def runtime_policy(self) -> dict[str, Any]:
        return dict(self._config)

    def observability_summary(self) -> dict[str, Any]:
        event_rows = self.db.fetchall(
            """
            SELECT provider_name, event_type, COUNT(*) AS count
            FROM provider_events
            GROUP BY provider_name, event_type
            """
        )
        grouped: dict[str, dict[str, int]] = {}
        for row in event_rows:
            grouped.setdefault(row["provider_name"], {})[row["event_type"]] = int(row["count"])
        prefetch_rows = self.db.fetchall(
            """
            SELECT provider_name, payload_json
            FROM provider_events
            WHERE event_type = 'prefetch'
            ORDER BY created_at DESC
            """
        )
        prefetch_hits: dict[str, int] = {}
        for row in prefetch_rows:
            payload = json.loads(row["payload_json"])
            prefetch_hits[row["provider_name"]] = prefetch_hits.get(row["provider_name"], 0) + len(payload.get("results", []))
        recent_failures = self.db.fetchall(
            """
            SELECT provider_name, status, last_error, updated_at
            FROM provider_runs
            WHERE status IN ('error', 'degraded')
            ORDER BY updated_at DESC
            LIMIT 5
            """
        )
        return {
            "policy": self.runtime_policy(),
            "events": grouped,
            "statuses": self.status(),
            "prefetch_hits": prefetch_hits,
            "recent_failures": [dict(row) for row in recent_failures],
        }

    def reload(self, provider_name: str, **kwargs: Any) -> ProviderStatus:
        runtime_kwargs = {**kwargs, "db": self.db}
        for provider in self._providers:
            if provider.name != provider_name:
                continue
            if not provider.is_available():
                status = ProviderStatus(provider.name, "unavailable", provider.capabilities())
                self._statuses[provider.name] = status
                self._record_status(status)
                return status
            provider.initialize(**runtime_kwargs)
            status = ProviderStatus(provider.name, "ready", provider.capabilities())
            self._statuses[provider.name] = status
            self._record_status(status)
            return status
        raise KeyError(provider_name)

    def get(self, provider_name: str) -> ProjectMemoryProvider:
        for provider in self._providers:
            if provider.name == provider_name:
                return provider
        raise KeyError(provider_name)
