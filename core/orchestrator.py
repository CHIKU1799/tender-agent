"""
Parallel scraping orchestrator — fixed scope routing.
Archive/awards scope now correctly routes ALL portal types.
"""
from __future__ import annotations
import asyncio
import logging
import queue as _queue
import random

from agents.base import ScrapeResult
from core.browser import BrowserSession, random_delay
from core.storage import save_csv, save_json, save_sqlite, save_combined_csv, SnapshotStore
from portals.configs import PORTALS

log = logging.getLogger("orchestrator")

MAX_CONCURRENT = 2
MAX_RETRIES    = 2

# ── THE FIX: complete scope-aware agent factory ───────────────────────────────

def _make_agent(portal_id: str, session: BrowserSession, scope: str = "active"):
    from agents.gepnic    import GePNICAgent
    from agents.gem       import GeMAgent
    from agents.ireps     import IREPSAgent
    from agents.cppp      import CPPPAgent
    from agents.generic   import GenericAgent
    from agents.karnataka import KarnatakaAgent

    cfg        = PORTALS[portal_id]

    # Skip disabled portals (DNS failures, dead sites, etc.)
    if not getattr(cfg, 'enabled', True):
        from agents.base import ScrapeResult
        log.info(f"[orch] Skipping disabled portal: {portal_id}")

        class _DisabledAgent:
            portal_id = portal_id
            async def scrape(self, **kw):
                r = ScrapeResult(portal_id=portal_id)
                r.skipped = True
                r.skip_reason = "Portal disabled in config"
                return r
        return _DisabledAgent()

    is_archive = scope in ("archive", "awards", "both", "all")

    # ── Archive / awards scope ────────────────────────────────────────────────
    if is_archive:
        archive_scope = "both" if scope == "all" else scope

        if cfg.platform == "gepnic":
            from agents.gepnic_archive import GePNICArchiveAgent
            return GePNICArchiveAgent(cfg, session, scope=archive_scope)

        if cfg.platform == "cppp":
            try:
                from agents.cppp_archive import CPPPArchiveAgent
                return CPPPArchiveAgent(cfg, session, scope=archive_scope)
            except ImportError:
                pass  # fall through to active agent

        if cfg.platform == "gem_api":
            try:
                from agents.gem_archive import GeMArchiveAgent
                return GeMArchiveAgent(cfg, session, scope=archive_scope)
            except ImportError:
                pass

        if cfg.platform == "ireps":
            try:
                from agents.gepnic_archive import GePNICArchiveAgent
                return GePNICArchiveAgent(cfg, session, scope=archive_scope)
            except ImportError:
                pass

        # ── New platform types with built-in scope support ────────────────────
        if cfg.platform == "tenderdetail":
            from agents.tenderdetail import TenderDetailAgent
            return TenderDetailAgent(cfg, session, scope=archive_scope)

        if cfg.platform == "tendertiger":
            from agents.tendertiger import TenderTigerAgent
            return TenderTigerAgent(cfg, session, scope=archive_scope)

        if cfg.platform == "tender247":
            from agents.tender247 import Tender247Agent
            return Tender247Agent(cfg, session, scope=archive_scope)

        if cfg.platform == "palladium":
            from agents.palladium import PalladiumAgent
            return PalladiumAgent(cfg, session, scope=archive_scope)

        if cfg.platform == "karnataka_seam":
            from agents.karnataka_archive import KarnatakaArchiveAgent
            return KarnatakaArchiveAgent(cfg, session, scope=archive_scope)

        if cfg.platform == "karnataka_eproc":
            from agents.karnataka_eproc import KarnatakaEprocAgent
            return KarnatakaEprocAgent(cfg, session, scope=archive_scope)

        # For portals without a dedicated archive agent,
        # use GenericAgent but mark status so results are labelled correctly
        log.warning(f"[orch] No archive agent for {portal_id} ({cfg.platform}) — using GenericAgent")
        agent = GenericAgent(cfg, session)
        agent._forced_status = "Archive"
        return agent

    # ── Active scope (original routing) ──────────────────────────────────────
    if   cfg.platform == "gepnic":          return GePNICAgent(cfg, session)
    elif cfg.platform == "gem_api":         return GeMAgent(cfg, session)
    elif cfg.platform == "ireps":           return IREPSAgent(cfg, session)
    elif cfg.platform == "cppp":            return CPPPAgent(cfg, session)
    elif cfg.platform == "karnataka_seam":  return KarnatakaAgent(cfg, session)
    elif cfg.platform == "tenderdetail":
        from agents.tenderdetail import TenderDetailAgent
        return TenderDetailAgent(cfg, session, scope="active")
    elif cfg.platform == "tendertiger":
        from agents.tendertiger import TenderTigerAgent
        return TenderTigerAgent(cfg, session, scope="active")
    elif cfg.platform == "tender247":
        from agents.tender247 import Tender247Agent
        return Tender247Agent(cfg, session, scope="active")
    elif cfg.platform == "palladium":
        from agents.palladium import PalladiumAgent
        return PalladiumAgent(cfg, session, scope="active")
    elif cfg.platform == "karnataka_eproc":
        from agents.karnataka_eproc import KarnatakaEprocAgent
        return KarnatakaEprocAgent(cfg, session, scope="active")
    else:                                   return GenericAgent(cfg, session)


# ── ScrapeTask ────────────────────────────────────────────────────────────────

class ScrapeTask:

    def __init__(self, task_id: str, portal_ids: list[str], filters: dict):
        self.task_id    = task_id
        self.portal_ids = portal_ids
        self.filters    = filters
        self.results:   dict[str, ScrapeResult] = {}
        self._events:   _queue.Queue = _queue.Queue()
        self._done:     bool = False

    def emit(self, event: dict):
        self._events.put(event)

    def next_event(self, timeout: float = 2.0):
        try:
            return self._events.get(timeout=timeout)
        except _queue.Empty:
            return None

    async def run(self):
        scope    = self.filters.get("scope", "active")
        max_pgs  = self.filters.get("max_pages")
        org_flt  = self.filters.get("org_filter")
        f_detail = self.filters.get("fetch_details", False)

        log.info(f"[orch] Starting task {self.task_id} scope={scope!r} portals={self.portal_ids}")

        # "all" scope = run active first, then archive+awards
        run_scopes = ["active", "archive", "awards"] if scope == "all" else [scope]

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        try:
            async with BrowserSession() as session:
                for run_scope in run_scopes:
                    log.info(f"[orch] Running scope={run_scope!r}")
                    tasks = [
                        self._run_one(pid, session, run_scope, max_pgs, org_flt, f_detail, semaphore)
                        for pid in self.portal_ids
                    ]
                    await asyncio.gather(*tasks, return_exceptions=True)

                    if len(run_scopes) > 1:
                        await asyncio.sleep(random.uniform(3, 6))

        except Exception as e:
            log.error(f"[orch] BrowserSession error: {e}")
            self.emit({"type": "error", "message": str(e)})

        # Save outputs
        all_tenders: list[dict] = []
        for res in self.results.values():
            all_tenders.extend(res.tenders)

        if all_tenders:
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
            log.info(f"[orch] Saved {len(all_tenders)} total tenders")

        self.emit({"type": "done", "total": len(all_tenders)})
        self._done = True

    async def _run_one(
        self, portal_id, session, scope, max_pages, org_filter, fetch_details, semaphore
    ):
        cfg = PORTALS[portal_id]
        self.emit({
            "type":      "start",
            "portal_id": portal_id,
            "name":      cfg.display_name,
            "scope":     scope,   # ← now visible in SSE stream for debugging
        })

        async with semaphore:
            await asyncio.sleep(random.uniform(0.5, 3.0))  # stagger starts

            result = None

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    agent = _make_agent(portal_id, session, scope)

                    async def progress_cb(page_num: int, count: int):
                        self.emit({
                            "type":      "progress",
                            "portal_id": portal_id,
                            "page":      page_num,
                            "count":     count,
                            "scope":     scope,
                        })

                    result = await agent.scrape(
                        max_pages=max_pages,
                        org_filter=org_filter,
                        fetch_details=fetch_details,
                        progress_cb=progress_cb,
                    )

                    if result.tenders or result.skipped:
                        break

                    if attempt < MAX_RETRIES:
                        log.warning(f"[orch] {portal_id} no data on attempt {attempt}, retrying...")
                        await asyncio.sleep(random.uniform(8, 15))

                except Exception as e:
                    log.error(f"[orch] {portal_id} attempt {attempt}: {e}")
                    if attempt >= MAX_RETRIES:
                        result = ScrapeResult(portal_id=portal_id, errors=[str(e)])
                    else:
                        await asyncio.sleep(random.uniform(8, 15))

            if result is None:
                result = ScrapeResult(portal_id=portal_id, errors=["No result"])

            # Merge results across scopes
            if portal_id in self.results:
                self.results[portal_id].tenders.extend(result.tenders)
                self.results[portal_id].errors.extend(result.errors)
            else:
                self.results[portal_id] = result

            self.emit({
                "type":      "complete",
                "portal_id": portal_id,
                "count":     len(result.tenders),
                "errors":    result.errors,
                "scope":     scope,
            })

            await random_delay(2.0, 4.0)
