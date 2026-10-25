"""Independent baseline tests; no changes to the team's packet tests."""

import socket
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import baseline_tcp_json as baseline


class ChunkSocket:
    def __init__(self, *chunks):
        self.chunks = iter(chunks)

    def recv(self, size):
        return next(self.chunks, b"")


class TestTCPJSON(unittest.TestCase):
    def test_partial_and_coalesced_reads(self):
        stream = baseline.JsonStream(ChunkSocket(b'{"a":', b'1}\n{"b":2}\n'))
        self.assertEqual(stream.receive(), ({"a": 1}, 7))
        self.assertEqual(stream.receive(), ({"b": 2}, 7))
        self.assertIsNone(stream.receive())

    def test_truncated_frame(self):
        stream = baseline.JsonStream(ChunkSocket(b'{"a":1}'))
        with self.assertRaisesRegex(ValueError, "during"):
            stream.receive()

    def test_invalid_json(self):
        for raw in (b"no", b"[]", b"\xff", b'{"a":NaN}',
                    b'{"a":1,"a":2}', b""):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                baseline.JsonStream(ChunkSocket(raw + b"\n")).receive()

    def test_frame_limit(self):
        for suffix in (b"", b"\n"):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                baseline.JsonStream(ChunkSocket(
                    b"x" * (baseline.MAX_FRAME_BYTES + 1) + suffix)).receive()

    def test_utf8_byte_size(self):
        raw = baseline.encode_json({"name": "\u00b5"})
        self.assertEqual(len(raw), 13)
        self.assertEqual(baseline.JsonStream(ChunkSocket(raw + b"\n")).receive(),
                         ({"name": "\u00b5"}, len(raw)))

    def test_generated_sensor_semantics(self):
        readings = list(baseline.generate_readings(10, seed=7))
        self.assertEqual([r["sensor_id"] for r in readings], [1, 2, 3, 4, 5] * 2)
        self.assertEqual([r["seq"] for r in readings], [0] * 5 + [1] * 5)
        for reading in readings:
            baseline.validate_reading(reading)
            decoded = baseline.unpack_reading(baseline.pack_reading(
                reading["sensor_type"], reading["value"], reading["timestamp_ms"]))
            self.assertEqual(decoded[1], reading["value"])
            self.assertIsInstance(reading["value"], float)

    def test_invalid_reading_fields(self):
        reading = next(baseline.generate_readings(1, seed=1))
        invalid = [{}, dict(reading, extra=1), dict(reading, sensor_id=True),
                   dict(reading, sensor_type=2), dict(reading, value="25"),
                   dict(reading, value=float("inf")), dict(reading, seq=-1),
                   dict(reading, timestamp_ms=2**32), dict(reading, value=36),
                   dict(reading, sensor_id=5, sensor_type=3, value=0.5)]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                baseline.validate_reading(item)

    def test_loopback_repeated_connections_and_accounting(self):
        readings = list(baseline.generate_readings(10, seed=42))
        results, errors = [], []
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]

            def server():
                try:
                    results.append(baseline.serve(listener, timeout=2, max_messages=10))
                except Exception as error:
                    errors.append(error)

            thread = threading.Thread(target=server, daemon=True)
            thread.start()
            try:
                first = baseline.run_client("127.0.0.1", port, readings[:5], timeout=2)
                second = baseline.run_client("127.0.0.1", port, readings[5:], timeout=2)
            finally:
                thread.join(5)
            self.assertFalse(thread.is_alive(), "server did not exit")
        self.assertEqual(errors, [])
        self.assertEqual(results[0]["messages_received"], 10)
        self.assertEqual(results[0]["rejected_connections"], 0)
        self.assertEqual(results[0]["request_json_bytes"],
                         sum(len(baseline.encode_json(r)) for r in readings))
        for key in ("request_json_bytes", "reply_json_bytes",
                    "request_framing_bytes", "reply_framing_bytes"):
            self.assertEqual(results[0][key], first[key] + second[key])
        for stats in (first, second):
            self.assertEqual(stats["messages_acknowledged"], 5)
            self.assertGreaterEqual(stats["mean_rtt_ms"], 0)
            self.assertGreater(stats["acknowledged_messages_per_second"], 0)

    def test_server_rejects_bad_json_and_recovers(self):
        results = []
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            thread = threading.Thread(target=lambda: results.append(
                baseline.serve(listener, timeout=2, max_messages=1)), daemon=True)
            thread.start()
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
                    client.sendall(b"invalid\n")
                    self.assertEqual(client.recv(1), b"")
                baseline.run_client("127.0.0.1", port, baseline.generate_readings(1), 2)
            finally:
                thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]["rejected_connections"], 1)
        self.assertEqual(results[0]["messages_received"], 1)

    def test_idle_server_exits(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            self.assertEqual(baseline.serve(listener, timeout=0.01)["messages_received"], 0)


if __name__ == "__main__":
    unittest.main()
