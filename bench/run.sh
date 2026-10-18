#!/usr/bin/env bash
# Throughput and latency through the BFF, the way a browser reaches the shop. Needs a running stack
# started with the BFF's rate limit lifted, so the limiter is not what gets measured:
#
#   RATE_DEFAULT_PER_MINUTE=100000000 ./run.sh && bench/run.sh [seconds]
#
# Prints one block per scenario; bench/results/ keeps the reports committed with each change.
set -euo pipefail
cd "$(dirname "$0")/.."
secs=${1:-15}
base=http://localhost:8080
(cd bench/loadgen && go build -o ../../.bench-loadgen .)
gen=(./.bench-loadgen -c 32 -d "${secs}s")

cookie=$(python3 - <<'PY'
import sys
sys.path.insert(0, "scripts")
from bff import BffSession, read_env_file
from pathlib import Path
env = read_env_file(Path(".env"))
s = BffSession()
s.login(env.get("SEED_CUSTOMER_EMAIL", "customer@shop.local"), env["SEED_CUSTOMER_PASSWORD"])
print("; ".join(f"{c.name}={c.value}" for c in s.jar))
PY
)

echo "# $(date -u +%FT%TZ) $(nproc) CPUs, $(awk '/MemTotal/ {printf "%.0f GB", $2/1048576}' /proc/meminfo), c=32, ${secs}s each"
echo "## Anonymous catalog (BFF micro-cache on, as deployed)"
"${gen[@]}" "$base/api/catalog/products"
"${gen[@]}" "$base/api/catalog/search?q=mug"
"${gen[@]}" "$base/api/catalog/products?category=kitchen&page_size=4"
echo "## Signed-in customer: session, token, signed call to Django (commerce-core), shaped answer"
"${gen[@]}" -H "Cookie: $cookie" "$base/api/cart"
"${gen[@]}" -H "Cookie: $cookie" "$base/api/orders"
echo "## The storefront itself (precompressed, from the BFF's memory)"
"${gen[@]}" -H "Accept-Encoding: br" "$base/"
