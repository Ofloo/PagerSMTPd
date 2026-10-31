# PagerSMPTd — documentatie

PagerSMPTd is een **receive-only** SMTP catch-all: het ontvangt mail voor
`<uuid>@<PAGER_DOMAIN>`, zet die om naar een PagerAPP-page en stuurt hem naar
PagerMCP (`POST /notify`, Bearer = het uuid uit het adres). Bijlagen gaan naar
`SHARE_URL` (default share.h4ck.io) en de URL's komen in `attachments[]`.

Er wordt **nooit** mail verstuurd: geen outbound SMTP, dus geen open relay. De
enige uitgaande verbindingen zijn HTTPS naar `PAGER_URL` en `SHARE_URL`.

- Spec: [rfc-0001-mail-mapping.md](rfc-0001-mail-mapping.md)
- PagerAPP-zijde: `PagerAPP/docs/rfc-gateway.md` (RFC-PagerAPP-0003)
- Deployment: [Docker/Swarm](../docker-compose.swarm.yml), [Podman](../podman/README.md),
  [FreeBSD Bastille](../bastille/README.md), [FreeBSD port](../ports/mail/pagersmtpd/)

---

## Eén gewone mail is genoeg

Je hoeft niets speciaals te doen. Dit is alles:

```
To:      <uuid>@pager.ofloo.io
Subject: Deployment failed
Body:    De job stopte met exitcode 2.
```

Resultaat: een page met titel "Deployment failed", de body als detailtekst en
`status: 1` (warning).

### Automatische mapping

| Mail | Page-veld | Regel |
|---|---|---|
| `Subject` | `message` | leeg → eerste 120 tekens van de body → `"(no subject)"` |
| tekstbody (`text/plain`) | `logs` | opgeschoond, max 8000 tekens |
| HTML-body | `logs` | alleen als er geen bruikbare `text/plain` is; tags/scripts weg |
| doorgestuurde mail / spam-wrapper | `logs` | de binnenste mail wordt gebruikt, niet het rapport |
| bijlagen | `attachments[]` | geüpload naar `SHARE_URL`, URL's in het payload |
| `Message-ID` | `JOB_ID` | correlatie |
| `From` | — | niet in het payload (`from` is voor chat-sender-ids) |
| — | `status` | `1` (warning: nieuwe mail) |
| — | `PROJECT` | `PAGER_PROJECT` (default `smtp`) |

### Wat uit de mail gefilterd wordt

Transport- en spamheaders (`Received`, `DKIM-Signature`, `X-Spam-*`,
`Authentication-Results`, `Autocrypt`, …), SpamAssassin-rapporten, extreem lange
regels (base64-blobs) en dubbele lege regels. Quoted history (`> …`) en
handtekeningen blijven staan — dat strippen zou echte tekst kunnen wegsnijden.

### Bijlagen

Elke bijlage (behalve tekst en doorgestuurde mails) wordt geüpload. Mislukt een
upload, dan wordt de mail **niet** geweigerd: de fout staat in `logs`.
Let op: share-links zijn publiek leesbaar tot ze verlopen.

---

## Optionele sturing: `X-Pager-*`

Wil je urgentie, project of job-id per mail zetten? Dat kan **op twee manieren,
met dezelfde sleutels** — als mailheader of als blok bovenaan de body. Het
body-blok wint (het overleeft doorsturen en clients die custom headers strippen).

### Als header

```
X-Pager-Prio: critical
X-Pager-Project: CI
X-Pager-Job-Id: deploy-42
```

### Als body-blok

```
X-Pager-Prio: critical
X-Pager-Project: CI
X-Pager-Job-Id: deploy-42
X-Pager-Tags: deploy,prod

Vanaf hier is het de gewone berichttekst.
```

Het blok moet bovenaan staan, eindigt bij de eerste regel die geen
`X-Pager-*` is (max 25 regels) en verdwijnt uit de body.

### Sleutels

| Sleutel | Waarden | Effect |
|---|---|---|
| `X-Pager-Status` | `0`-`5` of `success`/`warning`/`failed`/`critical`/`running`/`queued` | status van de page |
| `X-Pager-Prio` | `debug` \| `info` \| `warning` \| `alarm` \| `critical` | wint van `status` |
| `X-Pager-Project` | tekst (max 64) | overschrijft `PROJECT` |
| `X-Pager-Job-Id` | tekst (max 200) | overschrijft `JOB_ID` |
| `X-Pager-Silent` | `1`/`true`/`yes`/`on` | geen notificatie (`prio: info`) |
| `X-Pager-Tags` | kommagescheiden (max 10, elk 32) | extra veld `tags[]` |

Onbekende sleutels of waarden worden genegeerd en als notitie in `logs` gezet.

### Veiligheid: alleen vertrouwde afzenders mogen escaleren

Een MX accepteert mail van iedereen, dus `X-Pager-*` is **niet vertrouwd**. Zet
daarom een allowlist in `PAGER_TRUSTED_SENDERS` (kommagescheiden e-mailadressen
en/of domeinen):

```sh
PAGER_TRUSTED_SENDERS=ops@example.net,@example.org,example.com
```

- **Vertrouwd**: alle waarden worden toegepast.
- **Niet vertrouwd**: `status` mag alleen verlaagd worden (max `1`/warning) en
  `prio` wordt geclamped naar `warning`; `critical`/`alarm` worden geweigerd.
  `project`, `job-id`, `tags` en `silent` blijven werken (onschuldig).
- Elke clamp/afwijzing staat als notitie in de page-body, zodat je ziet wat
  genegeerd is.

Zonder allowlist kan een willekeurige afzender dus geen alarm afdwingen.

---

## Wat een mail **niet** kan

| Functie | Waarom niet |
|---|---|
| ACK van een page | vereist `ack_uuid` en het native ACK-profiel |
| Chat / foto's / contacten | ander protocol (`from`, `mid`, `pub`, `enc`) |
| Versleutelde body | vereist de publieke sleutel van de ontvanger |
| Reply-mailbox | vereist `reply_uuid` + `ack_token` |

Een mail is een **one-way page**: lezen, niet antwoorden.

---

## Grenzen

- PagerMCP: 64 KiB per request, 128 pagina's per mailbox, 7 dagen retentie.
- SMTP: `MAX_MESSAGE_BYTES` (headers+tekst), `MAX_ATTACHMENT_BYTES` (100 MB),
  `MAX_ATTACHMENTS` (10), `MAX_TOTAL_BYTES` (~100 MB) — allemaal env-instelbaar.
- Te groot bericht → `550 message too large`; te grote bijlage → `550 attachment
  too large`; te veel bijlagen → `550`.
- Pager weigert (4xx) → `550`; pager vol of 5xx/netwerk → `451` (retry).

## Zie ook

- [RFC-PagerSMPTd-0001 — mail → page mapping](rfc-0001-mail-mapping.md)
- [Bastille (FreeBSD)](../bastille/README.md)
- [Podman](../podman/README.md) (Linux; niet native op FreeBSD)
- [FreeBSD port-skeleton](../ports/mail/pagersmtpd/)
- [Salt-runbook voor node-onderhoud](../deploy/salt/README.md)
