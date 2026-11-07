# Install/update Lights. -Uninstall keeps state.json; -Purge removes it too.
# irm https://raw.githubusercontent.com/SaarthurR/govee-lights-app/main/install.ps1 | iex
param([switch]$Uninstall, [switch]$Purge)
function Install-Lights {
  $ErrorActionPreference = 'Stop'
  $ProgressPreference = 'SilentlyContinue'
  $dir = Join-Path $HOME 'govee-lights'
  $taskName = 'Govee Lights'
  $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
  if ($task) {
    Stop-ScheduledTask -TaskName $taskName
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Start-Sleep 1
  }
  if ($Uninstall -or $Purge) {
    if (Test-Path $dir) {
      Get-ChildItem $dir -Force | Where-Object { $_.Name -ne 'state.json' -or $Purge } | Remove-Item -Recurse -Force
      if ($Purge) { Remove-Item $dir -Force }
    }
    Write-Host 'Lights uninstalled. State kept unless -Purge was used.'
    return
  }
  $py = 'py', 'python3', 'python' | Where-Object { Get-Command $_ -ErrorAction SilentlyContinue } |
    Where-Object { & $_ -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>$null; $LASTEXITCODE -eq 0 } | Select-Object -First 1
  if (-not $py) { throw 'Lights needs Python 3.10 or newer: https://www.python.org/downloads/' }
  $py = (& $py -c 'import sys; print(sys.executable)').Trim()
  function Test-Busy($p) { $c = New-Object Net.Sockets.TcpClient; try { $c.Connect('127.0.0.1', $p); $true } catch { $false } finally { $c.Close() } }
  $first = if ($env:PORT) { [int]$env:PORT } elseif (Test-Path "$dir/.port") { [int](Get-Content "$dir/.port") } else { 8765 }; $port = $first
  while (Test-Busy $port) {
    if ($port -ge $first + 5) { throw "Ports $first to $port are busy." }
    $port++
  }
  $tmp = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
  New-Item -ItemType Directory $tmp | Out-Null
  try {
    $url = if ($env:GOVEE_ZIP) { $env:GOVEE_ZIP } else { 'https://github.com/SaarthurR/govee-lights-app/archive/refs/heads/main.zip' }
    Invoke-WebRequest $url -OutFile "$tmp/app.zip" -UseBasicParsing
    Expand-Archive "$tmp/app.zip" "$tmp/app"
    $source = (Get-ChildItem "$tmp/app")[0].FullName
    if (-not (Test-Path "$source/server.py")) { throw 'Download is missing server.py' }
    New-Item -ItemType Directory -Force $dir | Out-Null
    Get-ChildItem $source -Force | Where-Object { $_.Name -ne 'state.json' } | Copy-Item -Destination $dir -Recurse -Force
  } finally { Remove-Item $tmp -Recurse -Force }
  Set-Content "$dir/.port" $port
  # A tiny launcher supplies the selected port on every logon and restart.
  Set-Content "$dir/run.py" "import os, runpy`nos.environ['PORT'] = '$port'`nrunpy.run_path('server.py', run_name='__main__')"
  $action = New-ScheduledTaskAction -Execute $py -Argument '-u run.py' -WorkingDirectory $dir
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
  $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
  $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
  Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
  Start-ScheduledTask -TaskName $taskName
  for ($i = 0; $i -lt 60; $i++) {
    try { Invoke-RestMethod "http://localhost:$port/api/state" -TimeoutSec 1 -UseBasicParsing | Out-Null; Start-Process "http://localhost:$port"; break } catch { Start-Sleep 1 }
  }
  Write-Host "Lights: http://localhost:$port. Starts automatically at logon."
}
Install-Lights
