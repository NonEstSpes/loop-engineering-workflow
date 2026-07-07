"""Factory and registry for research source drivers."""

from __future__ import annotations

from devflow.config import ResearchSourcesConfig
from devflow.research.sources.base import ResearchSource


class SourceFactory:
    """Registry that maps driver names to classes and builds enabled sources."""

    def __init__(self) -> None:
        self._registry: dict[str, type[ResearchSource]] = {}

    def register(self, name: str, driver_cls: type[ResearchSource]) -> None:
        """Register a driver class under the given name."""
        self._registry[name] = driver_cls

    def is_registered(self, name: str) -> bool:
        """Return True if a driver with this name is registered."""
        return name in self._registry

    def build(
        self,
        cfg: ResearchSourcesConfig,
    ) -> dict[str, ResearchSource]:
        """Build enabled sources from configuration.

        Skips disabled sources and drivers that are not registered.
        """
        built: dict[str, ResearchSource] = {}
        for source_cfg in cfg.sources:
            if not source_cfg.enabled:
                continue
            driver_cls = self._registry.get(source_cfg.driver)
            if driver_cls is None:
                continue
            built[source_cfg.name] = driver_cls(source_cfg.config)
        return built

    @classmethod
    def default(cls) -> SourceFactory:
        """Return a factory with all built-in drivers registered."""
        from devflow.research.sources.file_system import FileSystemSource
        from devflow.research.sources.git_tools import GitToolsSource
        from devflow.research.sources.graphify_mcp import GraphifyMcpSource
        from devflow.research.sources.mcp_generic import McpGenericSource
        from devflow.research.sources.web_search import WebSearchSource

        factory = cls()
        factory.register("git_tools", GitToolsSource)
        factory.register("file_system", FileSystemSource)
        factory.register("web_search", WebSearchSource)
        factory.register("graphify_mcp", GraphifyMcpSource)
        factory.register("mcp_generic", McpGenericSource)
        return factory
