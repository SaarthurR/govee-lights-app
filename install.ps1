# Install/update Lights. -Uninstall keeps state.json; -Purge removes it too.
# irm https://raw.githubusercontent.com/SaarthurR/govee-lights-app/main/install.ps1 | iex
param([switch]$Uninstall, [switch]$Purge, [switch]$UpdateOnly)
function Install-Lights {
  $ErrorActionPreference = 'Stop'
  $ProgressPreference = 'SilentlyContinue'
  $dir = Join-Path $HOME 'govee-lights'
  $taskName = 'Govee Lights'
  $shortcuts = @((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Govee Lights.lnk'),
                 (Join-Path ([Environment]::GetFolderPath('Programs')) 'Govee Lights.lnk'))
  $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
  function Stop-Lights {
    # Stop through the launcher first: stopping the task kills only the launcher, leaving server.py on the port.
    if (-not $UpdateOnly -and (Test-Path "$dir/.launcher.json")) {
      $previous = Get-Content "$dir/.launcher.json" -Raw | ConvertFrom-Json
      if ($previous.python) {
        & $previous.python "$dir/launcher/launch.py" --stop
        if ($LASTEXITCODE -ne 0) { throw 'Lights is still running; try again.' }
      }
    }
    if (-not $UpdateOnly -and (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) {
      Stop-ScheduledTask -TaskName $taskName
      Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
      Start-Sleep 1
    }
  }
  if ($Uninstall -or $Purge) {
    Stop-Lights
    $shortcuts | Where-Object { Test-Path $_ } | Remove-Item -Force
    if (Test-Path $dir) {
      Get-ChildItem $dir -Force | Where-Object { $_.Name -ne 'state.json' -or $Purge } | Remove-Item -Recurse -Force
      if ($Purge) { Remove-Item $dir -Force }
    }
    Write-Host 'Lights uninstalled. State kept unless -Purge was used.'
    return
  }
  $py = @($env:GOVEE_PYTHON, 'py', 'python3', 'python') | Where-Object { $_ } | Where-Object { Get-Command $_ -ErrorAction SilentlyContinue } |
    Where-Object { & $_ -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>$null; $LASTEXITCODE -eq 0 } | Select-Object -First 1
  if (-not $py) { throw 'Lights needs Python 3.10 or newer: https://www.python.org/downloads/' }
  $py = (& $py -c 'import sys; print(sys.executable)').Trim()
  $pythonw = Join-Path (Split-Path $py) 'pythonw.exe'
  if (-not (Test-Path $pythonw)) { throw 'Your Python installation is missing pythonw.exe. Reinstall Python from python.org.' }
  function Test-Busy($p) { $c = New-Object Net.Sockets.TcpClient; try { $c.Connect('127.0.0.1', $p); $true } catch { $false } finally { $c.Close() } }
  $tmp = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
  New-Item -ItemType Directory $tmp | Out-Null
  $stopped = $false
  $keepBackup = $false
  try {
    $taskXml = if ($task -and -not $UpdateOnly) { Export-ScheduledTask -TaskName $taskName } else { $null }
    $version = $env:GOVEE_VERSION
    if (-not $version -and -not $env:GOVEE_ZIP) {
      # The GitHub API can rate-limit; then install main and let auto-update record the commit later.
      try { $version = (Invoke-RestMethod 'https://api.github.com/repos/SaarthurR/govee-lights-app/commits/main').sha } catch { $version = $null }
    }
    $url = if ($env:GOVEE_ZIP) { $env:GOVEE_ZIP } else { "https://github.com/SaarthurR/govee-lights-app/archive/$(if ($version) { $version } else { 'main' }).zip" }
    Invoke-WebRequest $url -OutFile "$tmp/app.zip" -UseBasicParsing
    Expand-Archive "$tmp/app.zip" "$tmp/app"
    $source = (Get-ChildItem "$tmp/app")[0].FullName
    if (-not (Test-Path "$source/server.py")) { throw 'Download is missing server.py' }
    & $py -m py_compile "$source/server.py" "$source/launcher/launch.py" "$source/launcher/update.py" "$source/launcher/build.py"
    if ($LASTEXITCODE -ne 0) { throw 'Download contains invalid Python' }
    if (-not $UpdateOnly) {
      if (Test-Path $dir) {
        New-Item -ItemType Directory "$tmp/old" | Out-Null
        Get-ChildItem $dir -Force | Where-Object { $_.Name -notin @('state.json', 'state.json.bad', '.server.lock', 'lights.log') } | Copy-Item -Destination "$tmp/old" -Recurse -Force
      }
      for ($i = 0; $i -lt $shortcuts.Count; $i++) {
        if (Test-Path $shortcuts[$i]) { Copy-Item $shortcuts[$i] "$tmp/shortcut-$i" }
      }
      $stopped = $true
      Stop-Lights
    }
    $first = if ($env:PORT) { [int]$env:PORT } elseif (Test-Path "$dir/.port") { [int](Get-Content "$dir/.port") } else { 8765 }; $port = $first
    while (-not $UpdateOnly -and (Test-Busy $port)) {
      if ($port -ge $first + 5) { throw "Ports $first to $port are busy." }
      $port++
    }
    New-Item -ItemType Directory -Force $dir | Out-Null
    Get-ChildItem $source -Force | Where-Object { $_.Name -ne 'state.json' } | Copy-Item -Destination $dir -Recurse -Force
    Set-Content "$dir/.port" $port
    & $py "$dir/launcher/build.py" task $taskName
    if ($LASTEXITCODE -ne 0) { throw 'Could not build the Lights launcher' }
    $launcher = Join-Path $dir 'launcher/launch.py'
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument "-u `"$launcher`" --server" -WorkingDirectory $dir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
    $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    if (-not $UpdateOnly) { Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null }
    if (-not $UpdateOnly) { Start-ScheduledTask -TaskName $taskName }
    $shell = New-Object -ComObject WScript.Shell
    foreach ($path in $shortcuts) {
      $shortcut = $shell.CreateShortcut($path)
      $shortcut.TargetPath = $pythonw
      $shortcut.Arguments = "`"$launcher`""
      $shortcut.WorkingDirectory = $dir
      $shortcut.Description = 'Govee Lights'
      $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,21"
      $shortcut.Save()
    }
    Set-Content "$dir/.version" $(if ($version) { $version } else { 'local' }) -Encoding Ascii
    if ($UpdateOnly) { return }
    Start-Process $pythonw -ArgumentList "`"$launcher`""
    Write-Host "Lights: http://localhost:$port. Starts automatically at logon."
  } catch {
    $failure = $_
    if ($stopped -and (Test-Path "$tmp/old")) {
      Write-Warning 'Install failed; restoring the previous Lights app.'
      try {
        try { Stop-Lights } catch { Write-Warning "Could not stop the failed install: $_" }
        Get-ChildItem $dir -Force | Where-Object { $_.Name -notin @('state.json', 'state.json.bad', '.server.lock', 'lights.log') } | Remove-Item -Recurse -Force
        Get-ChildItem "$tmp/old" -Force | Copy-Item -Destination $dir -Recurse -Force
        for ($i = 0; $i -lt $shortcuts.Count; $i++) {
          if (Test-Path "$tmp/shortcut-$i") { Copy-Item "$tmp/shortcut-$i" $shortcuts[$i] -Force }
        }
        if ($taskXml) {
          Register-ScheduledTask -TaskName $taskName -Xml $taskXml -Force | Out-Null
          Start-ScheduledTask -TaskName $taskName
        } else {
          $old = Get-Content "$dir/.launcher.json" -Raw | ConvertFrom-Json
          Start-Process $old.python -ArgumentList "`"$dir/launcher/launch.py`" --server"
        }
      } catch {
        $keepBackup = $true
        Write-Warning "Could not restart the previous Lights app: $_. Previous files are kept in $tmp"
      }
    }
    throw $failure
  } finally { if (-not $keepBackup) { Remove-Item $tmp -Recurse -Force } }
}
Install-Lights
