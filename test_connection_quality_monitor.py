#!/usr/bin/env python3
import importlib.machinery
import importlib.util
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).with_name("connection-quality-monitor")
loader = importlib.machinery.SourceFileLoader("connection_quality_monitor", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None
monitor = importlib.util.module_from_spec(spec)
loader.exec_module(monitor)


def sample_row(timestamp: str, check_type: str, target: str, ok: bool, latency_ms=None, packet_loss_pct=None, throughput_mbps=None, error=None):
    return {
        "timestamp_utc": timestamp,
        "local_time": timestamp,
        "check_type": check_type,
        "target": target,
        "ok": ok,
        "latency_ms": latency_ms,
        "jitter_ms": None,
        "packet_loss_pct": packet_loss_pct,
        "status_code": 200 if check_type in ("http", "download") and ok else None,
        "bytes": 100000 if check_type == "download" else None,
        "throughput_mbps": throughput_mbps,
        "error": error,
    }


class SqliteStorageTests(unittest.TestCase):
    def test_sqlite_is_the_default_storage_backend(self):
        args = monitor.parse_args(["--once"])

        self.assertEqual(args.storage, "sqlite")

    def test_appends_rows_to_sqlite_database(self):
        row = {
            "timestamp_utc": "2026-06-04T20:00:00+00:00",
            "local_time": "2026-06-04T22:00:00+02:00",
            "check_type": "ping",
            "target": "1.1.1.1",
            "ok": True,
            "latency_ms": 12.345,
            "jitter_ms": 0.5,
            "packet_loss_pct": 0.0,
            "status_code": None,
            "bytes": None,
            "throughput_mbps": None,
            "error": None,
        }

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "quality.sqlite"
            monitor.append_rows_sqlite(db_path, [row])

            with sqlite3.connect(db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT s.timestamp_utc, t.check_type, t.target, s.ok, s.latency_ms,
                           s.jitter_ms, s.packet_loss_pct, s.error
                    FROM samples s
                    JOIN targets t ON t.id = s.target_id
                    """
                ).fetchall()

        self.assertEqual(
            rows,
            [("2026-06-04T20:00:00+00:00", "ping", "1.1.1.1", 1, 12.345, 0.5, 0.0, None)],
        )

    def test_sqlite_schema_normalizes_repeated_targets(self):
        first = sample_row("2026-06-04T20:00:00+00:00", "ping", "1.1.1.1", True, latency_ms=10.0)
        second = sample_row("2026-06-04T20:01:00+00:00", "ping", "1.1.1.1", False, latency_ms=100.0, error="timeout")

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "quality.sqlite"
            monitor.append_rows_sqlite(db_path, [first, second])

            with sqlite3.connect(db_path) as conn:
                target_rows = conn.execute("SELECT check_type, target FROM targets").fetchall()
                sample_target_ids = conn.execute("SELECT DISTINCT target_id FROM samples").fetchall()
                sample_columns = [row[1] for row in conn.execute("PRAGMA table_info(samples)").fetchall()]

        self.assertEqual(target_rows, [("ping", "1.1.1.1")])
        self.assertEqual(len(sample_target_ids), 1)
        self.assertIn("target_id", sample_columns)
        self.assertNotIn("target", sample_columns)


class DownloadCadenceTests(unittest.TestCase):
    def test_download_every_defaults_to_every_sample_for_backward_compatibility(self):
        args = monitor.parse_args(["--once"])

        self.assertEqual(args.download_every, 0)
        self.assertTrue(monitor.should_run_download(args, sample_number=0, elapsed_s=0.0))

    def test_download_every_runs_on_configured_wall_clock_cadence(self):
        args = monitor.parse_args(["--interval", "60", "--download-every", "900"])

        self.assertTrue(monitor.should_run_download(args, sample_number=0, elapsed_s=0.0))
        self.assertFalse(monitor.should_run_download(args, sample_number=1, elapsed_s=60.0))
        self.assertFalse(monitor.should_run_download(args, sample_number=14, elapsed_s=840.0))
        self.assertTrue(monitor.should_run_download(args, sample_number=15, elapsed_s=900.0))

    def test_sample_can_skip_download_without_disabling_download_configuration(self):
        args = monitor.parse_args(["--ping-targets", "", "--dns-hosts", "", "--http-urls", "", "--download-url", "https://example.test/file"])

        with mock.patch.object(monitor, "run_download") as run_download:
            rows = monitor.sample(args, include_download=False)

        self.assertEqual(rows, [])
        run_download.assert_not_called()


class StorageEstimateTests(unittest.TestCase):
    def test_storage_estimate_reflects_interval_targets_and_download_cadence(self):
        args = monitor.parse_args([
            "--interval", "300",
            "--ping-targets", "1.1.1.1,8.8.8.8",
            "--dns-hosts", "cloudflare.com",
            "--http-urls", "https://www.google.com/generate_204",
            "--download-every", "900",
            "--estimate-storage",
        ])

        estimate = monitor.estimate_storage(args)

        self.assertEqual(estimate["checks_per_sample"], 4)
        self.assertEqual(estimate["download_checks_per_year"], 35040)
        self.assertEqual(estimate["rows_per_year"], 455520)
        self.assertGreater(estimate["sqlite_estimated_mib_per_year"], 0)
        self.assertAlmostEqual(estimate["download_traffic_gib_per_year"], 32.64, places=1)

    def test_estimate_storage_cli_flag_exits_before_sampling(self):
        with mock.patch.object(monitor, "sample") as sample:
            with redirect_stdout(io.StringIO()):
                rc = monitor.main(["--estimate-storage", "--download-url", ""])

        self.assertEqual(rc, 0)
        sample.assert_not_called()


class ReportingTests(unittest.TestCase):
    def test_build_report_summarizes_failures_latency_loss_and_throughput(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "quality.sqlite"
            monitor.append_rows_sqlite(
                db_path,
                [
                    sample_row("2026-06-04T20:00:00+00:00", "ping", "1.1.1.1", True, latency_ms=10.0, packet_loss_pct=0.0),
                    sample_row("2026-06-04T20:01:00+00:00", "ping", "1.1.1.1", False, latency_ms=100.0, packet_loss_pct=100.0, error="timeout"),
                    sample_row("2026-06-04T20:02:00+00:00", "download", "speed", True, latency_ms=50.0, throughput_mbps=25.0),
                ],
            )

            report = monitor.build_report(db_path)

        self.assertEqual(report["total_samples"], 3)
        self.assertEqual(report["failure_count"], 1)
        self.assertAlmostEqual(report["failure_pct"], 33.333, places=3)
        self.assertEqual(report["by_check_type"]["ping"]["samples"], 2)
        self.assertEqual(report["by_check_type"]["ping"]["failures"], 1)
        self.assertEqual(report["by_check_type"]["ping"]["latency_avg_ms"], 55.0)
        self.assertEqual(report["by_check_type"]["ping"]["packet_loss_avg_pct"], 50.0)
        self.assertEqual(report["by_check_type"]["download"]["throughput_avg_mbps"], 25.0)

    def test_render_html_report_contains_embedded_visualization_data(self):
        report = {
            "total_samples": 1,
            "failure_count": 0,
            "failure_pct": 0.0,
            "time_range": {"start": "2026-06-04T20:00:00+00:00", "end": "2026-06-04T20:00:00+00:00"},
            "by_check_type": {"ping": {"samples": 1, "failures": 0, "latency_avg_ms": 10.0}},
            "recent_failures": [{"timestamp_utc": "2026-06-04T20:00:00+00:00", "check_type": "http", "target": "</script><script>alert(1)</script>", "error": "<img src=x onerror=alert(1)>"}],
            "series": [{"timestamp_utc": "2026-06-04T20:00:00+00:00", "check_type": "ping", "target": "1.1.1.1", "ok": 1, "latency_ms": 10.0, "packet_loss_pct": 0.0, "throughput_mbps": None}],
        }

        html = monitor.render_html_report(report)

        self.assertIn("Connection Quality Report", html)
        self.assertIn("<canvas", html)
        self.assertIn("window.REPORT_DATA", html)
        self.assertIn("1.1.1.1", html)
        self.assertNotIn("</script><script>alert(1)</script>", html)
        self.assertIn("textContent = value", html)


if __name__ == "__main__":
    unittest.main()
