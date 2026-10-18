#!/bin/sh
# Starts Postgres tuned to the memory and CPUs its container is given (cgroup v2 or v1 limits, else
# the host's), so a bigger instance is used fully with no configuration. Explicit `-c` arguments after
# the image name still win: they come last.
#
# Rules of thumb for an OLTP shop on SSD/gp3: shared_buffers 25% of memory, the planner assumes the
# OS caches another 50%, sorts and hashes get a share of a quarter of memory across connections.
set -eu

host_mb=$(awk '/MemTotal/ {print int($2 / 1024)}' /proc/meminfo)
limit=$(cat /sys/fs/cgroup/memory.max 2>/dev/null || cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null || echo max)
mem_mb=$host_mb
case "$limit" in max | "") ;; *) [ $((limit / 1048576)) -lt "$host_mb" ] && mem_mb=$((limit / 1048576)) ;; esac  # v1 "unlimited" is a huge number

cpus=$(nproc)
if read -r quota period < /sys/fs/cgroup/cpu.max 2>/dev/null; then :
else quota=$(cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us 2>/dev/null || echo max); period=$(cat /sys/fs/cgroup/cpu/cpu.cfs_period_us 2>/dev/null || echo 100000); fi
case "$quota" in max | -1 | "") ;; *) cpus=$(( (quota + period - 1) / period )) ;; esac
[ "$cpus" -ge 1 ] || cpus=1

max_conn=${PG_MAX_CONNECTIONS:-100}
shared=$((mem_mb / 4))
cache=$((mem_mb * 3 / 4))
work=$((mem_mb / 4 / max_conn)); [ "$work" -ge 4 ] || work=4
maint=$((mem_mb / 16)); [ "$maint" -le 1024 ] || maint=1024; [ "$maint" -ge 32 ] || maint=32
gather=$((cpus / 2)); [ "$gather" -ge 1 ] || gather=1

echo "postgres tuned for ${mem_mb} MB, ${cpus} CPU(s): shared_buffers=${shared}MB work_mem=${work}MB" >&2

exec docker-entrypoint.sh postgres \
  -c max_connections="$max_conn" \
  -c shared_buffers="${shared}MB" \
  -c effective_cache_size="${cache}MB" \
  -c work_mem="${work}MB" \
  -c maintenance_work_mem="${maint}MB" \
  -c max_worker_processes="$((cpus + 4))" \
  -c max_parallel_workers="$cpus" \
  -c max_parallel_workers_per_gather="$gather" \
  -c random_page_cost=1.1 \
  -c effective_io_concurrency=200 \
  -c checkpoint_completion_target=0.9 \
  -c max_wal_size=1GB \
  -c wal_compression=on \
  -c jit=off \
  -c log_min_duration_statement="${PG_LOG_SLOW_MS:-500}" \
  "$@"
