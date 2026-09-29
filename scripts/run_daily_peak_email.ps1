param(
    [Parameter(Mandatory = $true)]
    [string]$SettingsPath
)

$ErrorActionPreference = 'Stop'
$exitCode = 1
$transcribing = $false
try {
    $settings = Get-Content -LiteralPath $SettingsPath -Raw | ConvertFrom-Json
    foreach ($field in @('DataDirectory', 'AccountsFile', 'PythonPath', 'Recipients')) {
        if ([string]::IsNullOrWhiteSpace([string]$settings.$field)) {
            throw "Missing required setting: $field"
        }
    }
    foreach ($path in @($settings.DataDirectory, $settings.AccountsFile, $settings.PythonPath)) {
        if (-not (Test-Path -LiteralPath $path)) {
            throw "Required local path does not exist: $path"
        }
    }
    $logDir = Join-Path $settings.DataDirectory 'logs'
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $logPath = Join-Path $logDir ("daily-email-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
    Start-Transcript -LiteralPath $logPath | Out-Null
    $transcribing = $true
    if ((Get-Date).Hour -lt 8) {
        Write-Output 'Before 08:00 local time; the daily trigger will handle delivery.'
        $exitCode = 0
    } else {
        $env:SYDNEY_ROAD_DATA_DIR = $settings.DataDirectory
        $env:SYDNEY_ROAD_ACCOUNTS_FILE = $settings.AccountsFile
        $env:CONNECT_DAILY_PEAK_RECIPIENTS = $settings.Recipients
        $env:PYTHONUNBUFFERED = '1'
        Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
        & $settings.PythonPath -m electricity_tool.daily_peak_alert
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            throw "Daily email command failed with exit code $exitCode. See $logPath"
        }
    }
} catch {
    $exitCode = 1
    Write-Error -ErrorRecord $_ -ErrorAction Continue
} finally {
    if ($transcribing) {
        Stop-Transcript | Out-Null
    }
}
exit $exitCode
