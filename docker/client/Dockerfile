# A workstation. Prints to the lab CUPS server over IPP, exactly as an office client would.
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        cups-client ghostscript python3 python3-reportlab curl \
    && rm -rf /var/lib/apt/lists/*

ENV CUPS_SERVER=cups:631
COPY docker/client/print-samples.sh /usr/local/bin/print-samples
RUN chmod +x /usr/local/bin/print-samples

CMD ["sleep", "infinity"]
