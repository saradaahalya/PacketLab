"""Bounded, correlated UDP reply reception shared by the LLSP senders."""

import socket
import time

from protocol import unpack


def wait_for_reply(sock, server_address, msg_type, sensor_id, seq,
                   payload_size, timeout):
    """Ignore unrelated datagrams within one deadline; restore socket timeout."""
    previous_timeout = sock.gettimeout()
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout("LLSP reply deadline expired")
            sock.settimeout(remaining)
            try:
                response, source = sock.recvfrom(2048)
            except ConnectionResetError:
                continue
            if source != server_address:
                continue
            try:
                message = unpack(response)
            except ValueError:
                continue
            if (message["type"] == msg_type
                    and message["sensor_id"] == sensor_id
                    and message["seq"] == seq
                    and message["flags"] == 0
                    and len(message["payload"]) == payload_size):
                return message
    finally:
        sock.settimeout(previous_timeout)
