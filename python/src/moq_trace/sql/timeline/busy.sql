-- Work for other connections on the threads that did the first and last copy's
-- work, in microseconds from `$origin`. A thread counts from the copy's start
-- until the copy's last work on it, so a gap is charged to the thread that did
-- what followed it.
WITH copies AS (
  SELECT trace_id, session_id, connection_id, start_ns FROM model.objects
  WHERE rx_trace_id = $rx AND copy_ordinal IS NOT NULL
  QUALIFY copy_ordinal IN (min(copy_ordinal) OVER (), max(copy_ordinal) OVER ())
), threads AS (
  SELECT trace_id, tid, max(end_ns) AS until_ns FROM (
    SELECT copy.trace_id, phase.tid, phase.end_ns
    FROM copies copy JOIN model.intervals phase USING (trace_id)
    WHERE phase.subject = 'object' AND list_contains($object_work, phase.phase)
    UNION ALL
    SELECT copy.trace_id, phase.tid, phase.end_ns
    FROM copies copy
    JOIN model.coverage_packets coverage ON coverage.object_trace_id = copy.trace_id
    JOIN model.intervals phase ON phase.trace_id = coverage.packet_trace_id
    WHERE phase.subject = 'packet' AND list_contains($packet_work, phase.phase)
  ) GROUP BY ALL
), span AS (
  SELECT (SELECT min(start_ns) FROM copies) AS start_ns, max(until_ns) AS end_ns FROM threads
), candidates AS (
  -- Narrowed before the joins below, which would otherwise scan the whole capture.
  SELECT phase.* FROM model.intervals phase, span
  WHERE phase.tid IN (SELECT tid FROM threads)
    AND phase.start_ns < span.end_ns AND phase.end_ns > span.start_ns
    AND list_contains(CASE phase.subject WHEN 'packet' THEN $packet_work ELSE $object_work END, phase.phase)
), work AS (
  SELECT phase.tid, phase.start_ns, phase.end_ns, packet.connection_id
  FROM candidates phase JOIN model.packets packet USING (trace_id)
  WHERE phase.subject = 'packet'
  UNION ALL
  SELECT phase.tid, phase.start_ns, phase.end_ns, object.connection_id
  FROM candidates phase JOIN model.objects object USING (trace_id)
  WHERE phase.subject = 'object'
)
SELECT copy.session_id,
       (greatest(work.start_ns, copy.start_ns) - $origin) / 1000.0,
       (least(work.end_ns, thread.until_ns) - $origin) / 1000.0
FROM copies copy JOIN threads thread USING (trace_id)
JOIN work ON work.tid = thread.tid AND work.connection_id <> copy.connection_id
  AND work.start_ns < thread.until_ns AND work.end_ns > copy.start_ns
ORDER BY 1, 2;
