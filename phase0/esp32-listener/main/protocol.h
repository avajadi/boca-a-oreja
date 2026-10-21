// Wire format shared with hub/loadgen.py and listener/listener.py. All fields are
// little-endian, which is also the ESP32-C6's native byte order, so the structs are
// sent and received as-is.
#pragma once

#include <stdint.h>

#define BAO_DATA_MAGIC   "BAOD"
#define BAO_REPORT_MAGIC "BAOR"

// hub -> listener, one per --ptime-ms, padded to the configured packet size
typedef struct __attribute__((packed)) {
    char     magic[4];
    uint32_t stream_id;
    uint32_t seq;
    uint64_t send_us;      // hub's monotonic clock; only differences are meaningful
} bao_data_t;

// listener -> hub, every report interval; counters are cumulative for stream_id
typedef struct __attribute__((packed)) {
    char     magic[4];
    uint8_t  mac[6];
    uint16_t reserved;
    uint32_t stream_id;    // 0 until the first packet arrives
    uint32_t uptime_ms;
    uint32_t highest_seq;
    uint32_t received;
    uint32_t lost;
    uint32_t reordered;
    uint32_t late;
    uint32_t jitter_us;
    uint32_t max_excess_us; // since the previous report
    int8_t   rssi;
    uint8_t  channel;
    uint16_t reserved2;
} bao_report_t;

_Static_assert(sizeof(bao_data_t) == 20, "must match DATA in loadgen.py");
_Static_assert(sizeof(bao_report_t) == 52, "must match REPORT in loadgen.py");
