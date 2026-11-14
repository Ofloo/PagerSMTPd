# PagerSMPTd

Receive-only SMTP catch-all die mail aan `<uuid>@<domein>` omzet naar een
PagerAPP-page en doorstuurt naar [PagerMCP](https://pager.ofloo.io) — met
bijlagen op [share.h4ck.io](https://share.h4ck.io).

- **Verstuurt nooit mail** (geen outbound SMTP, dus geen open relay); de enige
  uitgaande verbindingen zijn HTTPS naar `PAGER_URL` en `SHARE_URL`.
- **Eén gewone mail is genoeg**: onderwerp → pagetitel, body → detailtekst,
  bijlagen → share-URL's.
- Optioneel per mail: `X-Pager-*` als header **of** als blok bovenaan de body
  (urgentie alleen vanaf vertrouwde afzenders).

## Documentatie

- [Gebruikersgids](docs/README.md) — wat je met een mail kunt sturen
- [RFC-PagerSMPTd-0001](docs/rfc-0001-mail-mapping.md) — mail → page mapping en `X-Pager-*`
- [Configuratie](.env.example) — alle environment-variabelen

## Snel starten (Docker)

```sh
cp .env.example .env          # zet minimaal PAGER_DOMAIN
docker compose up -d --build
```

Container draait non-root (`PAGER_UID`/`PAGER_GID`), read-only, zonder caps.
Standaard bindt de compose op `127.0.0.1:2525`; voor een echte MX:
`SMTP_HOST_BIND=0.0.0.0 SMTP_HOST_PORT=25`.

## Deployment-varianten

| Variant | Waar |
|---|---|
| Docker / Swarm | `docker-compose.yml`, `docker-compose.swarm.yml` |
| Podman (Linux) | [podman/README.md](podman/README.md) — Quadlet |
| FreeBSD jail | [bastille/README.md](bastille/README.md) — Bastille template |
| FreeBSD package | [ports/mail/pagersmtpd](ports/mail/pagersmtpd) — port-skeleton |

## Bouwen en testen

```sh
./build.sh            # multiarch build+push (amd64+arm64), release commit+tag, pager-melding
tests/run_tests.sh    # 67 integratietests tegen mock pager + share (geen netwerk)
tests/e2e.sh          # end-to-end tegen de echte PAGER_URL/SHARE_URL
```

Versie staat in `VERSION`; `build.sh` pusht `:<tag>` én `v<VERSION>` en maakt
alleen na een geslaagde build een release-commit en tag.

## Licentie

0BSD — zie [LICENSE](LICENSE).
