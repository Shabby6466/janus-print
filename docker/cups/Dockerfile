# The lab print server: a real CUPS instance with the janus backend installed in front of
# a virtual PDF sink, so a job can be printed end to end without any hardware.
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        cups cups-client cups-filters cups-pdf python3 python3-minimal \
        ghostscript ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY docker/cups/cupsd.conf /etc/cups/cupsd.conf
COPY docker/cups/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY backend/janus /usr/lib/cups/backend/janus

# CUPS refuses to run a backend that is group- or world-writable, and runs it as root only
# when it is 0700. That gives it access to the job file regardless of spool permissions.
RUN chmod 0700 /usr/lib/cups/backend/janus \
    && chown root:root /usr/lib/cups/backend/janus \
    && chmod +x /usr/local/bin/entrypoint.sh \
    && mkdir -p /etc/janus-print

EXPOSE 631
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
