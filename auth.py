"""
auth.py — Egen OAuth 2.1-autorisasjonsserver innebygd i MCP-serveren, slik at
hver bruker av SVV-MCP-unofficial logger inn med SITT EGET DATEX-brukernavn/
passord i stedet for å dele én driftskonto.

⚠️ Sikkerhetsmerknad: Dette er en pragmatisk, selv-hostet autorisasjonsserver
   — ikke en fullverdig identity provider. Test flyten grundig (se README,
   seksjon "Teste OAuth-flyten") før du deler URL-en videre.
"""

import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Optional
from urllib.parse import urlencode

from cryptography.fernet import Fernet, InvalidToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

DB_PATH = os.environ.get("SVV_MCP_DB_PATH", "/data/svv_mcp.db")
AUTH_CODE_TTL_SECONDS = 5 * 60
ACCESS_TOKEN_TTL_SECONDS = 90 * 24 * 60 * 60
LOGIN_TTL_SECONDS = 15 * 60

REGISTER_URL = "https://www.vegvesen.no/en/fag/technology/open-data/a-selection-of-open-data/what-is-datex/get-access/"


def _get_fernet() -> Fernet:
    key = os.environ.get("CREDENTIAL_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "CREDENTIAL_ENCRYPTION_KEY mangler. Generer én med "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"` "
            "og sett den som miljøvariabel på Railway-servicen (aldri i git)."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


@contextmanager
def _db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS oauth_clients (
                client_id TEXT PRIMARY KEY,
                client_secret TEXT,
                redirect_uris TEXT NOT NULL,
                client_name TEXT,
                token_endpoint_auth_method TEXT,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pending_logins (
                login_id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                redirect_uri TEXT NOT NULL,
                code_challenge TEXT,
                code_challenge_method TEXT,
                scopes TEXT,
                state TEXT,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_codes (
                code TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                redirect_uri TEXT NOT NULL,
                code_challenge TEXT,
                code_challenge_method TEXT,
                scopes TEXT,
                user_id TEXT NOT NULL,
                expires_at REAL NOT NULL,
                used INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS access_tokens (
                token TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                scopes TEXT,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                token TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                scopes TEXT,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS datex_credentials (
                user_id TEXT PRIMARY KEY,
                username_enc BLOB NOT NULL,
                password_enc BLOB NOT NULL,
                created_at REAL NOT NULL
            );
            """
        )
        try:
            conn.execute("ALTER TABLE oauth_clients ADD COLUMN token_endpoint_auth_method TEXT")
        except sqlite3.OperationalError:
            pass  # kolonnen finnes allerede


def _encrypt(value: str) -> bytes:
    return _get_fernet().encrypt(value.encode())


def _decrypt(value: bytes) -> str:
    return _get_fernet().decrypt(value).decode()


def store_datex_credentials(user_id: str, username: str, password: str) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO datex_credentials (user_id, username_enc, password_enc, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, _encrypt(username), _encrypt(password), time.time()),
        )


def load_datex_credentials(user_id: str) -> Optional[tuple[str, str]]:
    with _db() as conn:
        row = conn.execute(
            "SELECT username_enc, password_enc FROM datex_credentials WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row:
        return None
    try:
        return _decrypt(row[0]), _decrypt(row[1])
    except InvalidToken:
        return None


def load_datex_credentials_for_token(access_token: str) -> Optional[tuple[str, str]]:
    """Slår opp DATEX-credentials for eieren av et gitt (rått) access-token."""
    with _db() as conn:
        row = conn.execute(
            "SELECT user_id, expires_at FROM access_tokens WHERE token = ?", (access_token,)
        ).fetchone()
    if not row or row[1] < time.time():
        return None
    return load_datex_credentials(row[0])


# --------------------------------------------------------------------------
# Innloggingsside (HTML)
# --------------------------------------------------------------------------

def _login_page_html(login_id: str, error: Optional[str] = None) -> str:
    error_html = f'<p class="error">⚠️ {error}</p>' if error else ""
    return f"""<!DOCTYPE html>
<html lang="no">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Koble til SVV-MCP-unofficial</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 480px; margin: 48px auto; padding: 0 16px; color: #1a1a1a; }}
  h1 {{ font-size: 1.3rem; }}
  .disclaimer {{ background: #fff4e5; border: 1px solid #f0c674; padding: 12px 16px; border-radius: 8px; font-size: 0.9rem; margin-bottom: 24px; }}
  .info {{ background: #eef4ff; border: 1px solid #b8d0ff; padding: 12px 16px; border-radius: 8px; font-size: 0.9rem; margin-bottom: 24px; }}
  .info ul {{ margin: 8px 0 0; padding-left: 20px; }}
  label {{ display: block; margin-top: 16px; font-weight: 600; font-size: 0.9rem; }}
  input {{ width: 100%; padding: 10px; margin-top: 6px; border: 1px solid #ccc; border-radius: 6px; box-sizing: border-box; }}
  button {{ margin-top: 24px; width: 100%; padding: 12px; background: #1a56db; color: white; border: none; border-radius: 6px; font-size: 1rem; cursor: pointer; }}
  .error {{ color: #b91c1c; font-weight: 600; }}
  a {{ color: #1a56db; }}
  .skip {{ margin-top: 20px; text-align: center; font-size: 0.9rem; }}
  hr {{ margin: 28px 0; border: none; border-top: 1px solid #e5e5e5; }}
</style>
</head>
<body>
  <h1>Koble til Statens vegvesens værdata</h1>
  <div class="disclaimer">
    Dette er en <strong>uoffisiell</strong> tjeneste, ikke tilknyttet Statens vegvesen.
    Brukernavnet/passordet du oppgir her sendes kun videre til Vegvesenets egne
    DATEX-API og lagres kryptert på denne serveren for å knytte det til din
    tilkobling i Claude.
  </div>
  <div class="info">
    <strong>Med DATEX-innlogging får du tilgang til:</strong>
    <ul>
      <li>🌡️ Sanntids værmålinger fra vegstasjoner (vegbanetemp, lufttemp, vind), med varsel om glatt vegbane</li>
      <li>🌦️ Værprognoser time for time, 24t frem</li>
      <li>📷 Webkamera langs vegnettet</li>
      <li>🚗 Sanntids reisetider på hovedstrekninger</li>
      <li>⚠️ Trafikkmeldinger: ulykker, vegarbeid, stenginger, ras/flom</li>
    </ul>
    <strong>Har du ikke DATEX-tilgang ennå?</strong><br>
    DATEX-endepunktene hos Statens vegvesen krever en gratis, registrert konto.
    Be om tilgang her: <a href="{REGISTER_URL}" target="_blank" rel="noopener">
    vegvesen.no – Be om tilgang til DATEX</a>. Du får tilsendt brukernavn og
    passord som du limer inn under.
  </div>
  {error_html}
  <form method="post" action="/login">
    <input type="hidden" name="login_id" value="{login_id}">
    <label for="u">DATEX-brukernavn</label>
    <input type="text" id="u" name="datex_username" required autocomplete="username">
    <label for="p">DATEX-passord</label>
    <input type="password" id="p" name="datex_password" required autocomplete="current-password">
    <button type="submit">Koble til</button>
  </form>
  <hr>
  <div class="skip">
    Vil du ikke skaffe DATEX-tilgang nå?<br>
    <a href="/login/skip?login_id={login_id}">Fortsett uten konto</a> — du får da kun
    tilgang til Vegvesenets åpne vegdata (NVDB), ikke vær/webkamera/trafikkmeldinger.
  </div>
</body>
</html>"""


async def login_get(request: Request) -> HTMLResponse:
    login_id = request.query_params.get("login_id", "")
    with _db() as conn:
        row = conn.execute(
            "SELECT expires_at FROM pending_logins WHERE login_id = ?", (login_id,)
        ).fetchone()
    if not row or row[0] < time.time():
        return HTMLResponse("Denne innloggingslenken er ugyldig eller utløpt. Prøv å koble til på nytt fra Claude.", status_code=400)
    return HTMLResponse(_login_page_html(login_id))


def _complete_login(login_id, username, password):
    with _db() as conn:
        row = conn.execute(
            "SELECT client_id, redirect_uri, code_challenge, code_challenge_method, scopes, state, expires_at "
            "FROM pending_logins WHERE login_id = ?",
            (login_id,),
        ).fetchone()

    if not row or row[6] < time.time():
        return HTMLResponse("Innloggingsøkten er utløpt. Gå tilbake til Claude og koble til på nytt.", status_code=400)

    client_id, redirect_uri, code_challenge, code_challenge_method, scopes, state, _ = row

    user_id = str(uuid.uuid4())
    if username and password:
        store_datex_credentials(user_id, username, password)
    # Uten username/password: "anonym" bruker uten lagrede DATEX-credentials.
    # DATEX-avhengige verktøy ber da brukeren koble til på nytt for å logge inn,
    # mens verktøy mot åpne kilder (f.eks. NVDB) fungerer som normalt.

    code = secrets.token_urlsafe(32)
    with _db() as conn:
        conn.execute(
            "INSERT INTO auth_codes (code, client_id, redirect_uri, code_challenge, code_challenge_method, "
            "scopes, user_id, expires_at, used) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (code, client_id, redirect_uri, code_challenge, code_challenge_method, scopes, user_id,
             time.time() + AUTH_CODE_TTL_SECONDS),
        )
        conn.execute("DELETE FROM pending_logins WHERE login_id = ?", (login_id,))

    params = {"code": code}
    if state:
        params["state"] = state
    return RedirectResponse(url=f"{redirect_uri}?{urlencode(params)}", status_code=302)


async def login_post(request: Request):
    form = await request.form()
    login_id = str(form.get("login_id", ""))
    datex_username = str(form.get("datex_username", "")).strip()
    datex_password = str(form.get("datex_password", "")).strip()

    if not datex_username or not datex_password:
        return HTMLResponse(_login_page_html(login_id, error="Fyll ut både brukernavn og passord, eller velg «Fortsett uten konto» under."), status_code=400)

    return _complete_login(login_id, datex_username, datex_password)


async def login_skip(request: Request):
    login_id = request.query_params.get("login_id", "")
    return _complete_login(login_id, None, None)


LOGIN_ROUTES = [
    Route("/login", login_get, methods=["GET"]),
    Route("/login", login_post, methods=["POST"]),
    Route("/login/skip", login_skip, methods=["GET"]),
]


# --------------------------------------------------------------------------
# OAuthAuthorizationServerProvider-implementasjon
# --------------------------------------------------------------------------

class DatexAuthProvider(OAuthAuthorizationServerProvider):
    """Innebygd autorisasjonsserver: viser vår egen /login i stedet for en
    ekstern IdP, og utsteder tokens knyttet til krypterte DATEX-credentials.
    """

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        with _db() as conn:
            row = conn.execute(
                "SELECT client_id, client_secret, redirect_uris, client_name, token_endpoint_auth_method FROM oauth_clients WHERE client_id = ?",
                (client_id,),
            ).fetchone()
        if not row:
            return None
        import json as _json
        return OAuthClientInformationFull(
            client_id=row[0],
            client_secret=row[1],
            redirect_uris=_json.loads(row[2]),
            client_name=row[3] or "MCP client",
            token_endpoint_auth_method=row[4] or ("client_secret_post" if row[1] else "none"),
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        import json as _json
        with _db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO oauth_clients (client_id, client_secret, redirect_uris, client_name, token_endpoint_auth_method, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    client_info.client_id,
                    client_info.client_secret,
                    _json.dumps([str(u) for u in client_info.redirect_uris]),
                    client_info.client_name,
                    client_info.token_endpoint_auth_method,
                    time.time(),
                ),
            )

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        login_id = str(uuid.uuid4())
        with _db() as conn:
            conn.execute(
                "INSERT INTO pending_logins (login_id, client_id, redirect_uri, code_challenge, "
                "code_challenge_method, scopes, state, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    login_id,
                    client.client_id,
                    str(params.redirect_uri),
                    params.code_challenge,
                    "S256",
                    " ".join(params.scopes) if params.scopes else "",
                    params.state,
                    time.time() + LOGIN_TTL_SECONDS,
                ),
            )
        base_url = os.environ.get("SVV_MCP_PUBLIC_URL", "").rstrip("/")
        return f"{base_url}/login?login_id={login_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> Optional[AuthorizationCode]:
        with _db() as conn:
            row = conn.execute(
                "SELECT client_id, redirect_uri, code_challenge, scopes, expires_at, used "
                "FROM auth_codes WHERE code = ?",
                (authorization_code,),
            ).fetchone()
        if not row or row[0] != client.client_id or row[5] or row[4] < time.time():
            return None
        return AuthorizationCode(
            code=authorization_code,
            client_id=row[0],
            redirect_uri=row[1],
            redirect_uri_provided_explicitly=True,
            scopes=row[3].split() if row[3] else [],
            expires_at=row[4],
            code_challenge=row[2],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        with _db() as conn:
            row = conn.execute(
                "SELECT user_id, scopes FROM auth_codes WHERE code = ? AND used = 0",
                (authorization_code.code,),
            ).fetchone()
            if not row:
                raise ValueError("Autorisasjonskoden er allerede brukt eller finnes ikke.")
            user_id, scopes = row
            conn.execute("UPDATE auth_codes SET used = 1 WHERE code = ?", (authorization_code.code,))

            access_token = secrets.token_urlsafe(32)
            refresh_token = secrets.token_urlsafe(32)
            now = time.time()
            conn.execute(
                "INSERT INTO access_tokens (token, client_id, user_id, scopes, expires_at) VALUES (?, ?, ?, ?, ?)",
                (access_token, client.client_id, user_id, scopes, now + ACCESS_TOKEN_TTL_SECONDS),
            )
            conn.execute(
                "INSERT INTO refresh_tokens (token, client_id, user_id, scopes, expires_at) VALUES (?, ?, ?, ?, ?)",
                (refresh_token, client.client_id, user_id, scopes, now + ACCESS_TOKEN_TTL_SECONDS * 4),
            )

        return OAuthToken(
            access_token=access_token,
            token_type="bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=refresh_token,
            scope=scopes,
        )

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        with _db() as conn:
            row = conn.execute(
                "SELECT client_id, user_id, scopes, expires_at FROM access_tokens WHERE token = ?", (token,)
            ).fetchone()
        if not row or row[3] < time.time():
            return None
        return AccessToken(
            token=token,
            client_id=row[0],
            scopes=row[2].split() if row[2] else [],
            expires_at=int(row[3]),
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Optional[RefreshToken]:
        with _db() as conn:
            row = conn.execute(
                "SELECT client_id, scopes, expires_at FROM refresh_tokens WHERE token = ?", (refresh_token,)
            ).fetchone()
        if not row or row[0] != client.client_id or row[2] < time.time():
            return None
        return RefreshToken(token=refresh_token, client_id=row[0], scopes=row[1].split() if row[1] else [], expires_at=int(row[2]))

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        with _db() as conn:
            row = conn.execute(
                "SELECT user_id FROM refresh_tokens WHERE token = ? AND client_id = ?",
                (refresh_token.token, client.client_id),
            ).fetchone()
            if not row:
                raise ValueError("Ugyldig refresh-token.")
            user_id = row[0]
            new_access = secrets.token_urlsafe(32)
            now = time.time()
            conn.execute(
                "INSERT INTO access_tokens (token, client_id, user_id, scopes, expires_at) VALUES (?, ?, ?, ?, ?)",
                (new_access, client.client_id, user_id, " ".join(scopes) if scopes else "", now + ACCESS_TOKEN_TTL_SECONDS),
            )
        return OAuthToken(
            access_token=new_access,
            token_type="bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=refresh_token.token,
            scope=" ".join(scopes) if scopes else "",
        )

    async def revoke_token(self, token: str, token_type_hint: Optional[str] = None) -> None:
        with _db() as conn:
            conn.execute("DELETE FROM access_tokens WHERE token = ?", (token,))
            conn.execute("DELETE FROM refresh_tokens WHERE token = ?", (token,))
