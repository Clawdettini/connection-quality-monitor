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


if __name__ == "__main__":
    unittest.main()
