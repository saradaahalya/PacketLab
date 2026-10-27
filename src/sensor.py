
import random
import os
import socket
import sys
import threading
import time
from pathlib import Path

# Allow Python to import protocol.py from the same src folder
sys.path.insert(0, str(Path(__file__).resolve().parent))

from protocol import (
    MSG_DATA,
    MSG_ACK,
    FLAG_ACK_REQ,
    pack,
    pack_reading,
)

from llsp_reply import wait_for_reply

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 9999

# Sensor IDs: 1-2 temperature, 3-4 humidity, 5 motion
SENSORS = {
    1: {"type": 1, "name": "Temperature"},
    2: {"type": 1, "name": "Temperature"},
    3: {"type": 2, "name": "Humidity"},
    4: {"type": 2, "name": "Humidity"},
    5: {"type": 3, "name": "Motion"},
}

SEND_INTERVAL = 2
ACK_TIMEOUT = 1.0
MAX_RETRIES = 3

# Packet-loss rate: 0.0 = 0%, 0.05 = 5%, 0.20 = 20%
LOSS_RATE = float(os.getenv("LLSP_LOSS_RATE", "0.0"))

if not 0.0 <= LOSS_RATE <= 1.0:
    raise ValueError("LLSP_LOSS_RATE must be between 0.0 and 1.0")


def generate_value(sensor_type):
    """Generate a realistic value for the selected sensor."""

    if sensor_type == 1:
        return round(random.uniform(20.0, 35.0), 2)

    if sensor_type == 2:
        return round(random.uniform(30.0, 80.0), 2)

    return float(random.choice([0, 1]))


def run_sensor(sensor_id, sensor_info):
    """Send readings and retry when an ACK is not received."""

    sensor_type = sensor_info["type"]
    sensor_name = sensor_info["name"]
    sequence_number = 0

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(ACK_TIMEOUT)

        print(f"Sensor {sensor_id} ({sensor_name}) started")

        while True:
            value = generate_value(sensor_type)
            timestamp_ms = int(time.time() * 1000)

            payload = pack_reading(
                sensor_type,
                value,
                timestamp_ms,
            )

            packet = pack(
                MSG_DATA,
                sensor_id,
                sequence_number,
                flags=FLAG_ACK_REQ,
                payload=payload,
            )

            acknowledged = False

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    if random.random() < LOSS_RATE:
                        print(
                            f"SIMULATED LOSS: sensor={sensor_id}, "
                            f"seq={sequence_number}, attempt={attempt}"
                        )
                    else:
                        client.sendto(packet, (SERVER_HOST, SERVER_PORT))

                    wait_for_reply(
                        client, (SERVER_HOST, SERVER_PORT), MSG_ACK,
                        sensor_id, sequence_number, 0, ACK_TIMEOUT,
                    )
                    acknowledged = True
                    print(
                        f"Sensor {sensor_id}: "
                        f"value={value:.2f}, "
                        f"seq={sequence_number}, ACK received"
                    )
                    break

                except socket.timeout:
                    print(
                        f"Sensor {sensor_id}: ACK timeout, "
                        f"attempt {attempt}/{MAX_RETRIES}"
                    )

                except (OSError, ValueError) as error:
                    print(f"Sensor {sensor_id}: error: {error}")
                    break

            if not acknowledged:
                print(
                    f"Sensor {sensor_id}: delivery unconfirmed "
                    f"for seq={sequence_number}"
                )

            sequence_number = (sequence_number + 1) & 0xFFFF
            time.sleep(SEND_INTERVAL)


def main():
    threads = []

    for sensor_id, sensor_info in SENSORS.items():
        thread = threading.Thread(
            target=run_sensor,
            args=(sensor_id, sensor_info),
            daemon=True,
        )
        threads.append(thread)
        thread.start()

    print("\nAll five sensors are running concurrently.")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping sensor simulator...")


if __name__ == "__main__":
    main()
