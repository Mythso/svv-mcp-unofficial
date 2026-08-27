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

export $(grep -v '^#' .env | xargs)  # eller bruk python-dotenv
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

## Deploy til Railway

**Alternativ A — via Railway-dashbordet (enklest):**

1. Logg inn på [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**.
2. Velg `svv-mcp-unofficial`-repoet ditt. Railway finner `Dockerfile` automatisk.
3. Gå til **Variables** på servicen og legg inn:
   - `DATEX_USERNAME`
   - `DATEX_PASSWORD`
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

Fordi `DATEX_USERNAME`/`DATEX_PASSWORD` er satt som miljøvariabler på
Railway-servicen (ikke som parametere i verktøyene), trenger ikke den som
kobler seg til å oppgi noen legitimasjon selv — kun eieren av
Railway-deploymentet.

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
  feil på serveren. Sjekk Railway-variablene.
- **Tomme/uventede resultater ** → kjør `inspiser_datex_publikasjon` mot den
  aktuelle publikasjonen (se tabellen over `DATEX_ENDPOINTS`-nøkler i
  `server.py`) for å se nøyaktig hvilke XML-tagger NPRA sender akkurat nå,
  og juster `candidate_tags` i det aktuelle verktøyet ved behov.
- **Timeout** → NPRAs DATEX-node kan være midlertidig nede; prøv igjen om
  litt, eller sjekk driftsmeldinger på vegvesen.no.

## Lisens

Dataene er tilgjengelige under [Norsk lisens for offentlige data (NLOD)](https://data.norge.no/nlod/en/).
Oppgi Statens vegvesen som kilde ved bruk av dataene, slik NPRA ber om.
