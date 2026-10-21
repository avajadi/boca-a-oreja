#!/usr/bin/env python3
"""Software listener for the Phase 0 capacity test.

Does exactly what the ESP32-C6 firmware in esp32-listener/ does - receive one
audio-like stream from hub/loadgen.py and report what arrived - so any laptop, phone
with a Python environment, or spare Pi can join the test as an extra listener. Unlike
the C6, these can use 5 GHz.

Several instances can run on one machine with different --id values. That loads the
hub's software but not the Wi-Fi in a realistic way: one radio carrying N streams
costs less airtime than N radios carrying one each.

Python standard library only.
"""
import argparse
import hashlib
import socket
import struct
import time

# Must match hub/loadgen.py and esp32-listener/main/protocol.h.
DATA = struct.Struct("<4sIIQ")
REPORT = struct.Struct("<4s6sH9IbBH")
DATA_MAGIC = b"BAOD"
REPORT_MAGIC = b"BAOR"

BUCKET_US = 5_000_000   # delay baseline window half; see Stats.on_packet


class Stats:
    """Per-stream receive statistics, identical to the firmware's."""

    def __init__(self, late_us):
        self.late_us = late_us
        self.reset(0)

    def reset(self, stream_id):
        self.stream_id = stream_id
        self.highest = self.received = self.lost = self.reordered = self.late = 0
        self.jitter16 = 0           # RFC 3550 jitter, scaled by 16 to stay in integers
        self.max_excess = 0         # worst delay over baseline since the last report
        self.prev_transit = None
        self.bucket_min = [None, None]
        self.bucket = 0
        self.bucket_started = time.monotonic_ns() // 1000

    def on_packet(self, stream_id, seq, send_us, recv_us):
        if stream_id != self.stream_id:
            self.reset(stream_id)
        # Hub and listener clocks have unrelated offsets, so transit is only meaningful
        # relative to other transits on the same stream.
        transit = recv_us - send_us

        if self.prev_transit is None:
            self.highest = seq
        elif seq > self.highest:
            self.lost += seq - self.highest - 1
            self.highest = seq
        elif seq == self.highest:
            return                  # duplicate
        elif self.lost > 0:
            self.lost -= 1          # an earlier gap filled in late
            self.reordered += 1
        self.received += 1

        if self.prev_transit is not None:
            d = abs(transit - self.prev_transit)
            self.jitter16 += d - ((self.jitter16 + 8) >> 4)
        self.prev_transit = transit

        # "Late" = delayed more than late_us beyond the lowest recent transit. The
        # baseline is the minimum over two rolling 5 s buckets rather than all time, so
        # clock drift between hub and listener (tens of ppm) can't accumulate into
        # false lateness over a long run.
        if recv_us - self.bucket_started >= BUCKET_US:
            self.bucket ^= 1
            self.bucket_min[self.bucket] = None
            self.bucket_started = recv_us
        b = self.bucket_min[self.bucket]
        self.bucket_min[self.bucket] = transit if b is None else min(b, transit)
        baseline = min(x for x in self.bucket_min if x is not None)
        excess = transit - baseline
        if excess > self.late_us:
            self.late += 1
        self.max_excess = max(self.max_excess, excess)


def u32(x):
    return x & 0xFFFFFFFF


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("hub", help="hub address, e.g. 10.42.0.1")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--id", default=socket.gethostname(),
                   help="listener name; hashed into the 6-byte ID the hub shows (default: hostname)")
    p.add_argument("--report-ms", type=int, default=2000, help="report interval (default 2000)")
    p.add_argument("--late-ms", type=float, default=60,
                   help="packets delayed more than this count as late (default 60)")
    args = p.parse_args()

    # Locally administered MAC-style ID, so it can't collide with a real device's.
    ident = bytearray(hashlib.sha1(args.id.encode()).digest()[:6])
    ident[0] = (ident[0] | 0x02) & 0xFE

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
    sock.settimeout(0.05)
    hub = (args.hub, args.port)

    stats = Stats(int(args.late_ms * 1000))
    start = time.monotonic_ns() // 1000
    next_report = 0
    print(f"listener {bytes(ident).hex(':')} -> {args.hub}:{args.port}, Ctrl-C to stop")

    try:
        while True:
            try:
                data, _ = sock.recvfrom(2048)
                recv_us = time.monotonic_ns() // 1000
                if len(data) >= DATA.size and data[:4] == DATA_MAGIC:
                    _, stream_id, seq, send_us = DATA.unpack_from(data)
                    stats.on_packet(stream_id, seq, send_us, recv_us)
            except socket.timeout:
                pass

            now = time.monotonic_ns() // 1000
            if now >= next_report:
                s = stats
                try:
                    sock.sendto(REPORT.pack(
                        REPORT_MAGIC, bytes(ident), 0, s.stream_id, u32((now - start) // 1000),
                        u32(s.highest), u32(s.received), u32(s.lost), u32(s.reordered), u32(s.late),
                        u32(s.jitter16 >> 4), u32(s.max_excess), 0, 0, 0), hub)
                except OSError:
                    pass    # Wi-Fi down or reconnecting; the next report will try again
                print(f"rx {s.received}  lost {s.lost}  late {s.late}  "
                      f"jitter {(s.jitter16 >> 4) / 1000:.1f} ms  maxdly {s.max_excess / 1000:.0f} ms")
                s.max_excess = 0
                next_report = now + args.report_ms * 1000
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
