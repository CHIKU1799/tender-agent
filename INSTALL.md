# Installing the 9 New Portals

## Step 1 — Copy new files into your project

| File in zip              | Copy to                                      |
|--------------------------|----------------------------------------------|
| portals/new_portals.py   | tender-agent-full\portals\new_portals.py     |
| agents/tenderdetail.py   | tender-agent-full\agents\tenderdetail.py     |
| agents/tendertiger.py    | tender-agent-full\agents\tendertiger.py      |
| agents/palladium.py      | tender-agent-full\agents\palladium.py        |

---

## Step 2 — Edit portals/configs.py (add 2 lines at the bottom)

Open:  tender-agent-full\portals\configs.py
Add at the very BOTTOM:

    from portals.new_portals import NEW_PORTALS
    PORTALS.update(NEW_PORTALS)

---

## Step 3 — Edit core/orchestrator.py

Open:  tender-agent-full\core\orchestrator.py

Find this line (near end of _make_agent function):
    else:                                   return GenericAgent(cfg, session)

REPLACE it with:

    elif cfg.platform == "tenderdetail":
        from agents.tenderdetail import TenderDetailAgent
        return TenderDetailAgent(cfg, session, scope=scope if is_archive else "active")

    elif cfg.platform == "tendertiger":
        from agents.tendertiger import TenderTigerAgent
        return TenderTigerAgent(cfg, session, scope=scope if is_archive else "active")

    elif cfg.platform == "tender247":
        from agents.tender247 import Tender247Agent
        return Tender247Agent(cfg, session, scope=scope if is_archive else "active")

    elif cfg.platform == "palladium":
        from agents.palladium import PalladiumAgent
        return PalladiumAgent(cfg, session, scope=scope if is_archive else "active")

    else:
        from agents.universal import UniversalAgent
        return UniversalAgent(cfg, session, scope=scope if is_archive else "active")

---

## Step 4 — Run

    python dashboard.py

The 9 new portals will appear in the sidebar grouped by category.

---

## Portal Summary

| Portal ID       | Site                                    | Type       | State         |
|-----------------|-----------------------------------------|------------|---------------|
| tenderdetail    | tenderdetail.com                        | Aggregator | All India     |
| tendertiger     | tendertiger.com                         | Aggregator | All India     |
| delhi_gep       | govtprocurement.delhi.gov.in            | GePNIC     | Delhi         |
| mp_tenders      | mptenders.gov.in                        | GePNIC     | M.P.          |
| uk_tenders      | uktenders.gov.in                        | GePNIC     | Uttarakhand   |
| tenderkart      | tenderkart.in                           | Aggregator | All India     |
| tender247_new   | tender247.com                           | Aggregator | All India     |
| gujarat_tenders | gujarattenders.in                       | State      | Gujarat       |
| palladium       | app.palladium.primenumbers.in           | PSU/Corp   | All India     |

Note: delhi_gep, mp_tenders, uk_tenders use the GePNIC platform
      so archive + awarded data is fully supported via GePNICArchiveAgent.
