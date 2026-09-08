#!/bin/bash
# Deploy thoughts site to Cloudflare Pages if the repo has new commits on main.
# Runs via cron (no LLM). Reads credentials from /opt/data/thoughts/.secrets/.
set -euo pipefail
SECRETS=/opt/data/thoughts/.secrets
STATE=/opt/data/thoughts/.secrets/.last_deployed_sha
export PATH=/opt/data/tools/gh_2.100.0_linux_amd64/bin:$PATH

SHA=$(gh api repos/mu1ze/thoughts/commits/main --jq '.sha' 2>/dev/null) || exit 0
[ -f "$STATE" ] && [ "$SHA" = "$(cat "$STATE")" ] && exit 0

export CLOUDFLARE_API_TOKEN="$(cat $SECRETS/cf_token.235503)"
export CLOUDFLARE_ACCOUNT_ID="$(cat $SECRETS/cf_account_id.235503)"

cd /opt/data/thoughts
/opt/data/tools/wrangler/bin/wrangler pages deploy . --project-name=thoughts --branch=main --commit-dirty=true > /tmp/thoughts-deploy.log 2>&1 || exit 0
echo "$SHA" > "$STATE"
echo "deployed $SHA at $(date -u +%FT%TZ)"
