# RFC-PagerSMPTd-0001 — Wat je met een mail kunt sturen (mail → page mapping)

Status: draft · Versie: 0.7.0 · Gerelateerd: PagerAPP `docs/rfc-gateway.md` (RFC-PagerAPP-0003)

## 1. Samenvatting

**Eén gewone mail is genoeg.** Er zijn geen speciale headers, markers of een
bepaald formaat nodig: onderwerp, tekst en bijlagen worden automatisch omgezet
naar een PagerAPP-page volgens RFC-PagerAPP-0003.

Optionele sturing (urgentie, project, job-id) kan later via `X-Pager-*`-headers.
Die zijn **niet geïmplementeerd** in 0.7.0 en staan in §6 als voorstel, met een
belangrijke veiligheidskanttekening: headers zijn door de afzender bepaald en op
een publieke MX dus door iedereen te zetten.

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

## 6. Voorstel: optionele sturing per mail (`X-Pager-*`)

Niet geïmplementeerd. Bedoeld voor afzenders die méér willen dan de defaults.

| Header | Voorbeeld | Effect |
|---|---|---|
| `X-Pager-Status` | `1` \| `warning` \| `2` \| `failed` \| `3` \| `critical` | urgency/status van de page |
| `X-Pager-Prio` | `info` \| `warning` \| `alarm` \| `critical` | wint van `status` (RFC-PagerAPP-0002) |
| `X-Pager-Project` | `CI`, `ProLOG` | overschrijft `PROJECT` |
| `X-Pager-Job-Id` | `deploy-1234` | overschrijft `JOB_ID` (anders Message-ID) |
| `X-Pager-Silent` | `1` | geen notificatie (page blijft in de lijst) |
| `X-Pager-Tags` | `deploy,prod` | extra veld `tags[]` (clients negeren het) |

Volgorde: header > default. Onbekende waarden → negeren (default gebruiken).

### Veiligheid (belangrijk)

Een MX accepteert mail van iedereen; headers zijn dus **niet vertrouwd**. Zonder
maatregelen kan een willekeurige afzender `X-Pager-Prio: critical` zetten en
's nachts een alarm afdwingen.

Voorstel:

1. **Standaard**: `X-Pager-*` alleen toepassen als de afzender in een
   allowlist staat (`PAGER_TRUSTED_SENDERS`, bv. e-mailadressen/domeinen of
   CIDR's), of als de mail via een vertrouwde relay binnenkomt.
2. **Zonder allowlist**: alleen "verlagende" waarden toestaan (bv. `info`),
   nooit verhogen boven `warning`. `critical`/`alarm` alleen vertrouwd.
3. `X-Pager-Silent` en `X-Pager-Tags` zijn onschuldig en kunnen altijd.
4. Alles loggen wat een header heeft overschreven (audit trail).

Alternatief zonder headers: een onderwerpprefix (`[CRITICAL] …`) — minder
betrouwbaar (wordt door clients/forwarding aangepast) en even goed
afzender-bepaald, dus dezelfde allowlist nodig.

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
| `X-Pager-*` (§6) | voorstel |
