
import binascii
import struct
import unittest

import sys
from pathlib import Path

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "src")
)

import protocol


class TestLLSPProtocol(unittest.TestCase):

    def test_all_sensor_types_round_trip(self):
        test_readings = [
            (protocol.SENSOR_TEMPERATURE, 28.5, 1000),
            (protocol.SENSOR_HUMIDITY, 65.0, 2000),
            (protocol.SENSOR_MOTION, 1.0, 3000),
        ]

        for sensor_type, value, timestamp in test_readings:
            with self.subTest(sensor_type=sensor_type):
                payload = protocol.pack_reading(
                    sensor_type, value, timestamp
                )

                result = protocol.unpack_reading(payload)
            
                self.assertEqual(result[0], sensor_type)
                self.assertAlmostEqual(result[1], value, places=5)
                self.assertEqual(result[2], timestamp)

    def test_packet_round_trip(self):
        """A packed packet should decode to the original fields."""
        packet = protocol.pack(
            protocol.MSG_DATA,
            sensor_id=1,
            seq=100,
            flags=protocol.FLAG_ACK_REQ,
            payload=b"hello",
        )

        result = protocol.unpack(packet)

        self.assertEqual(result["type"], protocol.MSG_DATA)
        self.assertEqual(result["flags"], protocol.FLAG_ACK_REQ)
        self.assertEqual(result["sensor_id"], 1)
        self.assertEqual(result["seq"], 100)
        self.assertEqual(result["payload"], b"hello")

    def test_empty_payload(self):
        """A packet without payload should still be valid."""
        packet = protocol.pack(protocol.MSG_ACK, 1, 10)
        result = protocol.unpack(packet)

        self.assertEqual(result["type"], protocol.MSG_ACK)
        self.assertEqual(result["payload"], b"")

    def test_sequence_number_wraps(self):
        """Sequence numbers are stored as unsigned 16-bit values."""
        packet = protocol.pack(protocol.MSG_DATA, 1, 65536)
        result = protocol.unpack(packet)

        self.assertEqual(result["seq"], 0)

    def test_packet_too_short(self):
        """Packets shorter than the minimum size must be rejected."""
        with self.assertRaisesRegex(ValueError, "too short"):
            protocol.unpack(b"\x01\x02\x03")

    def test_invalid_checksum(self):
        """Corrupted packet data should fail CRC validation."""
        packet = bytearray(protocol.pack(protocol.MSG_DATA, 1, 10))
        packet[2] ^= 0x01

        with self.assertRaisesRegex(ValueError, "checksum"):
            protocol.unpack(bytes(packet))

    def test_unsupported_version(self):
        """Packets with a different version must be rejected."""
        packet = bytearray(protocol.pack(protocol.MSG_DATA, 1, 10))

        # Change the version field to 2.
        packet[0] = (2 << 4) | (packet[0] & 0x0F)

        # Recalculate CRC so validation reaches the version check.
        checksum = binascii.crc_hqx(packet[:-2], 0xFFFF)
        packet[-2:] = struct.pack(">H", checksum)

        with self.assertRaisesRegex(ValueError, "version"):
            protocol.unpack(bytes(packet))

    def test_reading_round_trip(self):
        """Sensor readings should retain their values."""
        payload = protocol.pack_reading(
            sensor_type=1,
            value=28.5,
            ts_ms=123456,
        )

        sensor_type, value, timestamp = (
            protocol.unpack_reading(payload)
        )

        self.assertEqual(sensor_type, 1)
        self.assertAlmostEqual(value, 28.5, places=5)
        self.assertEqual(timestamp, 123456)

    def test_reading_payload_size(self):
        """A sensor reading payload should contain exactly 9 bytes."""
        payload = protocol.pack_reading(1, 28.5, 123456)

        self.assertEqual(len(payload), 9)

    def test_timestamp_wraps(self):
        """Timestamps are stored as unsigned 32-bit values."""
        payload = protocol.pack_reading(1, 25.0, 2**32)
        _, _, timestamp = protocol.unpack_reading(payload)

        self.assertEqual(timestamp, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
