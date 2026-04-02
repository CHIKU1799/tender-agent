"""
Run this ONCE to verify scope is flowing correctly end-to-end.
Usage: python scope_debug.py
"""
import json, sys
sys.path.insert(0, ".")

from portals.configs import PORTALS

print("\n=== SCOPE ROUTING CHECK ===\n")

scopes = ["active", "archive", "awards", "both", "all"]

for scope in scopes:
    print(f"Scope: {scope!r}")
    for pid, cfg in list(PORTALS.items())[:5]:
        # Simulate _make_agent routing
        if scope in ("archive", "awards", "both") and cfg.platform == "gepnic":
            agent = "GePNICArchiveAgent ✓"
        elif scope in ("archive", "awards", "both") and cfg.platform == "cppp":
            agent = "CPPPArchiveAgent ✓"
        elif scope in ("archive", "awards", "both") and cfg.platform == "gem_api":
            agent = "GeMArchiveAgent ✓"
        elif scope == "active" or scope not in ("archive","awards","both","all"):
            agent = f"{cfg.platform}Agent (ACTIVE only ⚠)"
        else:
            agent = f"GenericAgent (ACTIVE only ⚠)"
        print(f"  {pid:20} → {agent}")
    print()
