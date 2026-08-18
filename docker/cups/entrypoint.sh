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
# The lab queues (sink-raw, office-laser, finance-laser) print to a virtual PDF sink, not
# to hardware. Set false on a real print server, or they reappear on every restart after
# you delete them.
CREATE_LAB_QUEUES="${CREATE_LAB_QUEUES:-true}"

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

# /etc/cups is a persistence volume so operator-created queues survive a rebuild. The
# config is therefore re-rendered from the image's template on every start, otherwise the
# volume's stale copy would silently win over any shipped change.
mkdir -p /etc/cups
sed "s|@@ALLOW_FROM@@|${CUPS_ALLOW_FROM}|g" \
  /usr/share/janus-print/cupsd.conf.template > /etc/cups/cupsd.conf

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

# Docker signals PID 1 (this script), not cupsd. Without forwarding, cupsd is eventually
# SIGKILLed and shuts down without saving state.
shutdown() {
  kill -TERM "${CUPSD_PID}" 2>/dev/null || true
  wait "${CUPSD_PID}" 2>/dev/null || true
  exit 0
}
trap shutdown TERM INT

for _ in $(seq 1 40); do
  lpstat -r 2>/dev/null | grep -q "is running" && break
  sleep 0.5
done

if ! lpstat -r 2>/dev/null | grep -q "is running"; then
  echo "cupsd did not start; its error log follows" >&2
  tail -40 /var/log/cups/error_log >&2 || true
  exit 1
fi

if [ "${CREATE_LAB_QUEUES}" = "true" ]; then
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
  echo "== lab queues created =="
else
  echo "== lab queues disabled (CREATE_LAB_QUEUES=false) =="
fi

# Sharing decides what is advertised, and it has to converge without human action.
#
# Only queues whose device URI is janus:// are shared: those are inspected. Anything else
# talks to hardware directly, and advertising it would hand clients a documented route
# around the inspector.
#
# This runs as a loop rather than once at startup because printers are created from the
# console, by an API that is usually on another host — and CUPS refuses
# printer-is-shared over some remote connections ("Cannot change printer-is-shared for
# remote queues"), inconsistently enough that it cannot be relied on. Without this sweep a
# newly added printer stays invisible to clients until someone restarts the container.
#
# Idempotent: printers.conf is read first, so only queues in the wrong state are touched.
share_state() {
  awk '
    /^<(Default)?Printer /  { name = $2; sub(/>$/, "", name); uri = ""; shared = "No" }
    /^DeviceURI /           { uri = $2 }
    /^Shared /              { shared = $2 }
    /^<\/Printer>/          { if (name != "") print name, uri, shared; name = "" }
  ' /etc/cups/printers.conf 2>/dev/null
}

apply_sharing() {
  changed=""
  while read -r queue uri shared; do
    [ -z "${queue}" ] && continue
    case "${uri}" in
      janus://*)
        if [ "${shared}" != "Yes" ]; then
          lpadmin -p "${queue}" -o printer-is-shared=true 2>/dev/null \
            && changed="${changed} +${queue}"
        fi
        ;;
      *)
        if [ "${shared}" = "Yes" ]; then
          lpadmin -p "${queue}" -o printer-is-shared=false 2>/dev/null \
            && changed="${changed} -${queue}"
        fi
        ;;
    esac
  done <<EOF
$(share_state)
EOF
  [ -n "${changed}" ] && echo "== sharing updated:${changed} =="
  return 0
}

if [ "${ENABLE_DNSSD}" = "true" ]; then
  apply_sharing
  (
    while sleep "${SHARE_SWEEP_INTERVAL:-15}"; do
      apply_sharing
    done
  ) &
  echo "== sharing sweep every ${SHARE_SWEEP_INTERVAL:-15}s: janus:// queues advertised, others hidden =="
fi

echo "== queues ready =="
# `lpstat -v` exits non-zero when no queues exist, which under `set -e` would kill this
# script before it reaches `wait` — the container would exit the moment it had no
# printers, exactly when an operator is mid-way through recreating one.
lpstat -v || echo "(no queues yet — add one with lpadmin)"

wait "${CUPSD_PID}"
