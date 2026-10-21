#!/usr/bin/env python3
"""Wi-Fi load generator and collector for the Phase 0 capacity test.

Runs on the hub. Every listener (an ESP32-C6 running esp32-listener/, or any machine
running listener/listener.py) registers by sending a report; from then on the hub sends
it one audio-like UDP stream - one packet every --ptime-ms, sized to --rate-kbps,
which by default matches a single Opus listener. Listeners report back what arrived,
and this prints a live table and logs every report to CSV.

Python standard library only, so it runs on a fresh Raspberry Pi OS without setup.

Wire format (little-endian; must match esp32-listener/main/protocol.h):

  DATA    hub -> listener   "BAOD", u32 stream_id, u32 seq, u64 send_us, padding
  REPORT  listener -> hub   "BAOR", 6-byte MAC, u16 reserved, u32 stream_id,
                            u32 uptime_ms, u32 highest_seq, u32 received, u32 lost,
                            u32 reordered, u32 late, u32 jitter_us, u32 max_excess_us,
                            i8 rssi, u8 channel, u16 reserved

Report counters are cumulative for the listener's current stream_id. The hub assigns
a new stream_id whenever it (re)starts streaming to a listener, and the listener resets
its counters when it sees one, so a gap in streaming never shows up as packet loss.
"""
import argparse
import csv
import random
import select
import socket
import struct
import sys
import time
from dataclasses import dataclass, field

DATA = struct.Struct("<4sIIQ")
REPORT = struct.Struct("<4s6sH9IbBH")
DATA_MAGIC = b"BAOD"
REPORT_MAGIC = b"BAOR"


@dataclass
class Interval:
    """What happened between a listener's two most recent reports."""
    rx_per_s: float = 0.0
    loss_pct: float = 0.0
    late_pct: float = 0.0


@dataclass
class Listener:
    mac: str
    addr: tuple
    stream_id: int
    seq: int = 0
    last_seen: float = 0.0
    active: bool = True
    report: tuple = None    # most recent REPORT, unpacked
    prev: tuple = None      # the one before, for interval deltas
    interval: Interval = field(default_factory=Interval)


def new_stream_id():
    # Never 0: a listener reports stream_id 0 until its first packet arrives.
    return random.randrange(1, 2**32)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--port", type=int, default=5000, help="UDP port to listen on (default 5000)")
    p.add_argument("--rate-kbps", type=float, default=64,
                   help="payload rate per listener in kbit/s (default 64, about one Opus stream)")
    p.add_argument("--ptime-ms", type=float, default=20,
                   help="packet interval in ms (default 20, Opus's usual frame size)")
    p.add_argument("--timeout", type=float, default=10,
                   help="stop streaming to a listener after this many seconds without a report")
    p.add_argument("--csv", default=time.strftime("loadgen-%Y%m%d-%H%M%S.csv"),
                   help="where to log every report (default loadgen-<timestamp>.csv)")
    p.add_argument("--duration", type=float, default=0, help="stop after this many seconds (0 = run until Ctrl-C)")
    p.add_argument("--display-interval", type=float, default=2, help="seconds between table redraws")
    p.add_argument("--quiet", action="store_true", help="no live table, CSV only")
    return p.parse_args()


def handle_report(r, addr, now, listeners, writer):
    magic, mac_raw, _, stream_id, uptime_ms, highest, received, lost, reordered, late, \
        jitter_us, max_excess_us, rssi, channel, _ = r
    mac = mac_raw.hex(":")

    l = listeners.get(mac)
    if l is None:
        l = listeners[mac] = Listener(mac=mac, addr=addr, stream_id=new_stream_id())
    elif not l.active:
        # Coming back after a timeout: restart with a fresh stream so the listener
        # resets its counters instead of counting the silent gap as loss.
        l.stream_id, l.seq, l.prev, l.report = new_stream_id(), 0, None, None
        l.interval = Interval()
    l.addr, l.last_seen, l.active = addr, now, True

    d_rx = d_lost = d_late = ""
    if l.report and l.report[3] == stream_id and stream_id == l.stream_id and received >= l.report[6]:
        prev = l.report
        d_rx, d_lost, d_late = received - prev[6], lost - prev[7], late - prev[9]
        expected = d_rx + max(d_lost, 0)
        secs = max((uptime_ms - prev[4]) / 1000, 1e-3)
        l.interval = Interval(
            rx_per_s=d_rx / secs,
            loss_pct=100 * max(d_lost, 0) / expected if expected else 0.0,
            late_pct=100 * d_late / d_rx if d_rx else 0.0,
        )
    l.prev, l.report = l.report, r

    writer.writerow([
        f"{time.time():.3f}", mac, addr[0], rssi, channel, stream_id, received, lost, late,
        reordered, jitter_us, max_excess_us, d_rx, d_lost, d_late,
        sum(1 for x in listeners.values() if x.active),
    ])


def draw(listeners, args, overruns, started):
    active = [l for l in listeners.values() if l.active]
    rows = sorted(listeners.values(), key=lambda l: l.mac)
    now = time.monotonic()
    tx_mbps = len(active) * args.rate_kbps / 1000
    out = ["\033[H\033[2J",
           f"Boca a Oreja loadgen  port {args.port}  {args.rate_kbps:g} kbit/s per listener, "
           f"{args.ptime_ms:g} ms packets  running {now - started:.0f} s\n",
           f"active listeners {len(active)}   payload sent {tx_mbps:.2f} Mbit/s   "
           f"scheduler overruns {overruns}\n\n",
           f"{'LISTENER':<9}{'IP':<16}{'RSSI':>5}{'CH':>4}{'RX/S':>7}{'LOSS%':>7}{'LATE%':>7}"
           f"{'JITTER':>8}{'MAXDLY':>8}{'SEEN':>6}\n"]
    for l in rows:
        r = l.report
        if r is None:
            out.append(f"{l.mac[-8:]:<9}{l.addr[0]:<16}  waiting for first report\n")
            continue
        state = f"{now - l.last_seen:5.0f}s" if l.active else "  gone"
        out.append(
            f"{l.mac[-8:]:<9}{l.addr[0]:<16}{r[12]:>5}{r[13]:>4}{l.interval.rx_per_s:>7.1f}"
            f"{l.interval.loss_pct:>7.1f}{l.interval.late_pct:>7.1f}"
            f"{r[10] / 1000:>6.1f}ms{r[11] / 1000:>6.0f}ms{state}\n")
    out.append("\nLOSS% and LATE% are for the last report interval. LATE = arrived too late to play "
               "(listener's threshold).\nJITTER is RFC 3550 interarrival jitter; MAXDLY the worst "
               "delay over the recent minimum.  Ctrl-C to stop.\n")
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def main():
    args = parse_args()
    ptime = args.ptime_ms / 1000
    size = max(DATA.size, round(args.rate_kbps * 1000 / 8 * ptime))
    padding = bytes(size - DATA.size)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)
    sock.bind(("0.0.0.0", args.port))
    sock.setblocking(False)

    listeners = {}
    overruns = 0
    started = time.monotonic()
    next_tick = started + ptime
    next_draw = started

    with open(args.csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "time", "mac", "ip", "rssi", "channel", "stream_id", "received", "lost", "late",
            "reordered", "jitter_us", "max_excess_us", "d_received", "d_lost", "d_late",
            "active_listeners",
        ])
        print(f"listening on UDP {args.port}, {size}-byte packets every {args.ptime_ms:g} ms, "
              f"logging to {args.csv}")
        try:
            while True:
                now = time.monotonic()
                if args.duration and now - started >= args.duration:
                    break

                if now >= next_tick:
                    send_us = time.monotonic_ns() // 1000
                    for l in listeners.values():
                        if l.active and now - l.last_seen > args.timeout:
                            l.active = False
                        if not l.active:
                            continue
                        pkt = DATA.pack(DATA_MAGIC, l.stream_id, l.seq, send_us) + padding
                        try:
                            sock.sendto(pkt, l.addr)
                        except OSError:
                            pass    # e.g. ENOBUFS when the Wi-Fi queue is full - that's loss too
                        l.seq = (l.seq + 1) & 0xFFFFFFFF
                    next_tick += ptime
                    if now - next_tick > ptime:
                        # More than a tick behind: skip ahead instead of bursting to catch up,
                        # which would look like jitter caused by the Wi-Fi.
                        overruns += 1
                        next_tick = now + ptime

                if not args.quiet and now >= next_draw:
                    draw(listeners, args, overruns, started)
                    next_draw = now + args.display_interval

                wait = max(0.0, min(next_tick, next_draw if not args.quiet else next_tick) - time.monotonic())
                readable, _, _ = select.select([sock], [], [], wait)
                if readable:
                    while True:
                        try:
                            data, addr = sock.recvfrom(2048)
                        except BlockingIOError:
                            break
                        if len(data) >= REPORT.size and data[:4] == REPORT_MAGIC:
                            handle_report(REPORT.unpack_from(data), addr, time.monotonic(), listeners, writer)
                    f.flush()
        except KeyboardInterrupt:
            pass
    print(f"\nstopped; reports logged to {args.csv}")


if __name__ == "__main__":
    main()
