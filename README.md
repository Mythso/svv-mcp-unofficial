# SVV-MCP-unofficial

> ⚠️ **Uoffisielt prosjekt.** Dette er en community-bygget MCP-server og er
> **ikke** laget av, driftet av, eller tilknyttet Statens vegvesen (SVV) på
> noen måte. "SVV" i navnet beskriver kun hvilken datakilde serveren bruker.
> Dataene som formidles er hentet fra Statens vegvesens egne, åpne
> DATEX II-endepunkter, og bruk er underlagt deres vilkår (se
> [Lisens](#lisens)).

MCP-server (Model Context Protocol) som gir en LLM tilgang til Statens vegvesens
åpne sanntidsdata via [DATEX II](https://www.vegvesen.no/en/fag/technology/open-data/a-selection-of-open-data/what-is-datex/):

- 🌡️ **Værmålinger** fra vegvesenets værstasjoner (vegbanetemp, lufttemp, vind, duggpunkt), med automatisk varsel om glatt vegbane
- 🌦️ **Værprognoser** (time for time, 24t frem)
- 📷 **Webkamera** langs vegnettet
- 🚗 **Reisetider** i sanntid på hovedstrekninger
- ⚠️ **Trafikkmeldinger** (ulykker, vegarbeid, stenginger, ras/flom m.m.)
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
4. Deploy på nytt. `/mcp` krever nå OAuth; `/login` er den selv-hostede innloggingssiden.

### Teste OAuth-flyten før du deler URL-en videre

Dette er den mest komplekse biten av prosjektet og bør testes én gang manuelt:

```bash
npx @modelcontextprotocol/inspector
```

Pek Inspector mot `https://<ditt-domene>/mcp`, velg autentisering, og gå
gjennom hele flyten (blir sendt til `/login`, fyller inn testverdier, sendes
tilbake, verktøykall fungerer). Sjekk Railway-loggene hvis noe feiler —
autorisasjonsserver-koden i `auth.py` bruker SDK-ets
`OAuthAuthorizationServerProvider`-grensesnitt, som kan ha avvikende feltnavn
mellom SDK-versjoner; loggene vil vise nøyaktig hvilken metode/felt som
eventuelt ikke stemmer.

## Deploy til Railway

**Alternativ A — via Railway-dashbordet (enklest):**

1. Logg inn på [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**.
2. Velg `svv-mcp-unofficial`-repoet ditt. Railway finner `Dockerfile` automatisk.
3. Gå til **Variables** på servicen og legg inn variablene for ønsket modus
   (se tabellen over).
4. Railway setter `PORT` automatisk — serveren starter da i Streamable
   HTTP-modus og lytter på `0.0.0.0:$PORT`.
5. Under **Settings → Networking**, trykk **Generate Domain** for å få en
   offentlig URL (f.eks. `https://svv-mcp-unofficial-production.up.railway.app`).
6. MCP-endepunktet blir da: `https://<ditt-domene>/mcp`

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
> eksterne MCP-servere i dagens spesifikasjon.

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
der (siden viser også hvordan du skaffer det hvis du ikke har det ennå).

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
| `inspiser_datex_publikasjon` | Feilsøking: viser rå tag-struktur fra et gitt DATEX-endepunkt |

Alle "hent"-verktøyene tar de samme parameterne:

- `sokeord` (valgfritt) — filtrerer på alle feltverdier, f.eks. `"E18"`, `"Hemsedal"`
- `maks_antall` (standard 10) — maks antall poster i svaret
- `format` — `"markdown"` (lesbart) eller `"json"` (strukturert)

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
- **OAuth-feil ved tilkobling (multi-bruker-modus)** → se Railway-loggene og
  seksjonen "Teste OAuth-flyten" over.

## Lisens

Dataene er tilgjengelige under [Norsk lisens for offentlige data (NLOD)](https://data.norge.no/nlod/en/).
Oppgi Statens vegvesen som kilde ved bruk av dataene, slik NPRA ber om.
