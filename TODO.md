# TODO

## Later
- [ ] Bastille-template testen op een echte FreeBSD-host (template staat in `bastille/`, nog niet live getest).
- [ ] STARTTLS op SMTP (cert via env of reverse proxy op 465/587).
- [ ] Eventueel DKIM/SPF/DMARC-controle op inkomende mail (nu alleen RCPT-uuid check).
- [ ] Metrics/health endpoint (nu alleen logs naar stdout).

## Gedaan (bij releases)
- [x] Docker receive-only SMTP → PagerMCP + share.h4ck.io attachments
- [x] pager.sh notificaties in build.sh + release commit/tag + dubbele image-tag (latest + v<VERSION>) na succesvolle build
- [x] tests/ met mocks + e2e (19 tests PASS)
- [x] `bastille/` FreeBSD template (zelfde env-interface, pf-rdr, non-root rc-service)
