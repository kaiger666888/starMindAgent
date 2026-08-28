# 验证任务注册详情 + 触发一次测试运行
$t = Get-ScheduledTask -TaskName 'starMindAgent 全栈自启'
Write-Host ('State: ' + $t.State)
Write-Host ('Execute: ' + $t.Actions[0].Execute)
Write-Host ('Arguments: ' + $t.Actions[0].Arguments)
Write-Host ('WorkDir: ' + $t.Actions[0].WorkingDirectory)
Write-Host ('Trigger: ' + $t.Triggers[0].CimClass.CimClassName)

# 触发运行（幂等脚本：已在跑的服务会跳过）
Start-ScheduledTask -TaskName 'starMindAgent 全栈自启'
Start-Sleep -Seconds 8
$info = Get-ScheduledTaskInfo -TaskName 'starMindAgent 全栈自启'
Write-Host ('LastRunTime: ' + $info.LastRunTime)
Write-Host ('LastTaskResult: ' + $info.LastTaskResult)
Write-Host ('NextRunTime: ' + $info.NextRunTime)
