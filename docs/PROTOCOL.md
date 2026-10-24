# LLSP — Lightweight Low-latency Sensor Protocol

## 1. Purpose

LLSP is a custom application-layer protocol designed for exchanging small IoT sensor readings over UDP. It defines a compact binary packet format, message types, packet validation, and optional reliability using acknowledgements and retransmissions.

## 2. Packet Structure

Every LLSP packet consists of a header, an optional payload, and a CRC-16 checksum.

| Field | Size | Description |
|---|---:|---|
| Version + Message Type | 1 byte | Upper 4 bits represent version; lower 4 bits represent message type |
| Flags | 1 byte | Communication options, including ACK request |
| Sensor ID | 2 bytes | Identifies the sensor |
| Sequence Number | 2 bytes | Identifies the message; wraps modulo 65,536 |
| Payload | Variable | Message-specific data |
| CRC-16 | 2 bytes | Detects accidental corruption |

The fixed header is 6 bytes. The checksum adds 2 bytes.

**Total packet size = 8 bytes + payload size.**

All multibyte integer fields use big-endian byte order. The CRC is calculated over the header and payload using `binascii.crc_hqx(data, 0xFFFF)`.

## 3. Protocol Constants

### Version

- Current protocol version: `1`

### Message Types

| Constant | Value | Purpose |
|---|---:|---|
| `MSG_DATA` | 1 | Carries a sensor reading |
| `MSG_ACK` | 2 | Acknowledges a received DATA packet |
| `MSG_HEARTBEAT` | 3 | Indicates that a sensor is active |

### Flags

| Constant | Value | Purpose |
|---|---:|---|
| `FLAG_ACK_REQ` | `0x01` | Requests an acknowledgement for a DATA packet |

Other flag bits are reserved and must be zero in version 1.

## Sensor Types and Sensor IDs

The LLSP system uses five simulated sensors with IDs from 1 to 5. Each DATA packet contains a 9-byte sensor reading payload formatted as `>BfI`:

- `B`: sensor type (1 byte)
- `f`: sensor value (4-byte floating-point number)
- `I`: timestamp in milliseconds (4 bytes)

### Sensor Type Definitions

| Type Byte | Sensor Type | Valid Values |
|---|---|---|
| 1 | Temperature | 20–35 °C |
| 2 | Humidity | 30–80% |
| 3 | Motion | 0 or 1 |

### Sensor ID Mapping

| Sensor ID | Sensor Type |
|---|---|
| 1 | Temperature |
| 2 | Temperature |
| 3 | Humidity |
| 4 | Humidity |
| 5 | Motion |

All five simulated sensors operate concurrently. Each sensor maintains its own sequence-number counter. Motion value `0` represents no motion, while `1` represents motion detected.

The receiver validates the sensor ID, sensor type, and value range before accepting a reading.


## 4. DATA Message

A DATA message carries one sensor reading.

Its payload uses the binary format `>BfI`:

| Field | Size | Description |
|---|---:|---|
| Sensor Type | 1 byte | Identifies the measurement type |
| Value | 4 bytes | IEEE 754 single-precision floating-point value |
| Timestamp | 4 bytes | Reading generation time in milliseconds, modulo 2³² |

The DATA payload is 9 bytes, so a DATA packet is 17 bytes total.

### Sensor Type IDs

| ID | Sensor Type | Unit or Meaning |
|---:|---|---|
| 1 | Temperature | Degrees Celsius |
| 2 | Humidity | Percentage |
| 3 | Light intensity | Lux |
| 4 | Motion | 0 = no motion, 1 = motion |

The sensor ID in the packet header identifies the sensor device. The sensor type in the payload identifies the kind of measurement.

## 5. ACK Message

An ACK message confirms receipt of a DATA packet.

- Message type: `MSG_ACK` (2)
- Sensor ID: ID of the originating sensor
- Sequence number: Sequence number of the DATA packet being acknowledged
- Payload: Empty
- Total packet size: 8 bytes

ACK messages do not request another ACK.

## 6. HEARTBEAT Message

A HEARTBEAT message indicates that a sensor is active.

Its payload contains one unsigned 32-bit timestamp in milliseconds, encoded using `>I`.

- Message type: `MSG_HEARTBEAT` (3)
- Sensor ID: ID of the sending sensor
- Sequence number: Sequence number assigned to the heartbeat
- Payload: 4-byte timestamp
- Total packet size: 12 bytes

HEARTBEAT messages do not request an ACK in version 1.

## 7. Reliability Modes

### Unreliable Mode

The client sends DATA packets without requesting acknowledgements. Lost packets are not retransmitted.

### Reliable Mode

The client sets `FLAG_ACK_REQ` on DATA packets and waits for an ACK with the matching sensor ID and sequence number.

If no matching ACK arrives before the configured timeout, the client retransmits the same packet, retaining its original sequence number.

The server uses the sensor ID and sequence number to detect duplicate DATA packets. A duplicate must not be processed as a new sensor reading. If the original DATA packet requested an ACK, the server sends the ACK again when a duplicate arrives.

Sequence numbers wrap from 65,535 to 0. Duplicate detection must account for this wraparound and use a documented cache or sequence-window policy.

## 8. Packet Validation

The receiver must:

1. Reject packets shorter than 8 bytes.
2. Verify the CRC-16 checksum.
3. Verify that the protocol version is supported.
4. Reject unknown message types and unsupported flag bits.
5. Validate each message type's expected payload length.
6. Validate DATA sensor type IDs and motion values.
7. Apply duplicate detection to DATA messages before processing readings.

Malformed or corrupted packets must not be processed as valid readings.

## 9. Performance Evaluation

LLSP will be evaluated against a TCP + JSON baseline using equivalent simulated sensor readings.

Experiments will include different message counts and simulated packet-loss rates of 0%, 5%, 10%, and 20%.

The following metrics will be measured:

- Average latency or round-trip time, with the exact measurement method documented
- Delivery ratio
- Number of retransmissions
- Application packet size
- Throughput in successfully delivered messages per second

Each configuration should be repeated, and results should be summarized using tables and graphs.

## 10. Implementation Notes

The Python implementation in `src/protocol.py` defines packet encoding, decoding, sensor-reading serialization, and CRC validation.

This document defines the intended version 1 protocol behavior. Implementation tests should verify that the code follows these rules.
