"""Offline Intel CPU references imported from toUpperCase78/intel-processors."""
from __future__ import annotations

import csv
import re
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

DATA = Path(__file__).resolve().parents[1] / "data" / "intel_cpus.csv"
SOURCE = "https://github.com/toUpperCase78/intel-processors"
TEMPERATURE_NOTE = (
    "Dataset reference only: the source mixes maximum operating, T-junction and "
    "T-base temperatures, taking the higher value when both are present. "
    "Verify the Intel specification and sensor type before using as an alert threshold."
)


def model_key(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"^\s*\d+(?:st|nd|rd|th)\s+gen\s+", "", text)
    text = re.sub(r"\((?:r|tm)\)|[®™]", "", text)
    text = re.sub(r"@.*$", "", text)
    text = re.sub(r"\b(?:intel|cpu|processor)\b", " ", text)
    return " ".join(text.split())


@lru_cache(maxsize=1)
def _catalog() -> dict[str, dict[str, str]]:
    with DATA.open(encoding="utf-8", newline="") as stream:
        return {model_key(row["product"]): row for row in csv.DictReader(stream)}


def lookup(model: Any, *, today: date | None = None) -> dict[str, Any] | None:
    row = _catalog().get(model_key(model))
    if not row:
        return None
    today = today or datetime.now(timezone.utc).date()
    release = row["release_date"]
    match = re.fullmatch(r"Q([1-4])'(\d{2})", release)
    age = None
    if match:
        year = 2000 + int(match[2]) if int(match[2]) < 70 else 1900 + int(match[2])
        # Mid-quarter estimate; retain the original quarter instead of inventing a day.
        midpoint = date(year, (int(match[1]) - 1) * 3 + 2, 15)
        if date(year, (int(match[1]) - 1) * 3 + 1, 1) <= today:
            age = round(max(0, (today - midpoint).days) / 365.2425, 1)
    temperature = float(row["max_operating_temp_c"]) if row["max_operating_temp_c"] else None
    return {
        "product": row["product"], "release_date": release or None,
        "approx_age": age, "max_operating_temp_c": temperature,
        "source": SOURCE, "source_file": row["source_file"],
        "temperature_note": TEMPERATURE_NOTE,
    }


def enrich_asset(asset: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(asset)
    reference = lookup(item.get("cpu_name") or item.get("cpu_model"))
    if reference:
        item["cpu_reference"] = reference
        if item.get("approx_age") in (None, ""):
            item["approx_age"] = reference["approx_age"]
    return item
