"""
Parallel scraping orchestrator for the web dashboard.
Runs up to MAX_CONCURRENT portals in parallel, emitting SSE-style events.
"""
from __future__ import annotations
import asyncio
import logging
from typing import AsyncIterator
from agents.base import ScrapeResult
from core.browser import BrowserSession
from core.storage import save_csv, save_json, save_sqlite, save_combined_csv, SnapshotStore
from portals.configs import PORTALS

log = logging.getLogger("orchestrator")

MAX_CONCURRENT = 3  # semaphore — max parallel portals


def _make_agent(portal_id: str, session: BrowserSession, scope: str = "active"):
    """
    Agent factory that supports scope="active"|"archive"|"awards"|"both".
    For archive/awards, wraps the GePNICArchiveAgent around GePNIC portals.
    """
    from portals.configs import PORTALS
    from agents.gepnic    import GePNICAgent
    from agents.gem       import GeMAgent
    from agents.ireps     import IREPSAgent
    from agents.cppp      import CPPPAgent
    from agents.generic   import GenericAgent
    from agents.karnataka import KarnatakaAgent

    cfg = PORTALS[portal_id]

    if scope != "active" and cfg.platform == "gepnic":
        from agents.gepnic_archive import GePNICArchiveAgent
        return GePNICArchiveAgent(cfg, session, scope=scope)

    if   cfg.platform == "gepnic":          return GePNICAgent(cfg, session)
    elif cfg.platform == "gem_api":         return GeMAgent(cfg, session)
    elif cfg.platform == "ireps":           return IREPSAgent(cfg, session)
    elif cfg.platform == "cppp":            return CPPPAgent(cfg, session)
    elif cfg.platform == "karnataka_seam":  return KarnatakaAgent(cfg, session)
    else:                                   return GenericAgent(cfg, session)


class ScrapeTask:
    """
    Runs a multi-portal scrape job and makes progress available via
    an async generator. Can be polled from the Flask SSE endpoint.
    """

    def __init__(self, task_id: str, portal_ids: list[str], filters: dict):
        self.task_id    = task_id
        self.portal_ids = portal_ids
        self.filters    = filters
        self.results:   dict[str, ScrapeResult] = {}
        self._events:   asyncio.Queue = asyncio.Queue()
        self._done = False

    def emit(self, event: dict):
        self._events.put_nowait(event)

    async def events(self) -> AsyncIterator[dict]:
        while not self._done or not self._events.empty():
            try:
                ev = await asyncio.wait_for(self._events.get(), timeout=0.5)
                yield ev
            except asyncio.TimeoutError:
                if self._done:
                    break

    async def run(self):
        scope    = self.filters.get("scope", "active")
        max_pgs  = self.filters.get("max_pages")
        org_flt  = self.filters.get("org_filter")
        f_detail = self.filters.get("fetch_details", False)
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        async with BrowserSession() as session:
            tasks = [
                self._run_one(pid, session, scope, max_pgs, org_flt, f_detail, semaphore)
                for pid in self.portal_ids
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

        # Save outputs
        all_tenders: list[dict] = []
        for res in self.results.values():
            all_tenders.extend(res.tenders)

        if all_tenders:
            from pathlib import Path
            from core.storage import OUTPUT_DIR, save_awards_csv
            OUTPUT_DIR.mkdir(exist_ok=True)
            snapshot = SnapshotStore()
            for pid, res in self.results.items():
                if res.tenders:
                    save_csv(res.tenders, pid)
                    save_json(res.tenders, pid)
                    save_sqlite(res.tenders)
                    snapshot.save(pid, res.tenders)
            save_combined_csv(all_tenders)
            save_awards_csv(all_tenders)

        self.emit({"type": "done", "total": len(all_tenders)})
        self._done = True

    async def _run_one(
        self, portal_id, session, scope, max_pages, org_filter, fetch_details, semaphore
    ):
        cfg = PORTALS[portal_id]
        self.emit({"type": "start", "portal_id": portal_id, "name": cfg.display_name})

        async with semaphore:
            agent = _make_agent(portal_id, session, scope)

            async def progress_cb(page_num: int, count: int):
                self.emit({
                    "type":      "progress",
                    "portal_id": portal_id,
                    "page":      page_num,
                    "count":     count,
                })

            try:
                result = await agent.scrape(
                    max_pages=max_pages,
                    org_filter=org_filter,
                    fetch_details=fetch_details,
                    progress_cb=progress_cb,
                )
            except Exception as e:
                log.error(f"[orch] {portal_id} crashed: {e}")
                result = ScrapeResult(portal_id=portal_id, errors=[str(e)])

            self.results[portal_id] = result
            self.emit({
                "type":      "complete",
                "portal_id": portal_id,
                "count":     len(result.tenders),
                "errors":    result.errors,
            })
