-- The decrypted STREAM frames, as coverage reads them. A wire frame completes
-- its bytes when its datagram was captured, in either direction, and its packet
-- is its row in `network.wire_packets`. An RX packet the trace never saw was
-- dropped by the kernel after capture, as the checks bound, so it covers nothing.
CREATE OR REPLACE TEMP VIEW coverage_frame_source AS
SELECT joined.trace_connection_id AS connection_id, packet.direction,
       frame.stream_id, frame.offset_start, frame.offset_end,
       packet.timestamp_ns AS completion_ns, packet.packet_id AS trace_id, packet.timestamp_ns AS start_ns
FROM network.wire_stream_frames AS frame
JOIN network.wire_packets AS packet USING (packet_id)
JOIN network.wire_connections AS joined USING (connection)
WHERE packet.direction = 'tx' OR packet.trace_id IS NOT NULL;
