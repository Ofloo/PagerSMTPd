# Bastille template (FreeBSD)

Draait dezelfde `smtp_server.py` uit de repo-root als Bastille jail — identical
gedrag als de Docker image, zelfde env-variabelen.

## Vereisten (host)
- FreeBSD met `bastille` en `pf` (default geinstalleerd)
- Jail krijgt een loopback alias (bastille regelt: bv. `127.0.0.2`)

## Installeren
```sh
# op de FreeBSD host, vanuit de repo-root:
bastille create pagersmtp                          # VNET uit, loopback alias aan
bastille template pagersmtp ./bastille
cp bastille/root/usr/local/etc/pagersmtpd.env.default /usr/local/bastille/jails/pagersmtp/root/usr/local/etc/pagersmtpd.env
# pas /usr/local/bastille/jails/pagersmtp/root/usr/local/etc/pagersmtpd.env aan (PAGER_DOMAIN etc.)
bastille cmd pagersmtp service pagersmtpd restart
```

## pf (host) — poort 25 naar de jail
Vind het jail-IP: `bastille cmd pagersmtp ifconfig lo0 | grep inet`
Zet in `/etc/pf.conf` (zie `pf-pagersmtp.conf`):
```
rdr on $ext_if inet proto tcp from any to ($ext_if) port 25 -> <jail-ip> port 8025
nat on $ext_if inet from <jail-ip> to any -> ($ext_if)   # outbound HTTPS naar pager/share
```
`pfctl -f /etc/pf.conf`

De pf-tabel `<jails>` moet het jail-IP bevatten (anders geen uitgaand HTTPS).
Maak dat persistent, bv. in `/etc/rc.local`:
```sh
pfctl -t jails -T add 10.13.40.202   # <jail-ip>
```
(en `sysrc gateway_enable=YES` voor NAT). Het jail-IP is te vinden met
`bastille list all`.

## Non-root
De rc-service draait als user `pager` (uid/gid 10000). Aanpassen:
```sh
sysrc pagersmtpd_uid=1000 pagersmtpd_gid=1000
bastille cmd pagersmtp service pagersmtpd restart
```

## Testen
Zelfde tests als Docker, maar tegen de jail:
`tests/run_tests.sh` draait lokaal (niet in de jail); voor de jail zelf:
```sh
bastille cmd pagersmtp sockstat -4 -l | grep 8025
# van buiten: mail naar <uuid>@PAGER_DOMAIN -> bericht in PagerMCP
```
