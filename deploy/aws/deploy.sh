#!/usr/bin/env bash
# Creates or updates the AWS stack (deploy/aws/template.yml) and prints where the shop is.
#
#   deploy/aws/deploy.sh                                   t4g.small, <ip>.sslip.io, branch main
#   deploy/aws/deploy.sh InstanceType=t4g.medium           resize (stop, start, everything re-sizes itself)
#   deploy/aws/deploy.sh DomainName=shop.example.com AcmeEmail=you@example.com
#
# Needs the AWS CLI with credentials for the target account and region (AWS_PROFILE, AWS_REGION).
set -euo pipefail
cd "$(dirname "$0")"
stack=${STACK_NAME:-babel-bazaar}
aws cloudformation deploy --stack-name "$stack" --template-file template.yml \
  --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset \
  ${1:+--parameter-overrides "$@"}
aws cloudformation describe-stacks --stack-name "$stack" \
  --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output table
echo "First boot builds the images (10 to 20 minutes on t4g.small). Follow it with the Shell command above and:"
echo "  sudo tail -f /var/log/babel-bazaar-bootstrap.log"
