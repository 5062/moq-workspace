"""Failures specific to a benchmark workload or configuration."""

from moq_trace.errors import MoqTraceError


class ExperimentError(MoqTraceError):
    """An experiment's configuration or workload was not met."""
