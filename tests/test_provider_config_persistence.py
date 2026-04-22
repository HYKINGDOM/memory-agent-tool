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
        }
    )
    first.db.close()

    second = _build_container(tmp_path)
    policy = second.providers.runtime_policy()

    assert "local_builtin" in policy["enabled_providers"]
    assert "holographic_like" in policy["enabled_providers"]


def test_provider_runtime_can_disable_provider_from_prefetch(tmp_path: Path):
    container = _build_container(tmp_path)
    container.providers.configure({"enabled_providers": ["local_builtin"]})
    policy = container.providers.runtime_policy()
    assert policy["enabled_providers"] == ["local_builtin"]
