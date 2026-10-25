
import argparse
import csv
import random
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from protocol import (
    MSG_DATA,
    MSG_ACK,
    FLAG_ACK_REQ,
    pack,
    unpack,
    pack_reading,
)

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 9999
SENSOR_ID = 1
SENSOR_TYPE = 1  # Temperature

MAX_RETRIES = 3

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_FILE = PROJECT_ROOT / "results" / "llsp_benchmark.csv"


def run_experiment(packet_count, loss_rate, sock, start_seq, timeout):
    latencies = []
    delivered = 0
    retransmissions = 0
    packet_sizes = []
    start_time = time.perf_counter()

    for index in range(packet_count):
        seq = (start_seq + index) & 0xFFFF

        value = round(random.uniform(20.0, 35.0), 2)
        timestamp_ms = int(time.time() * 1000) & 0xFFFFFFFF

        payload = pack_reading(
            SENSOR_TYPE, value, timestamp_ms
        )
        packet = pack(
            MSG_DATA,
            SENSOR_ID,
            seq,
            flags=FLAG_ACK_REQ,
            payload=payload,
        )
        packet_sizes.append(len(packet))

        message_start = time.perf_counter()
        acknowledged = False

        for attempt in range(1, MAX_RETRIES + 1):
            if attempt > 1:
                retransmissions += 1

            # Simulate loss of an outgoing transmission.
            if random.random() >= loss_rate:
                sock.sendto(
                    packet, (SERVER_HOST, SERVER_PORT)
                )

            try:
                response, _ = sock.recvfrom(2048)
                ack = unpack(response)

                if (
                    ack["type"] == MSG_ACK
                    and ack["sensor_id"] == SENSOR_ID
                    and ack["seq"] == seq
                ):
                    acknowledged = True
                    break

            except (socket.timeout, ConnectionResetError):
                pass
            except ValueError:
                pass

        if acknowledged:
            delivered += 1
            latencies.append(
                (time.perf_counter() - message_start) * 1000
            )

    elapsed = time.perf_counter() - start_time

    return {
        "loss_rate_pct": round(loss_rate * 100, 1),
        "timeout_ms": round(timeout * 1000, 2),
        "packets": packet_count,
        "delivered": delivered,
        "delivery_rate_pct": round(
            delivered / packet_count * 100, 2
        ),
        "avg_latency_ms": round(
            sum(latencies) / len(latencies), 2
        ) if latencies else 0,
        "retransmissions": retransmissions,
        "avg_packet_size_bytes": round(
            sum(packet_sizes) / len(packet_sizes), 2
        ),
        "elapsed_seconds": round(elapsed, 2),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark the LLSP UDP protocol."
    )
    parser.add_argument(
        "--packets", type=int, default=500,
        help="Number of logical packets per experiment."
    )
    parser.add_argument(
        "--timeout", type=float, default=0.05,
        help="ACK timeout in seconds."
    )
    parser.add_argument(
        "--loss-rates", type=float, nargs="+",
        default=[0.0, 0.05, 0.10, 0.20],
        help="Simulated loss probabilities."
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Replace the existing results file."
    )
    args = parser.parse_args()

    if args.packets <= 0:
        parser.error("--packets must be greater than zero")

    if any(not 0 <= rate <= 1 for rate in args.loss_rates):
        parser.error("Each loss rate must be between 0 and 1")

    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # One socket keeps the benchmark's receive path simple.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(args.timeout)

        # Random starting sequence reduces collisions with earlier runs.
        start_seq = random.randint(0, 65535)
        results = []

        for loss_rate in args.loss_rates:
            print(f"\nRunning experiment: {loss_rate * 100:.0f}% loss")

            result = run_experiment(
                args.packets, loss_rate, sock, start_seq, args.timeout
            )
            start_seq = (
                start_seq + args.packets
            ) & 0xFFFF

            results.append(result)

            print(f"Delivery rate: {result['delivery_rate_pct']}%")
            print(f"Average ACK latency: {result['avg_latency_ms']} ms")
            print(f"Retransmissions: {result['retransmissions']}")
            print(
                "Average packet size: "
                f"{result['avg_packet_size_bytes']} bytes"
            )

    fieldnames = list(results[0].keys())
    write_header = (
        args.overwrite
        or
        not RESULTS_FILE.exists()
        or RESULTS_FILE.stat().st_size == 0
    )

    with RESULTS_FILE.open(
        "w" if args.overwrite else "a",
        newline="", encoding="utf-8"
    ) as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)

        if write_header:
            writer.writeheader()

        writer.writerows(results)

    print(f"\nResults saved to: {RESULTS_FILE}")


if __name__ == "__main__":
    main()
