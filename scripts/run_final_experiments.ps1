param(
    [int]$Runs = 5
)

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if ($Runs -le 0) {
    throw "-Runs must be greater than zero."
}

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $Python = $VenvPython
} else {
    $Python = "python"
}

$LlspResults = Join-Path $RepoRoot "results\llsp_benchmark.csv"
$TcpResults = Join-Path $RepoRoot "results\tcp_baseline.csv"
$serverProcess = $null

function Stop-TrackedProcess {
    param(
        [System.Diagnostics.Process]$Process
    )

    if ($null -ne $Process) {
        $Process.Refresh()
        if (-not $Process.HasExited) {
            Stop-Process -Id $Process.Id -Force
            $Process.WaitForExit()
        }
    }
}

if (Test-Path $LlspResults) {
    Remove-Item $LlspResults
}
if (Test-Path $TcpResults) {
    Remove-Item $TcpResults
}

try {
    for ($run = 1; $run -le $Runs; $run++) {
        try {
            $serverProcess = Start-Process `
                -FilePath $Python `
                -ArgumentList @("src\server.py", "--quiet") `
                -PassThru `
                -WindowStyle Hidden
            Start-Sleep -Seconds 1

            & $Python "src\benchmark.py" "--packets" "500"
            if ($LASTEXITCODE -ne 0) {
                throw "LLSP benchmark failed with exit code $LASTEXITCODE."
            }
        } finally {
            Stop-TrackedProcess $serverProcess
            $serverProcess = $null
        }

        try {
            $serverProcess = Start-Process `
                -FilePath $Python `
                -ArgumentList @(
                    "-m", "src.baseline_tcp_json", "server",
                    "--max-messages", "500", "--timeout", "30"
                ) `
                -PassThru `
                -WindowStyle Hidden
            Start-Sleep -Seconds 1

            & $Python "-m" "src.baseline_tcp_json" "client" `
                "--count" "500" "--seed" $run "--csv" "results\tcp_baseline.csv"
            if ($LASTEXITCODE -ne 0) {
                throw "TCP baseline client failed with exit code $LASTEXITCODE."
            }

            $waited = 0
            while ($waited -lt 10) {
                $serverProcess.Refresh()
                if ($serverProcess.HasExited) {
                    break
                }
                Start-Sleep -Seconds 1
                $waited++
            }

            $serverProcess.Refresh()
            if (-not $serverProcess.HasExited) {
                Stop-TrackedProcess $serverProcess
            }
        } finally {
            Stop-TrackedProcess $serverProcess
            $serverProcess = $null
        }

        Write-Host "Run $run of $Runs finished"
    }
} finally {
    Stop-TrackedProcess $serverProcess
}

& $Python "src\plot.py"
if ($LASTEXITCODE -ne 0) {
    throw "Plot generation failed with exit code $LASTEXITCODE."
}

Write-Host "LLSP results: $LlspResults"
Write-Host "TCP results: $TcpResults"
Write-Host "Graphs: $(Join-Path $RepoRoot 'results\graphs')"
