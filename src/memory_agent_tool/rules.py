from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from memory_agent_tool.models import ProjectContext, RuleSummary

RULE_FILENAMES = ("AGENTS.md", "INSTRUCTIONS.md", ".cursorrules")


@dataclass(slots=True)
class LoadedRules:
    summaries: list[RuleSummary]
    raw_text: str


class RulesLoader:
    def load(self, context: ProjectContext) -> LoadedRules:
        root = Path(context.working_directory or Path.cwd()).resolve()
        discovered: list[RuleSummary] = []
        texts: list[str] = []
        for directory in [root, *root.parents]:
            for filename in RULE_FILENAMES:
                path = directory / filename
                if not path.is_file():
                    continue
                content = path.read_text(encoding="utf-8").strip()
                if not content:
                    continue
                summary = "\n".join(content.splitlines()[:8]).strip()
                discovered.append(
                    RuleSummary(path=str(path), content=content, summary=summary)
                )
                texts.append(content.lower())
        return LoadedRules(summaries=discovered, raw_text="\n".join(texts))

    def detect_overlap(self, content: str, loaded: LoadedRules) -> str:
        normalized = " ".join(content.lower().split())
        if not normalized:
            return "none"
        for summary in loaded.summaries:
            source = " ".join(summary.content.lower().split())
            if normalized in source or source[: min(len(source), 200)] in normalized:
                if summary.path.endswith("AGENTS.md"):
                    return "overlaps_agents"
                return "overlaps_checked_in_instruction"
        return "none"
