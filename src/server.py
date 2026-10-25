
import argparse
import socket
import time

from protocol import (
    MSG_DATA,
    MSG_ACK,
    MSG_STATS_REQ,
    MSG_STATS_REPLY,
    FLAG_ACK_REQ,
    unpack,
    unpack_reading,
    pack_stats,
    pack,
)

# Server configuration
HOST = "127.0.0.1"
PORT = 9999
BUFFER_SIZE = 2048

# Sensor IDs and their expected sensor types
SENSOR_CONFIG = {
    1: 1,  # Temperature
    2: 1,  # Temperature
    3: 2,  # Humidity
    4: 2,  # Humidity
    5: 3,  # Motion
}

# Store received (sensor_id, sequence_number) pairs
received_packets = set()


def validate_reading(sensor_id, sensor_type, value):
    """Check whether a reading matches its sensor and valid range."""

    if sensor_id not in SENSOR_CONFIG:
        raise ValueError("unknown sensor ID")

    if SENSOR_CONFIG[sensor_id] != sensor_type:
        raise ValueError("sensor type does not match sensor ID")

    if sensor_type == 1 and not 20.0 <= value <= 35.0:
        raise ValueError("temperature must be between 20 and 35")

    if sensor_type == 2 and not 30.0 <= value <= 80.0:
        raise ValueError("humidity must be between 30 and 80")

    if sensor_type == 3 and value not in (0.0, 1.0):
        raise ValueError("motion must be 0 or 1")


def start_server(quiet=False):
    """Receive, validate and acknowledge LLSP UDP packets."""

    unique_received = 0
    duplicates = 0

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind((HOST, PORT))

        print("=" * 48)
        print("       LLSP UDP SERVER STARTED")
        print("=" * 48)
        print(f"Listening on {HOST}:{PORT}")
        print("Press Ctrl+C to stop the server.\n")

        while True:
            try:
                packet, client_address = server.recvfrom(BUFFER_SIZE)
            except ConnectionResetError:
                continue

            try:
                # 1. Validate the LLSP packet and its checksum
                message = unpack(packet)

                sensor_id = message["sensor_id"]
                sequence_number = message["seq"]
                message_type = message["type"]

                if message_type == MSG_DATA:
                    # 2. Decode the sensor reading
                    sensor_type, value, timestamp = unpack_reading(
                        message["payload"]
                    )

                    # 3. Validate the sensor ID, type and value
                    validate_reading(sensor_id, sensor_type, value)

                    packet_key = (sensor_id, sequence_number)
                    is_duplicate = packet_key in received_packets

                    # 4. Acknowledge DATA packets when requested.
                    # ACK duplicates too, so a sender can stop retrying.
                    if message["flags"] & FLAG_ACK_REQ:
                        ack_packet = pack(
                            MSG_ACK,
                            sensor_id,
                            sequence_number,
                        )
                        server.sendto(ack_packet, client_address)
                        if not quiet:
                            print(
                                f"ACK sent for sensor={sensor_id}, "
                                f"seq={sequence_number}"
                            )

                    if is_duplicate:
                        duplicates += 1
                        if not quiet:
                            print(
                                f"DUPLICATE: sensor={sensor_id}, "
                                f"seq={sequence_number}"
                            )
                    else:
                        received_packets.add(packet_key)

                        arrival_time = time.time()

                        sensor_names = {
                            1: "Temperature",
                            2: "Humidity",
                            3: "Motion",
                        }

                        if not quiet:
                            print(
                                f"DATA received | "
                                f"sensor={sensor_id} | "
                                f"type={sensor_names[sensor_type]} | "
                                f"value={value:.2f} | "
                                f"seq={sequence_number} | "
                                f"timestamp={timestamp} | "
                                f"arrival={arrival_time:.3f}"
                            )

                        unique_received += 1

                elif message_type == MSG_STATS_REQ:
                    stats_packet = pack(
                        MSG_STATS_REPLY,
                        sensor_id,
                        sequence_number,
                        payload=pack_stats(unique_received, duplicates),
                    )
                    server.sendto(stats_packet, client_address)
                    if not quiet:
                        print(
                            f"STATS reply sent | "
                            f"unique={unique_received} | "
                            f"duplicates={duplicates}"
                        )

                elif message_type == MSG_ACK:
                    print(
                        f"ACK received from {client_address}: "
                        f"sensor={sensor_id}, seq={sequence_number}"
                    )

                else:
                    print(
                        f"Valid but currently unsupported message type: "
                        f"{message_type}"
                    )

            except (ValueError, KeyError, OSError) as error:
                print(
                    f"REJECTED packet from {client_address}: {error}"
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the LLSP UDP server.")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress per-packet DATA, ACK, and duplicate output",
    )
    args = parser.parse_args()

    try:
        start_server(quiet=args.quiet)
    except KeyboardInterrupt:
        print("\nServer stopped.")
