#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 OWNER/REPO" >&2
  exit 2
fi

repo="$1"

case "$repo" in
  */*) ;;
  *)
    echo "Repository must be in OWNER/REPO form." >&2
    exit 2
    ;;
esac

echo "== gh auth status =="
gh auth status -h github.com || true

echo
echo "== repository =="
gh repo view "$repo" --json nameWithOwner,owner,visibility,isPrivate,defaultBranchRef,hasIssuesEnabled,hasWikiEnabled,hasProjectsEnabled

echo
echo "== rulesets =="
gh api "repos/$repo/rulesets" || true

echo
echo "== actions permissions =="
gh api "repos/$repo/actions/permissions" || true

echo
echo "== workflow token permissions =="
gh api "repos/$repo/actions/permissions/workflow" || true

echo
echo "== fork PR contributor approval =="
gh api "repos/$repo/actions/permissions/fork-pr-contributor-approval" || true

echo
echo "== vulnerability alerts =="
if gh api -i "repos/$repo/vulnerability-alerts" >/tmp/github-oss-repo-hardening-vuln-alerts.$$ 2>&1; then
  cat /tmp/github-oss-repo-hardening-vuln-alerts.$$
else
  cat /tmp/github-oss-repo-hardening-vuln-alerts.$$ || true
fi
rm -f /tmp/github-oss-repo-hardening-vuln-alerts.$$

echo
echo "== repository secrets names only =="
gh secret list --repo "$repo" || true
