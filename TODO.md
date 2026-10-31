# TODO

## Later
- [ ] `bastille/` — FreeBSD Bastille jail template (zelfde env-interface als Docker). Te testen op een FreeBSD-host (nog op te zetten door eigenaar).
- [ ] STARTTLS op SMTP (cert via env of reverse proxy op 465/587).
- [ ] Eventueel DKIM/SPF/DMARC-controle op inkomende mail (nu alleen RCPT-uuid check).
- [ ] Metrics/health endpoint (nu alleen logs naar stdout).

## Gedaan (bij releases)
- [x] Docker receive-only SMTP → PagerMCP + share.h4ck.io attachments
- [x] pager.sh notificaties in build.sh + release commit/tag na succesvolle build
- [x] tests/ met mocks + e2e
