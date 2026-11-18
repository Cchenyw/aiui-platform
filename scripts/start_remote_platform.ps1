param(
    [Parameter(Mandatory=$true)][string]$Policies,
    [string]$GatewayHost = '127.0.0.1',
    [int]$GatewayPort = 8010,
    [int]$AdminPort = 8000,
    [string]$Certificate,
    [string]$PrivateKey,
    [string]$DatabasePath,
    [string]$RuntimeDir,
    [string]$ExecutorRoot,
    [switch]$AllowInsecureHttp,
    [switch]$NoAutostart
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONIOENCODING = 'utf-8'
if ($RuntimeDir) {
    $env:AIUI_RUNTIME_DIR = [System.IO.Path]::GetFullPath($RuntimeDir)
}
if ($ExecutorRoot) {
    $env:AIUI_EXECUTOR_ROOT = [System.IO.Path]::GetFullPath($ExecutorRoot)
}
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw '缺少项目虚拟环境，请先安装 Python 并创建 .venv。'
}
$platformArgs = @('-B', '-m', 'aiui.platform.server', '--port', [string]$AdminPort,
    '--node-policies', $Policies, '--gateway-host', $GatewayHost,
    '--gateway-port', $GatewayPort)
if ($DatabasePath) { $platformArgs += @('--db', $DatabasePath) }
if ($Certificate) { $platformArgs += @('--gateway-cert', $Certificate) }
if ($PrivateKey) { $platformArgs += @('--gateway-key', $PrivateKey) }
if ($AllowInsecureHttp) { $platformArgs += '--allow-insecure-gateway-http' }
if ($NoAutostart) { $platformArgs += '--no-autostart' }
& $pythonExe @platformArgs
exit $LASTEXITCODE
