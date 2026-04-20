#!/usr/bin/env python3
"""
Legacy cron compatibility shim.

Keeps old pipeline cron entries non-failing while V2 launch runs with
data-pipeline services out of the critical path.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

OUTPUT = "/home/ubuntu/RLD/docker/dashboard/pipeline_stats.json"


def main() -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "disabled",
        "launch_critical": False,
        "message": "data-pipeline is excluded from the Reth-only launch baseline",
    }

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="pipeline_stats.", suffix=".json", dir=os.path.dirname(OUTPUT))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(payload, tmp, indent=2)
            tmp.write("\n")
        os.replace(tmp_path, OUTPUT)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


if __name__ == "__main__":
    main()
