# starMindAgent 开机自启任务注册脚本（用户登录触发，无需管理员）
# 幂等：-Force 覆盖同名任务。注册后可用 Get-ScheduledTask 验证。
$bash = 'C:\Users\KZHANG82\AppData\Local\Programs\Git\usr\bin\bash.exe'
$scriptPath = '/c/Kais_Projects/Git_Projects/Github/starMindAgent/start_all.sh'
$workdir = 'C:\Kais_Projects\Git_Projects\Github\starMindAgent'

$action = New-ScheduledTaskAction -Execute $bash -Argument "-lc $scriptPath" -WorkingDirectory $workdir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName 'starMindAgent 全栈自启' -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

$t = Get-ScheduledTask -TaskName 'starMindAgent 全栈自启'
Write-Host "State: $($t.State)"
Write-Host "Execute: $($t.Actions[0].Execute)"
Write-Host "Argument: $($t.Actions[0].Arguments)"
Write-Host "WorkDir: $($t.Actions[0].WorkingDirectory)"
Write-Host "Trigger: $($t.Triggers[0].CimClass.CimClassName)"
