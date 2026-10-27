import argparse
import csv
import statistics
from pathlib import Path

import matplotlib

# This command exports PNGs and does not need a desktop/Tk installation.
matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LLSP = PROJECT_ROOT / "results" / "llsp_benchmark.csv"
DEFAULT_TCP = PROJECT_ROOT / "results" / "tcp_baseline.csv"
GRAPH_DIR = PROJECT_ROOT / "results" / "graphs"

LLSP_NUMERIC_COLUMNS = (
    "loss_rate_pct",
    "timeout_ms",
    "packets",
    "delivered",
    "delivery_rate_pct",
    "avg_latency_ms",
    "retransmissions",
    "avg_packet_size_bytes",
    "elapsed_seconds",
    "server_unique",
    "server_duplicates",
)

TCP_NUMERIC_COLUMNS = (
    "count",
    "mean_rtt_ms",
    "median_rtt_ms",
    "mean_request_json_bytes",
    "reply_json_bytes_per_message",
    "elapsed_seconds",
    "acknowledged_messages_per_second",
)


def average(values):
    """Return the average of a non-empty sequence."""
    return statistics.mean(values)


def standard_deviation(values):
    """Return sample standard deviation, or zero for one observation."""
    return statistics.stdev(values) if len(values) > 1 else 0.0


def read_llsp(path):
    """Read and aggregate LLSP results by loss rate and mode."""
    groups = {}
    with path.open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            key = (float(row["loss_rate_pct"]), row["mode"])
            groups.setdefault(key, {column: [] for column in LLSP_NUMERIC_COLUMNS})
            for column in LLSP_NUMERIC_COLUMNS:
                value = row.get(column, "")
                if value != "":
                    groups[key][column].append(float(value))

    aggregated = []
    for (loss_rate, mode), columns in sorted(groups.items()):
        means = {
            column: average(values)
            for column, values in columns.items()
            if values
        }
        deviations = {
            column: standard_deviation(values)
            for column, values in columns.items()
            if values
        }
        aggregated.append({
            "loss_rate_pct": loss_rate,
            "mode": mode,
            "means": means,
            "stddevs": deviations,
            "runs": max((len(values) for values in columns.values()), default=0),
        })
    return aggregated


def read_tcp(path):
    """Read and average TCP baseline results."""
    columns = {column: [] for column in TCP_NUMERIC_COLUMNS}
    with path.open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            for column in TCP_NUMERIC_COLUMNS:
                value = row.get(column, "")
                if value != "":
                    columns[column].append(float(value))

    return {
        "means": {
            column: average(values)
            for column, values in columns.items()
            if values
        },
        "stddevs": {
            column: standard_deviation(values)
            for column, values in columns.items()
            if values
        },
        "rows": max((len(values) for values in columns.values()), default=0),
    }


def rows_for_mode(rows, mode):
    """Select rows for one LLSP mode."""
    return [row for row in rows if row["mode"] == mode]


def add_footnote(figure, text):
    """Add a note below a chart."""
    figure.subplots_adjust(bottom=0.2)
    figure.text(0.5, 0.03, text, ha="center", fontsize=8)


def save_delivery_chart(rows):
    """Write delivery rate versus loss rate."""
    figure, axis = plt.subplots()
    for mode, color in (("reliable", "tab:blue"), ("unreliable", "tab:orange")):
        selected = rows_for_mode(rows, mode)
        if not selected:
            continue
        axis.errorbar(
            [row["loss_rate_pct"] for row in selected],
            [row["means"]["delivery_rate_pct"] for row in selected],
            yerr=[row["stddevs"]["delivery_rate_pct"] for row in selected],
            marker="o",
            capsize=3,
            label=mode,
            color=color,
        )
    axis.set_title("LLSP Delivery Rate vs. Loss Rate")
    axis.set_xlabel("Simulated loss rate (%)")
    axis.set_ylabel("Delivery rate (%)")
    axis.set_ylim(0, 105)
    axis.grid(True)
    axis.legend()
    add_footnote(figure, "Loss is simulated on the sender side only (UDP DATA packets)")
    figure.savefig(GRAPH_DIR / "delivery_vs_loss.png", dpi=150)
    plt.close(figure)


def save_latency_chart(rows, tcp):
    """Write reliable LLSP latency versus loss rate."""
    figure, axis = plt.subplots()
    selected = rows_for_mode(rows, "reliable")
    if selected:
        axis.errorbar(
            [row["loss_rate_pct"] for row in selected],
            [row["means"]["avg_latency_ms"] for row in selected],
            yerr=[row["stddevs"]["avg_latency_ms"] for row in selected],
            marker="o",
            capsize=3,
            label="LLSP reliable",
        )
    if tcp is not None and "mean_rtt_ms" in tcp["means"]:
        axis.axhline(
            tcp["means"]["mean_rtt_ms"],
            color="tab:red",
            linestyle="--",
            label="TCP+JSON, 0% loss, no impairment",
        )
    axis.set_title("Latency vs. Loss Rate")
    axis.set_xlabel("Simulated loss rate (%)")
    axis.set_ylabel("Average latency / RTT (ms)")
    axis.grid(True)
    axis.legend()
    add_footnote(figure, "Loss is simulated on the sender side only (UDP DATA packets)")
    figure.savefig(GRAPH_DIR / "latency_vs_loss.png", dpi=150)
    plt.close(figure)


def save_retransmission_chart(rows):
    """Write reliable LLSP retransmissions versus loss rate."""
    figure, axis = plt.subplots()
    selected = rows_for_mode(rows, "reliable")
    losses = [row["loss_rate_pct"] for row in selected]
    means = [row["means"]["retransmissions"] for row in selected]
    deviations = [row["stddevs"]["retransmissions"] for row in selected]
    axis.bar(losses, means, yerr=deviations, capsize=3, width=3.0, label="LLSP reliable")
    axis.set_title("Reliable LLSP Retransmissions vs. Loss Rate")
    axis.set_xlabel("Simulated loss rate (%)")
    axis.set_ylabel("Average retransmissions per run")
    axis.grid(True, axis="y")
    axis.legend()
    add_footnote(figure, "Loss is simulated on the sender side only (UDP DATA packets)")
    figure.savefig(GRAPH_DIR / "retransmissions_vs_loss.png", dpi=150)
    plt.close(figure)


def save_packet_size_chart(rows, tcp):
    """Write LLSP and TCP+JSON application-layer packet sizes."""
    figure, axis = plt.subplots()
    labels = []
    values = []
    deviations = []

    reliable = rows_for_mode(rows, "reliable")
    if reliable:
        labels.append("LLSP DATA")
        values.append(average([row["means"]["avg_packet_size_bytes"] for row in reliable]))
        deviations.append(standard_deviation(
            [row["means"]["avg_packet_size_bytes"] for row in reliable]
        ))

    if tcp is not None and "mean_request_json_bytes" in tcp["means"]:
        labels.append("TCP+JSON request")
        values.append(tcp["means"]["mean_request_json_bytes"] + 1)
        deviations.append(tcp["stddevs"].get("mean_request_json_bytes", 0.0))

    positions = list(range(len(labels)))
    bars = axis.bar(positions, values, yerr=deviations, capsize=3, label="Application-layer bytes")
    for bar, value in zip(bars, values):
        axis.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            f"{value:g} bytes",
            ha="center",
            va="bottom",
        )
    axis.set_title("Application-Layer Packet Size")
    axis.set_xlabel("Protocol")
    axis.set_ylabel("Bytes")
    axis.set_xticks(positions, labels)
    axis.grid(True, axis="y")
    axis.legend()
    add_footnote(figure, "Application-layer bytes only; excludes TCP/UDP/IP headers")
    figure.savefig(GRAPH_DIR / "packet_size.png", dpi=150)
    plt.close(figure)


def print_summary(rows, tcp):
    """Print averaged LLSP and TCP values."""
    print("\nLLSP averaged results:")
    for row in rows:
        means = row["means"]
        latency = (f"{means['avg_latency_ms']:.2f} ms"
                   if "avg_latency_ms" in means else "N/A")
        print(
            f"  {row['mode']:10} loss={row['loss_rate_pct']:5.1f}% "
            f"runs={row['runs']} delivery={means.get('delivery_rate_pct', 0):.2f}% "
            f"latency={latency} "
            f"retransmissions={means.get('retransmissions', 0):.2f} "
            f"server_unique={means.get('server_unique', 0):.2f} "
            f"server_duplicates={means.get('server_duplicates', 0):.2f}"
        )
    if tcp is not None:
        print("\nTCP baseline averages:")
        for column, value in tcp["means"].items():
            print(f"  {column}: {value:.2f}")


def main():
    global GRAPH_DIR
    parser = argparse.ArgumentParser(description="Plot LLSP benchmark results.")
    parser.add_argument("--llsp", type=Path, default=DEFAULT_LLSP)
    parser.add_argument("--tcp", type=Path, default=DEFAULT_TCP)
    parser.add_argument("--output-dir", type=Path, default=GRAPH_DIR)
    args = parser.parse_args()
    GRAPH_DIR = args.output_dir

    if not args.llsp.exists():
        parser.error(f"LLSP results file not found: {args.llsp}")

    rows = read_llsp(args.llsp)
    if not rows:
        parser.error(f"LLSP results file is empty: {args.llsp}")

    tcp = None
    if args.tcp.exists():
        tcp = read_tcp(args.tcp)
    else:
        print(f"Note: TCP baseline file not found; skipping TCP plots: {args.tcp}")

    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    save_delivery_chart(rows)
    save_latency_chart(rows, tcp)
    save_retransmission_chart(rows)
    save_packet_size_chart(rows, tcp)
    print_summary(rows, tcp)
    print(f"\nGraphs saved to: {GRAPH_DIR}")


if __name__ == "__main__":
    main()
