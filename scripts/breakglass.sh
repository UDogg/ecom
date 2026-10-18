#!/usr/bin/env bash
# Break glass: work without the BFF (docs/break-glass.md). Every grant is audited.
#
#   scripts/breakglass.sh users [EMAIL-FRAGMENT]                    find the user id to act as
#   scripts/breakglass.sh token USER_ID "reason"                    print a 15-minute token acting as that user
#   scripts/breakglass.sh call USER_ID "reason" METHOD SERVICE PATH [JSON]
#                                         e.g. call 0000...0002 "BFF down, checking an order" GET commerce /api/commerce/orders
#   scripts/breakglass.sh system METHOD SERVICE PATH [JSON]         call as the `ops` service account (SYSTEM)
#                                         e.g. system GET commerce /api/commerce/ops/status
#   scripts/breakglass.sh system-token                              print a SYSTEM token
#   scripts/breakglass.sh audit                                     every break-glass attempt, granted or refused
#
# SERVICE: auth commerce catalog intelligence. Calls run in the `opsbox` container on the private
# network (the core services have no host ports). Secrets come from .env.private (./run.sh makes it);
# the break-glass password is prompted for when it is not there.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# .env.private fills in only what the caller has not set: an explicit value always wins.
if [ -f .env.private ]; then
  while IFS='=' read -r key value; do
    case "$key" in OPS_CLIENT_ID|OPS_CLIENT_SECRET|BREAK_GLASS_PASSWORD) [ -n "${!key:-}" ] || printf -v "$key" '%s' "$value";; esac
  done < <(grep -E '^[A-Z_]+=' .env.private)
fi
COMPOSE=(docker compose --progress quiet --profile core --profile ops)

case "${1:-}" in
  ""|-h|--help|help) sed -n '2,15p' "$0"; exit 0;;
  users)
    # The fragment is a psql variable, quoted by psql (:'frag'), never spliced into SQL. (psql only
    # interpolates variables in input it reads, not in -c, hence the here-string.)
    "${COMPOSE[@]}" exec -T postgres psql -U "${POSTGRES_SUPERUSER:-postgres}" -d "${POSTGRES_DB:-shop}" \
      -v frag="%${2:-}%" <<< "SELECT id, email, roles, enabled FROM identity.app_user WHERE email ILIKE :'frag' ORDER BY email LIMIT 50;"
    exit;;
  token|call)
    if [ -z "${BREAK_GLASS_PASSWORD:-}" ]; then
      read -rsp "Break-glass password: " BREAK_GLASS_PASSWORD; echo >&2
    fi;;
esac

# `-e NAME` (no value) copies NAME from this environment, so secrets never appear in any argv or `ps`.
export OPS_CLIENT_ID="${OPS_CLIENT_ID:-}" OPS_CLIENT_SECRET="${OPS_CLIENT_SECRET:-}" BREAK_GLASS_PASSWORD="${BREAK_GLASS_PASSWORD:-}"
exec "${COMPOSE[@]}" run --rm --no-deps -T -e OPS_CLIENT_ID -e OPS_CLIENT_SECRET -e BREAK_GLASS_PASSWORD opsbox "$@"
