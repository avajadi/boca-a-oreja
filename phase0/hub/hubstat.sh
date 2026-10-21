#!/usr/bin/env bash
# Logs the hub's health during a test, one CSV line per sample: temperature,
# throttling, CPU load, memory, Wi-Fi traffic and the number of associated clients.
# Run it alongside loadgen.py; throttling or a saturated CPU invalidates a capacity
# result, because the limit measured is then the hub's, not the Wi-Fi's.
#
# Usage: ./hubstat.sh [interval-seconds] [ifname] | tee hubstat.csv
#
# throttled is vcgencmd's bit field: 0x0 means never throttled; any other value means
# undervoltage or overheating occurred (see the Raspberry Pi docs for the bits).
set -euo pipefail

INTERVAL="${1:-5}"
IFNAME="${2:-wlan0}"
TX=/sys/class/net/$IFNAME/statistics/tx_bytes
RX=/sys/class/net/$IFNAME/statistics/rx_bytes

echo "time,temp_c,throttled,load1,mem_used_mb,wifi_tx_kbps,wifi_rx_kbps,stations"
prev_tx=$(cat "$TX"); prev_rx=$(cat "$RX")
while true; do
  sleep "$INTERVAL"
  tx=$(cat "$TX"); rx=$(cat "$RX")
  temp=$(vcgencmd measure_temp | sed 's/[^0-9.]//g')
  throttled=$(vcgencmd get_throttled | cut -d= -f2)
  load1=$(cut -d' ' -f1 /proc/loadavg)
  mem=$(free -m | awk '/^Mem:/{print $3}')
  stations=$(iw dev "$IFNAME" station dump 2>/dev/null | grep -c '^Station' || true)
  echo "$(date +%T),$temp,$throttled,$load1,$mem,$(( (tx - prev_tx) * 8 / 1000 / INTERVAL )),$(( (rx - prev_rx) * 8 / 1000 / INTERVAL )),$stations"
  prev_tx=$tx; prev_rx=$rx
done
