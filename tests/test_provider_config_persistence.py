from __future__ import annotations

from pathlib import Path

from memory_agent_tool.config import AppSettings
from memory_agent_tool.services import AppContainer


def _build_container(tmp_path: Path) -> AppContainer:
    settings = AppSettings.from_env(cwd=tmp_path)
    return AppContainer.build(settings)


def test_provider_runtime_config_persists_across_restart(tmp_path: Path):
    first = _build_container(tmp_path)
    first.providers.configure(
        {
            "enabled_providers": ["local_builtin", "holographic_like"],
            "provider_order": ["holographic_like", "local_builtin"],
            "forced_failures": ["supermemory_like"],
            "provider_configs": {
                "supermemory_like": {"mode": "container_only"},
            },
        }
    )
    first.db.close()

    second = _build_container(tmp_path)
    policy = second.providers.runtime_policy()

    assert policy["enabled_providers"] == ["local_builtin", "holographic_like"]
    assert policy["provider_order"] == ["holographic_like", "local_builtin"]
    assert policy["forced_failures"] == ["supermemory_like"]
    assert policy["provider_configs"]["supermemory_like"]["mode"] == "container_only"


def test_provider_project_override_is_persisted_and_reported(tmp_path: Path):
    container = _build_container(tmp_path)
    project_key = "memory-agent-tool::shared"
    container.providers.configure_project(
        project_key,
        {
            "enabled_providers": ["local_builtin"],
            "provider_configs": {"local_builtin": {"notes": "project-override"}},
        },
    )

    effective = container.providers.runtime_policy(project_key)
    observability = container.providers.observability_summary(project_key)

    assert effective["enabled_providers"] == ["local_builtin"]
    assert effective["config_source"] == "project_override"
    assert observability["effective_policy"]["provider_configs"]["local_builtin"]["notes"] == "project-override"


def test_provider_runtime_can_clear_project_override(tmp_path: Path):
    container = _build_container(tmp_path)
    project_key = "memory-agent-tool::shared"
    container.providers.configure_project(project_key, {"enabled_providers": ["local_builtin"]})
    container.providers.clear_project_config(project_key)

    effective = container.providers.runtime_policy(project_key)
    assert effective["config_source"] == "global"
    assert "local_builtin" in effective["enabled_providers"]
