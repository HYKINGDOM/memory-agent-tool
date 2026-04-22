from __future__ import annotations

from dataclasses import dataclass

from memory_agent_tool.config import AppSettings
from memory_agent_tool.database import Database
from memory_agent_tool.gateway import ClientRegistry
from memory_agent_tool.logging import get_logger, setup_logging
from memory_agent_tool.mcp import CodexMCPServer
from memory_agent_tool.providers import ProviderManager
from memory_agent_tool.resolver import ProjectResolver
from memory_agent_tool.rules import RulesLoader
from memory_agent_tool.services.conflict_service import ConflictAndFeedbackService
from memory_agent_tool.services.maintenance_service import MemoryMaintenanceService
from memory_agent_tool.services.memory_service import ProjectMemoryService
from memory_agent_tool.services.project_service import ProjectRegistry
from memory_agent_tool.services.retrieval_service import RetrievalPipeline
from memory_agent_tool.services.session_service import SessionArchiveService
from memory_agent_tool.services.skill_service import SkillPromotionService
from memory_agent_tool.services.status_service import StatusReporter

logger = get_logger("container")


@dataclass(slots=True)
class AppContainer:
    settings: AppSettings
    db: Database
    resolver: ProjectResolver
    rules_loader: RulesLoader
    providers: ProviderManager
    projects: ProjectRegistry
    conflicts: ConflictAndFeedbackService
    memory: ProjectMemoryService
    archive: SessionArchiveService
    skills: SkillPromotionService
    maintenance: MemoryMaintenanceService
    retrieval: RetrievalPipeline
    reporter: StatusReporter
    client_registry: ClientRegistry
    codex_mcp: CodexMCPServer

    @classmethod
    def build(cls, settings: AppSettings) -> "AppContainer":
        setup_logging(level=settings.log_level, json_mode=settings.log_json)
        settings.ensure_directories()
        db = Database(settings.db_path)
        resolver = ProjectResolver()
        rules_loader = RulesLoader()
        providers = ProviderManager(db)
        providers.initialize(root_dir=str(settings.root_dir), db_path=str(settings.db_path))
        projects = ProjectRegistry(db, resolver)
        conflicts = ConflictAndFeedbackService(db, providers, trust=settings.trust)
        memory = ProjectMemoryService(db, settings, conflicts, trust=settings.trust)
        archive = SessionArchiveService(db, projects, rules_loader, memory, providers)
        skills = SkillPromotionService(db, settings, trust=settings.trust)
        maintenance = MemoryMaintenanceService(db, memory, archive, trust=settings.trust)
        retrieval = RetrievalPipeline(db, rules_loader, projects, memory, archive, skills, providers, scoring=settings.scoring)
        reporter = StatusReporter(db, providers, skills, projects)
        skeleton = cls.__new__(cls)
        skeleton.settings = settings
        skeleton.db = db
        skeleton.resolver = resolver
        skeleton.rules_loader = rules_loader
        skeleton.providers = providers
        skeleton.projects = projects
        skeleton.conflicts = conflicts
        skeleton.memory = memory
        skeleton.archive = archive
        skeleton.skills = skills
        skeleton.maintenance = maintenance
        skeleton.retrieval = retrieval
        skeleton.reporter = reporter
        skeleton.client_registry = ClientRegistry(skeleton)
        skeleton.codex_mcp = CodexMCPServer(skeleton)
        logger.info("container built: root=%s db=%s scoring=%s", settings.root_dir, settings.db_path, settings.scoring.strategy)
        return skeleton
