param(
    [int]$Runs = 5,
    [int]$Packets = 500,
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
if ($Runs -le 0 -or $Packets -le 0 -or $Packets * 8 -gt 65536) {
    throw "Runs and Packets must be positive; eight LLSP scenarios must fit 65536 sequences."
}
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Python = if (Test-Path -LiteralPath $VenvPython) { $VenvPython } else { (Get-Command python).Source }
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path ([System.IO.Path]::GetTempPath()) (
        "PacketLab-experiments\" + (Get-Date -Format "yyyyMMdd-HHmmss-fff") + "-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
    )
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $OutputDirectory) {
    throw "Output directory already exists. Choose a new directory; historical results will not be overwritten."
}
# Directory.CreateDirectory alone would accept an existing directory. Atomic creation
# of the leaf prevents two script instances from sharing one output set.
$parent = Split-Path -Parent $OutputDirectory
[System.IO.Directory]::CreateDirectory($parent) | Out-Null
New-Item -ItemType Directory -Path $OutputDirectory -ErrorAction Stop | Out-Null
$LlspResults = Join-Path $OutputDirectory "llsp_benchmark.csv"
$TcpResults = Join-Path $OutputDirectory "tcp_baseline.csv"
$GraphDirectory = Join-Path $OutputDirectory "graphs"
Write-Host "Output directory: $OutputDirectory"
$serverProcess = $null

function Assert-PortFree {
    param([int]$Port, [System.Net.Sockets.SocketType]$Type, [System.Net.Sockets.ProtocolType]$Protocol)
    $probe = New-Object System.Net.Sockets.Socket ([System.Net.Sockets.AddressFamily]::InterNetwork, $Type, $Protocol)
    try {
        $probe.ExclusiveAddressUse = $true
        $probe.Bind((New-Object System.Net.IPEndPoint ([System.Net.IPAddress]::Loopback, $Port)))
    } catch {
        throw "Local port $Port is unavailable. Stop the conflicting server before running experiments."
    } finally {
        $probe.Dispose()
    }
}

function Stop-TrackedProcess {
    param([System.Diagnostics.Process]$Process)
    if ($null -ne $Process) {
        $Process.Refresh()
        if (-not $Process.HasExited) {
            Stop-Process -Id $Process.Id -Force
            $Process.WaitForExit()
        }
    }
}

function Wait-ServerReady {
    param([System.Diagnostics.Process]$Process, [string]$Log, [string]$Banner)
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ([DateTime]::UtcNow -lt $deadline) {
        $Process.Refresh()
        if ($Process.HasExited) { throw "Server exited during startup. Inspect $Log and its error log." }
        if ((Test-Path -LiteralPath $Log) -and
            ((Get-Content -LiteralPath $Log -Raw) -like "*$Banner*")) { return }
        Start-Sleep -Milliseconds 100
    }
    throw "Server did not become ready within 10 seconds. Inspect $Log."
}

try {
    for ($run = 1; $run -le $Runs; $run++) {
        try {
            Assert-PortFree 9999 Dgram Udp
            $log = Join-Path $OutputDirectory "llsp-server-$run.log"
            $serverProcess = Start-Process -FilePath $Python -ArgumentList @("-B", "-u", "src/server.py", "--quiet") `
                -WorkingDirectory $RepoRoot -PassThru -WindowStyle Hidden `
                -RedirectStandardOutput $log -RedirectStandardError (Join-Path $OutputDirectory "llsp-server-$run.err")
            $null = $serverProcess.Handle
            Wait-ServerReady $serverProcess $log "Listening on 127.0.0.1:9999"
            & $Python -B (Join-Path $RepoRoot "src/benchmark.py") --packets $Packets --output $LlspResults
            if ($LASTEXITCODE -ne 0) { throw "LLSP benchmark failed with exit code $LASTEXITCODE." }
        } finally {
            Stop-TrackedProcess $serverProcess
            $serverProcess = $null
        }
        try {
            Assert-PortFree 10000 Stream Tcp
            $log = Join-Path $OutputDirectory "tcp-server-$run.log"
            $serverProcess = Start-Process -FilePath $Python `
                -ArgumentList @("-B", "-u", "-m", "src.baseline_tcp_json", "server", "--max-messages", "$Packets", "--timeout", "30") `
                -WorkingDirectory $RepoRoot -PassThru -WindowStyle Hidden `
                -RedirectStandardOutput $log -RedirectStandardError (Join-Path $OutputDirectory "tcp-server-$run.err")
            $null = $serverProcess.Handle
            Wait-ServerReady $serverProcess $log "TCP JSON listening on 127.0.0.1:10000"
            & $Python -B (Join-Path $RepoRoot "src/baseline_tcp_json.py") client --count $Packets --seed $run --csv $TcpResults
            if ($LASTEXITCODE -ne 0) { throw "TCP client failed with exit code $LASTEXITCODE." }
            if (-not $serverProcess.WaitForExit(10000)) { throw "TCP server did not finish after the expected messages." }
            if ($serverProcess.ExitCode -ne 0) { throw "TCP server failed; inspect its error log." }
        } finally {
            Stop-TrackedProcess $serverProcess
            $serverProcess = $null
        }
        Write-Host "Run $run of $Runs finished"
    }
    $previousMplConfig = $env:MPLCONFIGDIR
    try {
        $env:MPLCONFIGDIR = Join-Path $OutputDirectory ".matplotlib"
        & $Python -B (Join-Path $RepoRoot "src/plot.py") --llsp $LlspResults --tcp $TcpResults --output-dir $GraphDirectory
        if ($LASTEXITCODE -ne 0) { throw "Plot generation failed with exit code $LASTEXITCODE." }
    } finally {
        if ($null -eq $previousMplConfig) { Remove-Item Env:MPLCONFIGDIR -ErrorAction SilentlyContinue }
        else { $env:MPLCONFIGDIR = $previousMplConfig }
    }
} finally {
    Stop-TrackedProcess $serverProcess
}
Write-Host "Results and server logs: $OutputDirectory"
Write-Host "Graphs: $GraphDirectory"
