"""Build the bundled lookup from a local checkout of intel-processors (no network)."""
from __future__ import annotations

import argparse
import csv
import math
import re
from pathlib import Path


def build(source: Path, destination: Path) -> tuple[int, int]:
    files = list(source.glob("*.csv"))
    if not files:
        raise ValueError("No CSV files found in source directory")
    def version(path: Path) -> tuple[int, int, str]:
        match = re.search(r"_v(\d+)_(\d+)\.csv$", path.name, re.I)
        if not match:
            raise ValueError("Unrecognised dataset version: " + path.name)
        return int(match[1]), int(match[2]), path.name

    products: dict[str, dict[str, str]] = {}
    for path in sorted(files, key=version):
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not {"Product", "Release Date"}.issubset(reader.fieldnames or []):
                raise ValueError("Missing CPU columns: " + path.name)
            for row in reader:
                product = row["Product"].strip()
                if not product:
                    continue
                release = row["Release Date"].strip()
                if release in {"N/A", ""}:
                    release = ""
                elif not re.fullmatch(r"Q[1-4]'\d{2}", release):
                    raise ValueError("Unrecognised release date: " + release)
                raw = (row.get("Max Operating Temp.(°C)") or "").strip()
                temperature = ""
                if raw not in {"", "N/A"}:
                    value = float(raw)
                    if not math.isfinite(value) or not 0 < value <= 150:
                        raise ValueError("Invalid temperature: " + raw)
                    temperature = f"{value:g}"
                products[product.casefold()] = {
                    "product": product, "release_date": release,
                    "max_operating_temp_c": temperature, "source_file": path.name,
                }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["product", "release_date", "max_operating_temp_c", "source_file"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(products[key] for key in sorted(products))
    return len(products), sum(bool(row["max_operating_temp_c"]) for row in products.values())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "app/data/intel_cpus.csv")
    args = parser.parse_args()
    count, temperatures = build(args.source, args.output)
    print(f"Imported {count} CPUs; {temperatures} temperature references")
