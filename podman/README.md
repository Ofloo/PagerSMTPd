# PagerSMPTd on Podman

The Docker image is a plain OCI image and runs unchanged under Podman (no
changes to the code, no separate build).

## Quick test (rootless)

```sh
mkdir -p ~/.config/pagersmtpd
cp .env.example ~/.config/pagersmtpd/pagersmtpd.env   # fill in PAGER_DOMAIN
# rootless podman cannot bind port 25 unless you allow unprivileged low ports:
sudo sysctl net.ipv4.ip_unprivileged_port_start=0

podman run -d --name pagersmtpd \
  --env-file ~/.config/pagersmtpd/pagersmtpd.env \
  --read-only --tmpfs /tmp:size=16m --cap-drop=all --security-opt no-new-privileges \
  --user 10000:10000 \
  -p 25:8025 \
  docker.io/ofloo/pagersmtpd:latest
```

Check it:

```sh
podman ps
podman exec pagersmtpd python3 -c "import socket;s=socket.create_connection(('127.0.0.1',8025),3);print(s.recv(80).decode())"
```

## Quadlet (systemd, recommended)

Quadlet is Podman's systemd integration (Podman >= 4.4).

```sh
mkdir -p ~/.config/pagersmtpd
cp .env.example ~/.config/pagersmtpd/pagersmtpd.env
mkdir -p ~/.config/containers/systemd
cp pagersmtpd.container ~/.config/containers/systemd/

systemctl --user daemon-reload
systemctl --user start pagersmtpd.service
systemctl --user status pagersmtpd.service
journalctl --user -u pagersmtpd.service -f
```

For a system-wide (rootful) service, copy to `/etc/containers/systemd/` and use
`systemctl` without `--user`; then port 25 needs no sysctl tweak.

## Notes

- Volumes: none. The server is stateless; attachments go to `SHARE_URL`.
- Auto-update: `podman auto-update` works with `AutoUpdate=registry` in the
  quadlet (or add `--label io.containers.autoupdate=registry` to `podman run`).
- The same `docker-compose.yml` also works with `podman-compose`/`podman compose`
  when a `.env` is present; the quadlet is preferred for a single service.
- FreeBSD: Podman does not run natively; use the Bastille jail template
  (`bastille/`) or the FreeBSD port (`ports/mail/pagersmtpd`).
