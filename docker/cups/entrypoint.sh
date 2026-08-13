#!/bin/bash
# Bring up CUPS, then define the lab queues:
#
#   sink-raw       a cups-pdf queue standing in for a physical printer
#   office-laser   fronted by the janus backend, default policy (fail-open)
#   finance-laser  fronted by the janus backend, deep_scan_required + fail-closed
#
# Printing to office-laser exercises the whole path: backend -> API -> verdict -> release
# to the sink, or hold in the queue.
#
# Set ENABLE_DNSSD=true to advertise the queues over Bonjour/DNS-SD so macOS and Linux
# clients discover them with no per-machine setup. That requires host networking — mDNS
# does not cross Docker's bridge. See docker-compose.discovery.yml.
set -euo pipefail

API_URL="${JANUS_PRINT_API_URL:-http://api:8080}"
ENABLE_DNSSD="${ENABLE_DNSSD:-false}"
# Who may submit and administer. @LOCAL means any address on the server's own subnet,
# which is the right default for an office print server. "all" is lab-only.
CUPS_ALLOW_FROM="${CUPS_ALLOW_FROM:-@LOCAL}"

cat > /etc/janus-print/backend.conf <<EOF
[backend]
api_url = ${API_URL}
timeout = 8
fail_mode = open
max_bytes = 209715200
syslog_host = ${JANUS_PRINT_SYSLOG_HOST:-}
syslog_port = ${JANUS_PRINT_SYSLOG_PORT:-514}
EOF

mkdir -p /run/cups /var/spool/cups-pdf/ANONYMOUS
chown -R lp:lp /var/spool/cups-pdf || true

# Access control is templated rather than baked in, so the same image serves the isolated
# lab and a real LAN.
sed -i "s|@@ALLOW_FROM@@|${CUPS_ALLOW_FROM}|g" /etc/cups/cupsd.conf

if [ "${ENABLE_DNSSD}" = "true" ]; then
  sed -i 's|^Browsing Off|Browsing On|' /etc/cups/cupsd.conf
  mkdir -p /run/dbus
  rm -f /run/dbus/pid
  dbus-daemon --system --fork
  # reflector off: we advertise our own queues, we do not relay other hosts'.
  avahi-daemon --daemonize --no-drop-root 2>/dev/null || avahi-daemon --daemonize || {
    echo "avahi failed to start; DNS-SD discovery will not work" >&2
  }
  echo "== DNS-SD advertising enabled =="
fi

/usr/sbin/cupsd -f &
CUPSD_PID=$!

for _ in $(seq 1 40); do
  lpstat -r 2>/dev/null | grep -q "is running" && break
  sleep 0.5
done

if ! lpstat -r 2>/dev/null | grep -q "is running"; then
  echo "cupsd did not start; its error log follows" >&2
  tail -40 /var/log/cups/error_log >&2 || true
  exit 1
fi

# The stand-in for a real device. In production this is the printer's own ipp:// URI.
lpadmin -p sink-raw -E -v cups-pdf:/ -m drv:///cupsfilters.drv/pxlcolor.ppd 2>/dev/null \
  || lpadmin -p sink-raw -E -v cups-pdf:/ -m everywhere 2>/dev/null \
  || lpadmin -p sink-raw -E -v cups-pdf:/ -m raw

# Inspected queues. The janus backend strips its own prefix and execs the real backend
# with the same argv, so janus://ipp/localhost/printers/sink-raw becomes
# ipp://localhost/printers/sink-raw — the same ipp backend a real printer would use.
SINK="janus://ipp/localhost/printers/sink-raw"
lpadmin -p office-laser -E -v "${SINK}" -m everywhere 2>/dev/null \
  || lpadmin -p office-laser -E -v "${SINK}" -m raw
lpadmin -p finance-laser -E -v "${SINK}" -m everywhere 2>/dev/null \
  || lpadmin -p finance-laser -E -v "${SINK}" -m raw

cupsenable office-laser finance-laser sink-raw || true
cupsaccept office-laser finance-laser sink-raw || true
lpadmin -d office-laser

# A queue is only advertised over DNS-SD if it is explicitly shared. Recent CUPS defaults
# this to false, so discovery silently does nothing without it.
if [ "${ENABLE_DNSSD}" = "true" ]; then
  for queue in office-laser finance-laser; do
    lpadmin -p "${queue}" -o printer-is-shared=true
  done
  # The sink stands in for the physical device — never advertise it, or clients can print
  # straight to it and bypass inspection entirely.
  lpadmin -p sink-raw -o printer-is-shared=false
  echo "== shared queues: office-laser finance-laser =="
fi

echo "== lab queues ready =="
lpstat -v

wait "${CUPSD_PID}"
