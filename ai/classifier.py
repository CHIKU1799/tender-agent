"""
Tender industry classifier — uses GPT-4o-mini to categorise Indian govt
tenders into standard industry buckets.  Includes a free keyword-based
fallback for when no API key is available.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from ai.client import get_client

log = logging.getLogger("ai.classifier")

# ---------------------------------------------------------------------------
# Standard category taxonomy
# ---------------------------------------------------------------------------
TENDER_CATEGORIES: list[str] = [
    "IT Services & Software",
    "Construction & Civil Works",
    "Medical & Healthcare",
    "Food & Catering",
    "Transportation & Logistics",
    "Electrical & Power",
    "Water & Sanitation",
    "Education & Training",
    "Security & Defence",
    "Agriculture & Farming",
    "Printing & Stationery",
    "Furniture & Fittings",
    "Consulting & Professional Services",
    "Telecommunications",
    "Environmental Services",
    "Mining & Minerals",
    "Textiles & Garments",
    "Chemicals & Pharmaceuticals",
    "Machinery & Equipment",
    "Road & Highway",
    "Railway",
    "Real Estate & Housing",
    "Oil & Gas",
    "Banking & Finance",
    "Other",
]

# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------
# GPT-4o-mini pricing (as of 2025): $0.15 / 1M input, $0.60 / 1M output
_INPUT_COST_PER_TOKEN = 0.15 / 1_000_000
_OUTPUT_COST_PER_TOKEN = 0.60 / 1_000_000

_total_input_tokens = 0
_total_output_tokens = 0
_total_cost_usd = 0.0


def _track_cost(usage: Any) -> None:
    """Accumulate token usage and log estimated cost."""
    global _total_input_tokens, _total_output_tokens, _total_cost_usd
    if usage is None:
        return
    inp = getattr(usage, "prompt_tokens", 0) or 0
    out = getattr(usage, "completion_tokens", 0) or 0
    cost = inp * _INPUT_COST_PER_TOKEN + out * _OUTPUT_COST_PER_TOKEN
    _total_input_tokens += inp
    _total_output_tokens += out
    _total_cost_usd += cost
    log.debug(
        "Token usage — input: %d, output: %d, batch cost: $%.6f, "
        "cumulative cost: $%.6f",
        inp, out, cost, _total_cost_usd,
    )


def get_cumulative_cost() -> dict:
    """Return cumulative token usage and estimated spend."""
    return {
        "input_tokens": _total_input_tokens,
        "output_tokens": _total_output_tokens,
        "estimated_cost_usd": round(_total_cost_usd, 6),
    }


# ---------------------------------------------------------------------------
# System prompt shared by single and batch classifiers
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "You are an expert classifier for Indian government tenders published on "
    "portals such as GEM, CPPP, eProcure, and state e-procurement sites.\n\n"
    "Given tender information, classify each tender into exactly ONE primary "
    "industry category from the list below, provide a short sub-category, and "
    "a confidence score (0.0–1.0).\n\n"
    "Categories:\n"
    + "\n".join(f"- {c}" for c in TENDER_CATEGORIES)
    + "\n\n"
    "Rules:\n"
    "1. Choose the MOST specific category that fits.\n"
    "2. The sub_category should be a concise phrase (2-4 words) narrowing the "
    "   classification, e.g. 'Cloud Computing', 'Bridge Construction'.\n"
    "3. If nothing fits well, use 'Other'.\n"
    "4. Confidence 0.9+ means you are very sure; below 0.5 means a guess.\n"
    "5. Always reply with valid JSON — no markdown fences, no extra text."
)

_FALLBACK_RESULT: dict[str, Any] = {
    "category": "Other",
    "sub_category": "",
    "confidence": 0.0,
}

# ---------------------------------------------------------------------------
# Single tender classification
# ---------------------------------------------------------------------------

async def classify_tender(
    title: str,
    description: str = "",
    organisation: str = "",
) -> dict[str, Any]:
    """Classify a single tender using GPT-4o-mini.

    Returns
    -------
    dict  e.g. {"category": "IT Services & Software",
                "sub_category": "Cloud Computing",
                "confidence": 0.95}
    """
    if not title.strip():
        log.warning("Empty title passed to classify_tender — returning fallback.")
        return dict(_FALLBACK_RESULT)

    user_content = f"Title: {title}"
    if description:
        user_content += f"\nDescription: {description[:1000]}"
    if organisation:
        user_content += f"\nOrganisation: {organisation}"

    try:
        client = get_client()
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.0,
            max_tokens=150,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
        )
        _track_cost(response.usage)

        raw = response.choices[0].message.content or ""
        result = json.loads(raw)

        # Normalise keys
        category = result.get("category", "Other")
        if category not in TENDER_CATEGORIES:
            log.warning("Model returned unknown category '%s'; falling back.", category)
            category = "Other"

        return {
            "category": category,
            "sub_category": result.get("sub_category", ""),
            "confidence": float(result.get("confidence", 0.0)),
        }

    except json.JSONDecodeError as exc:
        log.error("Failed to parse model JSON: %s", exc)
        return dict(_FALLBACK_RESULT)
    except Exception as exc:  # noqa: BLE001
        log.error("classify_tender failed: %s", exc)
        return dict(_FALLBACK_RESULT)


# ---------------------------------------------------------------------------
# Batch classification
# ---------------------------------------------------------------------------

async def _classify_batch(batch: list[dict], batch_idx: int) -> list[dict]:
    """Send a batch of tenders in a single API call and return results."""
    # Build a numbered list for the prompt
    lines: list[str] = []
    for i, t in enumerate(batch, start=1):
        entry = f"{i}. Title: {t.get('title', '')}"
        desc = t.get("work_description", "") or t.get("description", "")
        if desc:
            entry += f" | Description: {desc[:300]}"
        org = t.get("organisation", "")
        if org:
            entry += f" | Organisation: {org}"
        lines.append(entry)

    user_content = (
        "Classify each of the following tenders. Return a JSON array of objects, "
        "one per tender in the same order, each with keys: "
        '"category", "sub_category", "confidence".\n\n'
        + "\n".join(lines)
    )

    try:
        client = get_client()
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.0,
            max_tokens=100 * len(batch),
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
        )
        _track_cost(response.usage)

        raw = response.choices[0].message.content or ""
        parsed = json.loads(raw)

        # The model may wrap the array in a key like {"results": [...]}
        if isinstance(parsed, dict):
            for key in ("results", "tenders", "classifications", "data"):
                if key in parsed and isinstance(parsed[key], list):
                    parsed = parsed[key]
                    break
            else:
                # Maybe it's a single-item dict with one list value
                for v in parsed.values():
                    if isinstance(v, list):
                        parsed = v
                        break

        if not isinstance(parsed, list):
            log.error("Batch %d: expected list, got %s", batch_idx, type(parsed))
            return [dict(_FALLBACK_RESULT) for _ in batch]

        # Pad or truncate to match batch length
        while len(parsed) < len(batch):
            parsed.append(dict(_FALLBACK_RESULT))
        parsed = parsed[: len(batch)]

        results: list[dict] = []
        for item in parsed:
            cat = item.get("category", "Other") if isinstance(item, dict) else "Other"
            if cat not in TENDER_CATEGORIES:
                cat = "Other"
            results.append({
                "category": cat,
                "sub_category": item.get("sub_category", "") if isinstance(item, dict) else "",
                "confidence": float(item.get("confidence", 0.0)) if isinstance(item, dict) else 0.0,
            })
        return results

    except Exception as exc:  # noqa: BLE001
        log.error("Batch %d classification failed: %s", batch_idx, exc)
        return [dict(_FALLBACK_RESULT) for _ in batch]


async def classify_tenders_batch(
    tenders: list[dict],
    batch_size: int = 20,
) -> list[dict]:
    """Classify a list of tender dicts, adding industry_category and
    sub_category fields in-place.  Returns the same list for convenience.

    Parameters
    ----------
    tenders : list[dict]
        Each dict should have at least a ``title`` key.  Optional:
        ``work_description``, ``organisation``.
    batch_size : int
        Number of tenders per API call (default 20).
    """
    if not tenders:
        return tenders

    max_concurrent = 5
    semaphore = asyncio.Semaphore(max_concurrent)

    # Split into batches
    batches: list[list[dict]] = [
        tenders[i : i + batch_size] for i in range(0, len(tenders), batch_size)
    ]
    log.info(
        "Classifying %d tenders in %d batches (batch_size=%d, max_concurrent=%d)",
        len(tenders), len(batches), batch_size, max_concurrent,
    )

    start_time = time.monotonic()

    async def _run_batch(batch: list[dict], idx: int) -> tuple[int, list[dict]]:
        async with semaphore:
            results = await _classify_batch(batch, idx)
            return idx, results

    tasks = [_run_batch(b, i) for i, b in enumerate(batches)]
    batch_results = await asyncio.gather(*tasks)

    # Re-order results and merge into the original tender dicts
    batch_results_sorted = sorted(batch_results, key=lambda x: x[0])
    flat_results: list[dict] = []
    for _, results in batch_results_sorted:
        flat_results.extend(results)

    for tender, classification in zip(tenders, flat_results):
        tender["industry_category"] = classification["category"]
        tender["sub_category"] = classification["sub_category"]
        tender["classification_confidence"] = classification["confidence"]

    elapsed = time.monotonic() - start_time
    cost_info = get_cumulative_cost()
    log.info(
        "Batch classification complete — %d tenders in %.1fs, "
        "estimated cost: $%.6f",
        len(tenders), elapsed, cost_info["estimated_cost_usd"],
    )

    return tenders


# ---------------------------------------------------------------------------
# Local keyword-based fallback (no API required)
# ---------------------------------------------------------------------------

# Mapping: category -> list of keyword patterns (case-insensitive)
_KEYWORD_MAP: dict[str, list[str]] = {
    "IT Services & Software": [
        r"\bsoftware\b", r"\bIT\b", r"\bcomputer\b", r"\bhardware\b",
        r"\bserver\b", r"\bnetwork\b", r"\bcloud\b", r"\bdata\s?cent",
        r"\bcyber\b", r"\bdigital\b", r"\bwebsite\b", r"\bweb\s?portal\b",
        r"\bERP\b", r"\bSAP\b", r"\bLAN\b", r"\bWAN\b", r"\bdesktop\b",
        r"\blaptop\b", r"\bprinter\b", r"\bUPS\b", r"\bCCTV\b",
        r"\bbiometric\b", r"\bapp\s?development\b", r"\bAI\b",
        r"\bmachine\s?learning\b", r"\bGIS\b",
    ],
    "Construction & Civil Works": [
        r"\bconstruction\b", r"\bcivil\b", r"\bbuilding\b", r"\bcement\b",
        r"\bconcrete\b", r"\bmasonry\b", r"\bplastering\b", r"\btiles\b",
        r"\bflooring\b", r"\bpainting\b", r"\brepair\s?(work|of)\b",
        r"\brenovation\b", r"\brestor(ation|ing)\b", r"\bdemolition\b",
        r"\bfoundation\b", r"\bstructural\b", r"\bPWD\b",
        r"\bbrick\b", r"\bsand\b", r"\brod\b", r"\bwall\b",
        r"\bplaster\b", r"\bshuttering\b", r"\bRCC\b", r"\bpiling\b",
        r"\bexcavat\b", r"\bearth\s?work\b", r"\bgrading\b",
        r"\binfrastructure\b", r"\bDUSIB\b", r"\bJal\s?Board\b",
    ],
    "Medical & Healthcare": [
        r"\bmedical\b", r"\bhospital\b", r"\bhealth\b", r"\bpharma\b",
        r"\bdrug\b", r"\bmedicine\b", r"\bsurgical\b", r"\bdiagnostic\b",
        r"\blaboratory\b", r"\bX[\s-]?ray\b", r"\bMRI\b", r"\bCT\s?scan\b",
        r"\bambulance\b", r"\bvaccin\b", r"\bblood\s?bank\b",
        r"\bdental\b", r"\bventilator\b", r"\bPPE\b",
    ],
    "Food & Catering": [
        r"\bfood\b", r"\bcatering\b", r"\bmess\b", r"\bration\b",
        r"\bcanteen\b", r"\bmeal\b", r"\brice\b", r"\bwheat\b",
        r"\bflour\b", r"\bmilk\b", r"\bcooking\b", r"\bkitchen\b",
    ],
    "Transportation & Logistics": [
        r"\btransport\b", r"\blogistic\b", r"\bvehicle\b", r"\bbus\b",
        r"\btruck\b", r"\bfreight\b", r"\bshipping\b", r"\bcargo\b",
        r"\bwarehouse\b", r"\bhiring\s?of\s?vehicle\b", r"\btaxi\b",
        r"\bcourier\b",
    ],
    "Electrical & Power": [
        r"\belectrical\b", r"\bpower\s?supply\b", r"\btransformer\b",
        r"\bsubstation\b", r"\bcable\b", r"\bgenerator\b", r"\bsolar\b",
        r"\brenewable\b", r"\bwind\s?energy\b", r"\bswitchgear\b",
        r"\bmeter\b", r"\bDG\s?set\b", r"\binverter\b", r"\bpanel\b",
        r"\belectrification\b", r"\bLED\b", r"\blighting\b",
        r"\bstreet\s?light\b", r"\bhigh\s?mast\b", r"\bcopper\b",
        r"\bfittings?\b", r"\bferrule\b", r"\bconnectors?\b",
        r"\binstrument\b",
    ],
    "Water & Sanitation": [
        r"\bwater\s?supply\b", r"\bsanitation\b", r"\bsewage\b",
        r"\bdrainage\b", r"\bpipeline\b", r"\bplumbing\b", r"\bSTP\b",
        r"\bWTP\b", r"\bwater\s?tank\b", r"\bborewell\b", r"\btube\s?well\b",
        r"\bJJM\b", r"\bwater\s?treatment\b", r"\bdesalination\b",
        r"\btoilet\b", r"\bbio[\s-]?toilet\b", r"\bhandpump\b",
        r"\bsubmersible\b", r"\bR\.?O\.?\s?plant\b", r"\bswachh\b",
        r"\bcleaning\b", r"\bhousekeep\b",
    ],
    "Education & Training": [
        r"\beducation\b", r"\btraining\b", r"\bschool\b", r"\bcollege\b",
        r"\buniversity\b", r"\bskill\s?development\b", r"\bworkshop\b",
        r"\bcoaching\b", r"\bbook\b", r"\btextbook\b", r"\blibrary\b",
    ],
    "Security & Defence": [
        r"\bsecurity\b", r"\bdefence\b", r"\bdefense\b", r"\barmy\b",
        r"\bnavy\b", r"\bair\s?force\b", r"\bweapon\b", r"\bammunition\b",
        r"\bbullet[\s-]?proof\b", r"\bguard\b", r"\bsurveillance\b",
        r"\bfirearm\b", r"\bborder\b", r"\bBSF\b", r"\bCRPF\b",
        r"\bCISF\b",
    ],
    "Agriculture & Farming": [
        r"\bagricult\b", r"\bfarming\b", r"\bfertili[sz]er\b", r"\bseed\b",
        r"\birrigation\b", r"\bcrop\b", r"\bpesticid\b", r"\bharvest\b",
        r"\btractor\b", r"\bhorticultur\b", r"\bfishery\b", r"\bdairy\b",
    ],
    "Printing & Stationery": [
        r"\bprinting\b", r"\bstationery\b", r"\bpaper\b", r"\bform\s?print\b",
        r"\boffset\b", r"\bbinding\b", r"\bregister\b",
    ],
    "Furniture & Fittings": [
        r"\bfurniture\b", r"\bchair\b", r"\btable\b", r"\bdesk\b",
        r"\bcupboard\b", r"\brack\b", r"\bshelv\b", r"\bwooden\b",
        r"\bsteel\s?almirah\b", r"\bpartition\b",
    ],
    "Consulting & Professional Services": [
        r"\bconsult\b", r"\badvisory\b", r"\baudit\b", r"\bCA\s?firm\b",
        r"\blegal\b", r"\bchartered\b", r"\bvaluation\b", r"\bfeasibility\b",
        r"\bDPR\b", r"\bproject\s?management\b", r"\bPMC\b",
    ],
    "Telecommunications": [
        r"\btelecom\b", r"\bbroadband\b", r"\bfiber\b", r"\bfibre\b",
        r"\b(OFC|FTTH)\b", r"\bbandwidth\b", r"\binternet\b",
        r"\bISP\b", r"\bWi[\s-]?Fi\b", r"\btower\b", r"\bantenna\b",
        r"\bBSNL\b",
    ],
    "Environmental Services": [
        r"\benvironment\b", r"\bwaste\s?manage\b", r"\bpollution\b",
        r"\bSWM\b", r"\bgarbage\b", r"\brecycl\b", r"\bEIA\b",
        r"\bgreen\b", r"\bforest\b", r"\bafforestation\b",
        r"\bcompostable\b", r"\bbio[\s-]?degrad\b", r"\btrees?\b",
        r"\bauction\b",
    ],
    "Mining & Minerals": [
        r"\bmining\b", r"\bmineral\b", r"\bcoal\b", r"\bore\b",
        r"\bquarry\b", r"\bexcavation\b", r"\bsand\b", r"\bgranite\b",
    ],
    "Textiles & Garments": [
        r"\btextile\b", r"\bgarment\b", r"\buniform\b", r"\bcloth\b",
        r"\bfabric\b", r"\bsewing\b", r"\bstitch\b", r"\bbedsheet\b",
        r"\bblanket\b", r"\btent\b",
    ],
    "Chemicals & Pharmaceuticals": [
        r"\bchemical\b", r"\bpharma\b", r"\breagent\b", r"\bacid\b",
        r"\bsolvent\b", r"\bdisinfect\b", r"\bchlorine\b", r"\bgas\s?cylinder\b",
    ],
    "Machinery & Equipment": [
        r"\bmachinery\b", r"\bequipment\b", r"\bpump\b", r"\bcompressor\b",
        r"\bcrane\b", r"\bforklift\b", r"\blathe\b", r"\bwelding\b",
        r"\btool\b", r"\bspare\s?part\b", r"\bbearing\b",
        r"\bdeep\s?freezer\b", r"\bhearse\b", r"\bE[\s-]?office\b",
        r"\bsupply\s?of\b",
    ],
    "Road & Highway": [
        r"\broad\b", r"\bhighway\b", r"\bNH[\s-]?\d", r"\bbridge\b",
        r"\bculvert\b", r"\basphalt\b", r"\bbitmumen\b", r"\btarring\b",
        r"\bflyover\b", r"\bNHAI\b", r"\bbypass\b",
    ],
    "Railway": [
        r"\brailway\b", r"\brail\b", r"\btrack\b", r"\bcoach\b",
        r"\blocomotive\b", r"\bstation\b", r"\bplatform\b",
        r"\bsignalling\b", r"\bIRCTC\b", r"\bRCF\b",
    ],
    "Real Estate & Housing": [
        r"\breal\s?estate\b", r"\bhousing\b", r"\bflat\b", r"\bapartment\b",
        r"\bDDA\b", r"\bPMay\b", r"\bawas\b", r"\btownship\b",
    ],
    "Oil & Gas": [
        r"\boil\b", r"\bgas\b", r"\bpetroleum\b", r"\brefinery\b",
        r"\bpipeline\b", r"\bONGC\b", r"\bIOCL\b", r"\bBPCL\b",
        r"\bHPCL\b", r"\bLPG\b", r"\bCNG\b", r"\bLNG\b",
    ],
    "Banking & Finance": [
        r"\bbank\b", r"\bfinance\b", r"\binsurance\b", r"\bloan\b",
        r"\bRBI\b", r"\bNABARD\b", r"\bSBI\b", r"\bATM\b",
    ],
}

# Pre-compile patterns for performance
_COMPILED_KEYWORDS: dict[str, list[re.Pattern[str]]] = {
    cat: [re.compile(p, re.IGNORECASE) for p in patterns]
    for cat, patterns in _KEYWORD_MAP.items()
}


def classify_tender_local(text: str) -> str:
    """Classify a tender using keyword matching only (no API call).

    Accepts title, description, or combined text (title + org + description).
    Returns the best-matching category name, or ``"Other"`` if no strong
    match is found.
    """
    if not text:
        return "Other"

    scores: dict[str, int] = {}
    for cat, patterns in _COMPILED_KEYWORDS.items():
        hits = sum(1 for p in patterns if p.search(text))
        if hits:
            scores[cat] = hits

    if not scores:
        return "Other"

    # Return category with most keyword hits
    return max(scores, key=scores.get)  # type: ignore[arg-type]
