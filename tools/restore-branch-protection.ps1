# Restore main branch protection (PR + review-gate required)
# Usage: pwsh tools/restore-branch-protection.ps1
# Requires: gh auth login (with repo admin scope)

$ErrorActionPreference = "Stop"
$repo = "na-navi/krita-agent-bridge"
$payload = Join-Path $PSScriptRoot "branch-protection-restore.json"

if (-not (Test-Path $payload)) {
    Write-Error "Restore payload not found: $payload"
    exit 1
}

Write-Host "Restoring main branch protection on $repo ..."
gh api `
    --method PUT `
    -H "Accept: application/vnd.github+json" `
    "repos/$repo/branches/main/protection" `
    --input $payload

if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to restore branch protection."
    exit $LASTEXITCODE
}

Write-Host "Verifying ..."
gh api "repos/$repo/branches/main/protection" `
    --jq '{enforce_admins: .enforce_admins.enabled, required_status_checks: .required_status_checks.contexts, linear: .required_linear_history.enabled, force_push: .allow_force_pushes.enabled}'

Write-Host "Done. main is protected again. Direct push to main is blocked."
