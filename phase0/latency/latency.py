#!/usr/bin/env python3
"""Measures audio latency from a stereo recording of clicks.

  latency.py generate clicks.wav     write a test signal: a short click every 2 s
  latency.py measure recording.wav   report the delay between the two channels

Play clicks.wav into the system under test (e.g. into the hub's audio input, as the
"floor"), and record two things at once on a stereo line input:

  left   the original click, straight from the player
  right  what comes out at the far end, e.g. a phone's headphone output

Each click on the right is then matched to the click on the left that caused it, and
the gap is the end-to-end latency, accurate to well under a millisecond. Export the
recording as 16- or 24-bit PCM WAV (Audacity: File > Export > WAV, "Signed 16-bit PCM").

Python standard library only.
"""
import argparse
import array
import statistics
import sys
import wave

RATE = 48000
CLICK_EVERY_S = 2.0
QUIET_BEFORE_S = 0.5    # an onset needs this much below-threshold signal before it


def generate(path, seconds):
    period = int(RATE * CLICK_EVERY_S)
    click = int(RATE * 0.001)      # 1 ms full-scale square pulse
    frames = array.array("h", bytes(2 * int(RATE * seconds)))
    for start in range(period // 2, len(frames) - click, period):
        for i in range(click):
            frames[start + i] = 30000 if i < click // 2 else -30000
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames.tobytes())
    print(f"wrote {path}: {seconds:g} s, a click every {CLICK_EVERY_S:g} s")


def read_stereo(path):
    with wave.open(path, "rb") as w:
        if w.getnchannels() != 2:
            sys.exit(f"{path}: need a stereo recording, got {w.getnchannels()} channel(s)")
        width, rate, raw = w.getsampwidth(), w.getframerate(), w.readframes(w.getnframes())
    if width == 2:
        samples = array.array("h", raw)
    elif width == 3:
        samples = array.array("i", (int.from_bytes(raw[i:i + 3], "little", signed=True)
                                    for i in range(0, len(raw), 3)))
    else:
        sys.exit(f"{path}: export as 16- or 24-bit PCM (this file is {8 * width}-bit)")
    return rate, samples[0::2], samples[1::2]


def onsets(channel, rate, threshold_ratio):
    """Sample positions where the signal first crosses the threshold after a quiet spell."""
    peak = max(max(channel), -min(channel))
    if peak == 0:
        return [], 0
    threshold = peak * threshold_ratio
    quiet_needed = int(rate * QUIET_BEFORE_S)
    found, quiet = [], quiet_needed
    for i, v in enumerate(channel):
        if v > threshold or v < -threshold:
            if quiet >= quiet_needed:
                found.append(i)
            quiet = 0
        else:
            quiet += 1
    return found, peak


def measure(path, threshold_ratio, max_delay_s):
    rate, left, right = read_stereo(path)
    l_on, l_peak = onsets(left, rate, threshold_ratio)
    r_on, r_peak = onsets(right, rate, threshold_ratio)
    if not l_on or not r_on:
        sys.exit("no clicks found on " + ("the left" if not l_on else "the right") +
                 " channel - check the recording levels and wiring")

    delays, j = [], 0
    for l in l_on:
        while j < len(r_on) and r_on[j] < l:
            j += 1
        if j < len(r_on) and r_on[j] - l <= max_delay_s * rate:
            delays.append((r_on[j] - l) * 1000 / rate)
            j += 1

    print(f"{path}: {rate} Hz, {len(l_on)} clicks on the left, {len(r_on)} on the right, "
          f"{len(delays)} matched")
    if len(r_on) > len(l_on):
        print("  note: more clicks on the right than the left - noise or echo may be "
              "crossing the threshold; try a higher --threshold")
    if not delays:
        sys.exit(f"no right-channel click within {max_delay_s:g} s of a left one")
    for n, d in enumerate(delays, 1):
        print(f"  click {n:3}: {d:7.1f} ms")
    print(f"\nlatency  min {min(delays):.1f}  median {statistics.median(delays):.1f}  "
          f"max {max(delays):.1f} ms  (spread {max(delays) - min(delays):.1f} ms)")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__.split("\n\n", 1)[1])
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="write a click test signal")
    g.add_argument("wav")
    g.add_argument("--seconds", type=float, default=60)
    m = sub.add_parser("measure", help="measure delay in a stereo recording")
    m.add_argument("wav")
    m.add_argument("--threshold", type=float, default=0.3,
                   help="onset threshold as a fraction of each channel's peak (default 0.3)")
    m.add_argument("--max-delay", type=float, default=1.5,
                   help="ignore right-channel clicks more than this many seconds after the left (default 1.5)")
    args = p.parse_args()
    if args.cmd == "generate":
        generate(args.wav, args.seconds)
    else:
        measure(args.wav, args.threshold, args.max_delay)


if __name__ == "__main__":
    main()
