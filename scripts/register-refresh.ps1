param([string]$At = '13:00')

$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = (& py -3.12 -c 'import sys; print(sys.executable)').Trim()
if (-not (Test-Path -LiteralPath $python)) { throw 'Python 3.12 is not available' }
$action = New-ScheduledTaskAction -Execute $python -Argument '-m sih081 refresh --regions Mumbai Pune' -WorkingDirectory $workspace
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'SIH26081-Refresh' -Action $action -Trigger $trigger -Principal $principal -Description 'Daily SIH26081 research forecast refresh, after 06:00 UTC' -Force | Out-Null
Get-ScheduledTask -TaskName 'SIH26081-Refresh' | Select-Object TaskName, State
