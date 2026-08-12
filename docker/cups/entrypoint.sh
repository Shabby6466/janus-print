#!/bin/bash
# Bring up CUPS, then define the lab queues:
#
#   sink-raw       a cups-pdf queue standing in for a physical printer
#   office-laser   fronted by the janus backend, default policy (fail-open)
#   finance-laser  fronted by the janus backend, deep_scan_required + fail-closed
#
# Printing to office-laser exercises the whole path: backend -> API -> verdict -> release
# to the sink, or hold in the queue.
set -euo pipefail

API_URL="${JANUS_PRINT_API_URL:-http://api:8080}"

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

echo "== lab queues ready =="
lpstat -v

wait "${CUPSD_PID}"
