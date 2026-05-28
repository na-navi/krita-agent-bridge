param(
    [string]$Commit = "HEAD",
    [string]$TargetDir = "$env:APPDATA\krita\pykrita\krita_agent_bridge_shim",
    [switch]$Force,
    [switch]$DryRun,
    [switch]$InstallHook,
    [switch]$ForceHook
)

$ErrorActionPreference = "Stop"

function Get-RepoRoot {
    $root = git rev-parse --show-toplevel
    if (-not $root) {
        throw "This script must be run from inside the git repository."
    }
    return $root.Trim()
}

function Get-ChangedFilesForCommit {
    param([string]$CommitRef)

    $raw = git diff-tree --no-commit-id --name-only -r $CommitRef 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect commit '$CommitRef'."
    }
    return @($raw | Where-Object { $_ })
}

function Install-PostCommitHook {
    param(
        [string]$RepoRoot,
        [bool]$OverwriteExisting
    )

    $hookPath = Join-Path $RepoRoot ".git\hooks\post-commit"
    $scriptPath = Join-Path $RepoRoot "tools\deploy_krita_shim.ps1"
    $marker = "krita-agent-bridge deploy hook"

    if ((Test-Path -LiteralPath $hookPath) -and -not $OverwriteExisting) {
        $existing = Get-Content -LiteralPath $hookPath -Raw
        if ($existing -notmatch [regex]::Escape($marker)) {
            throw "A non-krita-agent-bridge post-commit hook already exists. Re-run with -ForceHook to replace it, or merge the hook manually: $hookPath"
        }
    }

    $hook = @"
#!/bin/sh
# $marker
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$scriptPath" -Commit HEAD
"@

    Set-Content -LiteralPath $hookPath -Value $hook -Encoding ascii
    Write-Host "Installed local post-commit hook: $hookPath"
    Write-Host "The hook deploys Krita shim files only when the committed change touches shim/*.py."
}

$repoRoot = Get-RepoRoot

if ($InstallHook) {
    Install-PostCommitHook -RepoRoot $repoRoot -OverwriteExisting $ForceHook.IsPresent
    exit 0
}

$deployFiles = @(
    "shim/ai_diffusion_endpoints.py",
    "shim/document_ops.py",
    "shim/job_queue_endpoints.py",
    "shim/krita_api_server.py",
    "shim/safe_files.py"
)

if (-not $Force) {
    $changed = Get-ChangedFilesForCommit -CommitRef $Commit
    $deployableChanges = @($changed | Where-Object { $deployFiles -contains $_.Replace("\", "/") })
    if ($deployableChanges.Count -eq 0) {
        Write-Host "No Krita shim deployable files changed in commit '$Commit'. Nothing copied."
        exit 0
    }
}

if (-not (Test-Path -LiteralPath $TargetDir)) {
    throw "Krita shim target directory does not exist: $TargetDir. Import the plugin ZIP in Krita first."
}

foreach ($relativePath in $deployFiles) {
    $source = Join-Path $repoRoot $relativePath.Replace("/", "\")
    $destination = Join-Path $TargetDir (Split-Path $relativePath -Leaf)

    if (-not (Test-Path -LiteralPath $source)) {
        throw "Source file missing: $source"
    }

    if ($DryRun) {
        Write-Host "Would copy: $source -> $destination"
    } else {
        Copy-Item -LiteralPath $source -Destination $destination -Force
        Write-Host "Copied: $relativePath -> $TargetDir"
    }
}

if ($DryRun) {
    Write-Host "Dry run complete. Krita was not modified."
} else {
    Write-Host ""
    Write-Host "Krita shim files were deployed."
    Write-Host "Restart Krita before testing; already-imported Python modules may still be cached."
}
