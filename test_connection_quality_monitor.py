#!/usr/bin/env python3
import importlib.machinery
import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

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
                    SELECT timestamp_utc, check_type, target, ok, latency_ms,
                           jitter_ms, packet_loss_pct, error
                    FROM samples
                    """
                ).fetchall()

        self.assertEqual(
            rows,
            [("2026-06-04T20:00:00+00:00", "ping", "1.1.1.1", 1, 12.345, 0.5, 0.0, None)],
        )


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
