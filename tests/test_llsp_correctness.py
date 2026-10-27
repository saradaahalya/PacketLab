"""Local, bounded regression tests for benchmark accounting and LLSP replies."""
import contextlib
import csv
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import benchmark as benchmark
import llsp_reply
import protocol as protocol


@pytest.fixture
def udp_server(monkeypatch):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setattr(benchmark, "SERVER_PORT", port)
    process = subprocess.Popen(
        [sys.executable, "-B", "-u", "-c",
         "import sys; sys.path.insert(0,'src'); import server; "
         f"server.PORT={port}; server.start_server(quiet=True)"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.bind(("127.0.0.1", 0))
            client.settimeout(.03)
            deadline = time.monotonic() + 3
            while benchmark.get_stats(client) is None:
                assert process.poll() is None, "server exited during startup"
                assert time.monotonic() < deadline, "server did not start"
            yield client, ("127.0.0.1", port)
    finally:
        process.terminate()
        process.wait(timeout=5)


def run_main(path, *args):
    output = io.StringIO()
    with patch.object(sys, "argv", ["benchmark", "--output", str(path), *args]), contextlib.redirect_stdout(output):
        status = benchmark.main()
    return status, output.getvalue()


def data(seq, flags=0, value=25):
    return protocol.pack(protocol.MSG_DATA, 1, seq, flags,
                         protocol.pack_reading(1, value, 123))


def test_duplicates_never_increase_unique_delivery(udp_server):
    client, address = udp_server
    for _ in range(3):
        client.sendto(data(0), address)
    after = benchmark.get_stats(client)
    assert after == (1, 2)
    result = {"packets": 2, "transmitted_readings": 2, "ack_confirmed": ""}
    benchmark.apply_server_stats(result, (0, 0), after)
    assert result["delivered"] == 1
    assert result["delivery_rate_pct"] == 50
    assert result["server_duplicates"] == 2


@pytest.mark.parametrize("after", [(3, 0), (0, 0), (1, -1)])
def test_inconsistent_counters_are_rejected(after):
    result = {"packets": 2, "transmitted_readings": 2, "ack_confirmed": 1}
    with pytest.raises(ValueError, match="Inconsistent"):
        benchmark.apply_server_stats(result, (0, 0), after)


def test_server_receipt_and_ack_confirmation_are_separate(udp_server, monkeypatch):
    client, address = udp_server
    real_wait = benchmark.wait_for_reply
    def lose_acks(sock, address, kind, *args):
        if kind == protocol.MSG_ACK:
            raise socket.timeout()
        return real_wait(sock, address, kind, *args)
    monkeypatch.setattr(benchmark, "wait_for_reply", lose_acks)
    result = benchmark.run_experiment(1, 0, client, 0, .01, "reliable", 0)
    after = benchmark.get_stats(client)
    benchmark.apply_server_stats(result, (0, 0), after)
    assert result["ack_confirmed"] == 0
    assert result["delivered"] == 1
    assert result["delivery_rate_pct"] == 100
    assert result["retransmissions"] == 2
    assert result["server_duplicates"] == 2
    assert result["avg_latency_ms"] == ""


def test_both_modes_loss_and_repeated_invocation(udp_server, tmp_path):
    path = tmp_path / "llsp.csv"
    status, _ = run_main(path, "--packets", "3", "--mode", "both",
                         "--loss-rates", "0", "1", "--timeout", ".01", "--interval", "0")
    assert status is None
    rows = list(csv.DictReader(path.open()))
    assert [float(r["delivery_rate_pct"]) for r in rows] == [100, 100, 0, 0]
    assert [r["ack_confirmed"] for r in rows] == ["3", "", "0", ""]
    assert [int(r["retransmissions"]) for r in rows] == [0, 0, 6, 0]
    before = path.read_bytes()
    status, output = run_main(path, "--packets", "1", "--loss-rates", "0")
    assert status == 1
    assert "not fresh" in output
    assert path.read_bytes() == before


def test_restart_reuses_key_and_fresh_receiver_workaround(udp_server):
    client, address = udp_server
    client.sendto(data(0, protocol.FLAG_ACK_REQ), address)
    llsp_reply.wait_for_reply(client, address, protocol.MSG_ACK, 1, 0, 0, .1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as restarted:
        restarted.settimeout(.1)
        restarted.sendto(data(0, protocol.FLAG_ACK_REQ, value=30), address)
        llsp_reply.wait_for_reply(restarted, address, protocol.MSG_ACK, 1, 0, 0, .1)
    assert benchmark.get_stats(client) == (1, 1)


@pytest.mark.parametrize("option,value", [("--timeout", "0"), ("--timeout", "-1"),
    ("--timeout", "nan"), ("--interval", "inf"), ("--packets", "9000")])
def test_invalid_configuration_before_network(tmp_path, option, value):
    with pytest.raises(SystemExit) as error:
        run_main(tmp_path / "out.csv", option, value)
    assert error.value.code == 2
    assert not (tmp_path / "out.csv").exists()


def test_reply_filters_source_fields_crc_payload_and_restores_timeout():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client, \
         socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server, \
         socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as foreign:
        for sock in (client, server, foreign): sock.bind(("127.0.0.1", 0))
        client.settimeout(.5)
        target = client.getsockname()
        foreign.sendto(protocol.pack(protocol.MSG_ACK, 1, 7), target)
        for packet in [b"bad", protocol.pack(protocol.MSG_ACK, 1, 8),
                       protocol.pack(protocol.MSG_ACK, 2, 7),
                       protocol.pack(protocol.MSG_DATA, 1, 7),
                       protocol.pack(protocol.MSG_ACK, 1, 7, payload=b"x"),
                       protocol.pack(protocol.MSG_ACK, 1, 7, flags=1),
                       protocol.pack(protocol.MSG_ACK, 1, 7)]:
            server.sendto(packet, target)
        reply = llsp_reply.wait_for_reply(client, server.getsockname(), protocol.MSG_ACK, 1, 7, 0, .2)
        assert reply["seq"] == 7
        assert client.gettimeout() == .5


def test_unrelated_traffic_does_not_extend_deadline():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client, \
         socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        client.bind(("127.0.0.1", 0)); server.bind(("127.0.0.1", 0))
        client.settimeout(.5)
        stop = threading.Event()
        def spam():
            while not stop.wait(.001): server.sendto(b"bad", client.getsockname())
        thread = threading.Thread(target=spam)
        thread.start()
        start = time.monotonic()
        try:
            with pytest.raises(socket.timeout):
                llsp_reply.wait_for_reply(client, server.getsockname(), 2, 1, 0, 0, .03)
        finally:
            stop.set(); thread.join(timeout=1)
        assert not thread.is_alive()
        assert time.monotonic() - start < .5
        assert client.gettimeout() == .5


def test_stale_ack_does_not_trigger_retry(udp_server, monkeypatch):
    client, address = udp_server
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as foreign:
        foreign.sendto(protocol.pack(protocol.MSG_ACK, 1, 99), client.getsockname())
    result = benchmark.run_experiment(1, 0, client, 0, .1, "reliable", 0)
    assert result["ack_confirmed"] == 1
    assert result["retransmissions"] == 0


def test_stats_rejects_stale_response(monkeypatch):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client, \
         socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        client.bind(("127.0.0.1", 0)); server.bind(("127.0.0.1", 0))
        client.settimeout(.2); server.settimeout(1)
        monkeypatch.setattr(benchmark, "SERVER_PORT", server.getsockname()[1])
        errors = []
        def respond():
            try:
                for _ in range(2):
                    raw, target = server.recvfrom(2048)
                    seq = protocol.unpack(raw)["seq"]
                    server.sendto(protocol.pack(protocol.MSG_STATS_REPLY, 1, (seq - 1) & 65535,
                                  payload=protocol.pack_stats(999, 999)), target)
                    server.sendto(protocol.pack(protocol.MSG_STATS_REPLY, 1, seq,
                                  payload=protocol.pack_stats(0, 0)), target)
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=respond)
        thread.start()
        try:
            assert benchmark.get_stats(client) == (0, 0)
            assert benchmark.get_stats(client) == (0, 0)
        finally:
            thread.join(timeout=2)
        assert not thread.is_alive()
        assert not errors


def test_five_sensor_simulator(udp_server):
    _, address = udp_server
    environment = dict(os.environ, LLSP_LOSS_RATE="0")
    process = subprocess.Popen([sys.executable, "-B", "-u", "-c",
        "import sys;sys.path.insert(0,'src');import sensor;"
        f"sensor.SERVER_PORT={address[1]};sensor.main()"], cwd=ROOT,
        env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        try: process.communicate(timeout=.8)
        except subprocess.TimeoutExpired: pass
    finally:
        process.terminate()
        output = process.communicate(timeout=5)[0]
    for sensor_id in range(1, 6):
        assert any(f"Sensor {sensor_id}:" in line and "ACK received" in line
                   for line in output.splitlines()), output

def test_historical_results_and_plotting(tmp_path):
    environment = dict(os.environ, MPLCONFIGDIR=str(tmp_path / "mpl"))
    completed = subprocess.run([sys.executable, "-B", "src/plot.py",
        "--output-dir", str(tmp_path / "graphs")], cwd=ROOT, env=environment,
        capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stderr
    assert "latency=N/A" in completed.stdout
    assert "delivery=99.12%" in completed.stdout
    assert "delivery=79.72%" in completed.stdout
    graphs = list((tmp_path / "graphs").glob("*.png"))
    assert len(graphs) == 4
    assert all(f.stat().st_size > 1000 for f in graphs)
    rows = list(csv.DictReader((ROOT / "results/llsp_benchmark.csv").open()))
    assert len(rows) == 40
    assert all(int(r["server_duplicates"]) == 0 and r["server_unique"] == r["delivered"] for r in rows)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell script")
def test_experiment_script_preserves_existing_output(tmp_path):
    marker = tmp_path / "keep.txt"
    marker.write_text("historical output")
    completed = subprocess.run(["powershell", "-NoProfile", "-File",
        str(ROOT / "scripts/run_final_experiments.ps1"), "-Runs", "1",
        "-Packets", "1", "-OutputDirectory", str(tmp_path)],
        cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert completed.returncode != 0
    assert "already exists" in completed.stderr
    assert marker.read_text() == "historical output"
    assert list(tmp_path.iterdir()) == [marker]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell script")
def test_experiment_script_rejects_busy_port(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as owner:
        try: owner.bind(("127.0.0.1", 9999))
        except OSError: pytest.skip("UDP 9999 already occupied")
        completed = subprocess.run(["powershell", "-NoProfile", "-File",
            str(ROOT / "scripts/run_final_experiments.ps1"), "-Runs", "1",
            "-Packets", "1", "-OutputDirectory", str(tmp_path / "new")],
            cwd=ROOT, capture_output=True, text=True, timeout=10)
        assert completed.returncode != 0
        assert "9999 is unavailable" in completed.stderr
        assert owner.getsockname()[1] == 9999
        assert not (tmp_path / "new/llsp_benchmark.csv").exists()
