#!/usr/bin/env bash
# Turns the hub's Wi-Fi into a test access point, using NetworkManager (the default
# on Raspberry Pi OS Bookworm and later).
#
# The connection uses NetworkManager's "shared" mode: the hub gets 10.42.0.1/24 and
# runs DHCP for clients itself, with no extra packages. Clients get no route to the
# Internet unless the hub has one, which is fine - the test traffic never leaves the
# hub's network.
#
# Run it over Ethernet: the Wi-Fi interface stops being a client while the AP is up.
#
# Usage: sudo ./setup-ap.sh [--band bg|a] [--channel N] [--ssid S] [--password P] [--ifname wlan0]
#        sudo ./setup-ap.sh --down        # remove the AP again
#
# --band bg is 2.4 GHz (what the ESP32-C6 listeners need), --band a is 5 GHz.
set -euo pipefail

CON=bao-ap
BAND=bg
CHANNEL=
SSID=BAO-TEST
PASSWORD=boca-a-oreja
IFNAME=wlan0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --band)     BAND="$2"; shift 2 ;;
    --channel)  CHANNEL="$2"; shift 2 ;;
    --ssid)     SSID="$2"; shift 2 ;;
    --password) PASSWORD="$2"; shift 2 ;;
    --ifname)   IFNAME="$2"; shift 2 ;;
    --down)
      nmcli connection down "$CON" 2>/dev/null || true
      nmcli connection delete "$CON"
      exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

case "$BAND" in
  bg) CHANNEL="${CHANNEL:-6}" ;;
  a)  CHANNEL="${CHANNEL:-36}" ;;
  *)  echo "--band must be bg (2.4 GHz) or a (5 GHz)" >&2; exit 2 ;;
esac

[ "${#PASSWORD}" -ge 8 ] || { echo "WPA2 needs a password of at least 8 characters" >&2; exit 2; }

# Without a regulatory country the 5 GHz band is unavailable and 2.4 GHz may be limited.
if iw reg get 2>/dev/null | grep -q "country 00"; then
  echo "No Wi-Fi country is set. Set it first, e.g.: sudo raspi-config nonint do_wifi_country SE" >&2
  exit 1
fi

nmcli connection delete "$CON" >/dev/null 2>&1 || true
nmcli connection add type wifi ifname "$IFNAME" con-name "$CON" autoconnect no \
  ssid "$SSID" \
  802-11-wireless.mode ap \
  802-11-wireless.band "$BAND" \
  802-11-wireless.channel "$CHANNEL" \
  wifi-sec.key-mgmt wpa-psk \
  wifi-sec.psk "$PASSWORD" \
  ipv4.method shared \
  ipv6.method disabled
nmcli connection up "$CON"

echo
echo "AP up: SSID '$SSID', band $BAND, channel $CHANNEL, hub address 10.42.0.1"
echo "Connected clients:  iw dev $IFNAME station dump | grep -c ^Station"
echo "Remove again:       sudo $0 --down"
