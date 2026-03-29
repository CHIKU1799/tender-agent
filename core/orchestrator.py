"""
Parallel scraping orchestrator for the web dashboard.
Runs up to MAX_CONCURRENT portals in parallel, emitting SSE-style events.

Thread model:
  Flask runs in the main thread (sync).
  Each ScrapeTask spins up a background thread with its own asyncio event loop.
  Events are passed via a plain thread-safe queue.Queue — no cross-thread
  asyncio primitives needed, avoiding Python 3.9 "Future attached to a
  different loop" errors.
"""
from __future__ import annotations
import asyncio
import logging
import queue as _queue
from agents.base import ScrapeResult
from core.browser import BrowserSession
from core.storage import save_csv, save_json, save_sqlite, save_combined_csv, SnapshotStore
from portals.configs import PORTALS

log = logging.getLogger("orchestrator")

MAX_CONCURRENT = 3  # semaphore — max parallel portals


def _make_agent(portal_id: str, session: BrowserSession, scope: str = "active"):
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
    Runs a multi-portal scrape job in a background thread.
    Progress events are placed into a thread-safe queue.Queue so the
    Flask SSE endpoint can consume them from the main thread without
    any asyncio cross-thread complexity.
    """

    def __init__(self, task_id: str, portal_ids: list[str], filters: dict):
        self.task_id    = task_id
        self.portal_ids = portal_ids
        self.filters    = filters
        self.results:   dict[str, ScrapeResult] = {}
        # Plain thread-safe queue — works from any thread/loop without issues
        self._events:   _queue.Queue = _queue.Queue()
        self._done:     bool = False

    def emit(self, event: dict):
        """Put an event — safe to call from async or sync code, any thread."""
        self._events.put(event)

    def next_event(self, timeout: float = 2.0):
        """
        Block for up to `timeout` seconds waiting for the next event.
        Returns the event dict, or None on timeout.
        Raises queue.Empty only if timeout==0 (non-blocking).
        """
        try:
            return self._events.get(timeout=timeout)
        except _queue.Empty:
            return None

    async def run(self):
        scope    = self.filters.get("scope", "active")
        max_pgs  = self.filters.get("max_pages")
        org_flt  = self.filters.get("org_filter")
        f_detail = self.filters.get("fetch_details", False)
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        try:
            async with BrowserSession() as session:
                tasks = [
                    self._run_one(pid, session, scope, max_pgs, org_flt, f_detail, semaphore)
                    for pid in self.portal_ids
                ]
                await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            log.error(f"[orch] BrowserSession error: {e}")
            self.emit({"type": "error", "message": str(e)})

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
