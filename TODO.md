# TODO

## Later
- [x] Podman-variant (`podman/` Quadlet) — image is OCI, ongewijzigd bruikbaar.
- [x] FreeBSD port-skeleton (`ports/mail/pagersmtpd/`).
- [ ] Podman-variant testen op een host met Podman (hier niet geïnstalleerd).
- [ ] `X-Pager-*`-headers implementeren (RFC-PagerSMPTd-0001 §6) inclusief allowlist voor urgentie.
- [ ] Quoted history/handtekeningen strippen (heuristiek, voorzichtig).
- [ ] FreeBSD port testen via `poudriere testport` (template wel al getest in een jail).
- [x] Bastille-template getest op FreeBSD 15.1 (Bastille 1.4.4), jail `pagersmtp` op 10.13.40.202.
- [ ] STARTTLS op SMTP (cert via env of reverse proxy op 465/587).
- [ ] Eventueel DKIM/SPF/DMARC-controle op inkomende mail (nu alleen RCPT-uuid check).
- [ ] Metrics/health endpoint (nu alleen logs naar stdout).

## Gedaan (bij releases)
- [x] Docker receive-only SMTP → PagerMCP + share.h4ck.io attachments
- [x] pager.sh notificaties in build.sh + release commit/tag + dubbele image-tag (latest + v<VERSION>) na succesvolle build
- [x] tests/ met mocks + e2e (19 tests PASS)
- [x] `bastille/` FreeBSD template (zelfde env-interface, pf-rdr, non-root rc-service)
