"""Abstract base class for all scraping agents."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from portals.configs import PortalConfig


@dataclass
class ScrapeResult:
    portal_id:  str
    tenders:    list[dict] = field(default_factory=list)
    pages:      int = 0
    errors:     list[str] = field(default_factory=list)
    skipped:    bool = False   # True when platform not yet supported
    skip_reason: str = ""


class BaseAgent(ABC):
    def __init__(self, config: PortalConfig, browser=None):
        self.config = config
        self.portal_id = config.portal_id
        # Optional browser/session — subclasses may pass it via super().__init__
        if browser is not None:
            self.session = browser

    @abstractmethod
    async def scrape(
        self,
        max_pages: int | None = None,
        org_filter: str | None = None,
        progress_cb=None,           # async callable(page_num, tender_count)
    ) -> ScrapeResult:
        """Scrape listing pages. Must be implemented by every agent."""
        ...

    async def health_check(self) -> bool:
        """Quick connectivity check before starting a full run."""
        return True

    def __repr__(self):
        return f"{self.__class__.__name__}(portal={self.portal_id})"
