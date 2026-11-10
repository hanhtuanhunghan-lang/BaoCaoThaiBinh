$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeDir = Join-Path $ScriptDir "runtime"
$FlagPath = Join-Path $RuntimeDir "show_window.flag"

if (!(Test-Path -LiteralPath $RuntimeDir)) {
  New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
}

Set-Content -LiteralPath $FlagPath -Value "show" -Encoding Ascii
Write-Host "[OK] Da gui lenh mo lai cua so DuNoPro Thai Binh qua flag runtime."
Start-Sleep -Milliseconds 800
