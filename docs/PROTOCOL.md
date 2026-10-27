# LLSP protocol and implementation reference

LLSP is the project's compact application-layer sensor protocol over UDP. Its
goals are small messages, simple parsing, and optional acknowledgement and retry
behavior. It is experimental: neither guaranteed delivery nor lower latency than
TCP is assumed.

This specification records current behavior. Encoding is defined by
[src/protocol.py](../src/protocol.py), receiving by
[src/server.py](../src/server.py), and sending by
[src/sensor.py](../src/sensor.py) and [src/benchmark.py](../src/benchmark.py).
Features absent from these files are identified as unspecified or future work.

## Packet layout

Each UDP datagram carries one LLSP packet. Multibyte fields are big-endian, with
no padding. The header is `>BBHH` (6 bytes). Total length is
**6 + payload length + 2 CRC bytes**. UDP supplies the boundary; there is no length
field in LLSP.

| Offset | Field | Bytes | Type / encoding | Range and purpose |
|---:|---|---:|---|---|
| 0 | Version and type | 1 | Unsigned byte: `(VERSION << 4) \| msg_type` | High nibble version 1; low nibble type 0–15 on decode |
| 1 | Flags | 1 | Unsigned byte | 0–255; bit `0x01` requests an ACK |
| 2 | Sensor ID | 2 | Unsigned 16-bit | Encoder accepts 0–65535; server accepts DATA IDs 1–5 |
| 4 | Sequence | 2 | Unsigned 16-bit | `seq & 0xFFFF`, modulo 65536 |
| 6 | Payload | Variable | Raw bytes | DATA: 9 bytes; ACK/STATS request: zero; STATS reply: 8 bytes |
| 6 + payload length | CRC | 2 | Unsigned 16-bit | `binascii.crc_hqx(header + payload, 0xFFFF)` |

The CRC uses the CRC-CCITT polynomial `0x1021` through `crc_hqx`, initial value
`0xFFFF`, with no additional final XOR. It detects accidental corruption and
provides no authentication. The checksum itself is stored big-endian.

`pack()` relies on `struct.pack` for byte/integer bounds and masks sequence
numbers. It does not separately validate the type nibble or prevent a caller from
supplying a type that overlaps version bits. Use the defined constants. Undefined
flags are not rejected; only bit `0x01` is acted on.

## Constants and message types

| Constant | Value | Current behavior |
|---|---:|---|
| `VERSION` | 1 | Required by `unpack()` |
| `MSG_DATA` | 1 | Reading decoded and validated by the server |
| `MSG_ACK` | 2 | Replies to valid DATA requesting an ACK; inbound ACKs logged |
| `MSG_HEARTBEAT` | 3 | Constant only: payload format and operation unspecified |
| `MSG_STATS_REQ` | 4 | Requests the server's current unique-reading and duplicate counters |
| `MSG_STATS_REPLY` | 5 | Server response carrying the two counters |
| `FLAG_ACK_REQ` | `0x01` | ACK requested for DATA, including duplicates |

Unknown types pass the generic decoder and are logged as unsupported by the
server. No heartbeat sender, timer, payload codec or ACK policy is implemented.

## DATA payload and sensors

`pack_reading(sensor_type, value, ts_ms)` uses `>BfI`. DATA is **17 bytes**:
6-byte header + 9-byte reading + 2-byte CRC.

| Absolute offset | Field | Bytes | Representation | Meaning |
|---:|---|---:|---|---|
| 6 | Sensor type | 1 | Unsigned byte | 1 temperature, 2 humidity, 3 motion |
| 7 | Value | 4 | IEEE 754 binary32 | Server validates the ranges below |
| 11 | Timestamp | 4 | Unsigned 32-bit | `int(time.time() * 1000) & 0xFFFFFFFF` |

| Sensor IDs | Type | Generated values | Interpretation |
|---|---:|---|---|
| 1, 2 | 1 | Uniform 20.0–35.0, rounded to 2 decimal places | Temperature |
| 3, 4 | 2 | Uniform 30.0–80.0, rounded to 2 decimal places | Humidity |
| 5 | 3 | `0.0` or `1.0` | Binary motion indicator |

The source names temperature and humidity but does not encode or explicitly
define physical units or calibration. Celsius and relative humidity percent are
reasonable demo interpretations, not wire guarantees. No integer scaling is used.
Decimal rounding precedes binary32 encoding, so decoded values need not equal the
original two-decimal Python float exactly. Motion is encoded as a float.

Timestamp truncation wraps every `2**32` milliseconds (about 49.7 days). It is not
a full epoch timestamp after truncation. The server prints it but does not use it
for latency or ordering. Clock synchronization and rollover recovery are absent.

## Encoding, decoding and validation

1. `pack()` constructs the header, appends the caller's payload, computes the CRC
   over both, and appends it.
2. `unpack()` rejects packets shorter than 8 bytes, mismatched CRC, or version
   other than 1. It returns `type`, `flags`, `sensor_id`, `seq`, and raw `payload`.
3. `unpack_reading()` requires exactly 9 bytes, a known sensor type, and motion
   values 0 or 1. It does not check sensor IDs or temperature/humidity ranges.
4. `server.validate_reading()` checks IDs 1–5, matching types, temperature 20–35
   inclusive, humidity 30–80 inclusive, and binary motion. Non-finite DATA values
   fail these checks.
5. The server logs rejected packets and continues without ACKing invalid DATA.

The generic decoder does not enforce message-specific payload lengths or reject
unknown types/flags/IDs. Senders require empty ACK payloads and zero flags.
Inbound ACK logging at the server does not enforce a message-specific payload
shape. The server reads at most 2048 bytes per datagram. There is no LLSP
fragmentation/reassembly mechanism.

## Reliable operation and ACK format

The server binds UDP `127.0.0.1:9999`. For valid DATA with `flags & 0x01`, it sends
`pack(MSG_ACK, sensor_id, seq)` to the datagram's source address. This **8-byte**
packet has flags zero, matching ID/sequence, and empty payload; it requests no ACK.
The ACK is sent before a new reading is added to the duplicate set. It confirms
validation and receipt, not durable persistence or downstream processing.

`src/sensor.py` runs five daemon sensor threads. Each starts its sequence at zero,
increments modulo 65536 after each logical reading whether acknowledged or not,
and sleeps 2 seconds after its attempts. All readings request ACKs.
`ACK_TIMEOUT = 1.0` seconds. Despite its name, `MAX_RETRIES = 3` means **three total
attempts**, including the first send: at most two retransmissions. Retries reuse
the same packet, value, timestamp and sequence. Both senders use
`llsp_reply.wait_for_reply()` to check source address/port, CRC/version, type,
sensor ID, sequence, zero flags and empty ACK payload. Unrelated replies are
ignored until the original monotonic deadline; they do not consume another
attempt or extend the deadline. Socket timeout is restored after each wait.
These checks are correlation, not authentication.

`src/benchmark.py` also permits three total attempts, with default timeout 0.05
seconds (`--timeout`). It sends only sensor 1 temperature readings. A benchmark
requires a fresh, dedicated server, starts DATA sequences at zero and advances
disjoint ranges between scenarios. Total readings per invocation cannot exceed
65536. Attempts after the first count as retransmissions even when suppressed.

### Duplicate and sequence limitations

The server's process-global `received_packets` set stores `(sensor_id, seq)`.
Valid repeats are labelled duplicates and ACKed again when requested; they are
not logged as new readings. The key has no client address, session or timestamp.
There is no expiry, sliding window or wraparound handling. Sensor restarts,
sequence wrap, benchmark collisions or independent senders sharing an ID can
therefore misclassify new readings as duplicates. Restarting the server clears
the set. The benchmark rejects a used server and avoids sequence reuse within one
invocation. This does not fix sensor sessions or concurrent sender collisions.
This is not exactly-once delivery. A sender can report unconfirmed delivery
even when the server received DATA but all usable ACKs were lost or ignored.

## STATS request and reply

STATS is integrated into both benchmark modes. The benchmark queries counters
before and after each scenario. A request uses a sequence advanced between queries;
retries of that query reuse its sequence. Replies must match server source, type,
sensor ID, sequence, zero flags and an 8-byte payload. Each query permits three
attempts bounded by the configured socket timeout.

| Packet | Type | Payload | Total LLSP bytes |
|---|---:|---|---:|
| Request | `MSG_STATS_REQ = 4` | Empty | 8 |
| Reply | `MSG_STATS_REPLY = 5` | `pack_stats(unique_received, duplicates)`, format `>II` | 16 |

Both use the standard 6-byte header and 2-byte CRC. Construct a request with
`pack(MSG_STATS_REQ, sensor_id, seq)` (flags zero). On receipt, `src/server.py`
sends a reply to the request's source address, echoing its **sensor ID and sequence
number**, with flags zero. Those fields correlate the reply with the request;
the sensor ID does not filter the statistics. The reply needs no `FLAG_ACK_REQ`,
and is a STATS reply rather than an ACK.

The reply's 8-byte payload is two big-endian unsigned 32-bit integers:

| Absolute offset | Field | Bytes | Range | Meaning |
|---:|---|---:|---|---|
| 6 | `unique_received` | 4 | 0–4294967295 | Valid DATA readings first accepted as new `(sensor_id, seq)` keys |
| 10 | `duplicates` | 4 | 0–4294967295 | Valid DATA arrivals whose key was already in the duplicate set |

`pack_stats()` uses `struct.pack(">II", ...)`; it does not mask overflowing
counters. `unpack_stats()` requires exactly 8 payload bytes and returns
`(unique_received, duplicates)`. Requests have an empty payload by convention;
the current server does not enforce that length or check request flags. Generic
packet CRC/version validation still applies.

Counters cover the whole server process across sensors and clients. They reset
on restart, not after a query. The benchmark requires zero initial counters and
unchanged counters between scenarios. New unique keys cannot exceed logical
readings actually transmitted, and ACK confirmations cannot exceed new receipt.
Inconsistent counter deltas abort without recording that invocation's rows.

The server cannot associate counters with an experiment. Unrelated traffic that
fits those bounds can remain undetected. Use a fresh, dedicated server and stop
all other senders. Never add duplicate arrivals to unique delivery or clamp an
invalid percentage. STATS is unauthenticated UDP and can be lost.

### Restart and wraparound workaround

No safe session identity can be inferred from the existing wire fields. A new
source port is not a stable protocol session identifier; timestamps wrap and can
repeat; changed payloads are not proof of a new logical reading. Expiring keys
could accept delayed retransmissions as new readings. Duplicate detection is
therefore intentionally unchanged, rather than weakened before review.

For independent demos, stop senders, restart the server, then start sensors or
one benchmark invocation. A future wire-version design could include a session
identifier echoed by ACKs, with a defined duplicate retention window.

## Unreliable operation

DATA with flags zero is validated/deduplicated without ACK. The benchmark's
`--mode unreliable` sends once without waiting or retrying; `--mode both` runs
both modes (default). `--interval` controls unreliable pacing (default .002 s).
The five-sensor simulator remains reliable-only. `src/client.py` stays empty.

## Simulated loss

There is **no server-side loss simulation** in current `src/server.py`.
`src/sensor.py` reads `LLSP_LOSS_RATE` (0–1, default 0) and randomly skips outgoing
send attempts at that probability, still waiting for ACKs. `src/benchmark.py`
does the same with `--loss-rates` (default 0, .05, .10, .20). ACKs are not
deliberately dropped. Suppressed sends create no UDP traffic.

This models application-side DATA omission, not bidirectional network loss. TCP
is unimpaired by these settings. Results must not be presented as an equivalent
TCP-versus-UDP network-loss comparison.

## Reproducible packets

Run from the repository root:

```powershell
python -c "from src.protocol import *; p=pack(MSG_DATA,1,100,FLAG_ACK_REQ,pack_reading(1,28.5,123456)); print(p.hex(' ')); print(unpack(p)); print(unpack_reading(unpack(p)['payload'])); print(pack(MSG_ACK,1,100).hex(' '))"
```

DATA hex (17 bytes):

```text
11 01 00 01 00 64 01 41 e4 00 00 00 01 e2 40 ba a2
```

Decoded header: type 1, flags 1, sensor ID 1, sequence 100. Reading:
`(1, 28.5, 123456)`. ACK hex (8 bytes):

```text
12 00 00 01 00 64 84 c6
```

For STATS, generate a request and an illustrative reply containing 12 unique
readings and 3 duplicate arrivals with the actual codec:

```powershell
python -c "from src.protocol import *; request=pack(MSG_STATS_REQ,1,100); reply=pack(MSG_STATS_REPLY,1,100,payload=pack_stats(12,3)); print(request.hex(' ')); print(len(request), unpack(request)); print(reply.hex(' ')); print(len(reply), unpack(reply)); print(unpack_stats(unpack(reply)['payload']))"
```

STATS request hex (8 bytes):

```text
14 00 00 01 00 64 09 27
```

Decoded request: type 4, flags 0, sensor ID 1, sequence 100, empty payload.
STATS reply hex (16 bytes):

```text
15 00 00 01 00 64 00 00 00 0c 00 00 00 03 ee 92
```

Decoded reply: type 5, flags 0, sensor ID 1, sequence 100; `unpack_stats()` returns
`(12, 3)`. These are example counter values, not measured experiment results.

## TCP + JSON baseline and measurements

[src/baseline_tcp_json.py](../src/baseline_tcp_json.py) sends the equivalent fields
`sensor_id`, `seq`, `sensor_type`, `value`, `timestamp_ms` as compact UTF-8 JSON
followed by one LF byte (`0x0a`). JSON carries natural rounded sensor values such
as `27.65`, rather than the longer decimal expansion produced by converting them
to binary32 first. LLSP encodes the same logical values as binary32, so small
representation differences are expected. The timestamp retains LLSP's 32-bit
millisecond truncation. Generation follows the simulator's ranges without
importing its threads/environment configuration.

Valid requests receive `{"ack":true,"sensor_id":1,"seq":0}` plus LF. Both sides
buffer partial reads and retain following frames. Each frame may contain at most
4096 JSON bytes, excluding LF. Requests require exactly the five fields, valid
integer IDs/types/sequences/timestamps, and matching sensor/value ranges. Invalid
UTF-8/JSON, duplicate keys, non-finite values, incomplete/oversized frames or bad
readings close the connection; the server can accept another client. These JSON
schema rules are specific to the baseline.

TCP supplies reliable ordered bytes. The JSON reply measures application
validation turnaround without adding retries. There is no deduplication across
sessions or persistence. Connections are served sequentially to avoid concurrency
overhead. The default 5-second socket timeout covers idle accepts and connection
reads. The server exits after idle accept timeout, Ctrl+C or its optional total
valid-message limit. Both connected sockets use `TCP_NODELAY` to avoid Nagle
buffering of small request/reply frames.

Client RTT uses `time.perf_counter_ns()` immediately before `sendall()` through
reply receipt, JSON decoding and matching ACK validation, reported in milliseconds.
It includes socket buffering, scheduling and server processing; connection setup
and input generation/request serialization are excluded. No one-way latency is
reported. Throughput is acknowledged messages divided by elapsed seconds on the
established connection; that interval also includes generation and serialization.
JSON payload, LF framing and reply bytes are counted separately. None includes
TCP/UDP/IP/Ethernet headers, transport ACKs, handshakes or link retransmissions.

## Benchmark measurements, outputs and historical compatibility

LLSP `avg_latency_ms` starts immediately before the first attempt and ends at a
matching ACK. It includes earlier timeouts/retries and excludes unconfirmed
readings. It is application confirmation time, not one-way latency. A missing
measurement is blank and displays N/A, including all-failed reliable scenarios.
TCP RTT is measured for an unimpaired request/reply on an established connection.
Neither measurement proves a universal latency advantage.

In new LLSP CSVs, `delivered` and `delivery_rate_pct` use only the new unique
receiver count in both modes: unique received / distinct logical readings * 100.
`server_duplicates` is separate. `ack_confirmed` and
`ack_confirmation_rate_pct` preserve reliable confirmation semantics; they are
blank in unreliable mode. ACK receipt and unique server receipt may differ.
`transmitted_readings` counts logical readings with at least one actual send,
not all send attempts. `packets` includes readings suppressed on every attempt.
Other fields are `loss_rate_pct`, `mode`, `timeout_ms`, `avg_latency_ms`,
`retransmissions`, `avg_packet_size_bytes`, `elapsed_seconds`, `server_unique`,
and `server_duplicates`. Elapsed time is rounded; no LLSP throughput is supplied.

Historical CSVs used ACK-counted reliable delivery and unique-plus-duplicate
unreliable delivery. Existing historical rows have zero duplicates and matching
unique/delivered counts, so the stored percentages remain interpretable. They
are not evidence of the new implementation's timings. Preserve originals; run
fresh experiments into a new file for new-code measurements. Appending a new
schema to an old header is rejected, not silently converted.

`benchmark.py` options: `--packets` (500), `--timeout` (.05 seconds), `--mode`
(reliable/unreliable/both, default both), `--interval` (.002 seconds),
`--loss-rates` (0 .05 .10 .20), `--output` (results/llsp_benchmark.csv), and
explicit `--overwrite`. Timeout must be positive finite, interval nonnegative
finite, loss probabilities in [0,1], and the invocation must fit 65536 sequences.
Use a fresh output path and restart the receiver before each invocation.

The PowerShell script supports `-Runs` (5), `-Packets` (500), and
`-OutputDirectory`. It requires a new output directory, otherwise creates a
unique directory under the OS temporary directory. Every run uses a fresh server,
checks ports/readiness and preserves CSVs, server logs and four generated graphs.
It does not delete historical data or stop unrelated processes. See README for
verified demo commands.

`plot.py --llsp PATH --tcp PATH --output-dir NEW_DIRECTORY` reads historical and
new CSVs and writes delivery, latency, retransmission and application-size PNGs.
Missing unreliable latency displays N/A. Plotting uses a noninteractive PNG
backend. Error bars are sample standard deviations across runs, not confidence
intervals. Groups use loss and mode only; do not mix packet counts/timeouts.
Standalone plotting can replace same-named graphs in its selected directory.

DATA size includes LLSP header, 9-byte payload and CRC. TCP request size is JSON
plus one LF. These exclude TCP/UDP/IP/Ethernet headers, transport ACKs, handshakes
and network retries; a size reduction is not measured total bandwidth savings.
Only LLSP DATA attempts are suppressed. TCP remains unimpaired and ACK loss is
not simulated, so loss-resilience comparisons are not equivalent network tests.

The team contribution is the compact sensor-specific format, implementation and
experimental analysis. CRCs, ACKs, sequence numbers and retries are standard ideas.
Session-aware deduplication, security, heartbeat semantics and identical impaired
TCP/LLSP trials remain outside the current implementation.
