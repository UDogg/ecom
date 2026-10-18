#!/usr/bin/env bash
# On the host (through `aws ssm start-session`): deploy the latest commit of the configured branch.
#
#   sudo /data/babel-bazaar/deploy/aws/update.sh [ref]
set -euo pipefail
cd /data/babel-bazaar
ref=${1:-$(git rev-parse --abbrev-ref HEAD)}
git fetch --depth 1 origin "$ref" && git checkout -q -B "$ref" FETCH_HEAD
./run.sh               # rebuilds what changed, restarts what changed, re-sizes to the instance
