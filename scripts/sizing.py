#!/usr/bin/env python3
"""Memory limits for each container, as shares of this machine's RAM. Standard library only.

    eval "$(python3 scripts/sizing.py)"      # ./run.sh does this before `docker compose up`

Every service sizes itself from its container's limit (Postgres buffers, JVM heap, Go memory limit,
gunicorn workers), so moving to a bigger instance makes the whole stack use it, with no edits.
Limits are ceilings, not reservations: on a small machine the minimums may add up to more than the
RAM, which is fine because nothing sits at its ceiling at once. A MEM_* value already set in the
environment or in .env is kept.
"""

import os
import re
import sys
from pathlib import Path

# service: (share of the budget, minimum MB)
SHARES = {
    "POSTGRES": (0.25, 256),
    "AUTH": (0.14, 320),
    "COMMERCE": (0.14, 256),
    "RABBITMQ": (0.08, 192),
    "INTELLIGENCE": (0.07, 160),
    "BFF": (0.06, 64),
    "CATALOG": (0.05, 64),
    "EXPIRER": (0.04, 128),
    "PAYMENTS": (0.04, 96),
    "WORKER": (0.04, 96),
    "OUTBOX": (0.03, 80),
    "MAILPIT": (0.02, 48),
}


def total_mb():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) // 1024
    raise SystemExit("cannot read /proc/meminfo")


def limits(total):
    budget = total - max(256, total * 8 // 100)  # leave the OS, Docker and the page cache their part
    return {name: max(minimum, int(budget * share)) for name, (share, minimum) in SHARES.items()}


def main():
    env_file = Path(__file__).resolve().parent.parent / ".env"
    pinned = set(os.environ)
    if env_file.exists():
        pinned |= {m.group(1) for m in re.finditer(r"^(MEM_[A-Z]+)=", env_file.read_text(), re.M)}
    total = int(sys.argv[1]) if len(sys.argv) > 1 else total_mb()
    for name, mb in limits(total).items():
        key = f"MEM_{name}"
        if key not in pinned:
            print(f"export {key}={mb}M")
    print(f"export STACK_MEMORY_MB={total}")


if __name__ == "__main__":
    main()
