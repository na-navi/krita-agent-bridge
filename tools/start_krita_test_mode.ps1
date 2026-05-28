param(
    [string]$KritaExe = "C:\Program Files\Krita (x64)\bin\krita.exe",
    [string]$KritaApi = "http://127.0.0.1:8900",
    [string]$ComfyUiApi = "http://127.0.0.1:8188",
    [string]$DocumentName = "smoke-bootstrap",
    [int]$Width = 1024,
    [int]$Height = 1024,
    [double]$Timeout = 60,
    [double]$Interval = 1,
    [switch]$NoDocument,
    [switch]$Json
)

$ErrorActionPreference = "Stop"

$argsList = @(
    "--krita-api", $KritaApi,
    "--comfyui-api", $ComfyUiApi,
    "bootstrap",
    "--krita-exe", $KritaExe,
    "--document-name", $DocumentName,
    "--width", "$Width",
    "--height", "$Height",
    "--timeout", "$Timeout",
    "--interval", "$Interval"
)

if ($NoDocument) {
    $argsList += "--no-document"
}
if ($Json) {
    $argsList += "--json"
}

python -m krita_agent_bridge.cli @argsList
exit $LASTEXITCODE
