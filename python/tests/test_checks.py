from __future__ import annotations

from analysis_case import ModelCase

from moq_trace.analysis import sql
from moq_trace.errors import TraceError

# Each table that declares no key, the check that must reject a repeated row in
# it, and the defect that check names.
_DUPLICATES = (
    ("processes", "checks/processes", "processes sharing a process ID"),
    ("model.objects", "checks/model", "objects sharing a trace ID"),
    ("model.packets", "checks/model", "packets sharing a trace ID"),
    ("model.intervals", "checks/model", "phase intervals sharing a subject, trace ID, span ID, and phase"),
    ("model.send_blocked", "checks/model", "send-blocked intervals sharing a span ID"),
    ("model.emission_delays", "checks/model", "emission delays listing an event twice"),
    ("model.selected_objects", "checks/window", "selected objects listed twice"),
    ("metrics.definitions", "checks/samples", "metrics defined twice"),
    ("metrics.samples", "checks/samples", "duplicate metric sample identities"),
    ("metrics.statistics", "checks/samples", "metrics summarized twice"),
    ("metrics.position_statistics", "checks/samples", "metric positions summarized twice"),
    ("metrics.phase_totals", "checks/samples", "duplicate phase totals"),
    ("metrics.timeline_selections", "checks/samples", "timeline selections sharing an order"),
    ("model.coverage", "checks/samples", "objects covered twice"),
    ("model.coverage_frames", "checks/samples", "duplicate coverage frames"),
    ("model.coverage_packets", "checks/samples", "coverage packets listed twice for one object"),
)


class DuplicateCheckTests(ModelCase):
    """The check queries reject what a key on each table would have."""

    def setUp(self) -> None:
        super().setUp()
        self.object_start(1, "rx", 1)
        self.object_start(2, "tx", 2)
        self.packet(3, "rx", 1)
        self.packet(4, "tx", 2)
        self.phase(3, "routing", 110_000, 120_000)
        self._derive_all()
        self.connection.execute(
            "INSERT INTO model.send_blocked VALUES (1, 2, NULL, 'congestion_window', 220000, 230000)"
        )
        for check in ("checks/processes", "checks/raw", "checks/model", "checks/window", "checks/samples"):
            sql.check(self.connection, check)

    def test_every_check_rejects_a_repeated_row(self) -> None:
        for table, check, defect in _DUPLICATES:
            with self.subTest(table=table):
                self.assertTrue(self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                self.connection.execute("BEGIN")
                try:
                    self.connection.execute(f"INSERT INTO {table} SELECT * FROM {table} LIMIT 1")
                    with self.assertRaisesRegex(TraceError, f"{defect}: 1"):
                        sql.check(self.connection, check)
                finally:
                    self.connection.execute("ROLLBACK")

    def test_a_sample_of_an_undefined_metric_is_rejected(self) -> None:
        self.connection.execute("INSERT INTO metrics.samples (metric, elapsed_ns, value_ns) VALUES ('unknown', 0, 0)")
        with self.assertRaisesRegex(TraceError, "metric samples of undefined metrics: 1"):
            sql.check(self.connection, "checks/samples")
