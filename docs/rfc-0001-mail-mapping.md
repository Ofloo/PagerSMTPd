# RFC-PagerSMPTd-0001 — Wat je met een mail kunt sturen (mail → page mapping)

Status: draft · Versie: 0.7.0 · Gerelateerd: PagerAPP `docs/rfc-gateway.md` (RFC-PagerAPP-0003)

## 1. Samenvatting

**Eén gewone mail is genoeg.** Er zijn geen speciale headers, markers of een
bepaald formaat nodig: onderwerp, tekst en bijlagen worden automatisch omgezet
naar een PagerAPP-page volgens RFC-PagerAPP-0003.

Optionele sturing (urgentie, project, job-id) kan via `X-Pager-*`, als
mailheader **of** als blok bovenaan de body (geïmplementeerd sinds 0.8.0). Op een
publieke MX is die sturing door de afzender bepaald, dus urgentie wordt alleen
verhoogd voor afzenders in `PAGER_TRUSTED_SENDERS`; zie §6.

## 2. Automatische mapping (geïmplementeerd)

| Mail | Page-veld | Regel |
|---|---|---|
| `Subject` | `message` | leeg → eerste 120 tekens van de body → `"(no subject)"` |
| tekstbody (`text/plain`) | `logs` | opgeschoond, max 8000 tekens |
| HTML-body | `logs` | alleen als er geen bruikbare `text/plain` is; tags/scripts gestript |
| doorgestuurde mail (`message/rfc822`) | `logs` | de *binnenste* mail wordt gebruikt, niet de wrapper |
| bijlagen | `attachments[]` | HTTPS `PUT` naar `SHARE_URL`, URL's in het payload |
| `Message-ID` | `JOB_ID` | correlatie |
| afzender (`From`) | — | niet in het payload (`from` is voor chat-sender-ids) |
| — | `status` | altijd `1` (warning: nieuwe mail) |
| — | `PROJECT` | `PAGER_PROJECT` (default `smtp`) |
| — | `rcpt`, `smtpd_version`, `source` | informatieve extra velden |

Voorbeeld — dit is alles wat een afzender hoeft te doen:

```
To:      <uuid>@pager.ofloo.io
Subject: Deployment failed
Body:    De job stopte met exitcode 2.
```

## 3. Body-extractie (volgorde)

1. Als de mail een andere mail *bevat* (`message/rfc822`), gebruik die binnenste
   mail. Dit dekt spam/virus-wrappers (SpamAssassin) en "Forward as attachment".
2. Verzamel alle `text/plain`-delen (niet zijnde bijlagen). Niet-leeg → gebruik.
3. Anders: `text/html` omzetten naar platte tekst (`<br>`/`</p>` → newline, tags
   weg, `&nbsp;` → spatie, scripts/styles/head verwijderd).
4. Opschonen: transport-/spamheaders (`Received`, `DKIM-Signature`,
   `X-Spam-*`, `Authentication-Results`, `Autocrypt`, …; ook NL/Duitse/Franse
   headernamen), SpamAssassin-rapport, regels > 300 tekens (base64-blobs),
   opeenvolgende lege regels.
5. Afkappen op 8000 tekens (`logs`); `message`-fallback op 120 tekens.

Niet gedaan (bewust): geciteerde geschiedenis (`> ...`) en handtekeningen
strippen. Dat vereist heuristieken die echte tekst kunnen wegsnijden.

## 4. Bijlagen

- Elke part met `Content-Disposition: attachment` of een `filename`, behalve
  `text/*` en `message/rfc822`.
- Wordt geüpload naar `SHARE_URL` (default `https://share.h4ck.io`) met een
  unieke, veilige naam; de geretourneerde URL komt in `attachments[]`.
- Limieten: `MAX_ATTACHMENT_BYTES` (default 100 MB) per bestand,
  `MAX_ATTACHMENTS` (default 10), `MAX_TOTAL_BYTES` (default ~100 MB) voor de
  hele DATA.
- Mislukt een upload, dan wordt de mail **niet** geweigerd: de fout staat in
  `logs` en `attachments[]` blijft leeg voor dat bestand.
- Let op: share-links zijn publiekelijk leesbaar tot ze verlopen; zip/
  versleutel gevoelige inhoud zelf.

## 5. Wat een mail niet kan (nu)

Deze PagerAPP-functies hebben een expliciet protocol nodig en worden door de
SMTP-bridge niet ondersteund:

| Functie | Waarom niet |
|---|---|
| ACK van een page | vereist `ack_uuid` en het native ACK-profiel (RFC-PagerAPP-0002 §6) |
| Chat / foto's / contacten | ander protocol (`from`, `mid`, `pub`, `enc`) |
| Versleutelde body | vereist de publieke sleutel van de ontvanger |
| Reply-mailbox | vereist `reply_uuid` + `ack_token` |

Een mail is dus altijd een "one-way page": lezen, niet antwoorden.

## 6. Optionele sturing per mail (`X-Pager-*`) — geïmplementeerd

Twee manieren, met dezelfde sleutels:

1. **Mailheader** — `X-Pager-Prio: critical`
2. **Body-blok** — de eerste regels van de body:

```
X-Pager-Prio: critical
X-Pager-Project: CI
X-Pager-Job-Id: deploy-42
X-Pager-Tags: deploy,prod

Vanaf hier is het de gewone berichttekst.
```

Het body-blok eindigt bij de eerste regel die geen `X-Pager-*` is (max 25
regels) en wordt uit de body verwijderd. **Body wint van headers** (handig: het
overleeft doorsturen en clients die custom headers strippen).

| Sleutel | Waarden | Effect |
|---|---|---|
| `X-Pager-Status` | `0`-`5` of `success`/`warning`/`failed`/`critical`/`running`/`queued` | status van de page |
| `X-Pager-Prio` | `debug` \| `info` \| `warning` \| `alarm` \| `critical` | wint van `status` |
| `X-Pager-Project` | tekst (max 64) | overschrijft `PROJECT` |
| `X-Pager-Job-Id` | tekst (max 200) | overschrijft `JOB_ID` |
| `X-Pager-Silent` | `1`/`true`/`yes`/`on` | geen notificatie (`prio: info`) |
| `X-Pager-Tags` | kommagescheiden (max 10, elk 32) | extra veld `tags[]` |

Onbekende sleutels/waarden worden genegeerd en als notitie in `logs` gezet.

### Veiligheid

Een MX accepteert mail van iedereen; `X-Pager-*` is dus **niet vertrouwd**.

- `PAGER_TRUSTED_SENDERS` = kommagescheiden lijst van e-mailadressen en/of
  domeinen (bv. `ops@example.net,@example.org,example.com`). Leeg = niemand.
- **Vertrouwd**: alle waarden worden toegepast.
- **Niet vertrouwd**: `status` mag alleen verlaagd worden (max `1`/warning) en
  `prio` wordt geclamped naar `warning`; `critical`/`alarm` worden geweigerd.
  `project`, `job-id`, `tags` en `silent` zijn onschuldig en blijven werken.
- Elke clamp/afwijzing staat als notitie in de page-body (`logs`), zodat de
  ontvanger ziet wat er genegeerd is.

Zonder allowlist kan een willekeurige afzender dus geen alarm afdwingen.

## 7. Grenzen

- PagerMCP: 64 KiB per request, 128 pagina's per mailbox, 7 dagen retentie.
- SMTP: `MAX_MESSAGE_BYTES` (headers+tekst), `MAX_ATTACHMENT_BYTES`,
  `MAX_ATTACHMENTS`, `MAX_TOTAL_BYTES`.
- `message` hoort kort te zijn (titel); `logs` is de detailtekst.

## 8. Status

| Onderdeel | Status |
|---|---|
| Automatische mapping (§2) | geïmplementeerd sinds 0.6.x, RFC-conform sinds 0.6.12 |
| Body-extractie + opschonen (§3) | geïmplementeerd |
| Bijlagen (§4) | geïmplementeerd |
| `X-Pager-*` (§6), header + body-blok, allowlist | geïmplementeerd sinds 0.8.0 |
