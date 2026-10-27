
import argparse
import csv
import itertools
import math
import random
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llsp_reply import wait_for_reply

from protocol import (
    MSG_DATA,
    MSG_ACK,
    MSG_STATS_REQ,
    MSG_STATS_REPLY,
    FLAG_ACK_REQ,
    pack,
    pack_reading,
    unpack_stats,
)

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 9999
SENSOR_ID = 1
SENSOR_TYPE = 1  # Temperature

MAX_RETRIES = 3

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_FILE = PROJECT_ROOT / "results" / "llsp_benchmark.csv"
STATS_SEQUENCES = itertools.count()


def get_stats(sock):
    """Request the server's received-packet statistics."""
    seq = next(STATS_SEQUENCES) & 0xFFFF
    request = pack(MSG_STATS_REQ, SENSOR_ID, seq)
    for _ in range(3):
        try:
            sock.sendto(request, (SERVER_HOST, SERVER_PORT))
            message = wait_for_reply(
                sock, (SERVER_HOST, SERVER_PORT), MSG_STATS_REPLY,
                SENSOR_ID, seq, 8, sock.gettimeout(),
            )
            return unpack_stats(message["payload"])
        except (socket.timeout, ConnectionResetError):
            pass

    return None


def run_experiment(
    packet_count, loss_rate, sock, start_seq, timeout, mode, interval
):
    latencies = []
    delivered = 0
    retransmissions = 0
    packet_sizes = []
    transmitted_readings = 0
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
            flags=FLAG_ACK_REQ if mode == "reliable" else 0,
            payload=payload,
        )
        packet_sizes.append(len(packet))

        if mode == "unreliable":
            if random.random() >= loss_rate:
                sock.sendto(packet, (SERVER_HOST, SERVER_PORT))
                transmitted_readings += 1
            time.sleep(interval)
            continue

        message_start = time.perf_counter()
        acknowledged = False
        transmitted = False

        for attempt in range(1, MAX_RETRIES + 1):
            if attempt > 1:
                retransmissions += 1

            # Simulate loss of an outgoing transmission.
            if random.random() >= loss_rate:
                sock.sendto(
                    packet, (SERVER_HOST, SERVER_PORT)
                )
                transmitted = True

            try:
                wait_for_reply(sock, (SERVER_HOST, SERVER_PORT), MSG_ACK,
                               SENSOR_ID, seq, 0, timeout)
                acknowledged = True
                break

            except (socket.timeout, ConnectionResetError):
                pass
            except ValueError:
                pass

        transmitted_readings += int(transmitted)
        if acknowledged:
            delivered += 1
            latencies.append(
                (time.perf_counter() - message_start) * 1000
            )

    elapsed = time.perf_counter() - start_time

    return {
        "loss_rate_pct": round(loss_rate * 100, 1),
        "mode": mode,
        "timeout_ms": round(timeout * 1000, 2),
        "packets": packet_count,
        "delivered": delivered,
        "delivery_rate_pct": round(
            delivered / packet_count * 100, 2
        ),
        "avg_latency_ms": round(
            sum(latencies) / len(latencies), 2
        ) if latencies else "",
        "retransmissions": retransmissions,
        "avg_packet_size_bytes": round(
            sum(packet_sizes) / len(packet_sizes), 2
        ),
        "elapsed_seconds": round(elapsed, 2),
        "ack_confirmed": delivered if mode == "reliable" else "",
        "ack_confirmation_rate_pct": (
            round(delivered / packet_count * 100, 2)
            if mode == "reliable" else ""
        ),
        "transmitted_readings": transmitted_readings,
    }


def apply_server_stats(result, before, after):
    """Count new keys only; reject detectable contamination or counter reset."""
    unique = after[0] - before[0]
    duplicates = after[1] - before[1]
    if (not 0 <= unique <= min(result["packets"], result["transmitted_readings"])
            or duplicates < 0
            or (result["ack_confirmed"] != ""
                and unique < result["ack_confirmed"])):
        raise ValueError(
            "Inconsistent server counters: possible unrelated traffic, server "
            "restart or reused sequences. Restart a dedicated server and rerun."
        )
    result["server_unique"] = unique
    result["server_duplicates"] = duplicates
    result["delivered"] = unique
    result["delivery_rate_pct"] = round(unique / result["packets"] * 100, 2)


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
        "--mode", choices=("reliable", "unreliable", "both"),
        default="both",
        help="Benchmark mode to run."
    )
    parser.add_argument(
        "--interval", type=float, default=0.002,
        help="Delay between unreliable-mode sends in seconds."
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
    parser.add_argument("--output", type=Path, default=RESULTS_FILE,
                        help="CSV path; use a new file for each run set")
    args = parser.parse_args()

    if args.packets <= 0:
        parser.error("--packets must be greater than zero")

    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and greater than zero")

    if any(not 0 <= rate <= 1 for rate in args.loss_rates):
        parser.error("Each loss rate must be between 0 and 1")

    if not math.isfinite(args.interval) or args.interval < 0:
        parser.error("--interval must be finite and not negative")
    scenario_count = len(args.loss_rates) * (2 if args.mode == "both" else 1)
    if args.packets * scenario_count > 65536:
        parser.error("Cannot reuse 16-bit sequences: reduce --packets or scenarios")

    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)

    # One socket keeps the benchmark's receive path simple.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(args.timeout)

        # Fresh receiver and disjoint ranges prevent sequence reuse in this run.
        start_seq = 0
        expected_stats = (0, 0)
        print("Requires a fresh, dedicated UDP server with no other senders. "
              "Global STATS cannot identify all unrelated traffic.")
        results = []

        modes = (
            ("reliable", "unreliable")
            if args.mode == "both" else (args.mode,)
        )

        for loss_rate in args.loss_rates:
            for mode in modes:
                print(
                    f"\nRunning experiment: {mode}, "
                    f"{loss_rate * 100:.0f}% loss"
                )

                stats_before = get_stats(sock)
                if stats_before is None:
                    print(
                        "Server is not responding to stats requests. "
                        "Exiting."
                    )
                    return 1

                if stats_before != expected_stats:
                    print("Server is not fresh or counters changed between "
                          "scenarios. Stop other senders and restart the server.")
                    return 1

                result = run_experiment(
                    args.packets,
                    loss_rate,
                    sock,
                    start_seq,
                    args.timeout,
                    mode,
                    args.interval,
                )
                start_seq = (
                    start_seq + args.packets
                ) & 0xFFFF

                time.sleep(0.1)
                stats_after = get_stats(sock)
                if stats_after is None:
                    print(
                        "Server is not responding to stats requests. "
                        "Exiting."
                    )
                    return 1

                try:
                    apply_server_stats(result, stats_before, stats_after)
                except ValueError as error:
                    print(error)
                    return 1
                expected_stats = stats_after

                results.append(result)

                print(f"Delivery rate: {result['delivery_rate_pct']}%")
                if mode == "reliable":
                    print(f"ACK confirmation rate: "
                          f"{result['ack_confirmation_rate_pct']}%")
                latency = result["avg_latency_ms"]
                print("Average ACK latency: " +
                      (f"{latency} ms" if latency != "" else "N/A"))
                print(f"Retransmissions: {result['retransmissions']}")
                print(
                    "Average packet size: "
                    f"{result['avg_packet_size_bytes']} bytes"
                )

    fieldnames = list(results[0].keys())
    write_header = (
        args.overwrite
        or
        not output.exists()
        or output.stat().st_size == 0
    )

    if not write_header:
        with output.open(
            "r", newline="", encoding="utf-8"
        ) as file:
            existing_fieldnames = next(csv.reader(file), [])

        if existing_fieldnames != fieldnames:
            print(
                "Results CSV has a different header. "
                "Choose a new --output file to preserve historical results."
            )
            return 1

    with output.open(
        "w" if args.overwrite else "a",
        newline="", encoding="utf-8"
    ) as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)

        if write_header:
            writer.writeheader()

        writer.writerows(results)

    print(f"\nResults saved to: {output}")


if __name__ == "__main__":
    sys.exit(main())
