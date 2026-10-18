"""Starts the service: uvicorn with uvloop and httptools, workers from the container's CPUs."""

import os

import uvicorn


def cpus():
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


if __name__ == "__main__":
    uvicorn.run("app.asgi:app", host="0.0.0.0", port=8001, loop="uvloop", http="httptools",
                workers=int(os.environ.get("WEB_CONCURRENCY") or cpus()),
                server_header=False, access_log=False, timeout_keep_alive=30)
