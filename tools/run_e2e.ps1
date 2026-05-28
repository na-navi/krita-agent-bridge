<#
.SYNOPSIS
    One-command E2E smoke test: start Krita → wait for readiness → run smoke → save report.

.DESCRIPTION
    Krita's AI Diffusion plugin manages ComfyUI automatically (server_mode=managed),
    so this script only needs to start Krita. ComfyUI comes up as part of the
    plugin's startup sequence.

    The script reads the ComfyUI port from the AI Diffusion plugin settings
    (default 127.0.0.1:8000) so the CLI gets the correct endpoint automatically.

    Total wall time is bounded by the dynamic poll budget:
      bootstrap (1 task × 60s = 60s max) +
      smoke    (2 tasks × 60s = 120s max)
    = ~180s absolute worst case before handing back to a human.

.USAGE
    pwsh tools/run_e2e.ps1                          # defaults
    pwsh tools/run_e2e.ps1 -Verbose                  # detailed output
    pwsh tools/run_e2e.ps1 -ReportDir ./reports      # custom report location

.PARAMETER KritaExe
    Path to krita.exe.

.PARAMETER KritaApi
    Krita shim API endpoint.

.PARAMETER ReportDir
    Directory for smoke report and output PNG. Created if needed.

.PARAMETER Timeout
    Per-task timeout in seconds (default 60).

.PARAMETER SkipBootstrap
    Skip the bootstrap step (Krita is already running).

.PARAMETER ExtraSmokeArgs
    Additional arguments forwarded to `krita-agent smoke`.

.ENVIRONMENT
    KRITA_AGENT_ALLOW_LONG_POLL=1  Disable per-task timeout caps (human-supervised).
#>

param(
    [string]$KritaExe = "C:\Program Files\Krita (x64)\bin\krita.exe",
    [string]$KritaApi = "http://127.0.0.1:8900",
    [string]$ReportDir = "./e2e_reports",
    [double]$Timeout = 60,
    [switch]$SkipBootstrap,
    [string[]]$ExtraSmokeArgs
)

$ErrorActionPreference = "Stop"

# ── Resolve ComfyUI URL ─────────────────────────────────────────────────
# The AI Diffusion plugin's server_url (settings.json) is its own management
# port (default 8000), NOT the ComfyUI API port.  The actual ComfyUI API
# listens on 8188 (standard).  Probe both and use whichever responds.
$ComfyCandidates = @(
    "http://127.0.0.1:8188",
    "http://127.0.0.1:8000"
)
$ComfyUiApi = $ComfyCandidates[0]  # fallback
foreach ($url in $ComfyCandidates) {
    try {
        $resp = Invoke-WebRequest -Uri "$url/system_stats" -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
        if ($resp.StatusCode -eq 200) {
            $ComfyUiApi = $url
            Write-Verbose "ComfyUI API detected at: $url"
            break
        }
    } catch {
        Write-Verbose "ComfyUI not at $url"
    }
}

# ── Prepare report directory ─────────────────────────────────────────────
$ReportDir = [System.IO.Path]::GetFullPath($ReportDir)
if (-not (Test-Path $ReportDir)) {
    New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$reportPath = Join-Path $ReportDir "smoke_report_$timestamp.json"
$outputPath = Join-Path $ReportDir "smoke_output_$timestamp.png"
$bootstrapLog = Join-Path $ReportDir "bootstrap_$timestamp.json"

# ── Step 1: Bootstrap (start Krita + wait for readiness) ─────────────────
if (-not $SkipBootstrap) {
    Write-Host "=== Step 1: Bootstrap ===" -ForegroundColor Cyan
    Write-Host "Krita:   $KritaExe"
    Write-Host "Shim:    $KritaApi"
    Write-Host "ComfyUI: $ComfyUiApi"
    Write-Host ""

    $bootArgs = @(
        "--krita-api", $KritaApi,
        "--comfyui-api", $ComfyUiApi,
        "bootstrap",
        "--krita-exe", $KritaExe,
        "--timeout", "$Timeout",
        "--interval", "1",
        "--json"
    )

    & python -m krita_agent_bridge.cli @bootArgs 2>&1 | Tee-Object -Variable bootOut
    $bootExit = $LASTEXITCODE

    # Save bootstrap output
    $bootOut | Set-Content $bootstrapLog -Encoding UTF8

    if ($bootExit -ne 0) {
        Write-Host ""
        Write-Host "BOOTSTRAP FAILED (exit $bootExit)" -ForegroundColor Red
        Write-Host "Krita or ComfyUI did not become ready within ${Timeout}s." -ForegroundColor Red
        Write-Host "Human intervention required. Check:" -ForegroundColor Yellow
        Write-Host "  - Is Krita installed at: $KritaExe ?" -ForegroundColor Yellow
        Write-Host "  - Did the AI Diffusion plugin start ComfyUI?" -ForegroundColor Yellow
        Write-Host "  - Is the shim port 8900 accessible?" -ForegroundColor Yellow
        Write-Host "  - Log: $bootstrapLog" -ForegroundColor Yellow
        exit $bootExit
    }
    Write-Host ""
} else {
    Write-Host "=== Skipping bootstrap (Krita assumed running) ===" -ForegroundColor DarkGray
}

# ── Step 2: Smoke test ───────────────────────────────────────────────────
Write-Host "=== Step 2: E2E Smoke ===" -ForegroundColor Cyan
Write-Host "Report:  $reportPath"
Write-Host "Output:  $outputPath"
Write-Host ""

$smokeArgs = @(
    "--krita-api", $KritaApi,
    "--comfyui-api", $ComfyUiApi,
    "smoke",
    "--report", $reportPath,
    "--output", $outputPath,
    "--timeout", "$Timeout",
    "--interval", "1",
    "--json"
)

if ($ExtraSmokeArgs) {
    $smokeArgs += $ExtraSmokeArgs
}

& python -m krita_agent_bridge.cli @smokeArgs 2>&1 | Tee-Object -Variable smokeOut
$smokeExit = $LASTEXITCODE

Write-Host ""

if ($smokeExit -eq 0) {
    Write-Host "E2E SMOKE PASSED" -ForegroundColor Green
    Write-Host "Report: $reportPath"
    Write-Host "Output: $outputPath"
} else {
    Write-Host "E2E SMOKE FAILED (exit $smokeExit)" -ForegroundColor Red
    Write-Host "Report: $reportPath"
    Write-Host ""
    Write-Host "Possible causes:" -ForegroundColor Yellow
    Write-Host "  - ComfyUI crashed during generation" -ForegroundColor Yellow
    Write-Host "  - No checkpoint loaded in ComfyUI" -ForegroundColor Yellow
    Write-Host "  - GPU out of memory" -ForegroundColor Yellow
    Write-Host "  - Check the report JSON for step-by-step details." -ForegroundColor Yellow
}

exit $smokeExit
