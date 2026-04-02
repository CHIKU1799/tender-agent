"""
New portals — requested websites + credible archive/award sources.

Uses the SAME PortalConfig from configs.py (imported carefully to avoid
circular import — we import just the dataclass, not PORTALS).

Merged into PORTALS at the bottom of configs.py:
    from portals.new_portals import NEW_PORTALS
    PORTALS.update(NEW_PORTALS)
"""
from __future__ import annotations
from dataclasses import dataclass, field


# ── Local copy of PortalConfig (avoids circular import with configs.py) ──────
@dataclass
class PortalConfig:
    portal_id:        str
    display_name:     str
    base_url:         str
    platform:         str          # gepnic | gem_api | ireps | cppp | generic | ...
    category:         str          # Central | PSU | State | Aggregator | Archive

    # GePNIC / eTenders fields
    session_seed_url: str  = ""
    results_url:      str  = ""
    next_btn:         str  = "#linkFwd"
    row_selector:     str  = "table.list_table tr.even, table.list_table tr.odd, #table tr.even, #table tr.odd"
    col_map:          dict = field(default_factory=lambda: {
        "sno": 0, "published_date": 1, "closing_date": 2,
        "opening_date": 3, "title_raw": 4, "organisation": 5
    })

    # GeM API fields
    api_base:         str  = ""

    # Archive / awards URLs
    archive_url:      str  = ""
    awards_url:       str  = ""

    # Display
    emoji:            str  = "🏛️"
    notes:            str  = ""

    # Control
    enabled:          bool = True


# ─────────────────────────────────────────────────────────────────────────────
# USER-REQUESTED PORTALS
# ─────────────────────────────────────────────────────────────────────────────

NEW_PORTALS: dict[str, PortalConfig] = {

    # ══════════════════════════════════════════════════════
    # AGGREGATORS  (tender listing + archive + awards)
    # ══════════════════════════════════════════════════════

    "tenderdetail": PortalConfig(
        portal_id    = "tenderdetail",
        display_name = "TenderDetail",
        base_url     = "https://www.tenderdetail.com",
        platform     = "tenderdetail",
        category     = "Aggregator",
        results_url  = "https://www.tenderdetail.com/viewalltender.aspx",
        archive_url  = "https://www.tenderdetail.com/viewalltender.aspx?status=closed",
        awards_url   = "https://www.tenderdetail.com/Tender-Results",
        emoji        = "📋",
        notes        = "AI-driven aggregator — active, closed, awarded with pricing",
    ),

    "tendertiger": PortalConfig(
        portal_id    = "tendertiger",
        display_name = "TenderTiger",
        base_url     = "https://www.tendertiger.com",
        platform     = "tendertiger",
        category     = "Aggregator",
        results_url  = "https://www.tendertiger.com/tender/tenders.aspx",
        archive_url  = "https://www.tendertiger.com/tender/closed-tenders.aspx",
        awards_url   = "https://www.tendertiger.com/tender/awarded-tenders.aspx",
        emoji        = "🐯",
        notes        = "1M+ tenders — active, closed, awarded with awardee details",
    ),

    "tender247": PortalConfig(
        portal_id    = "tender247",
        display_name = "Tender247",
        base_url     = "https://tender247.com",
        platform     = "tender247",
        category     = "Aggregator",
        results_url  = "https://tender247.com/keyword/+/0/0/0/0/0/0/0/1",
        archive_url  = "https://tender247.com/closed-tenders/1",
        awards_url   = "https://tender247.com/awarded-tenders/1",
        emoji        = "📡",
        notes        = "Large aggregator — active, archive, awarded with pricing",
    ),

    "tenderkart": PortalConfig(
        portal_id    = "tenderkart",
        display_name = "TenderKart",
        base_url     = "https://www.tenderkart.in",
        platform     = "generic",
        category     = "Aggregator",
        results_url  = "https://www.tenderkart.in/tenders",
        archive_url  = "https://www.tenderkart.in/closed-tenders",
        awards_url   = "https://www.tenderkart.in/awarded-tenders",
        emoji        = "🛒",
        notes        = "Indian tender aggregator — free listings, active + closed",
    ),

    # ══════════════════════════════════════════════════════
    # STATE PORTALS  (GePNIC platform — full URL set)
    # ══════════════════════════════════════════════════════

    "delhi_gep": PortalConfig(
        portal_id    = "delhi_gep",
        display_name = "Delhi Govt eProcurement (GePNIC)",
        base_url     = "https://govtprocurement.delhi.gov.in/nicgep/app",
        platform     = "gepnic",
        category     = "State",
        session_seed_url = "https://govtprocurement.delhi.gov.in/nicgep/app?page=FrontEndLatestActiveTenders&service=page",
        results_url  = "https://govtprocurement.delhi.gov.in/nicgep/app?component=%24DirectLink&page=FrontEndAdvancedSearchResult&service=direct",
        archive_url  = "https://govtprocurement.delhi.gov.in/nicgep/app?page=FrontEndTendersInArchive&service=page",
        awards_url   = "https://govtprocurement.delhi.gov.in/nicgep/app?page=FrontEndResultOfTenders&service=page",
        emoji        = "🏙️",
        notes        = "Delhi Govt eProcurement — GePNIC platform with archive + awards",
    ),

    "mp_tenders": PortalConfig(
        portal_id    = "mp_tenders",
        display_name = "Madhya Pradesh Tenders (GePNIC)",
        base_url     = "https://mptenders.gov.in/nicgep/app",
        platform     = "gepnic",
        category     = "State",
        session_seed_url = "https://mptenders.gov.in/nicgep/app?page=FrontEndLatestActiveTenders&service=page",
        results_url  = "https://mptenders.gov.in/nicgep/app?component=%24DirectLink&page=FrontEndAdvancedSearchResult&service=direct",
        archive_url  = "https://mptenders.gov.in/nicgep/app?page=FrontEndTendersInArchive&service=page",
        awards_url   = "https://mptenders.gov.in/nicgep/app?page=FrontEndResultOfTenders&service=page",
        emoji        = "🌿",
        notes        = "Madhya Pradesh GePNIC portal — full archive + award results",
    ),

    "uk_tenders": PortalConfig(
        portal_id    = "uk_tenders",
        display_name = "Uttarakhand Tenders (GePNIC)",
        base_url     = "https://uktenders.gov.in/nicgep/app",
        platform     = "gepnic",
        category     = "State",
        session_seed_url = "https://uktenders.gov.in/nicgep/app?page=FrontEndLatestActiveTenders&service=page",
        results_url  = "https://uktenders.gov.in/nicgep/app?component=%24DirectLink&page=FrontEndAdvancedSearchResult&service=direct",
        archive_url  = "https://uktenders.gov.in/nicgep/app?page=FrontEndTendersInArchive&service=page",
        awards_url   = "https://uktenders.gov.in/nicgep/app?page=FrontEndResultOfTenders&service=page",
        emoji        = "🏔️",
        notes        = "Uttarakhand GePNIC portal — full archive + award results",
    ),

    # ══════════════════════════════════════════════════════
    # GUJARAT  (nprocure.com migrated to tender.nprocure.com)
    # ══════════════════════════════════════════════════════

    "gujarat_nprocure_new": PortalConfig(
        portal_id    = "gujarat_nprocure_new",
        display_name = "Gujarat (n)Procure New Portal",
        base_url     = "https://tender.nprocure.com",
        platform     = "generic",
        category     = "State",
        results_url  = "https://tender.nprocure.com",
        archive_url  = "https://www.nprocure.com/asp/home/AOCDetailsHome.asp",
        awards_url   = "https://www.nprocure.com/asp/home/AOCDetailsHome.asp",
        emoji        = "💛",
        notes        = "Gujarat's new nprocure portal (migrated 2024). AOC page has awarded details with pricing.",
    ),

    "gujarattenders": PortalConfig(
        portal_id    = "gujarattenders",
        display_name = "Gujarat Tenders (Aggregator)",
        base_url     = "https://gujarattenders.in",
        platform     = "generic",
        category     = "State",
        results_url  = "https://gujarattenders.in/tenders",
        archive_url  = "https://gujarattenders.in/closed-tenders",
        emoji        = "🦁",
        notes        = "Gujarat tender aggregator — scrapes nprocure + other Gujarat sources",
    ),

    # ══════════════════════════════════════════════════════
    # KARNATAKA  (JSF/Seam + KPPP portal)
    # ══════════════════════════════════════════════════════

    "karnataka_kppp": PortalConfig(
        portal_id    = "karnataka_kppp",
        display_name = "Karnataka KPPP (Public Procurement)",
        base_url     = "https://kppp.karnataka.gov.in",
        platform     = "generic",
        category     = "State",
        session_seed_url = "https://kppp.karnataka.gov.in/ui/public/tenders",
        results_url  = "https://kppp.karnataka.gov.in/ui/public/tenders",
        archive_url  = "https://kppp.karnataka.gov.in/ui/public/tenders?status=closed",
        awards_url   = "https://kppp.karnataka.gov.in/ui/public/tenders?status=awarded",
        emoji        = "🌿",
        notes        = "KPPP moved to React frontend — use GenericAgent with API interception at /api/tenders",
    ),

    "karnataka_eproc": PortalConfig(
        portal_id    = "karnataka_eproc",
        display_name = "Karnataka e-Procurement (eProc)",
        base_url     = "https://eproc.karnataka.gov.in",
        platform     = "karnataka_eproc",
        category     = "State",
        session_seed_url = "https://eproc.karnataka.gov.in/eprocportal/pages/index.jsp",
        results_url  = "https://eproc.karnataka.gov.in/eprocurement/common/eproc_tenders_list.seam",
        archive_url  = "https://eproc.karnataka.gov.in/eprocurement/common/eproc_tenders_list.seam",
        awards_url   = "https://eproc.karnataka.gov.in/eprocurement/common/eproc_tenders_list.seam",
        emoji        = "🌿",
        notes        = "JSF/Seam portal — uses requests+POST pagination, Playwright fallback for CAPTCHA",
    ),

    "karnatakatenders": PortalConfig(
        portal_id    = "karnatakatenders",
        display_name = "Karnataka Tenders (Aggregator)",
        base_url     = "https://www.karnatakatenders.in",
        platform     = "generic",
        category     = "State",
        results_url  = "https://www.karnatakatenders.in/",
        emoji        = "🌿",
        notes        = "Karnataka tender aggregator — scrapes eproc + KPPP sources",
    ),

    # ══════════════════════════════════════════════════════
    # PSU / SPECIAL PORTALS
    # ══════════════════════════════════════════════════════

    "palladium": PortalConfig(
        portal_id    = "palladium",
        display_name = "Palladium PrimeNumbers",
        base_url     = "https://app.palladium.primenumbers.in",
        platform     = "palladium",
        category     = "PSU",
        results_url  = "https://app.palladium.primenumbers.in/",
        emoji        = "💼",
        notes        = "React SPA — intercept JSON API calls for tender data",
    ),

    # ══════════════════════════════════════════════════════
    # CREDIBLE ARCHIVED TENDER SOURCES  (pricing + awardees)
    # ══════════════════════════════════════════════════════

    "tendersinfo": PortalConfig(
        portal_id    = "tendersinfo",
        display_name = "TendersInfo (Archive + Awards)",
        base_url     = "https://www.tendersinfo.com",
        platform     = "generic",
        category     = "Archive",
        results_url  = "https://www.tendersinfo.com/global-india-tenders.php",
        archive_url  = "https://www.tendersinfo.com/india-contracts.php",
        awards_url   = "https://www.tendersinfo.com/india-contracts.php",
        emoji        = "📰",
        notes        = "15L+ results — contract awards with supplier, pricing, scope, timeline",
    ),

    "thetenders": PortalConfig(
        portal_id    = "thetenders",
        display_name = "TheTenders (Award Results)",
        base_url     = "https://www.thetenders.com",
        platform     = "generic",
        category     = "Archive",
        results_url  = "https://www.thetenders.com/All-Tenders/1",
        archive_url  = "https://www.thetenders.com/Archived-Tenders/1",
        awards_url   = "https://www.thetenders.com/Awarded-Tenders/1",
        emoji        = "🏆",
        notes        = "Awarded tenders with awardee names, contract values, bid details",
    ),

    "bidassist": PortalConfig(
        portal_id    = "bidassist",
        display_name = "BidAssist (AI-powered)",
        base_url     = "https://bidassist.com",
        platform     = "generic",
        category     = "Archive",
        results_url  = "https://bidassist.com/tenders/latest",
        archive_url  = "https://bidassist.com/tenders/closed",
        awards_url   = "https://bidassist.com/tenders/awarded",
        emoji        = "🤝",
        notes        = "AI-powered aggregator — pricing, EMD, awardee info on detail pages",
    ),

    "indiantenders": PortalConfig(
        portal_id    = "indiantenders",
        display_name = "IndianTenders.in",
        base_url     = "https://www.indiantenders.in",
        platform     = "generic",
        category     = "Archive",
        results_url  = "https://www.indiantenders.in/",
        awards_url   = "https://www.indiantenders.in/awarded-tenders",
        emoji        = "🇮🇳",
        notes        = "GeM registration + tender listing with bid support, award details",
    ),

    "data_gov_tenders": PortalConfig(
        portal_id    = "data_gov_tenders",
        display_name = "Open Govt Data (data.gov.in) — Tenders",
        base_url     = "https://www.data.gov.in",
        platform     = "generic",
        category     = "Archive",
        results_url  = "https://www.data.gov.in/keywords/Tender",
        emoji        = "📊",
        notes        = "Govt open data — bulk CSV/JSON datasets of historical tenders with pricing",
    ),

    "cppp_archive": PortalConfig(
        portal_id    = "cppp_archive",
        display_name = "CPPP Archive (eprocure.gov.in)",
        base_url     = "https://eprocure.gov.in/cppp",
        platform     = "cppp",
        category     = "Archive",
        results_url  = "https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata",
        archive_url  = "https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata",
        awards_url   = "https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata",
        emoji        = "🏛️",
        notes        = "Cross-ministry aggregator archive — closed tenders with results",
    ),

    "eprocure_results": PortalConfig(
        portal_id    = "eprocure_results",
        display_name = "eProcure Tender Results (etenders.gov.in)",
        base_url     = "https://etenders.gov.in/eprocure/app",
        platform     = "gepnic",
        category     = "Archive",
        session_seed_url = "https://etenders.gov.in/eprocure/app?page=FrontEndLatestActiveTenders&service=page",
        results_url  = "https://etenders.gov.in/eprocure/app?component=%24DirectLink&page=FrontEndAdvancedSearchResult&service=direct",
        archive_url  = "https://etenders.gov.in/eprocure/app?page=FrontEndTendersInArchive&service=page",
        awards_url   = "https://etenders.gov.in/eprocure/app?page=FrontEndResultOfTenders&service=page",
        emoji        = "📜",
        notes        = "Central eProcure — Result of Tenders page shows awardee + pricing",
    ),

    "tendersontime": PortalConfig(
        portal_id    = "tendersontime",
        display_name = "TendersOnTime",
        base_url     = "https://www.tendersontime.com",
        platform     = "generic",
        category     = "Archive",
        results_url  = "https://www.tendersontime.com/india/tenders/",
        archive_url  = "https://www.tendersontime.com/india/closed-tenders/",
        awards_url   = "https://www.tendersontime.com/india/awarded-tenders/",
        emoji        = "⏰",
        notes        = "Aggregator with state-wise filtering — awarded tenders with details",
    ),

    "tendersplus": PortalConfig(
        portal_id    = "tendersplus",
        display_name = "TendersPlus",
        base_url     = "https://tendersplus.com",
        platform     = "generic",
        category     = "Archive",
        results_url  = "https://tendersplus.com/tenders/active",
        archive_url  = "https://tendersplus.com/tenders/closed",
        awards_url   = "https://tendersplus.com/tenders/awarded",
        emoji        = "➕",
        notes        = "Free aggregator — Karnataka/Gujarat focus, awarded tenders with pricing",
    ),

    # ══════════════════════════════════════════════════════
    # KARNATAKA-SPECIFIC ARCHIVE SOURCES
    # ══════════════════════════════════════════════════════

    "karnataka_tendersontime": PortalConfig(
        portal_id    = "karnataka_tendersontime",
        display_name = "Karnataka Tenders (TendersOnTime)",
        base_url     = "https://www.tendersontime.com/india/karnataka-tenders/",
        platform     = "generic",
        category     = "Archive",
        results_url  = "https://www.tendersontime.com/india/karnataka-tenders/",
        archive_url  = "https://www.tendersontime.com/india/karnataka-tenders/closed/",
        emoji        = "🌿",
        notes        = "Karnataka tenders via TendersOnTime — closed/awarded with pricing",
    ),

    # ══════════════════════════════════════════════════════
    # GUJARAT-SPECIFIC ARCHIVE SOURCES
    # ══════════════════════════════════════════════════════

    "gspl_nprocure": PortalConfig(
        portal_id    = "gspl_nprocure",
        display_name = "GSPL (Gujarat State Petronet) nProcure",
        base_url     = "https://gspl.nprocure.com",
        platform     = "generic",
        category     = "State",
        results_url  = "https://gspl.nprocure.com",
        emoji        = "⛽",
        notes        = "GSPL tenders on nprocure — DNS may fail",
        enabled      = False,  # DNS resolution failure
    ),

    "gsecl_nprocure": PortalConfig(
        portal_id    = "gsecl_nprocure",
        display_name = "GSECL (Gujarat Electricity) nProcure",
        base_url     = "https://gsecl.nprocure.com",
        platform     = "generic",
        category     = "State",
        results_url  = "https://gsecl.nprocure.com",
        emoji        = "⚡",
        notes        = "Gujarat State Electricity Corp tenders — DNS may fail",
        enabled      = False,  # DNS resolution failure
    ),
}
