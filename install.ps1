# Lights: download the app into ~\govee-lights, start it and open it in your browser.
# Run it again to update; your state.json (lights, themes, timers) is kept.
#   irm https://raw.githubusercontent.com/SaarthurR/govee-lights-app/main/install.ps1 | iex
function Install-Lights {  # a function, so its settings and "return" stay out of your PowerShell window
  $ProgressPreference = 'SilentlyContinue'
  $url = if ($env:GOVEE_ZIP) { $env:GOVEE_ZIP } else { 'https://github.com/SaarthurR/govee-lights-app/archive/refs/heads/main.zip' }
  $dir = Join-Path $HOME 'govee-lights'

  $py = 'py', 'python' | Where-Object { Get-Command $_ -ErrorAction SilentlyContinue } |
    Where-Object { & $_ -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>$null; $LASTEXITCODE -eq 0 } | Select-Object -First 1
  if (-not $py) { Write-Host 'Lights needs Python 3.10 or newer. Get it from https://www.python.org/downloads/ and run this again.'; return }

  function Test-Busy($p) { $c = New-Object Net.Sockets.TcpClient; try { $c.Connect('127.0.0.1', $p); $true } catch { $false } finally { $c.Close() } }
  $first = if ($env:PORT) { [int]$env:PORT } else { 8765 }; $port = $first
  while (Test-Busy $port) {
    try { if ((Invoke-RestMethod "http://localhost:$port/api/state" -TimeoutSec 2 -UseBasicParsing).phone) {
      Write-Host "Lights is already running at http://localhost:$port. Stop it (Ctrl+C in its window), then run this again."; return } } catch {}
    if ($port -ge $first + 5) { Write-Host "Ports $first to $port are all busy. Close something using one of them and run this again."; return }
    $port++
  }

  Write-Host "Downloading Lights into $dir"
  $zip = Join-Path $env:TEMP 'govee-lights.zip'; $tmp = Join-Path $env:TEMP 'govee-lights-unzip'
  Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
  Invoke-WebRequest $url -OutFile $zip -UseBasicParsing -ErrorAction Stop
  Expand-Archive $zip $tmp -ErrorAction Stop
  New-Item -ItemType Directory -Force $dir | Out-Null
  Copy-Item (Join-Path (Get-ChildItem $tmp)[0].FullName '*') $dir -Recurse -Force -ErrorAction Stop  # state.json is not in the download, so yours stays
  Remove-Item $zip, $tmp -Recurse -Force

  $oldPort = $env:PORT; $env:PORT = $port
  try {
    $server = Start-Process $py 'server.py' -WorkingDirectory $dir -NoNewWindow -PassThru
    for ($i = 0; $i -lt 60 -and -not $server.HasExited; $i++) {
      try { Invoke-RestMethod "http://localhost:$port/api/state" -TimeoutSec 1 -UseBasicParsing | Out-Null; Start-Process "http://localhost:$port"; break } catch { Start-Sleep 1 }
    }
    $server.WaitForExit()
  } finally { $env:PORT = $oldPort }
}
Install-Lights
