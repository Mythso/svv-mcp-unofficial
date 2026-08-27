"""
SVV-MCP-unofficial — MCP-server for Statens vegvesens åpne DATEX II-data.

⚠️ Dette er et uoffisielt, community-drevet prosjekt. Det er IKKE laget,
driftet av, eller tilknyttet Statens vegvesen (SVV). Bruk av navnet "SVV" er
kun beskrivende for hvilken datakilde serveren henter fra.

Dekker publikasjonene:
  - Værdata (målinger + målestasjoner)
  - Værprognoser (prognosedata + prognosepunkter)
  - Webkamera (CCTV)
  - Reisetider (målinger + strekninger)
  - Trafikkmeldinger / hendelser (situations)

Alle DATEX-endepunktene krever registrert bruker hos Statens vegvesen:
https://www.vegvesen.no/en/fag/technology/open-data/a-selection-of-open-data/what-is-datex/get-access/

Brukernavn/passord leses fra miljøvariablene DATEX_USERNAME / DATEX_PASSWORD
(satt én gang ved oppstart av serveren — sendes ALDRI som verktøy-parametere,
slik at ingen legitimasjon havner i samtalen med språkmodellen).
"""

import os
import json
import xml.etree.ElementTree as ET
from collections import Counter
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

import auth as datex_auth

# --------------------------------------------------------------------------
# Oppsett
# --------------------------------------------------------------------------

CLIENT_NAME = "svv-mcp-unofficial/1.0 (+https://github.com/; uoffisiell klient, ikke tilknyttet Statens vegvesen)"
REQUEST_TIMEOUT = 30.0

DATEX_ENDPOINTS: Dict[str, str] = {
    "weather_measurements": "https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetMeasuredWeatherData/pullsnapshotdata",
    "weather_sites": "https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetMeasurementWeatherSiteTable/pullsnapshotdata",
    "weather_forecast": "https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetForecastPointData/pullsnapshotdata",
    "weather_forecast_locations": "https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetForecastPointLocations/pullsnapshotdata",
    "webcams": "https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetCCTVSiteTable/pullsnapshotdata",
    "travel_times": "https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetTravelTimeData/pullsnapshotdata",
    "travel_time_locations": "https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetPredefinedTravelTimeLocations/pullsnapshotdata",
    "traffic_situations": "https://datex-server-get-v3-1.atlas.vegvesen.no/datexapi/GetSituation/pullsnapshotdata",
}

REGISTER_URL = "https://www.vegvesen.no/en/fag/technology/open-data/a-selection-of-open-data/what-is-datex/get-access/"

# NVDB (Nasjonal vegdatabank) er, i motsetning til DATEX, i hovedsak åpent
# tilgjengelig UTEN pålogging. Brukes for "fortsett uten konto"-sporet.
NVDB_BASE_URL = "https://nvdbapiles.atlas.vegvesen.no"
NVDB_CLIENT_HEADER = "svv-mcp-unofficial"

PUBLIC_URL = os.environ.get("SVV_MCP_PUBLIC_URL", "").rstrip("/")
MULTI_USER_AUTH = bool(PUBLIC_URL)  # Slått på når serveren er deployet med offentlig URL

if MULTI_USER_AUTH:
    datex_auth.init_db()
    _provider = datex_auth.DatexAuthProvider()
    mcp = FastMCP(
        "svv_mcp_unofficial",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        auth_server_provider=_provider,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(PUBLIC_URL),
            resource_server_url=AnyHttpUrl(f"{PUBLIC_URL}/mcp"),
            client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=["datex"], default_scopes=["datex"]),
        ),
    )
else:
    mcp = FastMCP(
        "svv_mcp_unofficial",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
    )


class DatexApiError(Exception):
    """Brukervennlig feil ved kall mot DATEX-tjenesten."""


# --------------------------------------------------------------------------
# HTTP / henting
# --------------------------------------------------------------------------

async def _fetch_datex_xml(endpoint_key: str) -> str:
    url = DATEX_ENDPOINTS[endpoint_key]

    if MULTI_USER_AUTH:
        token = get_access_token()
        if token is None:
            raise DatexApiError("Ikke innlogget. Koble til denne serveren på nytt fra Claude for å logge inn med ditt DATEX-brukernavn/passord.")
        creds = datex_auth.load_datex_credentials_for_token(token.token)
        if not creds:
            raise DatexApiError("Fant ingen lagrede DATEX-credentials for denne tilkoblingen. Koble til på nytt for å logge inn.")
        username, password = creds
    else:
        username = os.environ.get("DATEX_USERNAME")
        password = os.environ.get("DATEX_PASSWORD")

    auth = httpx.BasicAuth(username, password) if username and password else None
    headers = {"User-Agent": CLIENT_NAME, "Accept": "application/xml, text/xml, */*"}

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            resp = await client.get(url, headers=headers, auth=auth)
        except httpx.TimeoutException as e:
            raise DatexApiError("Tidsavbrudd mot Statens vegvesens DATEX-tjeneste. Prøv igjen om litt.") from e
        except httpx.RequestError as e:
            raise DatexApiError(f"Nettverksfeil mot DATEX-tjenesten: {e}") from e

    if resp.status_code == 401:
        raise DatexApiError(
            "401 Uautorisert: DATEX_USERNAME/DATEX_PASSWORD mangler eller er feil på serveren. "
            f"Registrer deg for tilgang på {REGISTER_URL} og sett miljøvariablene "
            "DATEX_USERNAME og DATEX_PASSWORD der MCP-serveren kjører."
        )
    if resp.status_code == 403:
        raise DatexApiError(
            "403 Forbudt: Brukeren har ikke tilgang til denne publikasjonen. "
            f"Sjekk tilgangene dine på {REGISTER_URL}."
        )
    if resp.status_code != 200:
        raise DatexApiError(f"DATEX-tjenesten svarte med HTTP {resp.status_code}: {resp.text[:300]}")
    if not resp.text.strip():
        raise DatexApiError("DATEX-tjenesten svarte med tomt innhold.")
    return resp.text


# --------------------------------------------------------------------------
# Generisk, namespace-uavhengig DATEX/XML-parsing
# --------------------------------------------------------------------------

def _strip_namespaces(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
        if el.attrib:
            el.attrib = {
                (k.split("}", 1)[1] if "}" in k else k): v for k, v in el.attrib.items()
            }
    return root


def _add(d: Dict[str, Any], key: str, val: str) -> None:
    if key in d:
        if isinstance(d[key], list):
            d[key].append(val)
        else:
            d[key] = [d[key], val]
    else:
        d[key] = val


def _flatten_record(elem: ET.Element) -> Dict[str, Any]:
    """Flater alle bladnoder (og id-lignende attributter) under elem til én dict."""
    flat: Dict[str, Any] = {}
    for k, v in elem.attrib.items():
        if k.lower() != "version":
            _add(flat, f"@{k}", v)
    for child in elem.iter():
        if child is elem:
            continue
        text = (child.text or "").strip()
        if len(child) == 0:
            if text:
                _add(flat, child.tag, text)
            elif child.attrib:
                for k, v in child.attrib.items():
                    if k.lower() != "version":
                        _add(flat, f"{child.tag}@{k}", v)
    return flat


def _find_record_elements(root: ET.Element, candidate_tags: List[str]) -> List[ET.Element]:
    for tag in candidate_tags:
        found = root.findall(f".//{tag}")
        if found:
            return found

    counts = Counter(el.tag for el in root.iter())
    repeated = sorted(((t, c) for t, c in counts.items() if c >= 2), key=lambda x: -x[1])
    for tag, c in repeated:
        elems = root.findall(f".//{tag}")
        with_children = [e for e in elems if len(e) > 0]
        if len(with_children) >= max(2, int(0.6 * len(elems))):
            return elems
    return []


def parse_datex_records(xml_text: str, candidate_tags: List[str]) -> List[Dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise DatexApiError(f"Kunne ikke tolke XML-svaret fra DATEX: {e}") from e
    root = _strip_namespaces(root)
    records = _find_record_elements(root, candidate_tags)
    return [_flatten_record(r) for r in records]


def _matches_keyword(record: Dict[str, Any], keyword: str) -> bool:
    kw = keyword.lower()
    for v in record.values():
        values = v if isinstance(v, list) else [v]
        if any(kw in str(x).lower() for x in values):
            return True
    return False


def _record_title(record: Dict[str, Any], fallback: str) -> str:
    for key in record:
        lk = key.lower()
        if "name" in lk or "descriptor" in lk:
            val = record[key]
            return str(val[0] if isinstance(val, list) else val)
    for key in record:
        if key.endswith("@id") or key.lower() == "id":
            val = record[key]
            return str(val[0] if isinstance(val, list) else val)
    return fallback


def format_records_markdown(records: List[Dict[str, Any]], tittel: str, maks_antall: int, total_ufiltrert: int) -> str:
    if not records:
        return f"**{tittel}**: Fant ingen treff."
    shown = records[:maks_antall]
    lines = [f"**{tittel}** — viser {len(shown)} av {len(records)} treff (totalt {total_ufiltrert} i publikasjonen)\n"]
    for i, rec in enumerate(shown, 1):
        name = _record_title(rec, f"Post {i}")
        lines.append(f"### {i}. {name}")
        for k, v in sorted(rec.items()):
            val = "; ".join(str(x) for x in v) if isinstance(v, list) else v
            lines.append(f"- **{k}**: {val}")
        lines.append("")
    return "\n".join(lines)


def format_records_json(records: List[Dict[str, Any]], maks_antall: int, total_ufiltrert: int) -> str:
    shown = records[:maks_antall]
    return json.dumps(
        {"antall_vist": len(shown), "antall_treff": len(records), "antall_totalt": total_ufiltrert, "poster": shown},
        ensure_ascii=False,
        indent=2,
    )


def _handle_datex_error(e: DatexApiError) -> str:
    return f"Feil: {e}"


# --------------------------------------------------------------------------
# Delt input-modell
# --------------------------------------------------------------------------

class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


class DatexQueryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    sokeord: Optional[str] = Field(
        default=None,
        description=(
            "Valgfritt søkeord for å filtrere resultatene, f.eks. stasjonsnavn, stedsnavn, "
            "vegnummer eller fylke (f.eks. 'E18', 'Hemsedal', 'Viken'). Søket sjekker alle "
            "feltverdier i hver post og skiller ikke mellom store/små bokstaver."
        ),
        max_length=200,
    )
    maks_antall: int = Field(
        default=10,
        description="Maks antall poster som returneres etter filtrering.",
        ge=1,
        le=100,
    )
    format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Svarformat: 'markdown' for lesbar tekst eller 'json' for strukturert data.",
    )


READ_ONLY_OPEN_WORLD = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}


async def _run_query(
    endpoint_key: str,
    candidate_tags: List[str],
    tittel: str,
    params: DatexQueryInput,
    post_process: Optional[Any] = None,
) -> str:
    try:
        xml_text = await _fetch_datex_xml(endpoint_key)
        records = parse_datex_records(xml_text, candidate_tags)
        total_ufiltrert = len(records)
        if post_process:
            records = [post_process(r) for r in records]
        if params.sokeord:
            records = [r for r in records if _matches_keyword(r, params.sokeord)]
        if params.format == ResponseFormat.JSON:
            return format_records_json(records, params.maks_antall, total_ufiltrert)
        return format_records_markdown(records, tittel, params.maks_antall, total_ufiltrert)
    except DatexApiError as e:
        return _handle_datex_error(e)
    except Exception as e:
        return f"Uventet feil: {type(e).__name__}: {e}"


# --------------------------------------------------------------------------
# Værmålinger
# --------------------------------------------------------------------------

def _weather_ice_warning(record: Dict[str, Any]) -> Dict[str, Any]:
    for key, val in list(record.items()):
        if "roadsurfacetemperature" in key.lower():
            raw = val[0] if isinstance(val, list) else val
            try:
                temp = float(str(raw).replace(",", "."))
            except ValueError:
                continue
            if temp <= 0.0:
                record["⚠️ advarsel"] = "Fare for is/glatt vegbane (vegbanetemperatur ≤ 0°C)"
            elif temp <= 2.0:
                record["❄️ merknad"] = "Lav vegbanetemperatur (≤ 2°C)"
    return record


@mcp.tool(
    name="hent_vaermalinger",
    annotations={"title": "Hent sanntids værmålinger fra veg", **READ_ONLY_OPEN_WORLD},
)
async def hent_vaermalinger(params: DatexQueryInput) -> str:
    """Henter sanntids meteorologiske målinger (vegbanetemperatur, lufttemperatur, vind,
    duggpunkt m.m.) fra Statens vegvesens værstasjoner langs riks- og fylkesveier.
    Oppdateres hvert 10. minutt. Flagger stasjoner med fare for is/glatt vegbane.

    Args:
        params (DatexQueryInput): sokeord (filtrer på stasjon/sted/veg), maks_antall, format.

    Returns:
        str: Markdown-liste eller JSON med værstasjoner og målte verdier.
    """
    return await _run_query(
        "weather_measurements",
        ["siteMeasurements"],
        "Værmålinger",
        params,
        post_process=_weather_ice_warning,
    )


@mcp.tool(
    name="hent_vaerstasjoner",
    annotations={"title": "List geografisk plassering av værstasjoner", **READ_ONLY_OPEN_WORLD},
)
async def hent_vaerstasjoner(params: DatexQueryInput) -> str:
    """Henter geografisk plassering (koordinater, vegnummer, navn) for Statens vegvesens
    værstasjoner. Bruk denne for å slå opp/filtrere stasjoner før du henter målinger.

    Args:
        params (DatexQueryInput): sokeord (filtrer på navn/veg/sted), maks_antall, format.

    Returns:
        str: Markdown-liste eller JSON med værstasjoner og deres plassering.
    """
    return await _run_query(
        "weather_sites",
        ["measurementSiteRecord", "weatherSiteRecord"],
        "Værstasjoner (plassering)",
        params,
    )


# --------------------------------------------------------------------------
# Værprognoser
# --------------------------------------------------------------------------

@mcp.tool(
    name="hent_vaerprognose",
    annotations={"title": "Hent værprognose for vegnettet", **READ_ONLY_OPEN_WORLD},
)
async def hent_vaerprognose(params: DatexQueryInput) -> str:
    """Henter værprognoser (time for time, 24 timer frem) fra Statens vegvesens
    prognosepunkter langs vegnettet. Oppdateres hver time.

    Args:
        params (DatexQueryInput): sokeord (filtrer på stasjon/sted), maks_antall, format.

    Returns:
        str: Markdown-liste eller JSON med prognosedata.
    """
    return await _run_query(
        "weather_forecast",
        ["forecastPointData", "pointData", "siteForecast", "measurementSiteRecord"],
        "Værprognose",
        params,
    )


@mcp.tool(
    name="hent_vaerprognose_punkter",
    annotations={"title": "List plassering av værprognosepunkter", **READ_ONLY_OPEN_WORLD},
)
async def hent_vaerprognose_punkter(params: DatexQueryInput) -> str:
    """Henter geografisk plassering av prognosepunktene som brukes i værprognosen.

    Args:
        params (DatexQueryInput): sokeord, maks_antall, format.

    Returns:
        str: Markdown-liste eller JSON med prognosepunkter og deres plassering.
    """
    return await _run_query(
        "weather_forecast_locations",
        ["measurementSiteRecord", "forecastPointRecord"],
        "Værprognosepunkter (plassering)",
        params,
    )


# --------------------------------------------------------------------------
# Webkamera
# --------------------------------------------------------------------------

@mcp.tool(
    name="hent_webkamera",
    annotations={"title": "List Vegvesenets webkamera", **READ_ONLY_OPEN_WORLD},
)
async def hent_webkamera(params: DatexQueryInput) -> str:
    """Henter oversikt over Statens vegvesens webkamera langs vegnettet, inkludert
    bildeadresse der det er tilgjengelig i publikasjonen.

    Args:
        params (DatexQueryInput): sokeord (filtrer på sted/veg), maks_antall, format.

    Returns:
        str: Markdown-liste eller JSON med kamera og metadata.
    """
    return await _run_query(
        "webcams",
        ["cctvSiteRecord", "cameraSiteRecord"],
        "Webkamera",
        params,
    )


# --------------------------------------------------------------------------
# Reisetider
# --------------------------------------------------------------------------

@mcp.tool(
    name="hent_reisetider",
    annotations={"title": "Hent sanntids reisetider", **READ_ONLY_OPEN_WORLD},
)
async def hent_reisetider(params: DatexQueryInput) -> str:
    """Henter sanntids reisetider (i sekunder) mellom målepunkter, oppdatert hvert 5.
    minutt. Dekker hovedvegnettet rundt Oslo, Bergen, Stavanger, Kristiansand og
    Trondheim, samt E18 Oslo–Agder, E6 Ås–Kolomoen og E8 Skibotn–riksgrensen.

    Args:
        params (DatexQueryInput): sokeord (filtrer på strekning/by), maks_antall, format.

    Returns:
        str: Markdown-liste eller JSON med reisetider.
    """
    return await _run_query(
        "travel_times",
        ["travelTimeData", "siteMeasurements"],
        "Reisetider",
        params,
    )


@mcp.tool(
    name="hent_reisetid_strekninger",
    annotations={"title": "List strekninger for reisetidsmåling", **READ_ONLY_OPEN_WORLD},
)
async def hent_reisetid_strekninger(params: DatexQueryInput) -> str:
    """Henter geometri, navn og id for strekningene det måles reisetid på.

    Args:
        params (DatexQueryInput): sokeord, maks_antall, format.

    Returns:
        str: Markdown-liste eller JSON med strekninger.
    """
    return await _run_query(
        "travel_time_locations",
        ["travelTimeSiteRecord", "measurementSiteRecord"],
        "Reisetidsstrekninger",
        params,
    )


# --------------------------------------------------------------------------
# Trafikkmeldinger / hendelser
# --------------------------------------------------------------------------

@mcp.tool(
    name="hent_trafikkmeldinger",
    annotations={"title": "Hent trafikkmeldinger og hendelser", **READ_ONLY_OPEN_WORLD},
)
async def hent_trafikkmeldinger(params: DatexQueryInput) -> str:
    """Henter aktuelle trafikkmeldinger for hele Norge: vegarbeid, midlertidige
    trafikkreguleringer/stenginger, ulykker, ras og flom m.m.

    Args:
        params (DatexQueryInput): sokeord (filtrer på type hendelse, veg eller sted,
            f.eks. 'ulykke', 'vegarbeid', 'E6'), maks_antall, format.

    Returns:
        str: Markdown-liste eller JSON med trafikkmeldinger.
    """
    return await _run_query(
        "traffic_situations",
        ["situationRecord"],
        "Trafikkmeldinger",
        params,
    )


# --------------------------------------------------------------------------
# Åpen vegdata (NVDB) — fungerer UTEN DATEX-innlogging
# --------------------------------------------------------------------------

def _flatten_json(obj, prefix=""):
    """Flater et NVDB JSON-objekt til en dict, i samme ånd som XML-flateren over."""
    flat = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                flat.update(_flatten_json(v, key))
            elif v is not None:
                flat[key] = v
    elif isinstance(obj, list):
        for i, item in enumerate(obj[:20]):
            flat.update(_flatten_json(item, f"{prefix}[{i}]"))
    return flat


class NvdbQueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vegobjekttype: int = Field(
        ...,
        description=(
            "NVDB-vegobjekttype-ID å hente. Vanlige eksempler: 105=Fartsgrense, "
            "95=Fartshump, 538=Vegbredde, 60=Rasteplass, 39=Rekkverk, 199=Trafikkmengde (ÅDT). "
            "Full liste over vegobjekttyper finnes i NVDBs datakatalog."
        ),
    )
    fylke: Optional[int] = Field(default=None, description="Valgfritt fylkesnummer å filtrere på (f.eks. 3 for Oslo).")
    kommune: Optional[int] = Field(default=None, description="Valgfritt kommunenummer å filtrere på.")
    maks_antall: int = Field(default=10, description="Maks antall objekter som returneres.", ge=1, le=50)


@mcp.tool(
    name="hent_apen_vegdata",
    annotations={"title": "Hent åpen vegdata fra NVDB (uten pålogging)", **READ_ONLY_OPEN_WORLD},
)
async def hent_apen_vegdata(params: NvdbQueryInput) -> str:
    """Henter vegobjekter (f.eks. fartsgrenser, rasteplasser, trafikkmengder) fra
    Statens vegvesens Nasjonale vegdatabank (NVDB). I motsetning til de andre
    verktøyene i denne serveren krever NVDB IKKE registrert bruker/passord —
    dette verktøyet fungerer også for brukere som koblet til uten DATEX-konto.

    Args:
        params (NvdbQueryInput): vegobjekttype (påkrevd), fylke, kommune, maks_antall.

    Returns:
        str: Markdown-liste med vegobjekter og deres egenskaper.
    """
    url = f"{NVDB_BASE_URL}/vegobjekter/{params.vegobjekttype}"
    query = {"antall": params.maks_antall, "inkluder": "alle"}
    if params.fylke:
        query["fylke"] = params.fylke
    if params.kommune:
        query["kommune"] = params.kommune

    headers = {"X-Client": NVDB_CLIENT_HEADER, "Accept": "application/vnd.vegvesen.nvdb-v4+json"}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            resp = await client.get(url, params=query, headers=headers)
        except httpx.TimeoutException:
            return "Feil: Tidsavbrudd mot NVDB API. Prøv igjen om litt."
        except httpx.RequestError as e:
            return f"Feil: Nettverksfeil mot NVDB API: {e}"

    if resp.status_code == 404:
        return f"Fant ingen vegobjekttype med ID {params.vegobjekttype}. Sjekk ID-en mot NVDBs datakatalog."
    if resp.status_code != 200:
        return f"Feil: NVDB API svarte med HTTP {resp.status_code}: {resp.text[:300]}"

    try:
        data = resp.json()
    except ValueError:
        return "Feil: Kunne ikke tolke JSON-svaret fra NVDB."

    objekter = data.get("objekter", [])
    metadata = data.get("metadata", {})
    if not objekter:
        return f"Fant ingen vegobjekter av type {params.vegobjekttype} med disse filtrene."

    lines = [
        f"**Åpen vegdata — vegobjekttype {params.vegobjekttype}** "
        f"(viser {len(objekter)} av totalt {metadata.get('antall', len(objekter))})\n"
    ]
    for i, obj in enumerate(objekter, 1):
        flat = _flatten_json(obj)
        obj_id = flat.get("id", "?")
        lines.append(f"### {i}. Objekt-ID {obj_id}")
        for k, v in sorted(flat.items()):
            if k == "id":
                continue
            lines.append(f"- **{k}**: {v}")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Feilsøkingsverktøy
# --------------------------------------------------------------------------

class InspectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publikasjon: str = Field(
        ...,
        description=(
            "Hvilken DATEX-publikasjon som skal inspiseres. Gyldige verdier: "
            + ", ".join(sorted(DATEX_ENDPOINTS.keys()))
        ),
    )


@mcp.tool(
    name="inspiser_datex_publikasjon",
    annotations={"title": "Feilsøk/inspiser rå DATEX-struktur", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def inspiser_datex_publikasjon(params: InspectInput) -> str:
    """Feilsøkingsverktøy: henter en DATEX-publikasjon og viser hvilke XML-tagger som
    finnes og hvor ofte, samt et eksempel på én flatet post.

    Args:
        params (InspectInput): publikasjon — nøkkel fra DATEX_ENDPOINTS.

    Returns:
        str: Markdown med tag-frekvenser og et eksempel-uttrekk.
    """
    if params.publikasjon not in DATEX_ENDPOINTS:
        return f"Ukjent publikasjon. Gyldige verdier: {', '.join(sorted(DATEX_ENDPOINTS.keys()))}"
    try:
        xml_text = await _fetch_datex_xml(params.publikasjon)
        root = ET.fromstring(xml_text)
        root = _strip_namespaces(root)
        counts = Counter(el.tag for el in root.iter())
        top = counts.most_common(30)
        lines = [f"**Tag-frekvenser i `{params.publikasjon}`** (topp 30):\n"]
        for tag, c in top:
            lines.append(f"- `{tag}`: {c}")

        repeated_candidates = [t for t, c in top if c >= 2]
        if repeated_candidates:
            example_tag = repeated_candidates[0]
            example_elem = root.find(f".//{example_tag}")
            if example_elem is not None:
                flat = _flatten_record(example_elem)
                lines.append(f"\n**Eksempel på flatet post for tag `{example_tag}`:**\n")
                lines.append("```json")
                lines.append(json.dumps(flat, ensure_ascii=False, indent=2))
                lines.append("```")
        return "\n".join(lines)
    except DatexApiError as e:
        return _handle_datex_error(e)
    except ET.ParseError as e:
        return f"Kunne ikke tolke XML: {e}"


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------

if __name__ == "__main__":
    if os.environ.get("PORT"):
        if MULTI_USER_AUTH:
            import uvicorn

            app = mcp.streamable_http_app()
            app.router.routes.extend(datex_auth.LOGIN_ROUTES)
            uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
        else:
            mcp.run(transport="streamable-http")
    else:
        mcp.run()
