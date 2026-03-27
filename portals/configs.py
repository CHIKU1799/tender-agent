"""
Portal registry — defines every supported tender portal.

Platform types:
  gepnic      → NIC GePNIC JSP platform (same codebase, different domains)
                CAPTCHA bypass: direct results URL works without search form submission
  gem_api     → Government e-Marketplace REST API (no browser needed)
  ireps       → Indian Railways e-Procurement System
  etenders    → NIC eTenders platform (etenders.gov.in) — different from GePNIC
  generic     → Any portal needing custom Playwright scraping
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PortalConfig:
    portal_id:        str
    display_name:     str
    base_url:         str
    platform:         str          # gepnic | gem_api | ireps | etenders | generic
    category:         str          # Central | PSU | State | Info

    # GePNIC / eTenders fields
    session_seed_url: str  = ""    # First URL to hit → establishes JSESSIONID
    results_url:      str  = ""    # Direct results URL (CAPTCHA-free for GePNIC)
    next_btn:         str  = "#linkFwd"
    row_selector:     str  = "table.list_table tr.even, table.list_table tr.odd, #table tr.even, #table tr.odd"
    col_map:          dict = field(default_factory=lambda: {
        "sno": 0, "published_date": 1, "closing_date": 2,
        "opening_date": 3, "title_raw": 4, "organisation": 5
    })

    # GeM API fields
    api_base:         str  = ""

    # Archive / awards URLs (GePNIC portals only)
    archive_url:      str  = ""    # Past/closed tenders (requires CAPTCHA solve)
    awards_url:       str  = ""    # Result of tenders / awarded contracts (requires CAPTCHA solve)

    # Display
    emoji:            str  = "🏛️"
    notes:            str  = ""     # Any known quirks


# ─────────────────────────────────────────────────────────────────────────────
# CENTRAL GOVERNMENT PORTALS
# ─────────────────────────────────────────────────────────────────────────────

PORTALS: dict[str, PortalConfig] = {

    # ── GeM ───────────────────────────────────────────────────────────────────
    "gem": PortalConfig(
        portal_id    = "gem",
        display_name = "Government e-Marketplace (GeM)",
        base_url     = "https://gem.gov.in",
        platform     = "gem_api",
        category     = "Central",
        api_base     = "https://bidplus.gem.gov.in/rest/bidlists",
        emoji        = "💎",
        notes        = "Public REST API — no browser needed",
    ),

    # ── CPPP / eprocure ───────────────────────────────────────────────────────
    "cppp": PortalConfig(
        portal_id    = "cppp",
        display_name = "Central Public Procurement Portal (CPPP)",
        base_url     = "https://eprocure.gov.in/cppp",
        platform     = "cppp",
        category     = "Central",
        results_url  = "https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata",
        emoji        = "🏛️",
        notes        = "Cross-ministry aggregator — no CAPTCHA, paginated, ~10 tenders/page",
    ),

    # ── Ministry of Defence ───────────────────────────────────────────────────
    "defproc": PortalConfig(
        portal_id    = "defproc",
        display_name = "Ministry of Defence (defproc.gov.in)",
        base_url     = "https://defproc.gov.in/nicgep/app",
        platform     = "gepnic",
        category     = "Central",
        session_seed_url = "https://defproc.gov.in/nicgep/app?page=FrontEndLatestActiveTenders&service=page",
        results_url  = "https://defproc.gov.in/nicgep/app?component=%24DirectLink&page=FrontEndAdvancedSearchResult&service=direct",
        archive_url  = "https://defproc.gov.in/nicgep/app?page=FrontEndTendersInArchive&service=page",
        awards_url   = "https://defproc.gov.in/nicgep/app?page=FrontEndResultOfTenders&service=page",
        emoji        = "🛡️",
        notes        = "MES, BRO, Navy, Air Force, Army tenders",
    ),

    # ── Railways / IREPS ─────────────────────────────────────────────────────
    "ireps": PortalConfig(
        portal_id    = "ireps",
        display_name = "Indian Railways e-Procurement (IREPS)",
        base_url     = "https://www.ireps.gov.in",
        platform     = "ireps",
        category     = "Central",
        session_seed_url = "https://www.ireps.gov.in/ireps/tender/tenderSearchPublic.action",
        results_url  = "https://www.ireps.gov.in/ireps/tender/tenderSearchPublic.action",
        emoji        = "🚂",
        notes        = "Struts 2 platform; search uses POST with railway zone params",
    ),

    # ── NHAI / MoRTH / etenders.gov.in ───────────────────────────────────────
    "etenders": PortalConfig(
        portal_id    = "etenders",
        display_name = "NIC eTenders (NHAI / MoRTH / BSNL)",
        base_url     = "https://etenders.gov.in/eprocure/app",
        platform     = "gepnic",
        category     = "Central",
        session_seed_url = "https://etenders.gov.in/eprocure/app?page=FrontEndLatestActiveTenders&service=page",
        results_url  = "https://etenders.gov.in/eprocure/app?component=%24DirectLink&page=FrontEndAdvancedSearchResult&service=direct",
        archive_url  = "https://etenders.gov.in/eprocure/app?page=FrontEndTendersInArchive&service=page",
        awards_url   = "https://etenders.gov.in/eprocure/app?page=FrontEndResultOfTenders&service=page",
        emoji        = "🛣️",
        notes        = "Hosts NHAI, MoRTH, BSNL and other central org tenders",
    ),

    # ── NTPC ─────────────────────────────────────────────────────────────────
    "ntpc": PortalConfig(
        portal_id    = "ntpc",
        display_name = "NTPC e-Procurement",
        base_url     = "https://eprocurentpc.nic.in/nicgep/app",
        platform     = "gepnic",
        category     = "PSU",
        session_seed_url = "https://eprocurentpc.nic.in/nicgep/app?page=FrontEndLatestActiveTenders&service=page",
        results_url  = "https://eprocurentpc.nic.in/nicgep/app?component=%24DirectLink&page=FrontEndAdvancedSearchResult&service=direct",
        archive_url  = "https://eprocurentpc.nic.in/nicgep/app?page=FrontEndTendersInArchive&service=page",
        awards_url   = "https://eprocurentpc.nic.in/nicgep/app?page=FrontEndResultOfTenders&service=page",
        emoji        = "⚡",
        notes        = "National Thermal Power Corporation",
    ),

    # ── BHEL ─────────────────────────────────────────────────────────────────
    "bhel": PortalConfig(
        portal_id    = "bhel",
        display_name = "BHEL e-Procurement",
        base_url     = "https://eprocure.bhel.co.in",
        platform     = "generic",
        category     = "PSU",
        session_seed_url = "https://eprocure.bhel.co.in",
        results_url  = "https://eprocure.bhel.co.in",
        emoji        = "⚙️",
        notes        = "SAP Ariba-based platform; requires further exploration",
    ),

    # ── Coal India ────────────────────────────────────────────────────────────
    "coalindia": PortalConfig(
        portal_id    = "coalindia",
        display_name = "Coal India Tenders",
        base_url     = "https://coalindiatenders.nic.in/nicgep/app",
        platform     = "gepnic",
        category     = "PSU",
        session_seed_url = "https://coalindiatenders.nic.in/nicgep/app?page=FrontEndLatestActiveTenders&service=page",
        results_url  = "https://coalindiatenders.nic.in/nicgep/app?component=%24DirectLink&page=FrontEndAdvancedSearchResult&service=direct",
        archive_url  = "https://coalindiatenders.nic.in/nicgep/app?page=FrontEndTendersInArchive&service=page",
        awards_url   = "https://coalindiatenders.nic.in/nicgep/app?page=FrontEndResultOfTenders&service=page",
        emoji        = "⛏️",
        notes        = "Coal India Limited and subsidiaries",
    ),

    # ── ONGC ─────────────────────────────────────────────────────────────────
    "ongc": PortalConfig(
        portal_id    = "ongc",
        display_name = "ONGC e-Tender",
        base_url     = "https://etender.ongc.co.in",
        platform     = "generic",
        category     = "PSU",
        session_seed_url = "https://etender.ongc.co.in",
        results_url  = "https://etender.ongc.co.in",
        emoji        = "🛢️",
        notes        = "Oil and Natural Gas Corporation; platform requires exploration",
    ),

    # ── HAL ───────────────────────────────────────────────────────────────────
    "hal": PortalConfig(
        portal_id    = "hal",
        display_name = "HAL e-Procurement",
        base_url     = "https://eproc.hal-india.co.in",
        platform     = "generic",
        category     = "PSU",
        session_seed_url = "https://eproc.hal-india.co.in",
        results_url  = "https://eproc.hal-india.co.in",
        emoji        = "✈️",
        notes        = "Hindustan Aeronautics Limited; platform requires exploration",
    ),

    # ─────────────────────────────────────────────────────────────────────────
    # STATE PORTALS
    # ─────────────────────────────────────────────────────────────────────────

    # ── Karnataka ────────────────────────────────────────────────────────────
    "karnataka": PortalConfig(
        portal_id    = "karnataka",
        display_name = "Karnataka e-Procurement",
        base_url     = "https://eproc.karnataka.gov.in/eprocportal/pages/index.jsp",
        platform     = "karnataka_seam",
        category     = "State",
        session_seed_url = "https://eproc.karnataka.gov.in/eprocportal/pages/index.jsp",
        results_url  = "https://eproc.karnataka.gov.in/eprocurement/common/eproc_tenders_list.seam",
        emoji        = "🌿",
        notes        = "JSF/Seam portal — form-based search with date range, no CAPTCHA",
    ),

    # ── Maharashtra ───────────────────────────────────────────────────────────
    "maharashtra": PortalConfig(
        portal_id    = "maharashtra",
        display_name = "Maharashtra Tenders (mahatenders.gov.in)",
        base_url     = "https://mahatenders.gov.in/nicgep/app",
        platform     = "gepnic",
        category     = "State",
        session_seed_url = "https://mahatenders.gov.in/nicgep/app?page=FrontEndLatestActiveTenders&service=page",
        results_url  = "https://mahatenders.gov.in/nicgep/app?component=%24DirectLink&page=FrontEndAdvancedSearchResult&service=direct",
        archive_url  = "https://mahatenders.gov.in/nicgep/app?page=FrontEndTendersInArchive&service=page",
        awards_url   = "https://mahatenders.gov.in/nicgep/app?page=FrontEndResultOfTenders&service=page",
        emoji        = "🦁",
    ),

    # ── Uttar Pradesh ─────────────────────────────────────────────────────────
    "up": PortalConfig(
        portal_id    = "up",
        display_name = "Uttar Pradesh e-Tender",
        base_url     = "https://etender.up.nic.in/nicgep/app",
        platform     = "gepnic",
        category     = "State",
        session_seed_url = "https://etender.up.nic.in/nicgep/app?page=FrontEndLatestActiveTenders&service=page",
        results_url  = "https://etender.up.nic.in/nicgep/app?component=%24DirectLink&page=FrontEndAdvancedSearchResult&service=direct",
        archive_url  = "https://etender.up.nic.in/nicgep/app?page=FrontEndTendersInArchive&service=page",
        awards_url   = "https://etender.up.nic.in/nicgep/app?page=FrontEndResultOfTenders&service=page",
        emoji        = "🏰",
        notes        = "Also hosts Smart Cities and AMRUT/Jal Shakti tenders",
    ),

    # ── Tamil Nadu ───────────────────────────────────────────────────────────
    "tamilnadu": PortalConfig(
        portal_id    = "tamilnadu",
        display_name = "Tamil Nadu Tenders (tntenders.gov.in)",
        base_url     = "https://tntenders.gov.in/nicgep/app",
        platform     = "gepnic",
        category     = "State",
        session_seed_url = "https://tntenders.gov.in/nicgep/app?page=FrontEndLatestActiveTenders&service=page",
        results_url  = "https://tntenders.gov.in/nicgep/app?component=%24DirectLink&page=FrontEndAdvancedSearchResult&service=direct",
        archive_url  = "https://tntenders.gov.in/nicgep/app?page=FrontEndTendersInArchive&service=page",
        awards_url   = "https://tntenders.gov.in/nicgep/app?page=FrontEndResultOfTenders&service=page",
        emoji        = "🌺",
    ),

    # ── Gujarat ──────────────────────────────────────────────────────────────
    "gujarat": PortalConfig(
        portal_id    = "gujarat",
        display_name = "Gujarat Tenders (nprocure)",
        base_url     = "https://nprocure.com/eprocure/app",
        platform     = "gepnic",
        category     = "State",
        session_seed_url = "https://nprocure.com/eprocure/app?page=FrontEndLatestActiveTenders&service=page",
        results_url  = "https://nprocure.com/eprocure/app?component=%24DirectLink&page=FrontEndAdvancedSearchResult&service=direct",
        archive_url  = "https://nprocure.com/eprocure/app?page=FrontEndTendersInArchive&service=page",
        awards_url   = "https://nprocure.com/eprocure/app?page=FrontEndResultOfTenders&service=page",
        emoji        = "💛",
        notes        = "Gujarat uses nprocure.com portal",
    ),

    # ── Rajasthan ─────────────────────────────────────────────────────────────
    "rajasthan": PortalConfig(
        portal_id    = "rajasthan",
        display_name = "Rajasthan e-Procurement",
        base_url     = "https://eproc.rajasthan.gov.in/nicgep/app",
        platform     = "gepnic",
        category     = "State",
        session_seed_url = "https://eproc.rajasthan.gov.in/nicgep/app?page=FrontEndLatestActiveTenders&service=page",
        results_url  = "https://eproc.rajasthan.gov.in/nicgep/app?component=%24DirectLink&page=FrontEndAdvancedSearchResult&service=direct",
        archive_url  = "https://eproc.rajasthan.gov.in/nicgep/app?page=FrontEndTendersInArchive&service=page",
        awards_url   = "https://eproc.rajasthan.gov.in/nicgep/app?page=FrontEndResultOfTenders&service=page",
        emoji        = "🏜️",
    ),

    # ─────────────────────────────────────────────────────────────────────────
    # INFO / SCHEME PORTALS (tender notices on ministry websites)
    # ─────────────────────────────────────────────────────────────────────────

    "nhm": PortalConfig(
        portal_id    = "nhm",
        display_name = "NHM / Health Ministry Tenders",
        base_url     = "https://nhm.gov.in",
        platform     = "generic",
        category     = "Info",
        results_url  = "https://nhm.gov.in/index1.php?lang=1&level=0&linkid=626&lid=1059",
        emoji        = "🏥",
        notes        = "HTML tender notice listings — no structured search",
    ),

    "meity": PortalConfig(
        portal_id    = "meity",
        display_name = "MeitY / Digital India Tenders",
        base_url     = "https://www.meity.gov.in",
        platform     = "generic",
        category     = "Info",
        results_url  = "https://www.meity.gov.in/tenders",
        emoji        = "💻",
        notes        = "Ministry of Electronics and IT",
    ),

    "education": PortalConfig(
        portal_id    = "education",
        display_name = "Education Ministry Tenders (NEP)",
        base_url     = "https://www.education.gov.in",
        platform     = "generic",
        category     = "Info",
        results_url  = "https://www.education.gov.in/en/tenders",
        emoji        = "📚",
        notes        = "Ministry of Education — mostly PDF tender notices",
    ),
}

# ── Grouped views ─────────────────────────────────────────────────────────────

def by_category(category: str) -> dict[str, PortalConfig]:
    return {pid: cfg for pid, cfg in PORTALS.items() if cfg.category == category}

def by_platform(platform: str) -> dict[str, PortalConfig]:
    return {pid: cfg for pid, cfg in PORTALS.items() if cfg.platform == platform}

GEPNIC_PORTALS   = by_platform("gepnic")
API_PORTALS      = by_platform("gem_api")
GENERIC_PORTALS  = by_platform("generic")
