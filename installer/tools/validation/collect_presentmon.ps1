param(
    [Parameter(Mandatory=$true)][string]$PresentMon,
    [Parameter(Mandatory=$true)][int]$GameProcessId,
    [Parameter(Mandatory=$true)][int]$Seconds,
    [Parameter(Mandatory=$true)][string]$RunDirectory
)
$ErrorActionPreference = 'Stop'
$resultPath = Join-Path $RunDirectory 'profiler.json'
try {
    # Elevate the native collector directly. No elevated script or policy change.
    $arguments = @('--process_id', $GameProcessId, '--timed', $Seconds,
        '--terminate_after_timed', '--no_console_stats', '--v1_metrics',
        '--session_name', ('Infamous-' + $GameProcessId), '--terminate_on_proc_exit',
        '--output_file', ('"' + (Join-Path $RunDirectory 'frames.csv') + '"'))
    $child = Start-Process -FilePath $PresentMon -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -PassThru
    $null = $child.Handle
    $started = [DateTimeOffset]::UtcNow.ToString('o')
    @{status='running'; started_utc=$started} | ConvertTo-Json | Set-Content -LiteralPath $resultPath
    $child.WaitForExit()
    $code = $child.ExitCode
    if ($null -eq $code) { throw 'Collector exit code unavailable' }
    @{status=$(if ($code -eq 0) {'completed'} else {'failed'}); exit_code=$code; started_utc=$started; ended_utc=[DateTimeOffset]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $resultPath
    exit $code
} catch {
    @{status='failed'; error=$_.Exception.Message; ended_utc=[DateTimeOffset]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $resultPath
    exit 1
}
