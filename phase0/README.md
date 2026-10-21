# Phase 0 — Feasibility tooling

Tools for the Phase 0 tests in [docs/PLAN.md](../docs/PLAN.md): how many listeners a
hub's Wi-Fi can carry, how much delay the audio path adds, and whether the hub itself
keeps up.

| Path                  | Runs on      | Does                                                          |
|-----------------------|--------------|---------------------------------------------------------------|
| `hub/setup-ap.sh`     | Hub          | Turns the hub's Wi-Fi into a test access point                |
| `hub/loadgen.py`      | Hub          | Streams audio-like traffic to every listener, collects reports |
| `hub/hubstat.sh`      | Hub          | Logs temperature, throttling, CPU, Wi-Fi traffic, clients     |
| `esp32-listener/`     | ESP32-C6     | Firmware: one fake listener per board (2.4 GHz)               |
| `listener/listener.py`| Any computer | The same fake listener in Python, e.g. on a laptop (5 GHz too) |
| `latency/latency.py`  | Any computer | Generates click signals and measures delay in a recording     |

The Python tools use only the standard library; nothing needs installing on the hub.

## Wi-Fi capacity test

### Idea

Each listener receives one stream shaped like a real Opus audio stream — a packet
every 20 ms, 64 kbit/s — and reports back every 2 s how many packets were lost, how
many arrived too late to play, and how uneven the arrivals were. Adding listeners
one at a time shows where the access point stops coping. A real phone listening at
the same time tells you whether the numbers match what you hear.

The listeners don't run WebRTC, so this tests the Wi-Fi, not LiveKit. LiveKit's own
load gets tested separately (see below).

### 1. Set up the hub

Raspberry Pi OS Lite (64-bit), connected by **Ethernet** — the Wi-Fi becomes the
access point, so SSH must come in another way. Set the Wi-Fi country first:

```bash
sudo raspi-config nonint do_wifi_country SE
sudo ./hub/setup-ap.sh                 # 2.4 GHz, channel 6, SSID BAO-TEST
sudo ./hub/setup-ap.sh --band a        # or 5 GHz, channel 36
```

The hub is then `10.42.0.1` on the test network and hands out addresses itself.
`sudo ./hub/setup-ap.sh --down` removes the access point again.

### 2. Flash the ESP32-C6 listeners

The defaults match `setup-ap.sh` (SSID `BAO-TEST`, password `boca-a-oreja`, hub
`10.42.0.1:5000`); change them under *Boca a Oreja listener* in `idf.py menuconfig`
if needed. With ESP-IDF 5.4:

```bash
cd esp32-listener
idf.py build
idf.py -p /dev/ttyACM0 flash monitor    # once per board
```

Each board identifies itself by its MAC address, so the same image goes on all of
them. They only use 2.4 GHz; for 5 GHz tests use `listener.py` on laptops or other
devices instead.

### 3. Run the test

On the hub, in two terminals:

```bash
./hub/loadgen.py                         # live table, logs loadgen-<time>.csv
./hub/hubstat.sh | tee hubstat.csv       # hub health every 5 s
```

Then power on listeners one at a time, waiting a minute or so after each. Extra
software listeners on a laptop:

```bash
./listener/listener.py 10.42.0.1
```

At the same time, listen on a real phone (once LiveKit is running), or watch the
loadgen numbers for a phone-sized device.

### 4. Read the results

The live table shows, per listener, for the last report interval:

| Column   | Meaning                                                                 |
|----------|-------------------------------------------------------------------------|
| `RSSI`   | Signal strength at the listener, dBm. Below about −75 the listener itself is the problem. |
| `RX/S`   | Packets received per second; 50 is everything at the default 20 ms.     |
| `LOSS%`  | Packets that never arrived.                                             |
| `LATE%`  | Packets that arrived more than 60 ms behind the recent best — too late for a typical playout buffer, so as bad as lost. |
| `JITTER` | RFC 3550 interarrival jitter.                                           |
| `MAXDLY` | Worst single delay over the recent best.                                |

Rough guide for audio: **loss + late under 1%** is clean, a few percent is audible
as clicks that Opus partly hides, above 5% is not usable. The capacity of a setup is
the number of listeners at which the typical listener crosses about 1%.

With power save on (the default, as on phones), the access point holds frames for
dozing clients until the next beacon, so `MAXDLY` of around 100 ms and some `LATE`
are expected and realistic. Build the firmware with power save off to see what the
radio alone can do.

Discard a run if `hubstat.csv` shows `throttled` other than `0x0`, or `load1` near 4
on a Pi 4: then the limit measured is the hub's, not the Wi-Fi's.

### What to compare

Run the same test for each option in the plan, on the same channel and with the
listeners in the same places:

1. The hub's built-in Wi-Fi (`--ifname wlan0`)
2. A USB Wi-Fi adapter (`--ifname wlan1`, or whatever `iw dev` lists)
3. A separate access point, with `loadgen.py` on the hub over Ethernet

For each, note the listener count where loss + late crosses 1%, and at 2.4 and
5 GHz where possible.

## Latency test

Measures end-to-end delay by recording the original and the delayed signal side by
side, using the stereo input of a USB audio interface (e.g. a UCA202).

```bash
./latency/latency.py generate clicks.wav
```

1. Play `clicks.wav` from a laptop. Split the output with a Y-cable: one leg to the
   **left** line input of the recorder, the other into the system under test (the
   hub's audio input, standing in for the PA).
2. Connect the far end — a phone's headphone output — to the **right** line input.
3. Record a minute in Audacity, export as WAV, signed 16-bit PCM, then:

```bash
./latency/latency.py measure recording.wav
```

It matches each click on the right to the one on the left that caused it and prints
the delay per click, plus min, median and max. The spread between min and max is the
jitter you'd hear.

To get the delay of one stage alone, measure the setup without it (e.g. the laptop
straight into the recorder) and subtract.

## Not covered yet

- **LiveKit itself.** Install `livekit-server` on the hub and test it with LiveKit's
  own load tester from a computer on Ethernet, so the Wi-Fi isn't the bottleneck.
  This gives the hub's CPU limit to compare with the Wi-Fi limit above.
- **Phone behaviour on a Wi-Fi without Internet.** A manual checklist, per phone:
  does it stay on the hub's Wi-Fi or switch to mobile data; does iOS ask for the
  Local Network permission; does audio keep playing with the screen locked for 10
  minutes; does it come back after walking out of range and back in.
