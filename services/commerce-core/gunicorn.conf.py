"""gunicorn settings that follow the container: workers from the CPUs and memory it is given.

A bigger instance (or a raised limit) gets more workers with no configuration; WEB_CONCURRENCY
and WEB_THREADS override the computed values.
"""

import os

WORKER_MB = 70  # resident size of one preloaded worker with its threads, measured


def _cpus():
    """CPUs this container may use: its CFS quota (cgroup v2, then v1), else the CPUs it is pinned to."""
    try:
        quota, period = open("/sys/fs/cgroup/cpu.max").read().split()
    except (OSError, ValueError):
        try:
            quota = open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read().strip()
            period = open("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read().strip()
        except OSError:
            quota, period = "max", "1"
    if quota not in ("max", "-1"):
        return max(1, -(-int(quota) // int(period)))
    return len(os.sched_getaffinity(0))


def _memory_mb():
    """This container's memory limit (cgroup v2, then v1), or None when it has none."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = open(path).read().strip()
        except OSError:
            continue
        if raw == "max":
            return None
        limit = int(raw) // 2**20
        host = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 2**20
        return limit if limit < host else None  # v1 says "unlimited" with a huge number
    return None


def _workers():
    if os.environ.get("WEB_CONCURRENCY"):
        return int(os.environ["WEB_CONCURRENCY"])
    by_cpu = _cpus() + 1
    mem = _memory_mb()
    by_memory = max(1, (mem - 100) // WORKER_MB) if mem else by_cpu
    return max(1, min(by_cpu, by_memory))


bind = "0.0.0.0:8000"
workers = _workers()
worker_class = "gthread"  # threads share a worker's memory; requests mostly wait on Postgres
threads = int(os.environ.get("WEB_THREADS", "4"))
preload_app = True  # import Django once; workers share those pages copy-on-write
timeout = 30
graceful_timeout = 20
keepalive = 30  # the BFF keeps connections open; do not close them between its requests
max_requests = 10_000  # recycle workers now and then (bounded memory growth)
max_requests_jitter = 1_000
accesslog = None  # the BFF logs every request with its trace id; no second log line per request
errorlog = "-"
