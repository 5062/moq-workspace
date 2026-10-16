CREATE SCHEMA IF NOT EXISTS network;

CREATE TABLE network.capture_statistics (
    captured BIGINT, received_by_filter BIGINT, kernel_drops BIGINT, interface_drops BIGINT
);

CREATE TABLE network.datagrams (
    elapsed_ns BIGINT NOT NULL,
    direction VARCHAR,
    peer VARCHAR,
    role VARCHAR,
    bytes INTEGER
);

CREATE TABLE network.recovery (
    elapsed_ns BIGINT NOT NULL,
    connection VARCHAR,
    smoothed_rtt_ns DOUBLE,
    min_rtt_ns DOUBLE,
    latest_rtt_ns DOUBLE,
    congestion_window BIGINT,
    bytes_in_flight BIGINT,
    role VARCHAR
);

CREATE TABLE network.losses (
    elapsed_ns BIGINT NOT NULL,
    connection VARCHAR,
    packet_number UBIGINT,
    bytes INTEGER,
    trigger VARCHAR,
    role VARCHAR
);
