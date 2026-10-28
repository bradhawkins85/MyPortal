"""Write a changes/<guid>.json entry in the format app/services/change_log.py reads."""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("change_type", choices=["Feature", "Fix", "Improvement", "Security"])
    parser.add_argument("summary")
    args = parser.parse_args()

    summary = args.summary.strip()
    occurred = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    # Must match ChangeLogEntry.content_hash in app/services/change_log.py.
    base = "|".join([occurred.isoformat(timespec="minutes"), args.change_type.lower(), summary])
    guid = str(uuid.uuid4())
    entry = {
        "guid": guid,
        "occurred_at": occurred.isoformat(timespec="minutes").replace("+00:00", "Z"),
        "change_type": args.change_type,
        "summary": summary,
        "content_hash": sha256(base.encode("utf-8")).hexdigest(),
    }

    path = Path("changes") / f"{guid}.json"
    path.write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
