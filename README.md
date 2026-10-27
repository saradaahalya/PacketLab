# Design and Performance Evaluation of a Custom Application-Layer Protocol for Low-Latency IoT Sensor Data Exchange

PacketLab is a Computer Networks laboratory project that simulates five IoT
sensors sending readings to a receiver. Our Lightweight IoT Sensor Protocol
(LLSP) uses compact binary messages over UDP, with optional acknowledgments and
retries. A TCP + JSON baseline supports comparison of application message size,
delivery and confirmation time. No physical sensors are required.

## Features and architecture

- 17-byte LLSP DATA, 8-byte ACK, version checking and CRC validation.
- Five concurrent sensors: two temperature, two humidity and one motion.
- Reliable transmission with up to three total attempts; unreliable benchmark
  mode sends once without waiting for ACKs.
- Duplicate detection, correlated ACK/STATS replies and separate unique-receipt
  and ACK-confirmation measurements.
- TCP + JSON framing, validation, RTT, byte accounting and throughput.
- CSV benchmarks, repeated experiment orchestration and four result graphs.

```text
Sensor simulator or LLSP benchmark -> Binary encoding -> UDP receiver
                                      ^                 | validation/deduplication
                                      +--- ACK/retry ---+
LLSP benchmark <-> Receiver STATS -> LLSP CSV --+
TCP JSON client <-> TCP JSON server -> TCP CSV -+-> Aggregation -> Graphs
```

The live simulator runs all five sensors. The LLSP benchmark uses sensor 1
(temperature); TCP cycles selected sensors over one persistent connection.
See [docs/PROTOCOL.md](docs/PROTOCOL.md) for packet fields and detailed behavior.

## Software requirements and installation

Validated on Windows with **Python 3.14.7** and PowerShell. Dependencies are
`pytest` and `matplotlib`, listed without version pins in
[requirements.txt](requirements.txt). Network components use Python's standard
library. Other Python versions are not independently verified.

Clone the integration branch and install in a virtual environment:

```powershell
git clone --branch dev https://github.com/saradaahalya/PacketLab.git
cd PacketLab
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

`dev` is the integration branch; `main` is the final submission branch. Reuse an
existing environment when available. Run the following commands from the project
root, activating the environment in each terminal. If activation is blocked,
replace `python` with `.\.venv\Scripts\python.exe`; changing system execution
policy is unnecessary.

## Execution

### 1. Five-sensor LLSP demonstration

Terminal 1:

```powershell
python -B src/server.py
```

Terminal 2:

```powershell
python -B src/sensor.py
```

Both use `127.0.0.1:9999`. The receiver prints decoded readings and ACKs; sensors
print matching confirmations. Sensors request ACKs, wait up to one second per
attempt, allow at most two retries and sleep two seconds after each reading's
attempts. Stop both programs with Ctrl+C.

To demonstrate sender-side DATA omission, set this in Terminal 2 before starting
sensors. It affects LLSP DATA attempts, including retries, and does not affect TCP:

```powershell
$env:LLSP_LOSS_RATE = "0.20"
python -B src/sensor.py
# After stopping the simulator:
Remove-Item Env:LLSP_LOSS_RATE
```

**Restart the UDP server before each independent sensor session or benchmark
invocation.** Stop other senders first: sensor restarts reuse sequence numbers,
and benchmark STATS counters require exclusive receiver use.

### 2. Reliable and unreliable benchmarks

Start a fresh receiver in Terminal 1:

```powershell
python -B src/server.py --quiet
```

Terminal 2:

```powershell
$csv = Join-Path $env:TEMP ("llsp-" + [guid]::NewGuid() + ".csv")
python -B src/benchmark.py --packets 30 --timeout 0.01 --mode both --loss-rates 0 0.20 --output $csv
```

The command prints delivery, ACK confirmation, latency, retry and size summaries,
then saves four scenario rows to `$csv`. Use `--mode reliable` or
`--mode unreliable` to select one mode. Unreliable mode has no ACK wait/retries;
its latency is not measured and displays `N/A`.

Defaults are 500 readings per scenario, 0.05-second timeout, both modes, and loss
probabilities 0, .05, .10 and .20. `--interval` controls unreliable pacing
(default .002 seconds). Total readings per invocation must fit 65536 distinct
sequence values. Use a new `--output` path; without it, output targets the tracked
`results/llsp_benchmark.csv`. `--overwrite` explicitly replaces the selected CSV
and should not be used on historical results.

### 3. TCP + JSON baseline

Terminal 1:

```powershell
python -B -m src.baseline_tcp_json server --timeout 60 --max-messages 10
```

Terminal 2:

```powershell
python -B -m src.baseline_tcp_json client --count 10 --sensors 1 2 3 4 5 --seed 42
```

Both use `127.0.0.1:10000` by default. The client should report 10 acknowledged
messages; the server reports 10 received messages and exits. Summaries include
mean/median application RTT, JSON/framing bytes and messages per second.
`--count` is total across selected sensors. Optional `--csv PATH` appends a
completed client summary; choose a new file. Both sides support `--host`,
`--port` and `--timeout`. TCP supplies reliable ordered bytes; application replies
confirm validation, not persistent storage.

### 4. Repeated experiments and graph generation

Stop manually started servers and sensors first. Run in one terminal:

```powershell
$out = Join-Path $env:TEMP ("PacketLab-review-" + [guid]::NewGuid())
.\scripts\run_final_experiments.ps1 -Runs 2 -Packets 30 -OutputDirectory $out
```

The script starts/stops its own servers, checks ports/readiness, runs eight LLSP
scenarios plus one TCP trial per repetition, and generates four graphs. CSVs,
server logs, graphs and plotting cache go into `$out`. Existing output directories
are refused. Defaults are five runs and 500 readings; omitting `-OutputDirectory`
creates a unique temporary directory. Historical repository results are preserved.

To plot existing results separately, use a new graph directory:

```powershell
$graphs = Join-Path $env:TEMP ("PacketLab-graphs-" + [guid]::NewGuid())
python -B src/plot.py --llsp results/llsp_benchmark.csv --tcp results/tcp_baseline.csv --output-dir $graphs
```

Plotting exports PNGs without a GUI. Same-named graphs in a selected output
directory are replaced, so use a fresh directory.

### 5. Automated tests

```powershell
python -B -m pytest -p no:cacheprovider tests
```

The verified Windows suite has **44 passing cases**: 12 protocol, 12 TCP baseline
and 20 LLSP/plot/script cases. Tests cover CRC/framing, reply filtering, bounded
retries, duplicates/restarts, delivery accounting, five sensors, CSV plotting and
output safety. They use localhost and temporary files. Windows-only checks skip
on other platforms; the busy-port check may skip if its port is already occupied.

## Experimental methodology and results

The stored experiment used five trials of 500 logical readings for each LLSP
mode/loss combination, plus five 500-message TCP trials. LLSP omission probabilities
were 0%, 5%, 10% and 20%, with a 50 ms timeout. New runs should record their settings
and environment and use a fresh, dedicated receiver. LLSP has no CLI seed; TCP's
seed controls values but not timestamps.

| Measurement | Meaning |
|---|---|
| Delivery rate | New unique receiver keys / logical readings, in both LLSP modes |
| ACK confirmation rate | Reliable readings with a matching ACK / logical readings; separate from receipt |
| LLSP latency | First attempt through matching ACK, including earlier timeouts/retries; unconfirmed readings excluded |
| TCP RTT | Send through validated application reply; connection setup and request generation/serialization excluded |
| Retry count | LLSP attempts after the first, including simulated suppressed attempts |
| Application bytes | LLSP DATA includes header/payload/CRC; TCP JSON and one-byte LF framing are counted separately |
| TCP throughput | Acknowledged messages / elapsed time, including generation/serialization |

Latency is not one-way delay. Missing latency is not zero. Graphs aggregate trials
using means and sample standard deviations; error bars are not confidence intervals.
Plot grouping uses loss and mode, so do not combine different packet counts/timeouts.

Historical data: [LLSP CSV](results/llsp_benchmark.csv) and
[TCP CSV](results/tcp_baseline.csv). Graphs:
[delivery](results/graphs/delivery_vs_loss.png),
[latency](results/graphs/latency_vs_loss.png),
[retransmissions](results/graphs/retransmissions_vs_loss.png), and
[application size](results/graphs/packet_size.png).

Historical reliable delivery counted ACKs and unreliable delivery counted unique
plus duplicate arrivals. All stored rows have zero duplicates and matching unique/
delivered counts, so their percentages remain interpretable. New CSVs also separate
ACK confirmations and transmitted readings. Use a new CSV for the new schema;
fresh runs are required to measure the revised implementation's performance.

## Project structure

```text
PacketLab/
|-- src/
|   |-- protocol.py              # Binary packet, reading and STATS codecs
|   |-- server.py                # UDP receiver, validation, ACKs and duplicates
|   |-- sensor.py                # Five concurrent reliable sensor threads
|   |-- llsp_reply.py            # Correlated replies within fixed deadlines
|   |-- benchmark.py             # Reliable/unreliable LLSP experiments
|   |-- baseline_tcp_json.py     # TCP reference and CSV export
|   |-- plot.py                  # CSV aggregation and four graphs
|   `-- client.py                # Empty, unused placeholder
|-- tests/
|   |-- test_protocol.py
|   |-- baseline_tcp_json_test.py
|   `-- test_llsp_correctness.py
|-- scripts/run_final_experiments.ps1
|-- docs/PROTOCOL.md
|-- results/                     # Two historical CSVs and graphs/
|-- requirements.txt
|-- .gitignore
`-- README.md
```

## Limitations

- Duplicate keys use `(sensor_id, sequence)` without session identity or expiry.
  Restarted sensors and sequence wrap can suppress valid new readings; restart
  the receiver between independent sessions.
- STATS counters are server-global. The benchmark rejects detectable inconsistencies,
  but cannot identify every unrelated sender; isolate experiments.
- Only outgoing LLSP DATA attempts are suppressed. ACKs and TCP are unimpaired;
  these are not equivalent TCP-versus-UDP network-loss experiments. The TCP graph
  line is explicitly an unimpaired reference.
- Different inputs/schedules and localhost testing limit performance conclusions.
  Smaller application messages do not establish universal speed/reliability gains.
- Application bytes exclude TCP/UDP/IP/Ethernet headers, transport ACKs, handshakes
  and network retransmissions. They are not total network bandwidth.
- CRC is not authentication. There is no encryption, durable storage, operational
  heartbeat or physical sensor support.
