from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from memory_agent_tool.app import create_app
from memory_agent_tool.config import AppSettings
from memory_agent_tool.services import AppContainer


@pytest.fixture()
def temp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MEMORY_AGENT_TOOL_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def settings(temp_home: Path) -> AppSettings:
    return AppSettings.from_env(cwd=temp_home)


@pytest.fixture()
def container(settings: AppSettings) -> AppContainer:
    return AppContainer.build(settings)


@pytest.fixture()
def client(settings: AppSettings) -> TestClient:
    app = create_app(settings)
    return TestClient(app)
