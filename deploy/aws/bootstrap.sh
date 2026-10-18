#!/usr/bin/env bash
# Prepares an Amazon Linux 2023 (arm64) host and starts Babel Bazaar. Run by the instance's user data
# on every first boot of an instance (deploy/aws/template.yml); safe to run again.
#
# Inputs (environment): REPO_URL, REPO_REF, DATA_VOLUME (vol-...), SITE_ADDRESS, ACME_EMAIL, BACKUP_BUCKET.
set -euo pipefail

COMPOSE_VERSION=v5.6.0   # docker compose plugin (kept current by hand with docs/dependency-updates.md)
BUILDX_VERSION=v0.38.0   # BuildKit client: Compose needs it for the BFF's two build contexts
DATA=/data
APP=$DATA/babel-bazaar

log() { echo "[bootstrap] $*"; }

# ── packages ──────────────────────────────────────────────────────────────────
log "packages"
dnf install -y docker git python3 xfsprogs dnf-automatic awscli-2 >/dev/null
# Security updates install themselves; the stack restarts with the host when a reboot is needed.
sed -i 's/^upgrade_type = .*/upgrade_type = security/; s/^apply_updates = .*/apply_updates = yes/' /etc/dnf/automatic.conf
systemctl enable --now dnf-automatic.timer

plugins=/usr/local/lib/docker/cli-plugins
mkdir -p "$plugins"
fetch() { curl -fsSL --retry 5 -o "$1" "$2" && chmod +x "$1"; }
[ -x "$plugins/docker-compose" ] || fetch "$plugins/docker-compose" \
  "https://github.com/docker/compose/releases/download/$COMPOSE_VERSION/docker-compose-linux-aarch64"
[ -x "$plugins/docker-buildx" ] || fetch "$plugins/docker-buildx" \
  "https://github.com/docker/buildx/releases/download/$BUILDX_VERSION/buildx-$BUILDX_VERSION.linux-arm64"

# A small instance builds the images once (Java, Go, Node, Python): swap keeps that from running out of memory.
if [ "$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)" -lt 4096 ] && [ ! -f /swapfile ]; then
  log "swap for the first build"
  dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none && chmod 600 /swapfile
  mkswap /swapfile >/dev/null && swapon /swapfile && echo "/swapfile none swap sw 0 0" >> /etc/fstab
  sysctl -qw vm.swappiness=10 && echo "vm.swappiness=10" > /etc/sysctl.d/90-swappiness.conf
fi

# ── the data volume ───────────────────────────────────────────────────────────
# Attached by the instance itself: when CloudFormation replaces the instance, the old one still holds
# the volume until it is terminated, so keep asking until it is free.
imds() { curl -fsS -H "X-aws-ec2-metadata-token: $(curl -fsS -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' http://169.254.169.254/latest/api/token)" "http://169.254.169.254/latest/$1"; }
instance=$(imds meta-data/instance-id)
export AWS_DEFAULT_REGION=$(imds meta-data/placement/region)
device=/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${DATA_VOLUME//-/}
if [ ! -e "$device" ]; then
  log "attaching $DATA_VOLUME"
  for i in $(seq 1 120); do
    attached_to=$(aws ec2 describe-volumes --volume-ids "$DATA_VOLUME" --query 'Volumes[0].Attachments[0].InstanceId' --output text)
    [ "$attached_to" = "$instance" ] && break
    if [ "$attached_to" = None ]; then
      aws ec2 attach-volume --volume-id "$DATA_VOLUME" --instance-id "$instance" --device /dev/sdf >/dev/null || true
    fi
    sleep 10
  done
  for i in $(seq 1 60); do [ -e "$device" ] && break; sleep 2; done
fi
[ -e "$device" ] || { log "data volume $DATA_VOLUME did not appear"; exit 1; }
if ! blkid "$device" >/dev/null 2>&1; then
  log "formatting the new data volume"
  mkfs.xfs -q "$device"
fi
mkdir -p $DATA
uuid=$(blkid -s UUID -o value "$device")
grep -q "$uuid" /etc/fstab || echo "UUID=$uuid $DATA xfs defaults,noatime,nofail 0 2" >> /etc/fstab
mountpoint -q $DATA || mount $DATA
xfs_growfs -d $DATA >/dev/null || true   # picks up a volume grown in the stack

# ── Docker, with everything it stores on the data volume ──────────────────────
mkdir -p /etc/docker $DATA/docker
cat > /etc/docker/daemon.json <<JSON
{
  "data-root": "$DATA/docker",
  "log-driver": "local",
  "log-opts": { "max-size": "20m", "max-file": "5" },
  "live-restore": true
}
JSON
systemctl enable docker && systemctl restart docker

# ── the application ───────────────────────────────────────────────────────────
if [ -d $APP/.git ]; then
  log "updating $APP to $REPO_REF (its .env and secrets are kept)"
  git -C $APP fetch --depth 1 origin "$REPO_REF" && git -C $APP checkout -q -B "$REPO_REF" FETCH_HEAD
else
  log "cloning $REPO_URL@$REPO_REF"
  git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" $APP
fi
cd $APP
python3 scripts/devsecrets.py ensure || [ $? = 3 ]   # fresh random secrets on first boot, kept afterwards
python3 - "$SITE_ADDRESS" "${ACME_EMAIL:-}" <<'PY'
import sys
sys.path.insert(0, "scripts")
import devsecrets
site, email = sys.argv[1], sys.argv[2]
devsecrets.set_values(devsecrets.ENV, {
    "COMPOSE_FILE": "docker-compose.yml:deploy/compose.prod.yml",   # Caddy in front, no developer ports
    "SITE_ADDRESS": site,
    "ACME_EMAIL": email,
    "PUBLIC_API_BASE_URL": f"https://{site}",
    "COOKIE_SECURE": "true",
    "DJANGO_DEBUG": "false",
})
PY
chmod 600 .env .env.private

# ── units: start at boot (re-sizing to the instance every time), nightly backups ─
cat > /etc/systemd/system/babel-bazaar.service <<UNIT
[Unit]
Description=Babel Bazaar (docker compose, sized to this instance by run.sh)
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$APP
ExecStart=$APP/run.sh
ExecStop=/usr/bin/docker compose --profile core down
TimeoutStartSec=3600

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/babel-bazaar-backup.service <<UNIT
[Unit]
Description=Babel Bazaar: dump every database to S3
Requires=docker.service

[Service]
Type=oneshot
WorkingDirectory=$APP
Environment=AWS_DEFAULT_REGION=$AWS_DEFAULT_REGION
ExecStart=/bin/bash -o pipefail -c 'docker compose exec -T postgres pg_dumpall -U postgres | gzip -6 | aws s3 cp - "s3://$BACKUP_BUCKET/\$(date -u +%%Y-%%m-%%dT%%H%%MZ).sql.gz" --only-show-errors'
UNIT

cat > /etc/systemd/system/babel-bazaar-backup.timer <<UNIT
[Unit]
Description=Nightly Babel Bazaar backup

[Timer]
OnCalendar=*-*-* 03:17:00 UTC
RandomizedDelaySec=15m
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable babel-bazaar.service babel-bazaar-backup.timer
systemctl start babel-bazaar-backup.timer
log "building and starting (the first build takes 10 to 20 minutes on a small instance)"
systemctl restart babel-bazaar.service
log "up: https://$SITE_ADDRESS"
