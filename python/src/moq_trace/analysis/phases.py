"""The processing phases the providers emit and the analysis and figures name.

This is the one table of phases on the Python side. The provider enums in
`crates/*-lttng-sys/provider/interface.h` declare the same names, and a test
holds the two together. The analysis still measures a phase this table lacks,
because the generic analyzer uses whichever phases a provider emits; the table
says which direction each known phase belongs to and how figures label it.
"""

from __future__ import annotations

import dataclasses
from typing import Literal

import duckdb

Subject = Literal["object", "packet"]
Direction = Literal["rx", "tx"]
#: What a work phase spends its time on. Waits have no category, and neither do
#: phases that overlap others (`application`, `transport_call`), because their
#: time is already charged to the phases they overlap or contain.
Category = Literal["framing", "crypto", "routing"]


@dataclasses.dataclass(frozen=True)
class Phase:
    """One processing phase of a MoQ object or a QUIC packet."""

    subject: Subject
    direction: Direction
    name: str
    label: str
    #: Whether figures draw the phase as a step of its own. The RX `application`
    #: phase is the MoQ work of a stack that runs MoQ inside packet processing,
    #: which the object phases already show. Transport calls are summarized by
    #: the MoQ work figure, and notify stays measured in the work totals.
    drawn: bool = True
    #: Whether the phase is a wait rather than work. Work totals leave waits
    #: out. Many packets wait in one queue at once, so the object timeline leaves
    #: packet waits out too, and draws an object's own waits apart from its work.
    wait: bool = False
    #: Whether the phase runs inside another work phase of its subject rather
    #: than after it. Work totals subtract a nested phase from the phase around
    #: it instead of adding it.
    nested: bool = False
    category: Category | None = None


# Each tuple is in pipeline order.
OBJECT_PHASES = (
    Phase("object", "rx", "header_parse", "Header parse", category="framing"),
    Phase("object", "rx", "create", "Create", category="routing"),
    Phase("object", "rx", "payload_read", "Payload read", category="framing"),
    Phase("object", "rx", "frame_commit", "Frame commit", category="routing"),
    # Time inside the transport API, nested in the phase that called it. The
    # same phase exists in both directions, as the object it belongs to does.
    Phase("object", "rx", "transport_call", "Transport call", drawn=False, nested=True),
    # The fan-out work every copy shares, so it is charged once, to the inbound
    # object. A relay that wakes consumers inside its commit call emits none.
    Phase("object", "rx", "notify", "Notify", drawn=False, category="routing"),
    # From the instant the relay made the object readable until this copy's
    # clone starts: waking the consumer and the scheduler delay before it runs.
    Phase("object", "tx", "delivery_wait", "Delivery wait", wait=True, drawn=False),
    Phase("object", "tx", "clone", "Clone", category="routing"),
    Phase("object", "tx", "header_encode", "Header encode", category="framing"),
    Phase("object", "tx", "payload_write", "Payload write", category="framing"),
    # A write that could not proceed, until the relay resumed writing.
    Phase("object", "tx", "write_blocked", "Write blocked", wait=True),
    Phase("object", "tx", "transport_call", "Transport call", drawn=False, nested=True),
)

PACKET_PHASES = (
    # An RX packet starts when the socket read that returned its datagram
    # completes, and `read_queue` covers the wait from there until its
    # processing begins.
    Phase("packet", "rx", "read_queue", "Read queue", wait=True),
    Phase("packet", "rx", "header_parse", "Header parse", category="framing"),
    Phase("packet", "rx", "routing", "Routing", category="routing"),
    # Quinn's `scheduling` phase is the wait in the connection's queue,
    # including waking its task, so it is labeled for what it measures.
    Phase("packet", "rx", "scheduling", "Queuing", wait=True),
    Phase("packet", "rx", "header_unprotect", "Header unprotect", category="crypto"),
    Phase("packet", "rx", "payload_decrypt", "Payload decrypt", category="crypto"),
    Phase("packet", "rx", "frame_process", "Frame process", category="framing"),
    Phase("packet", "rx", "application", "Application", drawn=False),
    Phase("packet", "tx", "frame_encode", "Frame encode", category="framing"),
    Phase("packet", "tx", "packet_encrypt", "Packet encrypt", category="crypto"),
    # A TX packet ends when the socket send that accepted its datagram
    # completes, and `send_queue` covers the wait from encryption until then,
    # including the send itself.
    Phase("packet", "tx", "send_queue", "Send queue", wait=True),
)

PHASES = OBJECT_PHASES + PACKET_PHASES


def select(subject: Subject, direction: Direction) -> tuple[Phase, ...]:
    """The known phases of one subject and direction, in pipeline order."""

    return tuple(phase for phase in PHASES if phase.subject == subject and phase.direction == direction)


def register(connection: duckdb.DuckDBPyConnection) -> None:
    """Publish the table as `phase_catalog` for the analysis SQL to join against."""

    connection.execute(
        "CREATE OR REPLACE TEMP TABLE phase_catalog("
        "subject VARCHAR, direction VARCHAR, phase VARCHAR, label VARCHAR, wait BOOLEAN, nested BOOLEAN)"
    )
    connection.executemany(
        "INSERT INTO phase_catalog VALUES (?, ?, ?, ?, ?, ?)",
        [(phase.subject, phase.direction, phase.name, phase.label, phase.wait, phase.nested) for phase in PHASES],
    )
