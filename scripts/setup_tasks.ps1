# Creates InternIQ's two Windows Task Scheduler jobs (run once, from any folder):
#   InternIQ Daily Check: 10 PM every day, wakes the PC from sleep, runs scripts\daily.cmd
#   InternIQ Discord Bot: starts at logon in the background, restarts within a minute if it stops
# Both run only while the user is logged in: the agent's Chrome windows need a desktop.

$root = Split-Path -Parent $PSScriptRoot
$user = $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive

$daily = New-ScheduledTaskAction -Execute (Join-Path $root "scripts\daily.cmd") -WorkingDirectory $root
$at10 = New-ScheduledTaskTrigger -Daily -At "22:00"
$dailySettings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "InternIQ Daily Check" -Action $daily -Trigger $at10 -Settings $dailySettings `
    -Principal $principal -Force | Out-Null

$bot = New-ScheduledTaskAction -Execute (Join-Path $root ".venv\Scripts\pythonw.exe") -Argument "-m interniq bot" `
    -WorkingDirectory $root
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $user
$botSettings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "InternIQ Discord Bot" -Action $bot -Trigger $atLogon -Settings $botSettings `
    -Principal $principal -Force | Out-Null

Get-ScheduledTask -TaskName "InternIQ*" | ForEach-Object { "{0}: {1}" -f $_.TaskName, $_.State }
