-- The decrypted STREAM frames, as coverage reads them. A wire frame completes
-- its bytes when its datagram was captured, in either direction, and its packet
-- is its row in `network.wire_packets`. An RX packet the trace never saw was
-- dropped by the kernel after capture, as the checks bound, so it covers nothing.
--
-- The trace counts first TX transmissions only. A sender writes new stream bytes
-- at the stream's send frontier, so a TX frame ending at or below the farthest
-- offset an earlier-numbered packet sent on the stream repeats old bytes and is
-- left out the same way.
CREATE OR REPLACE TEMP VIEW coverage_frame_source AS
SELECT connection_id, direction, stream_id, offset_start, offset_end, completion_ns, trace_id, start_ns
FROM (
  SELECT joined.trace_connection_id AS connection_id, packet.direction,
         frame.stream_id, frame.offset_start, frame.offset_end,
         packet.timestamp_ns AS completion_ns, packet.packet_id AS trace_id, packet.timestamp_ns AS start_ns,
         max(frame.offset_end) OVER (
           PARTITION BY packet.connection, packet.direction, packet.packet_space, frame.stream_id
           ORDER BY packet.packet_number, frame.offset_start
           ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
         ) AS frontier
  FROM network.wire_stream_frames AS frame
  JOIN network.wire_packets AS packet USING (packet_id)
  JOIN network.wire_connections AS joined USING (connection)
  WHERE packet.direction = 'tx' OR packet.trace_id IS NOT NULL
)
WHERE direction = 'rx' OR frontier IS NULL OR offset_end > frontier;
