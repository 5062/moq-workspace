from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

import duckdb

from moq_trace.analysis import monitoring, sql
from moq_trace.analysis.network import capture_statistics
from moq_trace.errors import TraceError
from moq_trace.manifest import MonitoringHost, MonitoringManifest
from moq_trace.plot.monitoring import plot_monitoring


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db = self.enterContext(duckdb.connect(":memory:"))
        self.db.execute(sql.read("monitoring/schema"))
        self.path = self.root / "monitoring.json"
        manifest = MonitoringManifest(
            hosts=(
                MonitoringHost(
                    host="relay",
                    roles={"relay": 10},
                    resources="resources.jsonl",
                    resource_roles=("relay",),
                    offset_ns=0,
                    uncertainty_ns=0,
                ),
                MonitoringHost(
                    host="peer", roles={"subscriber": 10}, offset_ns=100_000_000_000, uncertainty_ns=1_000_000
                ),
            ),
            start_ns=1_000_000_000,
            end_ns=5_000_000_000,
            interval_seconds=1.0,
            subscribers=2,
            goodput=True,
        )
        self.path.write_text(manifest.model_dump_json())
        self.samples = [
            dict(
                schema_version=1,
                role="relay",
                pid=10,
                start_ticks=123,
                monotonic_ns=second * 1_000_000_000,
                clock_ticks=100,
                user_ticks=second * 100,
                system_ticks=second * 50,
                rss_bytes=second * 1024,
            )
            for second in (0, 1, 3, 5, 6)
        ]
        self.deliveries = [
            dict(
                schema_version=1,
                monotonic_ns=(100 + second) * 1_000_000_000,
                pid=10,
                connection=connection,
                bytes=second * 1000 * (connection + 1),
                objects=second * 10 * (connection + 1),
            )
            for second in (0, 1, 3, 5, 6)
            for connection in (0, 1)
        ]

    def _ingest(self):
        (self.root / "resources.jsonl").write_text("".join(json.dumps(row) + "\n" for row in self.samples))
        (self.root / "subscriber.log").write_text(
            "readiness log\n" + "".join("MOQ_BENCH_DELIVERY " + json.dumps(row) + "\n" for row in self.deliveries)
        )
        monitoring.ingest(self.db, self.path, origin_ns=0, start_ns=1_000_000_000, end_ns=5_000_000_000, subscribers=2)

    def test_real_intervals_process_identity_remote_clock_fanout_and_offline_plot(self):
        self._ingest()
        row = self.db.execute(
            "SELECT covered_seconds, user_cpu_seconds, system_cpu_seconds, mean_cpu_cores, "
            "sampled_max_rss_bytes, rss_growth_bytes FROM monitoring.resource_summary"
        ).fetchone()
        self.assertEqual(row, (4.0, 4.0, 2.0, 1.5, 5120, 4096))
        rows = self.db.execute(
            "SELECT connection, covered_seconds, bytes, objects, bytes_per_second "
            "FROM monitoring.delivery_summary ORDER BY connection"
        ).fetchall()
        self.assertEqual(rows, [(0, 4.0, 4000, 40, 1000.0), (1, 4.0, 8000, 80, 2000.0)])
        output = self.root / "monitoring.png"
        plot_monitoring(output, "test", self.db)
        self.assertGreater(output.stat().st_size, 1000)
        monitoring.write_summary(self.path)
        summary = json.loads((self.root / "monitoring-summary.json").read_text())
        self.assertEqual(summary["total_goodput_bytes_per_second"], 3000.0)

    def test_counter_reset_and_pid_reuse_are_rejected(self):
        for field, value in (("user_ticks", 0), ("start_ticks", 999), ("monotonic_ns", 1_000_000_000)):
            with self.subTest(field=field):
                old = self.samples[2][field]
                self.samples[2][field] = value
                with self.assertRaisesRegex(TraceError, "identity|monotonic"):
                    self._ingest()
                self.samples[2][field] = old
                for table in ("window", "hosts", "resources", "deliveries"):
                    self.db.execute(f"DELETE FROM monitoring.{table}")

    def test_unknown_schema_fields_and_missing_connection_are_rejected(self):
        self.samples[0]["unknown"] = 1
        with self.assertRaisesRegex(TraceError, "Extra inputs"):
            self._ingest()
        self.samples[0].pop("unknown")
        for table in ("window", "hosts", "resources", "deliveries"):
            self.db.execute(f"DELETE FROM monitoring.{table}")
        self.deliveries = [row for row in self.deliveries if row["connection"] == 0]
        with self.assertRaisesRegex(TraceError, "missing delivered"):
            self._ingest()

    def test_capture_drops_are_separate_and_missing_interface_is_unknown(self):
        path = self.root / "tcpdump.log"
        path.write_text("10 packets captured\n12 packets received by filter\n2 packets dropped by kernel\n")
        self.assertEqual(
            capture_statistics(path), dict(captured=10, received_by_filter=12, kernel_drops=2, interface_drops=None)
        )
        path.write_text("tcpdump did not finish\n")
        with self.assertRaisesRegex(TraceError, "did not exit cleanly"):
            capture_statistics(path)
