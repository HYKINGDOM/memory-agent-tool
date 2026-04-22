from __future__ import annotations

import time


def now_ts() -> float:
    return time.time()


def summarize_text(value: str, limit: int = 180) -> str:
    collapsed = " ".join((value or "").split())
    return collapsed[:limit]


def extract_fact_key(title: str | None, content: str) -> str:
    from memory_agent_tool.scoring import normalize_text

    if title:
        return normalize_text(title)[:80]
    if ":" in content:
        return normalize_text(content.split(":", 1)[0])[:80]
    words = normalize_text(content).split()
    return " ".join(words[:4])[:80]


def build_focused_summary(messages: list[dict[str, any]], query: str | None = None, limit: int = 3) -> str:
    from memory_agent_tool.scoring import normalize_text

    normalized_query = normalize_text(query or "")
    ranked: list[tuple[int, str]] = []
    for message in messages:
        text = message.get("normalized_summary") or summarize_text(message.get("content") or "")
        haystack = normalize_text(text)
        score = 0
        if normalized_query:
            score = sum(1 for token in normalized_query.split() if token in haystack)
        ranked.append((score, text))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected = [text for _, text in ranked[:limit] if text]
    if not selected:
        selected = [summarize_text(message.get("content") or "") for message in messages[:limit]]
    return "; ".join(selected)


def freshness_score(updated_at: float | None, verified_at: float | None) -> float:
    reference = verified_at or updated_at or 0.0
    if not reference:
        return 0.0
    age_days = max(0.0, (now_ts() - reference) / 86400)
    if age_days <= 1:
        return 1.0
    if age_days <= 7:
        return 0.8
    if age_days <= 30:
        return 0.5
    return 0.2
