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
| 6 | Payload | Variable | Raw bytes | DATA uses 9 bytes; server-generated ACK uses zero |
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
unknown types/flags/IDs. Inbound ACK payload shape is not checked by the sender or
server. The server reads at most 2048 bytes per datagram. There is no LLSP
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
the same packet, value, timestamp and sequence. A reply is accepted when its
CRC/version decode and type, sensor ID, and sequence match. The sender does not
verify the reply source address. An unexpected ACK consumes an attempt rather
than starting a fresh wait within that attempt. There is no backoff or delayed-ACK
queue; duplicate/stale ACKs have no dedicated handling.

`src/benchmark.py` also permits three total attempts. Default timeout is 0.05
seconds, configurable with `--timeout`. It sends only sensor 1 temperature
readings, starts at a random sequence and advances sequence ranges between loss
configurations. It counts each attempt after the first as a retransmission, even
when the simulator suppresses that attempt's send.

### Duplicate and sequence limitations

The server's process-global `received_packets` set stores `(sensor_id, seq)`.
Valid repeats are labelled duplicates and ACKed again when requested; they are
not logged as new readings. The key has no client address, session or timestamp.
There is no expiry, sliding window or wraparound handling. Sensor restarts,
sequence wrap, benchmark collisions or independent senders sharing an ID can
therefore misclassify new readings as duplicates. Restarting the server clears
the set. Random benchmark start sequences reduce collisions but do not eliminate
them. This is not exactly-once delivery. A sender can report unconfirmed delivery
even when the server received DATA but all usable ACKs were lost or ignored.

## Unreliable operation

DATA with flags zero is validated/deduplicated without an ACK. A fire-and-forget
sender would send once without ACK waiting or application retries. **Neither the
current sensor nor benchmark CLI selects this mode**: both request ACKs.
`src/client.py` is an empty placeholder. An unreliable sender CLI remains future
work; no command is invented here.

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

## TCP + JSON baseline and measurements

[src/baseline_tcp_json.py](../src/baseline_tcp_json.py) sends the equivalent fields
`sensor_id`, `seq`, `sensor_type`, `value`, `timestamp_ms` as compact UTF-8 JSON
followed by one LF byte (`0x0a`). Generated values pass through the LLSP reading
codec to preserve binary32 precision and truncated timestamps. Generation follows
the simulator's ranges without importing its threads/environment configuration.

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

LLSP benchmark latency begins before the first attempt and ends at a matching
ACK, so successful readings may include earlier timeouts and retries. It uses
`perf_counter()` and milliseconds, not timestamp subtraction. This differs from
single request/reply baseline RTT under unimpaired TCP. LLSP `delivered` counts
matching ACKs, not independently measured unique server receipts. Its zero latency
when nothing delivers is a placeholder, not a zero-duration observation.

LLSP offers compact fixed DATA encoding and application reliability options;
JSON is self-describing but larger, while TCP adds connection/transport behavior.
Neither proves a speed advantage. Future comparisons should supply identical
pre-generated readings to `run_client()` and LLSP encoding, repeat matched trials,
measure unique server receipts and apply equivalent documented network impairment.
Unified benchmarking, unreliable sender CLI, graphs, session-aware deduplication,
security and heartbeat semantics remain future work.
