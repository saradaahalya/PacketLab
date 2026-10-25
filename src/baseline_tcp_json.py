"""Standalone TCP/JSON reference: one compact JSON object plus LF per frame.

run_client() accepts an iterable of validated readings for future benchmark use.
RTT includes send, server validation/reply, and client reply validation; connection
setup and input generation/serialization are outside each RTT measurement.
"""

import argparse
import json
import math
import random
import socket
import statistics
import sys
import time
from typing import Iterable

if __package__:
    from .protocol import SENSOR_CONFIG, pack_reading, unpack_reading
else:
    from protocol import SENSOR_CONFIG, pack_reading, unpack_reading


MAX_FRAME_BYTES = 4096  # JSON bytes, excluding the single LF delimiter
READING_FIELDS = {"sensor_id", "seq", "sensor_type", "value", "timestamp_ms"}


class JsonStream:
    """Retain unread bytes so split and coalesced TCP frames both work."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buffer = bytearray()

    def receive(self):
        """Return (object, JSON byte count), or None at a clean frame boundary."""
        while True:
            delimiter = self.buffer.find(b"\n")
            if delimiter >= 0:
                if delimiter > MAX_FRAME_BYTES:
                    raise ValueError("JSON frame exceeds byte limit")
                raw = bytes(self.buffer[:delimiter])
                del self.buffer[:delimiter + 1]
                return decode_json(raw), len(raw)
            if len(self.buffer) > MAX_FRAME_BYTES:
                raise ValueError("JSON frame exceeds byte limit")
            chunk = self.sock.recv(4096)
            if not chunk:
                if self.buffer:
                    raise ValueError("connection closed during a JSON frame")
                return None
            self.buffer.extend(chunk)


def _reject_constant(value):
    raise ValueError(f"non-finite JSON constant: {value}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def decode_json(raw: bytes) -> dict:
    try:
        obj = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant,
                         object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError(f"invalid JSON: {error}") from error
    if not isinstance(obj, dict):
        raise ValueError("JSON frame must be an object")
    return obj


def encode_json(obj: dict) -> bytes:
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False,
                     allow_nan=False).encode("utf-8")
    if len(raw) > MAX_FRAME_BYTES:
        raise ValueError("JSON frame exceeds byte limit")
    return raw


def _integer(obj, field, low, high):
    value = obj[field]
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{field} must be an integer in {low}..{high}")


def validate_reading(reading: dict):
    if set(reading) != READING_FIELDS:
        raise ValueError("reading must contain exactly the five documented fields")
    _integer(reading, "sensor_id", 1, 5)
    _integer(reading, "sensor_type", 1, 3)
    _integer(reading, "seq", 0, 65535)
    _integer(reading, "timestamp_ms", 0, 0xFFFFFFFF)
    sensor_type = reading["sensor_type"]
    if SENSOR_CONFIG[reading["sensor_id"]] != sensor_type:
        raise ValueError("sensor type does not match sensor ID")
    value = reading["value"]
    if type(value) not in (int, float):
        raise ValueError("value must be a finite number")
    low, high = {1: (20.0, 35.0), 2: (30.0, 80.0), 3: (0.0, 1.0)}[sensor_type]
    if not low <= value <= high or not math.isfinite(value):
        raise ValueError("value is outside the sensor range")
    if sensor_type == 3 and value not in (0.0, 1.0):
        raise ValueError("motion value must be 0 or 1")


def generate_readings(count: int, sensor_ids=(1, 2, 3, 4, 5), seed=None):
    """Round-robin sensors; match sensor.py ranges and protocol float32 values."""
    rng = random.Random(seed)
    sequences = {sensor_id: 0 for sensor_id in sensor_ids}
    for index in range(count):
        sensor_id = sensor_ids[index % len(sensor_ids)]
        sensor_type = SENSOR_CONFIG[sensor_id]
        if sensor_type == 1:
            value = round(rng.uniform(20.0, 35.0), 2)
        elif sensor_type == 2:
            value = round(rng.uniform(30.0, 80.0), 2)
        else:
            value = float(rng.choice([0, 1]))
        # Preserve the value and timestamp actually decoded from LLSP on the wire.
        _, value, timestamp = unpack_reading(pack_reading(
            sensor_type, value, int(time.time() * 1000)))
        yield {"sensor_id": sensor_id, "seq": sequences[sensor_id],
               "sensor_type": sensor_type, "value": value,
               "timestamp_ms": timestamp}
        sequences[sensor_id] = (sequences[sensor_id] + 1) & 0xFFFF


def serve(listener: socket.socket, timeout=5.0, max_messages=0) -> dict:
    """Serve connections sequentially; optional total count permits clean demos.

    The caller owns the bound/listening socket. Socket timeout also limits idle
    accepts, so an abandoned finite smoke test does not leave a server hanging.
    """
    stats = {"messages_received": 0, "rejected_connections": 0,
             "request_json_bytes": 0, "request_framing_bytes": 0,
             "reply_json_bytes": 0, "reply_framing_bytes": 0}
    listener.settimeout(timeout)
    try:
        while not max_messages or stats["messages_received"] < max_messages:
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                break
            with connection:
                connection.settimeout(timeout)
                connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                stream = JsonStream(connection)
                try:
                    while not max_messages or stats["messages_received"] < max_messages:
                        frame = stream.receive()
                        if frame is None:
                            break
                        reading, size = frame
                        validate_reading(reading)
                        stats["messages_received"] += 1
                        stats["request_json_bytes"] += size
                        stats["request_framing_bytes"] += 1
                        reply = encode_json({"ack": True,
                                             "sensor_id": reading["sensor_id"],
                                             "seq": reading["seq"]})
                        connection.sendall(reply + b"\n")
                        stats["reply_json_bytes"] += len(reply)
                        stats["reply_framing_bytes"] += 1
                except (ValueError, OSError) as error:
                    stats["rejected_connections"] += 1
                    print(f"Connection closed: {error}", file=sys.stderr)
    except KeyboardInterrupt:
        pass
    return stats


def run_client(host: str, port: int, readings: Iterable[dict], timeout=5.0) -> dict:
    """Send one reading at a time, await validation ACK; no retries or files.

    Failure raises an exception: an interrupted run is not a delivery result.
    Server receipt is confirmed only by a matching reply, not persistence.
    """
    rtts = []
    request_bytes = reply_bytes = 0
    with socket.create_connection((host, port), timeout=timeout) as connection:
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        stream = JsonStream(connection)
        run_start = time.perf_counter_ns()
        for reading in readings:
            validate_reading(reading)
            raw = encode_json(reading)
            start = time.perf_counter_ns()
            connection.sendall(raw + b"\n")
            response = stream.receive()
            if response is None:
                raise ValueError("server closed before acknowledging the reading")
            reply, size = response
            if (set(reply) != {"ack", "sensor_id", "seq"}
                    or reply["ack"] is not True
                    or type(reply["sensor_id"]) is not int
                    or type(reply["seq"]) is not int
                    or reply["sensor_id"] != reading["sensor_id"]
                    or reply["seq"] != reading["seq"]):
                raise ValueError("invalid or mismatched acknowledgment")
            rtts.append((time.perf_counter_ns() - start) / 1_000_000)
            request_bytes += len(raw)
            reply_bytes += size
        elapsed = (time.perf_counter_ns() - run_start) / 1_000_000_000
    count = len(rtts)
    return {"messages_sent": count, "messages_acknowledged": count,
            "mean_rtt_ms": statistics.mean(rtts) if rtts else None,
            "median_rtt_ms": statistics.median(rtts) if rtts else None,
            "request_json_bytes": request_bytes, "request_framing_bytes": count,
            "mean_request_json_bytes": request_bytes / count if count else None,
            "reply_json_bytes": reply_bytes, "reply_framing_bytes": count,
            "elapsed_seconds": elapsed,
            "acknowledged_messages_per_second": count / elapsed if elapsed else 0.0}


def _positive_float(raw):
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    for mode in ("server", "client"):
        command = modes.add_parser(mode)
        command.add_argument("--host", default="127.0.0.1")
        command.add_argument("--port", type=int, default=10000)
        command.add_argument("--timeout", type=_positive_float, default=5.0,
                             help="socket idle timeout in seconds (default: 5)")
        if mode == "server":
            command.add_argument("--max-messages", type=int, default=0,
                                 help="stop after total valid messages; 0 has no count limit")
        else:
            command.add_argument("--count", type=int, default=100,
                                 help="total messages across selected sensors")
            command.add_argument("--sensors", type=int, choices=sorted(SENSOR_CONFIG),
                                 nargs="+", default=sorted(SENSOR_CONFIG))
            command.add_argument("--seed", type=int)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be in 1..65535")
    if args.mode == "client" and args.count <= 0:
        parser.error("--count must be greater than zero")
    if args.mode == "server" and args.max_messages < 0:
        parser.error("--max-messages must be nonnegative")
    try:
        if args.mode == "server":
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind((args.host, args.port))
                listener.listen()
                print(f"TCP JSON listening on {args.host}:{args.port}", flush=True)
                result = serve(listener, args.timeout, args.max_messages)
        else:
            result = run_client(args.host, args.port,
                                generate_readings(args.count, args.sensors, args.seed),
                                args.timeout)
    except (OSError, ValueError) as error:
        print(f"TCP JSON error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
