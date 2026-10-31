FROM python:3.12-alpine

ARG BUILD_NUMBER=0
ENV PYTHONUNBUFFERED=1 \
    BUILD_NUMBER=${BUILD_NUMBER}

RUN apk add --no-cache su-exec \
 && addgroup -g 10000 pager \
 && adduser -D -u 10000 -G pager pager

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY smtp_server.py /app/smtp_server.py
COPY VERSION /app/VERSION
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

LABEL org.opencontainers.image.title="PagerSMPTd" \
      org.opencontainers.image.description="Receive-only SMTP catch-all forwarding to PagerMCP" \
      org.opencontainers.image.source="https://github.com/Ofloo/PagerSMTPd"

EXPOSE 8025

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import socket;s=socket.create_connection(('127.0.0.1',8025),3);s.close()"

# Start as root so entrypoint.sh can switch to PAGER_UID/PAGER_GID (or the
# built-in 'pager' user) via su-exec. The server itself never runs as root.
ENTRYPOINT ["/entrypoint.sh"]
