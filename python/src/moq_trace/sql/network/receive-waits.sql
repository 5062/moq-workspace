-- Join every qlog receive to its captured packet by the peer's original
-- connection ID, packet number space, and packet number. The wait is
-- the qlog time minus the capture time. Quinn stamps a whole receive poll with
-- the instant it began, so a packet arriving during the poll can wait slightly
-- less than zero.
INSERT INTO network.receive_waits BY NAME
SELECT wire.timestamp_ns - $origin AS elapsed_ns, qlog.connection, qlog.monotonic_ns - wire.timestamp_ns AS wait_ns
FROM qlog_receives AS qlog
JOIN network.wire_connections AS joined ON joined.original_remote_cid = qlog.connection
JOIN network.wire_packets AS wire
  ON wire.connection = joined.connection AND wire.direction = 'rx'
 AND wire.packet_space = qlog.packet_space AND wire.packet_number = qlog.packet_number
WHERE wire.timestamp_ns >= $origin;
