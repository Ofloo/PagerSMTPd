FROM python:3.12-alpine

ARG BUILD_NUMBER=0
ENV PYTHONUNBUFFERED=1 \
    BUILD_NUMBER=${BUILD_NUMBER}

RUN apk add --no-cache su-exec \
 && addgroup -g 10000 pager \
 && adduser -D -u 10000 -G pager pager \
 && pip install --no-cache-dir aiosmtpd==1.4.6

WORKDIR /app
COPY smtp_server.py /app/smtp_server.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8025

# Start as root so entrypoint.sh can switch to PAGER_UID/PAGER_GID (or the
# built-in 'pager' user) via su-exec. The server itself never runs as root.
ENTRYPOINT ["/entrypoint.sh"]
