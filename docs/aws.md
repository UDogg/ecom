# Deploying to AWS

One Graviton (arm64) instance runs the whole stack with Docker Compose, the same `./run.sh` as on a
laptop, behind Caddy for HTTPS. It is the cheapest shape that still keeps every security property of
the design (only the BFF behind Caddy is reachable; every core service refuses unsigned calls), and it
scales up by changing one parameter.

```
Internet ──443──▶ Caddy (TLS, HTTP/2 and 3) ──▶ BFF ──signed──▶ core services ──▶ Postgres, RabbitMQ
                   └ the only open ports: 80, 443          (private Docker networks; no SSH at all)
```

## Deploy

Needs the AWS CLI with credentials for the account, and a region:

```bash
AWS_REGION=eu-west-1 deploy/aws/deploy.sh
```

That creates the stack in `deploy/aws/template.yml` and prints the shop's address, by default
`https://<elastic-ip>.sslip.io` (sslip.io is a free DNS name that resolves to the IP inside it, so HTTPS
works with no DNS of your own). The first boot builds the images; on a t4g.small that takes 10 to 20
minutes. Follow it:

```bash
aws ssm start-session --target <instance-id>          # printed as "Shell"
sudo tail -f /var/log/babel-bazaar-bootstrap.log
```

Your own domain: point an A record at the stack's `PublicIp`, then

```bash
deploy/aws/deploy.sh DomainName=shop.example.com AcmeEmail=you@example.com
```

The template is checked with `cfn-lint`.

## What the stack contains

| Resource | Why |
|---|---|
| EC2 instance, Amazon Linux 2023 arm64, IMDSv2 only | Graviton is the cheapest compute per request on AWS; the instance profile cannot be reached from containers (hop limit 1) |
| Data volume (gp3, encrypted, 20 GB by default), separate from the instance | Postgres, RabbitMQ, the Docker images and the generated secrets live here. Replacing the instance (a new AMI arrives with a stack update) keeps them; deleting the stack leaves a snapshot |
| Elastic IP | The address and the certificate survive stop, start and resize |
| Security group: 80 and 443 only | No SSH: shells go through Session Manager, audited in CloudTrail |
| S3 bucket, private, TLS only, expiring after 14 days | Nightly `pg_dumpall`, compressed |
| IAM role | Session Manager, writing backups, attaching its own data volume; nothing else |

On the host, `deploy/aws/bootstrap.sh` installs Docker (its data on the data volume, log rotation on),
generates random secrets once, points the shop at its public address, turns on automatic security
updates, and installs three systemd units: the stack (started at every boot through `./run.sh`), the
nightly backup and its timer.

## Operate

| Task | How |
|---|---|
| A shell | `aws ssm start-session --target <instance-id>` |
| Deploy new code | `sudo /data/babel-bazaar/deploy/aws/update.sh` (fetches the branch, `./run.sh` rebuilds what changed) |
| Status, logs | `cd /data/babel-bazaar && sudo ./run.sh status`, `sudo ./run.sh logs bff` |
| The demo accounts' passwords | `sudo grep SEED_ /data/babel-bazaar/.env` |
| Break-glass | `sudo ./run.sh breakglass ...` (`docs/break-glass.md`) |
| Back up now | `sudo systemctl start babel-bazaar-backup` |
| Restore | `aws s3 cp s3://<bucket>/<dump>.sql.gz - \| gunzip \| sudo docker compose exec -T postgres psql -U postgres` on a fresh data volume |
| psql, RabbitMQ's console, Mailpit | Not published in production. Forward a port: `aws ssm start-session --target <id> --document-name AWS-StartPortForwardingSession`, then `docker compose port` inside |

E-mail goes to Mailpit on the host (nothing leaves the machine). To send real mail, set `SMTP_HOST`,
`SMTP_PORT` and `MAIL_FROM` in `/data/babel-bazaar/.env` for Amazon SES and run `update.sh`.

## Scale up

```bash
deploy/aws/deploy.sh InstanceType=t4g.medium
```

CloudFormation stops the instance, changes its type and starts it (a few minutes of downtime; the IP,
certificate and data stay). At boot `./run.sh` gives every container a share of the new RAM, and each
service sizes itself from its share: Postgres buffers, JVM heap, Go memory limits, gunicorn and uvicorn
workers, the catalog connection pool (`docs/performance.md`). Nothing is edited by hand.

Signs it is time: sustained CPU above 70% (`run.sh status`, CloudWatch), Postgres buffers too small
for the catalog, or p99 latency rising in `bench/run.sh`. Beyond one machine, the next steps are a
managed Postgres (RDS) and a second instance behind a load balancer; the services are already stateless
apart from the BFF's sessions.

## Cost

Monthly, on demand, for `us-east-1` at the time of writing; check the AWS pricing pages for your region.

| Item | t4g.small | t4g.medium |
|---|---|---|
| Instance (730 hours) | ~$12.30 | ~$24.50 |
| EBS gp3: 16 GB system + 20 GB data | ~$2.90 | ~$2.90 |
| Public IPv4 address | ~$3.65 | ~$3.65 |
| S3 backups (14 days of compressed dumps) | under $0.10 | under $0.10 |
| Data out (first 100 GB a month free) | $0 | $0 |
| **Total** | **~$19** | **~$31** |

Cheaper still: a one- or three-year Compute Savings Plan cuts the instance line substantially, and the
stack can stop outside business hours if it is only a demo. Spot is not a good fit (one stateful host).
