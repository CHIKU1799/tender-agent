"""
Extra Indian Tender Portals — 40+ additional sources.
Includes: Tender247, TenderTiger, state portals, PSU portals,
municipal corporations, and ministry-specific portals.

Import this in portals/configs.py:
    from portals.extra_portals import EXTRA_PORTALS
    PORTALS.update(EXTRA_PORTALS)
"""
from __future__ import annotations
from portals.configs import PortalConfig


def _auto_gepnic(cfg: PortalConfig):
    """Auto-populate GePNIC URLs from base_url."""
    if cfg.platform == "gepnic" and cfg.base_url:
        base = cfg.base_url.rstrip("/")
        if not cfg.session_seed_url:
            cfg.session_seed_url = f"{base}/nicgep/app?page=FrontEndLatestActiveTenders&service=page"
        if not cfg.results_url:
            cfg.results_url = f"{base}/nicgep/app?component=%24DirectLink&page=FrontEndAdvancedSearchResult&service=direct"
        if not cfg.archive_url:
            cfg.archive_url = f"{base}/nicgep/app?page=FrontEndTendersInArchive&service=page"
        if not cfg.awards_url:
            cfg.awards_url = f"{base}/nicgep/app?page=FrontEndResultOfTenders&service=page"
    return cfg


EXTRA_PORTALS: dict[str, PortalConfig] = {

    # ══════════════════════════════════════════════════════
    # AGGREGATOR PORTALS  (have ALL India tenders in one place)
    # ══════════════════════════════════════════════════════

    "tender247": PortalConfig(
        portal_id    = "tender247",
        display_name = "Tender247",
        base_url     = "https://www.tender247.com",
        platform     = "tender247",
        category     = "Aggregator",
        emoji        = "📡",
        results_url  = "https://www.tender247.com/keyword/+/0/0/0/0/0/0/0/1",
        archive_url  = "https://www.tender247.com/closed-tenders",
        awards_url   = "https://www.tender247.com/awarded-tenders",
        notes        = "Large aggregator — active, archive, awarded",
    ),

    "tendertiger": PortalConfig(
        portal_id    = "tendertiger",
        display_name = "TenderTiger",
        base_url     = "https://www.tendertiger.com",
        platform     = "tendertiger",
        category     = "Aggregator",
        emoji        = "🐯",
        results_url  = "https://www.tendertiger.com/tender/tenders.aspx",
        archive_url  = "https://www.tendertiger.com/tender/closed-tenders.aspx",
        notes        = "Aggregator with 1M+ tenders",
    ),

    "bidassist": PortalConfig(
        portal_id    = "bidassist",
        display_name = "BidAssist",
        base_url     = "https://bidassist.com",
        platform     = "bidassist",
        category     = "Aggregator",
        emoji        = "🤝",
        results_url  = "https://bidassist.com/tenders",
        notes        = "AI-powered aggregator",
    ),

    "tendernews": PortalConfig(
        portal_id    = "tendernews",
        display_name = "TenderNews",
        base_url     = "https://www.tendernews.com",
        platform     = "generic",
        category     = "Aggregator",
        emoji        = "📰",
        results_url  = "https://www.tendernews.com/tenders/",
        notes        = "News + tender listing",
    ),

    "etenders_in": PortalConfig(
        portal_id    = "etenders_in",
        display_name = "eTenders.in",
        base_url     = "https://www.etenders.in",
        platform     = "generic",
        category     = "Aggregator",
        emoji        = "💻",
        results_url  = "https://www.etenders.in/tenders/",
        notes        = "Free tender aggregator",
    ),

    "tendersonline": PortalConfig(
        portal_id    = "tendersonline",
        display_name = "TendersOnline",
        base_url     = "https://www.tendersonline.in",
        platform     = "generic",
        category     = "Aggregator",
        emoji        = "🌐",
        results_url  = "https://www.tendersonline.in/",
    ),

    # ══════════════════════════════════════════════════════
    # STATE PORTALS
    # ══════════════════════════════════════════════════════

    "delhi": PortalConfig(
        portal_id    = "delhi",
        display_name = "Delhi e-Procurement",
        base_url     = "https://etenders.delhi.gov.in",
        platform     = "gepnic",
        category     = "State",
        emoji        = "🏙️",
        archive_url  = "https://etenders.delhi.gov.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
        notes        = "State: Delhi — GePNIC platform",
    ),

    "andhra": PortalConfig(
        portal_id    = "andhra",
        display_name = "Andhra Pradesh eProcurement",
        base_url     = "https://tender.apeprocurement.gov.in",
        platform     = "gepnic",
        category     = "State",
        emoji        = "🌊",
        notes        = "State: Andhra Pradesh",
        archive_url  = "https://tender.apeprocurement.gov.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
    ),

    "telangana": PortalConfig(
        portal_id    = "telangana",
        display_name = "Telangana eProcurement",
        base_url     = "https://tender.telangana.gov.in",
        platform     = "gepnic",
        category     = "State",
        emoji        = "🦚",
        notes        = "State: Telangana",
        archive_url  = "https://tender.telangana.gov.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
    ),

    "kerala": PortalConfig(
        portal_id    = "kerala",
        display_name = "Kerala e-Procurement",
        base_url     = "https://www.etenders.kerala.gov.in",
        platform     = "gepnic",
        category     = "State",
        emoji        = "🌴",
        notes        = "State: Kerala",
        archive_url  = "https://www.etenders.kerala.gov.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
    ),

    "westbengal": PortalConfig(
        portal_id    = "westbengal",
        display_name = "West Bengal e-Tender",
        base_url     = "https://wbtenders.gov.in",
        platform     = "gepnic",
        category     = "State",
        emoji        = "🐯",
        notes        = "State: West Bengal",
        archive_url  = "https://wbtenders.gov.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
    ),

    "bihar": PortalConfig(
        portal_id    = "bihar",
        display_name = "Bihar e-Procurement",
        base_url     = "https://eproc.bihar.gov.in",
        platform     = "gepnic",
        category     = "State",
        emoji        = "🏯",
        notes        = "State: Bihar",
        archive_url  = "https://eproc.bihar.gov.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
    ),

    "odisha": PortalConfig(
        portal_id    = "odisha",
        display_name = "Odisha e-Procurement",
        base_url     = "https://tendersodisha.gov.in",
        platform     = "gepnic",
        category     = "State",
        emoji        = "🛕",
        notes        = "State: Odisha",
        archive_url  = "https://tendersodisha.gov.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
    ),

    "mp": PortalConfig(
        portal_id    = "mp",
        display_name = "Madhya Pradesh e-Procurement",
        base_url     = "https://mpeproc.gov.in",
        platform     = "gepnic",
        category     = "State",
        emoji        = "🌿",
        notes        = "State: Madhya Pradesh",
        archive_url  = "https://mpeproc.gov.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
    ),

    "chhattisgarh": PortalConfig(
        portal_id    = "chhattisgarh",
        display_name = "Chhattisgarh e-Procurement",
        base_url     = "https://eproc.cgstate.gov.in",
        platform     = "gepnic",
        category     = "State",
        emoji        = "🌾",
        notes        = "State: Chhattisgarh",
        archive_url  = "https://eproc.cgstate.gov.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
    ),

    "jharkhand": PortalConfig(
        portal_id    = "jharkhand",
        display_name = "Jharkhand e-Procurement",
        base_url     = "https://jharkhandtenders.gov.in",
        platform     = "gepnic",
        category     = "State",
        emoji        = "⛏️",
        notes        = "State: Jharkhand",
        archive_url  = "https://jharkhandtenders.gov.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
    ),

    "uttarakhand": PortalConfig(
        portal_id    = "uttarakhand",
        display_name = "Uttarakhand e-Procurement",
        base_url     = "https://uktenders.gov.in",
        platform     = "gepnic",
        category     = "State",
        emoji        = "🏔️",
        notes        = "State: Uttarakhand",
        archive_url  = "https://uktenders.gov.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
    ),

    "himachal": PortalConfig(
        portal_id    = "himachal",
        display_name = "Himachal Pradesh e-Procurement",
        base_url     = "https://hptenders.gov.in",
        platform     = "gepnic",
        category     = "State",
        emoji        = "❄️",
        notes        = "State: Himachal Pradesh",
        archive_url  = "https://hptenders.gov.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
    ),

    "haryana": PortalConfig(
        portal_id    = "haryana",
        display_name = "Haryana e-Procurement",
        base_url     = "https://etenders.hry.nic.in",
        platform     = "gepnic",
        category     = "State",
        emoji        = "🌾",
        notes        = "State: Haryana",
        archive_url  = "https://etenders.hry.nic.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
    ),

    "punjab": PortalConfig(
        portal_id    = "punjab",
        display_name = "Punjab e-Procurement",
        base_url     = "https://eproc.punjab.gov.in",
        platform     = "gepnic",
        category     = "State",
        emoji        = "🌻",
        notes        = "State: Punjab",
        archive_url  = "https://eproc.punjab.gov.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
    ),

    "assam": PortalConfig(
        portal_id    = "assam",
        display_name = "Assam e-Procurement",
        base_url     = "https://assamtenders.gov.in",
        platform     = "gepnic",
        category     = "State",
        emoji        = "🍃",
        notes        = "State: Assam",
        archive_url  = "https://assamtenders.gov.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
    ),

    "goa": PortalConfig(
        portal_id    = "goa",
        display_name = "Goa e-Procurement",
        base_url     = "https://goatenders.gov.in",
        platform     = "gepnic",
        category     = "State",
        emoji        = "🏖️",
        notes        = "State: Goa",
        archive_url  = "https://goatenders.gov.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
    ),

    "jk": PortalConfig(
        portal_id    = "jk",
        display_name = "J&K e-Procurement",
        base_url     = "https://jktenders.gov.in",
        platform     = "gepnic",
        category     = "State",
        emoji        = "🏔️",
        notes        = "State: Jammu & Kashmir",
        archive_url  = "https://jktenders.gov.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
    ),

    "tripura": PortalConfig(
        portal_id    = "tripura",
        display_name = "Tripura e-Procurement",
        base_url     = "https://tripuratenders.gov.in",
        platform     = "gepnic",
        category     = "State",
        emoji        = "🌺",
        notes        = "State: Tripura",
        archive_url  = "https://tripuratenders.gov.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
    ),

    "manipur": PortalConfig(
        portal_id    = "manipur",
        display_name = "Manipur e-Procurement",
        base_url     = "https://manipurtenders.gov.in",
        platform     = "gepnic",
        category     = "State",
        emoji        = "💐",
        notes        = "State: Manipur",
        archive_url  = "https://manipurtenders.gov.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
    ),

    "meghalaya": PortalConfig(
        portal_id    = "meghalaya",
        display_name = "Meghalaya e-Procurement",
        base_url     = "https://megtenders.gov.in",
        platform     = "gepnic",
        category     = "State",
        emoji        = "☁️",
        notes        = "State: Meghalaya",
        archive_url  = "https://megtenders.gov.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
    ),

    # ══════════════════════════════════════════════════════
    # PSU / CENTRAL ORGANISATION PORTALS
    # ══════════════════════════════════════════════════════

    "nhai": PortalConfig(
        portal_id    = "nhai",
        display_name = "NHAI (National Highway)",
        base_url     = "https://etender.nhai.gov.in",
        platform     = "gepnic",
        category     = "PSU",
        emoji        = "🛣️",
        archive_url  = "https://etender.nhai.gov.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
        awards_url   = "https://etender.nhai.gov.in/nicgep/app?component=AwardedTender&page=AwardedTender&service=page",
    ),

    "bsnl": PortalConfig(
        portal_id    = "bsnl",
        display_name = "BSNL",
        base_url     = "https://etender.bsnl.co.in",
        platform     = "gepnic",
        category     = "PSU",
        emoji        = "📞",
        archive_url  = "https://etender.bsnl.co.in/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
        awards_url   = "https://etender.bsnl.co.in/nicgep/app?component=AwardedTender&page=AwardedTender&service=page",
    ),

    "gail": PortalConfig(
        portal_id    = "gail",
        display_name = "GAIL",
        base_url     = "https://etender.gail.co.in",
        platform     = "generic",
        category     = "PSU",
        emoji        = "⛽",
        results_url  = "https://etender.gail.co.in/irj/portal/",
        notes        = "SAP Ariba portal",
    ),

    "ioc": PortalConfig(
        portal_id    = "ioc",
        display_name = "Indian Oil (IOC)",
        base_url     = "https://iocl.com/tenders",
        platform     = "generic",
        category     = "PSU",
        emoji        = "🛢️",
        results_url  = "https://iocl.com/tenders",
    ),

    "bpcl": PortalConfig(
        portal_id    = "bpcl",
        display_name = "BPCL",
        base_url     = "https://www.bpcl.in/bpcl/tenders",
        platform     = "generic",
        category     = "PSU",
        emoji        = "⛽",
        results_url  = "https://www.bpcl.in/bpcl/tenders",
    ),

    "nhpc": PortalConfig(
        portal_id    = "nhpc",
        display_name = "NHPC",
        base_url     = "https://etender.nhpcindia.com",
        platform     = "gepnic",
        category     = "PSU",
        emoji        = "💧",
        archive_url  = "https://etender.nhpcindia.com/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
    ),

    "powergrid": PortalConfig(
        portal_id    = "powergrid",
        display_name = "Power Grid Corporation",
        base_url     = "https://etender.powergridindia.com",
        platform     = "gepnic",
        category     = "PSU",
        emoji        = "⚡",
        archive_url  = "https://etender.powergridindia.com/nicgep/app?component=BasicSearchTender&page=BasicSearchTender&service=page",
        awards_url   = "https://etender.powergridindia.com/nicgep/app?component=AwardedTender&page=AwardedTender&service=page",
    ),

    "sail": PortalConfig(
        portal_id    = "sail",
        display_name = "SAIL",
        base_url     = "https://www.sail.co.in/en/tenders",
        platform     = "generic",
        category     = "PSU",
        emoji        = "⚙️",
        results_url  = "https://www.sail.co.in/en/tenders",
    ),

    "bel": PortalConfig(
        portal_id    = "bel",
        display_name = "BEL (Bharat Electronics)",
        base_url     = "https://bel-india.in/tenders",
        platform     = "generic",
        category     = "PSU",
        emoji        = "📡",
        results_url  = "https://bel-india.in/tenders",
    ),

    "drdo": PortalConfig(
        portal_id    = "drdo",
        display_name = "DRDO",
        base_url     = "https://www.drdo.gov.in/drdo/pub/tenders",
        platform     = "generic",
        category     = "Central",
        emoji        = "🔬",
        results_url  = "https://www.drdo.gov.in/drdo/pub/tenders",
    ),

    "isro": PortalConfig(
        portal_id    = "isro",
        display_name = "ISRO",
        base_url     = "https://www.isro.gov.in/Tenders.html",
        platform     = "generic",
        category     = "Central",
        emoji        = "🚀",
        results_url  = "https://www.isro.gov.in/Tenders.html",
    ),

    "aai": PortalConfig(
        portal_id    = "aai",
        display_name = "Airport Authority of India",
        base_url     = "https://www.aai.aero/en/tenders",
        platform     = "generic",
        category     = "PSU",
        emoji        = "✈️",
        results_url  = "https://www.aai.aero/en/tenders",
    ),

    "ircon": PortalConfig(
        portal_id    = "ircon",
        display_name = "IRCON International",
        base_url     = "https://www.ircon.org/index.php/tenders",
        platform     = "generic",
        category     = "PSU",
        emoji        = "🚂",
        results_url  = "https://www.ircon.org/index.php/tenders",
    ),

    "rites": PortalConfig(
        portal_id    = "rites",
        display_name = "RITES Ltd",
        base_url     = "https://www.rites.com/web/tenders.php",
        platform     = "generic",
        category     = "PSU",
        emoji        = "🚆",
        results_url  = "https://www.rites.com/web/tenders.php",
    ),

    # ══════════════════════════════════════════════════════
    # MUNICIPAL / SMART CITY PORTALS
    # ══════════════════════════════════════════════════════

    "bbmp": PortalConfig(
        portal_id    = "bbmp",
        display_name = "BBMP (Bengaluru Municipal)",
        base_url     = "https://bbmptenders.in",
        platform     = "generic",
        category     = "Municipal",
        emoji        = "🏙️",
        notes        = "State: Karnataka",
        results_url  = "https://bbmptenders.in",
    ),

    "mcgm": PortalConfig(
        portal_id    = "mcgm",
        display_name = "MCGM (Mumbai Municipal)",
        base_url     = "https://mcgm.gov.in/gndbPortal/pFrmTenderList.aspx",
        platform     = "generic",
        category     = "Municipal",
        emoji        = "🌆",
        notes        = "State: Maharashtra",
        results_url  = "https://mcgm.gov.in/gndbPortal/pFrmTenderList.aspx",
    ),

    "nmmc": PortalConfig(
        portal_id    = "nmmc",
        display_name = "NMMC (Navi Mumbai)",
        base_url     = "https://www.nmmc.gov.in/tender",
        platform     = "generic",
        category     = "Municipal",
        emoji        = "🏗️",
        notes        = "State: Maharashtra",
        results_url  = "https://www.nmmc.gov.in/tender",
    ),

    "smartcities": PortalConfig(
        portal_id    = "smartcities",
        display_name = "Smart Cities Mission",
        base_url     = "https://smartcities.gov.in/tenders",
        platform     = "generic",
        category     = "Central",
        emoji        = "🌆",
        results_url  = "https://smartcities.gov.in/tenders",
    ),
}

# Auto-populate GePNIC URLs for all portals that have empty results_url
for _pid, _cfg in EXTRA_PORTALS.items():
    _auto_gepnic(_cfg)
