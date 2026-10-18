#!/usr/bin/env bash
# Babel Bazaar: the whole stack with one command.
#
#   ./run.sh                 check the machine, prepare secrets, build, start, wait until healthy, seed, show where things are
#   ./run.sh --no-build      the same, using images already built
#   ./run.sh check           only check tools, ports and configuration; start nothing
#   ./run.sh status          health of every container
#   ./run.sh logs [SERVICE]  follow logs (all services, or one)
#   ./run.sh down            stop everything (data is kept)
#   ./run.sh reset           stop and delete all data and volumes (asks first)
#   ./run.sh test            the test suites that can run here (commerce-core in Docker; Go if installed)
#   ./run.sh scenario        scenario C: the broker goes down mid-traffic, nothing is lost
#   ./run.sh breakglass ...  privileged access without the BFF (same as scripts/breakglass.sh)
#   ./run.sh api ...         call the BFF (same as scripts/bff.py), e.g.  ./run.sh api --role owner GET /api/admin/dashboard
#
# Needs: Docker with Compose v2, python3. Everything else runs in containers.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$ROOT"
COMPOSE=(docker compose --profile core)
TIMEOUT="${RUN_TIMEOUT:-420}"

red(){ printf '\033[31m%s\033[0m\n' "$*"; }; grn(){ printf '\033[32m%s\033[0m\n' "$*"; }
ylw(){ printf '\033[33m%s\033[0m\n' "$*"; }; bold(){ printf '\033[1m%s\033[0m\n' "$*"; }
FAIL=0; bad(){ red "  ✗ $*"; FAIL=1; }; ok(){ grn "  ✓ $*"; }; warn(){ ylw "  ! $*"; }
envval(){ sed -n "s/^$1=//p" .env 2>/dev/null | tail -1 | sed 's/ #.*//'; }
port_busy(){ (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

check() {
  bold "1. Tools"
  command -v docker >/dev/null && ok "docker" || bad "docker not found: install Docker Desktop or Docker Engine"
  docker info >/dev/null 2>&1 && ok "Docker daemon reachable" || bad "the Docker daemon is not running (start Docker)"
  docker compose version >/dev/null 2>&1 && ok "docker compose $(docker compose version --short 2>/dev/null)" \
    || bad "Docker Compose v2 not found (docker compose ...)"
  command -v python3 >/dev/null && ok "python3 $(python3 -c 'import sys;print(sys.version.split()[0])')" \
    || bad "python3 not found (used for secrets and the client scripts)"

  bold "2. Configuration"
  if [ -f .env ]; then ok ".env present"; else warn ".env will be created with fresh random secrets"; fi
  [ -f .env.private ] && ok ".env.private present (operator secrets)" || warn ".env.private will be created"
  if [ -f .env ]; then
    [ "$(envval GATEWAY_SECRET | wc -c)" -gt 32 ] && ok "GATEWAY_SECRET set" || bad "GATEWAY_SECRET missing or short in .env"
    [ "$(envval OIDC_CLIENT_SECRET | wc -c)" -gt 32 ] && ok "OIDC_CLIENT_SECRET set" || bad "OIDC_CLIENT_SECRET missing or short in .env"
  fi

  bold "3. Ports"
  if "${COMPOSE[@]}" ps --status running --services 2>/dev/null | grep -qx bff; then
    ok "the stack is already running (it will be updated in place)"
  else
    # Ports this stack already publishes are fine (a re-run); anything else holding them is not.
    ours=$("${COMPOSE[@]}" ps --format '{{.Publishers}}' 2>/dev/null || true)
    for p in 8080 8025 15672 5432; do
      if ! port_busy "$p"; then ok "port $p free"
      elif grep -qE ":$p([],]|$)" <<<"$ours"; then ok "port $p is this stack's"
      else bad "port $p is already in use by something else"; fi
    done
  fi
}

wait_healthy() {
  local start=$SECONDS line svc state health code pending failed
  printf '  waiting for every service to be healthy'
  while :; do
    pending=0; failed=""
    # '|' delimiter: Health is empty for services without a healthcheck, and plain read would shift the columns.
    while IFS='|' read -r svc state health code; do
      [ -z "$svc" ] && continue
      case "$state" in
        exited) [ "$svc" = commerce-migrate ] && [ "$code" = 0 ] || failed="$failed $svc";;
        running) [ -z "$health" ] || [ "$health" = healthy ] || pending=1;;
        *) pending=1;;
      esac
    done < <("${COMPOSE[@]}" ps -a --format '{{.Service}}|{{.State}}|{{.Health}}|{{.ExitCode}}' 2>/dev/null)
    if [ -n "$failed" ]; then
      echo; red "  ✗ stopped:$failed"
      for s in $failed; do "${COMPOSE[@]}" logs --tail 30 "$s"; done
      return 1
    fi
    [ $pending = 0 ] && { echo; ok "all services healthy ($((SECONDS - start))s)"; return 0; }
    [ $((SECONDS - start)) -ge "$TIMEOUT" ] && { echo; red "  ✗ not healthy after ${TIMEOUT}s:"; "${COMPOSE[@]}" ps; return 1; }
    printf '.'; sleep 3
  done
}

summary() {
  local owner customer
  owner="$(envval SEED_OWNER_EMAIL)"; customer="$(envval SEED_CUSTOMER_EMAIL)"
  echo
  bold "Babel Bazaar is running. Only the BFF has a public port; every core service refuses anything it did not sign."
  cat <<EOF

  API and sign-in (BFF)    http://localhost:8080        sign in: http://localhost:8080/auth/login
  Mail (Mailpit)           http://localhost:8025
  RabbitMQ console         http://localhost:15672       user $(envval RABBITMQ_USER), password in .env (RABBITMQ_PASSWORD)

  Shop owner               $owner   password in .env (SEED_OWNER_PASSWORD)
  Customer                 $customer   password in .env (SEED_CUSTOMER_PASSWORD)

  Try it
    ./run.sh api GET "/api/catalog/products?q=mug"
    ./run.sh api --role owner GET /api/admin/dashboard
    ./run.sh breakglass system GET commerce /api/commerce/ops/status      SYSTEM, no BFF
    ./run.sh breakglass users                                             then: breakglass call USER_ID "why" ...
    ./run.sh status | logs bff | scenario | down
EOF
}

up() {
  local build=1
  [ "${1:-}" = "--no-build" ] && build=0
  check; [ $FAIL = 0 ] || { red "Not starting: fix the ✗ lines above."; exit 1; }

  bold "4. Secrets"
  if [ ! -f .env ] && docker volume inspect babel-bazaar_pgdata >/dev/null 2>&1; then
    red "  ✗ there is database data from an earlier setup but no .env: the new random passwords would not match it."
    red "    Restore that .env, or start clean with ./run.sh reset"; exit 1
  fi
  python3 scripts/devsecrets.py ensure | sed 's/^/  ✓ /'; [ "${PIPESTATUS[0]}" -le 3 ] || exit 1

  if [ $build = 1 ]; then
    bold "5. Building images (the first build downloads dependencies and takes a few minutes)"
    "${COMPOSE[@]}" build --quiet 2>&1 | tail -20; [ "${PIPESTATUS[0]}" = 0 ] || { red "  ✗ build failed"; exit 1; }
    ok "images built"
  else
    bold "5. Using existing images (--no-build)"
  fi

  bold "6. Starting"
  # Memory ceilings as shares of this machine's RAM; every service sizes itself from its own.
  eval "$(python3 scripts/sizing.py)"
  echo "  sized for $((STACK_MEMORY_MB / 1024)) GB of RAM and $(nproc) CPU(s) (scripts/sizing.py)"
  "${COMPOSE[@]}" up -d --remove-orphans $([ $build = 0 ] && echo --no-build) >/dev/null 2>&1 \
    || { "${COMPOSE[@]}" up -d --remove-orphans $([ $build = 0 ] && echo --no-build); red "  ✗ docker compose up failed"; exit 1; }
  wait_healthy || { red "Some services did not come up; see the logs above (./run.sh logs SERVICE)."; exit 1; }

  bold "7. Demo data"
  "${COMPOSE[@]}" run --rm --no-deps commerce-core python manage.py seed 2>/dev/null | tail -1 | sed 's/^/  ✓ /'
  summary
}

case "${1:-up}" in
  up) up "${2:-}";;
  --no-build) up --no-build;;
  check) check; [ $FAIL = 0 ] && grn "Everything needed is in place." || { red "Fix the ✗ lines above."; exit 1; };;
  status) "${COMPOSE[@]}" ps -a --format 'table {{.Service}}\t{{.State}}\t{{.Health}}\t{{.Ports}}';;
  logs) shift; "${COMPOSE[@]}" logs -f --tail 100 "$@";;
  down) docker compose --profile full --profile ops down; ok "stopped (data kept; ./run.sh starts it again)";;
  reset)
    read -rp "Delete ALL Babel Bazaar data (database, broker, signing keys)? Type 'reset' to confirm: " answer
    [ "$answer" = reset ] || { echo "Nothing deleted."; exit 1; }
    docker compose --profile full --profile ops down -v; ok "all data deleted; ./run.sh starts from scratch";;
  test)
    bold "commerce-core (in Docker, against the stack's PostgreSQL)"
    "${COMPOSE[@]}" up -d postgres >/dev/null 2>&1
    "${COMPOSE[@]}" run --rm -v "$ROOT/docs/contracts:/contracts:ro" -e CONTRACTS_DIR=/contracts/events commerce-core pytest || FAIL=1
    if command -v go >/dev/null; then
      bold "Go: bff and catalog-reader"
      (cd services/bff && go vet ./... && go test ./...) || FAIL=1
      (cd services/catalog-reader && go vet ./... && go test ./...) || FAIL=1
    else
      warn "Go not installed: skipping the BFF and catalog-reader tests (CI runs them)"
    fi
    [ $FAIL = 0 ] && grn "All tests passed." || { red "Some tests failed."; exit 1; };;
  scenario) python3 tests/scenarios/scenario_c.py;;
  breakglass) shift; exec scripts/breakglass.sh "$@";;
  api) shift; exec python3 scripts/bff.py "$@";;
  -h|--help|help) sed -n '2,16p' "$0";;
  *) red "unknown command: $1"; sed -n '2,16p' "$0"; exit 2;;
esac
