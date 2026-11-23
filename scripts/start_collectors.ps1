# Start the three region collectors, each in its own PowerShell window.
# They resume where they left off (progress is in data/hiddenop.db).
#
#   powershell -ExecutionPolicy Bypass -File scripts\start_collectors.ps1
#
# To start them automatically when you log in, run once from the hidden-op folder:
#   $a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File `"$((Get-Location).Path)\scripts\start_collectors.ps1`""
#   Register-ScheduledTask -TaskName "hidden-op collectors" -Action $a -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME)

$repo = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python).Source
foreach ($platform in "na1", "euw1", "kr") {
    $cmd = "Set-Location '$repo'; `$host.UI.RawUI.WindowTitle = 'hidden-op $platform'; " +
           "& '$python' -m hiddenop.collector.collect --platform $platform --days 14 --loop --max-matches 2000000"
    Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", $cmd
}
