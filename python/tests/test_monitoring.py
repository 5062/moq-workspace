from __future__ import annotations

import json
import os
import pathlib
import tempfile
import threading
import unittest

import duckdb

from moq_trace.analysis import monitoring, sql
from moq_trace.analysis.network import capture_statistics
from moq_trace.capture import resources
from moq_trace.errors import TraceError
from moq_trace.manifest import (
    MONITORING_SUMMARY,
    MonitoringHost,
    MonitoringManifest,
    MonitoringSummary,
    ResourceSample,
)
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
                schema_version=2,
                role="relay",
                pid=10,
                start_ticks=123,
                monotonic_ns=second * 1_000_000_000,
                clock_ticks=100,
                user_ticks=second * 100,
                system_ticks=second * 50,
                rss_bytes=second * 1024,
                threads=[
                    dict(tid=10, name="relay", start_ticks=123, user_ticks=second * 60, system_ticks=0),
                    dict(tid=11, name="worker", start_ticks=124, user_ticks=second * 40, system_ticks=second * 50),
                    # Starts after the first samples, so it has fewer intervals.
                    *(
                        [dict(tid=12, name="late", start_ticks=300, user_ticks=second * 10, system_ticks=0)]
                        if second >= 3
                        else []
                    ),
                ],
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
        row = self.db.execute(
            "SELECT sampled_threads, busiest_thread_mean_cpu_cores, busiest_thread_max_cpu_cores "
            "FROM monitoring.resource_summary"
        ).fetchone()
        self.assertEqual(row, (3, 0.9, 0.9))
        rows = self.db.execute(
            "SELECT tid, name, covered_seconds, mean_cpu_cores FROM monitoring.thread_summary ORDER BY tid"
        ).fetchall()
        self.assertEqual(rows, [(10, "relay", 4.0, 0.6), (11, "worker", 4.0, 0.9), (12, "late", 2.0, 0.1)])
        rows = self.db.execute(
            "SELECT connection, covered_seconds, bytes, objects, bytes_per_second "
            "FROM monitoring.delivery_summary ORDER BY connection"
        ).fetchall()
        self.assertEqual(rows, [(0, 4.0, 4000, 40, 1000.0), (1, 4.0, 8000, 80, 2000.0)])
        output = self.root / "monitoring.png"
        plot_monitoring(output, "test", self.db)
        self.assertGreater(output.stat().st_size, 1000)
        monitoring.write_summary(self.path)
        summary = MonitoringSummary.model_validate_json((self.root / MONITORING_SUMMARY).read_text())
        self.assertEqual(summary.total_goodput_bytes_per_second, 3000.0)
        self.assertEqual([row.tid for row in summary.threads], [10, 11, 12])

    def test_counter_reset_and_pid_reuse_are_rejected(self):
        for field, value in (("user_ticks", 0), ("start_ticks", 999), ("monotonic_ns", 1_000_000_000)):
            with self.subTest(field=field):
                old = self.samples[2][field]
                self.samples[2][field] = value
                with self.assertRaisesRegex(TraceError, "identity|monotonic"):
                    self._ingest()
                self.samples[2][field] = old
                for table in ("window", "hosts", "resources", "threads", "deliveries"):
                    self.db.execute(f"DELETE FROM monitoring.{table}")

    def test_thread_counter_reset_duplicate_and_missing_main_thread_are_rejected(self):
        cases = (
            (lambda threads: threads[1].update(user_ticks=0), "thread counters"),
            (lambda threads: threads.append(dict(threads[1])), "duplicated"),
            (lambda threads: threads.pop(0), "main thread"),
        )
        for change, message in cases:
            with self.subTest(message=message):
                old = [dict(thread) for thread in self.samples[2]["threads"]]
                change(self.samples[2]["threads"])
                with self.assertRaisesRegex(TraceError, message):
                    self._ingest()
                self.samples[2]["threads"] = old
                for table in ("window", "hosts", "resources", "threads", "deliveries"):
                    self.db.execute(f"DELETE FROM monitoring.{table}")

    def test_collector_reads_its_own_threads(self):
        stopped = threading.Event()
        worker = threading.Thread(target=stopped.wait, name="moq-test-worker")
        worker.start()
        try:
            row = ResourceSample.model_validate_json(json.dumps(resources.sample("relay", os.getpid())))
        finally:
            stopped.set()
            worker.join()
        self.assertIn(os.getpid(), [thread.tid for thread in row.threads])
        self.assertIn("moq-test-worker"[:15], [thread.name for thread in row.threads])

    def test_unknown_schema_fields_and_missing_connection_are_rejected(self):
        self.samples[0]["unknown"] = 1
        with self.assertRaisesRegex(TraceError, "Extra inputs"):
            self._ingest()
        self.samples[0].pop("unknown")
        for table in ("window", "hosts", "resources", "threads", "deliveries"):
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
