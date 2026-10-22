import binascii
import struct


VERSION = 1

MSG_DATA = 1
MSG_ACK = 2
MSG_HEARTBEAT = 3

FLAG_ACK_REQ = 0x01


def pack(msg_type, sensor_id, seq, flags=0, payload=b""):
    """Build an LLSP packet with a CRC16 checksum."""
    header = struct.pack(
        ">BBHH",
        (VERSION << 4) | msg_type,
        flags,
        sensor_id,
        seq & 0xFFFF,
    )
    body = header + payload
    checksum = binascii.crc_hqx(body, 0xFFFF)
    return body + struct.pack(">H", checksum)


def unpack(pkt):
    """Parse and validate an LLSP packet."""
    if len(pkt) < 8:
        raise ValueError("packet is too short")

    body = pkt[:-2]
    checksum = struct.unpack(">H", pkt[-2:])[0]
    if binascii.crc_hqx(body, 0xFFFF) != checksum:
        raise ValueError("invalid checksum")

    first, flags, sensor_id, seq = struct.unpack(">BBHH", body[:6])
    version = first >> 4
    if version != VERSION:
        raise ValueError("unsupported version")

    return {
        "type": first & 0x0F,
        "flags": flags,
        "sensor_id": sensor_id,
        "seq": seq,
        "payload": body[6:],
    }


def pack_reading(sensor_type, value, ts_ms):
    """Pack a sensor reading payload."""
    return struct.pack(">BfI", sensor_type, value, ts_ms & 0xFFFFFFFF)


def unpack_reading(payload):
    """Unpack a sensor reading payload."""
    return struct.unpack(">BfI", payload)