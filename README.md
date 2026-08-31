# SVV-MCP-unofficial

> ⚠️ **Uoffisielt prosjekt.** Dette er en community-bygget MCP-server og er
> **ikke** laget av, driftet av, eller tilknyttet Statens vegvesen (SVV) på
> noen måte. "SVV" i navnet beskriver kun hvilken datakilde serveren bruker.
> Dataene som formidles er hentet fra Statens vegvesens egne, åpne
> DATEX II-endepunkter, og bruk er underlagt deres vilkår (se
> [Lisens](#lisens)).

✅ **Status:** Kjører live på Railway med multi-bruker OAuth-innlogging,
verifisert fungerende ende-til-ende (discovery → registrering → autorisasjon
→ innlogging → token → autentiserte verktøykall) mot en ekte Claude.ai-tilkobling.

MCP-server (Model Context Protocol) som gir en LLM tilgang til Statens vegvesens
åpne sanntidsdata via [DATEX II](https://www.vegvesen.no/en/fag/technology/open-data/a-selection-of-open-data/what-is-datex/):

- 🌡️ **Værmålinger** fra vegvesenets værstasjoner (vegbanetemp, lufttemp, vind, duggpunkt), med automatisk varsel om glatt vegbane
- 🌦️ **Værprognoser** (time for time, 24t frem)
- 📷 **Webkamera** langs vegnettet
- 🚗 **Reisetider** i sanntid på hovedstrekninger
- ⚠️ **Trafikkmeldinger** (ulykker, vegarbeid, stenginger, ras/flom m.m.)
- 🌍 **Åpen vegdata fra NVDB** (fartsgrenser, rasteplasser, ÅDT m.m.) — krever ikke DATEX-innlogging
- 🔧 Et innebygd feilsøkingsverktøy som viser rå XML-struktur, siden NPRAs skjema kan endre seg

Alle DATEX-endepunktene krever registrert bruker hos Statens vegvesen (gratis).
[Be om tilgang her →](https://www.vegvesen.no/en/fag/technology/open-data/a-selection-of-open-data/what-is-datex/get-access/)

---

## Innhold

- [Kom i gang lokalt](#kom-i-gang-lokalt)
- [Sette opp GitHub-repoet](#sette-opp-github-repoet)
- [Innloggingsmodus: delt konto vs. per bruker](#innloggingsmodus-delt-konto-vs-per-bruker)
- [Deploy til Railway](#deploy-til-railway)
- [Koble til serveren](#koble-til-serveren)
  - [Claude.ai / Claude-appen (custom connector)](#claudeai--claude-appen-custom-connector)
  - [Claude Desktop (stdio, lokalt)](#claude-desktop-stdio-lokalt)
  - [Andre MCP-klienter](#andre-mcp-klienter)
- [Tilgjengelige verktøy](#tilgjengelige-verktøy)
- [Feilsøking](#feilsøking)
- [Kjente løste bugs (for de som forker prosjektet)](#kjente-løste-bugs-for-de-som-forker-prosjektet)

---

## Kom i gang lokalt

Krever Python 3.10+.

```bash
git clone https://github.com/<ditt-brukernavn>/svv-mcp-unofficial.git
cd svv-mcp-unofficial
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# rediger .env og fyll inn DATEX_USERNAME / DATEX_PASSWORD

export $(grep -v '^#' .env | xargs)   # eller bruk python-dotenv
python server.py
```

Uten `PORT` satt i miljøet starter serveren over **stdio** — praktisk for lokal
testing med [MCP Inspector](https://github.com/modelcontextprotocol/inspector):

```bash
npx @modelcontextprotocol/inspector python server.py
```

## Sette opp GitHub-repoet

1. Opprett et nytt (tomt) repo på GitHub, f.eks. `svv-mcp-unofficial`.
2. Push koden:

   ```bash
   cd svv-mcp-unofficial
   git init
   git add .
   git commit -m "Initial commit: svv-mcp-unofficial server"
   git branch -M main
   git remote add origin https://github.com/<ditt-brukernavn>/svv-mcp-unofficial.git
   git push -u origin main
   ```

3. **Viktig:** `.env` er allerede i `.gitignore` — dobbeltsjekk at du aldri
   committer faktiske DATEX-brukernavn/passord. Legg heller inn
   `.env.example` (uten verdier), som allerede ligger i repoet.

## Innloggingsmodus: delt konto vs. per bruker

Serveren støtter to modi, styrt av om `SVV_MCP_PUBLIC_URL` er satt:

| | Delt konto | Multi-bruker (OAuth) |
|---|---|---|
| Miljøvariabler | `DATEX_USERNAME` / `DATEX_PASSWORD` | `SVV_MCP_PUBLIC_URL`, `CREDENTIAL_ENCRYPTION_KEY` |
| Hvem sin DATEX-konto brukes | Din, delt av alle | Hver brukers egen |
| Onboarding for nye brukere | Ingen | Logger inn med eget DATEX-passord første gang |
| Krever Railway-volum | Nei | Ja (lagrer krypterte credentials) |

**Multi-bruker-modus** lar deg dele én Railway-URL med andre uten at de trenger
egen server: når noen legger til connectoren i Claude, sendes de til en
innloggingsside serveren selv hoster (`/login`), hvor de limer inn sitt eget
DATEX-brukernavn/passord og ser en lenke for hvordan de skaffer det. Credentials
lagres kryptert (Fernet) i en SQLite-fil på et Railway-volum, koblet til en
token MCP-serveren utsteder til Claude.

Siden vår kjører for tiden i multi-bruker-modus på
`https://svv-mcp-unofficial-production.up.railway.app`.

### Sette opp multi-bruker-modus på Railway

1. Legg til et **volum** på servicen, montert på `/data` (Railway-dashbord:
   **Settings → Volumes → New Volume**, mount path `/data`).
2. Generer en krypteringsnøkkel:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
3. Sett variablene på servicen:
   - `SVV_MCP_PUBLIC_URL` = din Railway-URL (f.eks. `https://svv-mcp-unofficial-production.up.railway.app`)
   - `CREDENTIAL_ENCRYPTION_KEY` = nøkkelen fra steg 2
   - `SVV_MCP_DB_PATH` = `/data/svv_mcp.db`
   - **Ikke** sett `DATEX_USERNAME`/`DATEX_PASSWORD` i denne modusen
4. Sett `dockerfilePath` til `Dockerfile` i service-innstillingene (Railway
   autodetekterer noen ganger Railpack i stedet — sjekk **Settings → Build**).
5. Deploy på nytt. `/mcp` krever nå OAuth; `/login` er den selv-hostede innloggingssiden.

### DNS-navn for DATEX-registrering

Når du (eller en bruker) registrerer seg for DATEX-tilgang hos Statens vegvesen,
spør skjemaet om en **"Fixed IP address or DNS name"**. Railway gir ikke en fast
utgående IP som standard (Static Outbound IP krever Pro-plan og er i tillegg en
delt IP mellom flere Railway-kunder). Bruk derfor **DNS-navnet** til Railway-
servicen din, f.eks.:

```
svv-mcp-unofficial-production.up.railway.app
```

Dette står også forklart direkte på `/login`-siden når man ikke har DATEX-tilgang ennå.

### Teste OAuth-flyten før du deler URL-en videre

```bash
npx @modelcontextprotocol/inspector
```

Pek Inspector mot `https://<ditt-domene>/mcp`, velg autentisering, og gå
gjennom hele flyten. Hele kjeden (discovery → DCR → `/authorize` → `/login` →
token-utveksling → autentiserte verktøykall) er verifisert å fungere både med
`curl`-simulering og en reell Claude.ai-tilkobling. Sjekk Railway-loggene
(`types: ["http"]`, snevert tidsvindu) hvis noe feiler — se
[Kjente løste bugs](#kjente-løste-bugs-for-de-som-forker-prosjektet) for de
vanligste fallgruvene i `auth.py`.

## Deploy til Railway

**Alternativ A — via Railway-dashbordet (enklest):**

1. Logg inn på [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**.
2. Velg `svv-mcp-unofficial`-repoet ditt.
3. Under **Settings → Build**, sett **Builder** til **Dockerfile** eksplisitt
   (Railway velger noen ganger Railpack automatisk, som ikke setter opp
   `/data`-mappen riktig for SQLite-volumet).
4. Gå til **Variables** på servicen og legg inn variablene for ønsket modus
   (se tabellen over).
5. Railway setter `PORT` automatisk — serveren starter da i Streamable
   HTTP-modus og lytter på `0.0.0.0:$PORT`.
6. Under **Settings → Networking**, trykk **Generate Domain** for å få en
   offentlig URL (f.eks. `https://svv-mcp-unofficial-production.up.railway.app`).
7. MCP-endepunktet blir da: `https://<ditt-domene>/mcp`

**Alternativ B — via Railway CLI:**

```bash
npm i -g @railway/cli
railway login
railway init
railway up
railway variables --set "DATEX_USERNAME=..." --set "DATEX_PASSWORD=..."
railway domain
```

> Serveren bruker Streamable HTTP (ikke SSE), som er anbefalt transport for
> eksterne MCP-servere i dagens spesifikasjon. Merk: den installerte
> SDK-versjonen forventer transport-strengen `"streamable-http"` med
> bindestrek, ikke understrek — dette er allerede rettet i `server.py`.

## Koble til serveren

### Claude.ai / Claude-appen (custom connector)

1. Gå til **Settings → Connectors → Add custom connector**.
2. Lim inn Railway-URL-en din, f.eks.
   `https://<ditt-domene>/mcp`
3. Gi den et navn, f.eks. "Statens vegvesen".
4. Slå den på i samtalen din under verktøy/connectors.

**Delt-konto-modus:** ingen ekstra steg — `DATEX_USERNAME`/`DATEX_PASSWORD`
ligger på serveren, ikke i samtalen.

**Multi-bruker-modus:** Claude sender deg automatisk til `/login`-siden på
serveren første gang du kobler til. Fyll inn ditt eget DATEX-brukernavn/passord
der (siden viser også hvordan du skaffer det hvis du ikke har det ennå, hvilket
DNS-navn du trenger i søknadsskjemaet, og hvilke verktøy DATEX-tilgang faktisk
gir deg). Vil du ikke skaffe DATEX-tilgang, kan du velge «Fortsett uten konto»
— da får du kun `hent_apen_vegdata` (NVDB, ingen pålogging nødvendig).

### Claude Desktop (stdio, lokalt)

Legg til i `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "vegvesen": {
      "command": "python",
      "args": ["/full/path/til/svv-mcp-unofficial/server.py"],
      "env": {
        "DATEX_USERNAME": "...",
        "DATEX_PASSWORD": "..."
      }
    }
  }
}
```

(Multi-bruker-modus krever HTTP og fungerer ikke over stdio.)

### Andre MCP-klienter

Enhver klient som støtter Streamable HTTP kan peke direkte på
`https://<ditt-domene>/mcp`.

## Tilgjengelige verktøy

| Verktøy | Beskrivelse |
|---|---|
| `hent_vaermalinger` | Sanntids værmålinger fra vegstasjoner, med varsel om glatt vegbane |
| `hent_vaerstasjoner` | Plassering (koordinater/veg/navn) av værstasjoner |
| `hent_vaerprognose` | Værprognose time for time, 24t frem |
| `hent_vaerprognose_punkter` | Plassering av prognosepunkter |
| `hent_webkamera` | Oversikt over webkamera langs vegnettet |
| `hent_reisetider` | Sanntids reisetider på hovedstrekninger |
| `hent_reisetid_strekninger` | Geometri/navn på strekninger det måles reisetid på |
| `hent_trafikkmeldinger` | Ulykker, vegarbeid, stenginger, ras/flom m.m. |
| `hent_apen_vegdata` | Åpen vegdata fra NVDB (fartsgrenser, rasteplasser, ÅDT m.m.) — krever **ikke** DATEX-innlogging |
| `inspiser_datex_publikasjon` | Feilsøking: viser rå tag-struktur fra et gitt DATEX-endepunkt |

Alle "hent"-verktøyene tar de samme parameterne:

- `sokeord` (valgfritt) — filtrerer på alle feltverdier, f.eks. `"E18"`, `"Hemsedal"`
- `maks_antall` (standard 10) — maks antall poster i svaret
- `format` — `"markdown"` (lesbart) eller `"json"` (strukturert)

`hent_apen_vegdata` tar i tillegg `vegobjekttype` (påkrevd, f.eks. 105 for
fartsgrense), samt valgfri `fylke`/`kommune`.

## Feilsøking

- **"401 Uautorisert"** → `DATEX_USERNAME`/`DATEX_PASSWORD` mangler eller er
  feil på serveren (delt-konto-modus), eller brukeren må koble til på nytt for
  å logge inn (multi-bruker-modus).
- **Tomme/uventede resultater** → kjør `inspiser_datex_publikasjon` mot den
  aktuelle publikasjonen (se tabellen over `DATEX_ENDPOINTS`-nøkler i
  `server.py`) for å se nøyaktig hvilke XML-tagger NPRA sender akkurat nå,
  og juster `candidate_tags` i det aktuelle verktøyet ved behov.
- **Timeout** → NPRAs DATEX-node kan være midlertidig nede; prøv igjen om
  litt, eller sjekk driftsmeldinger på vegvesen.no.
- **"Authorization with [server] failed" i Claude.ai, ingen feil i Railway-loggene**
  → sjekk om `/authorize` redirecter rett tilbake til klientens `redirect_uri`
  med `error=invalid_scope` i stedet for til `/login`. Se
  [Kjente løste bugs](#kjente-løste-bugs-for-de-som-forker-prosjektet) under —
  dette var årsaken til akkurat denne feilen for oss, og er allerede rettet i
  koden, men er verdt å vite om ved videre endringer i `auth.py`.

## Kjente løste bugs (for de som forker prosjektet)

Disse ble funnet under uttesting mot en ekte Claude.ai-tilkobling og er
allerede rettet i koden, men er dokumentert her siden de er lette å
gjeninnføre ved videre arbeid på `auth.py`/`server.py`:

1. **`invalid_scope`-feil rett til klientens redirect_uri, aldri innom `/login`**
   `DatexAuthProvider.get_client()`/`register_client()` lagret ikke `scope`
   for registrerte OAuth-klienter. Når Claude ba om `scope=datex` i
   `/authorize`, validerte SDK-en dette mot klientens (tomme) registrerte
   scope og avviste umiddelbart — uten å noensinne kalle vår egen
   `authorize()`-metode. Løsning: lagre og returner `scope` i
   `oauth_clients`-tabellen.
2. **`token_endpoint_auth_method` manglet på klienter** → `/token` svarte
   `"Unsupported auth method: None"` for alle klienter. Løsning: lagre og
   returner feltet fra DCR-registreringen.
3. **Transport-streng** → installert SDK-versjon krever `"streamable-http"`
   (bindestrek), ikke `"streamable_http"` (understrek), i `mcp.run(transport=...)`.
4. **NVDB-kall manglet `inkluder=alle`** → `hent_apen_vegdata` returnerte kun
   href-referanser, ikke faktiske egenskaper (fartsgrenseverdi osv.).

## Lisens

Dataene er tilgjengelige under [Norsk lisens for offentlige data (NLOD)](https://data.norge.no/nlod/en/).
Oppgi Statens vegvesen som kilde ved bruk av dataene, slik NPRA ber om.
