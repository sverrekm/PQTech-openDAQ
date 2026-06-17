#!/usr/bin/env python3
"""
Enkel autentisering for PQTech Web GUI
=======================================
Session-basert auth med salted SHA-256 passord-hashing.
Standard-credentials: admin / pqtech
"""

import os
import json
import hashlib
import secrets

from flask import request, session, jsonify

KONFIG_DIR = "/data/konfig"
BRUKAR_FIL = os.path.join(KONFIG_DIR, "brukar.json")
SECRET_FIL = os.path.join(KONFIG_DIR, "flask_secret.key")

STANDARD_BRUKARNAVN = "admin"
STANDARD_PASSORD = "pqtech"


def _hash_passord(passord: str, salt: str) -> str:
    """SHA-256 hash med salt."""
    return hashlib.sha256((salt + passord).encode("utf-8")).hexdigest()


def les_brukar() -> dict:
    """Les brukardata fraa JSON-fil."""
    try:
        with open(BRUKAR_FIL, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def lagre_brukar(data: dict) -> None:
    """Lagre brukardata til JSON-fil."""
    os.makedirs(KONFIG_DIR, exist_ok=True)
    with open(BRUKAR_FIL, "w") as f:
        json.dump(data, f, indent=2)


def _opprett_standard_brukar():
    """Opprett standard admin-brukar viss den ikkje finst."""
    data = les_brukar()
    if data.get("brukarnavn"):
        return
    salt = secrets.token_hex(16)
    data = {
        "brukarnavn": STANDARD_BRUKARNAVN,
        "salt": salt,
        "passord_hash": _hash_passord(STANDARD_PASSORD, salt),
    }
    lagre_brukar(data)


def _hent_eller_opprett_secret_key() -> str:
    """Hent persistent secret key, eller generer ny."""
    os.makedirs(KONFIG_DIR, exist_ok=True)
    try:
        with open(SECRET_FIL, "r") as f:
            key = f.read().strip()
            if key:
                return key
    except FileNotFoundError:
        pass
    key = secrets.token_hex(32)
    with open(SECRET_FIL, "w") as f:
        f.write(key)
    return key


def _delte_token() -> set:
    """Dei delte flåte-nøklane noden stolar på frå hubben (parent_token /
    ingest_token / env INGEST_TOKEN). Hub-proxyen signerer kall med ein av
    desse i X-Hub-Auth → single sign-on."""
    ut = set()
    try:
        import push_konfig
        k = push_konfig.les_push_konfig()
        for v in (k.parent_token, k.ingest_token):
            if v and v.strip():
                ut.add(v.strip())
    except Exception:
        pass
    env = os.environ.get("INGEST_TOKEN", "").strip()
    if env:
        ut.add(env)
    return ut


def sjekk_passord(brukarnavn: str, passord: str) -> bool:
    """Sjekk om brukarnavn og passord er korrekt."""
    data = les_brukar()
    if data.get("brukarnavn") != brukarnavn:
        return False
    salt = data.get("salt", "")
    return data.get("passord_hash") == _hash_passord(passord, salt)


def endre_passord(brukarnavn: str, gammalt: str, nytt: str) -> tuple:
    """Endre passord. Returnerer (ok, melding)."""
    if not sjekk_passord(brukarnavn, gammalt):
        return False, "Feil gammalt passord"
    if len(nytt) < 4:
        return False, "Nytt passord maa vere minst 4 teikn"
    data = les_brukar()
    salt = secrets.token_hex(16)
    data["salt"] = salt
    data["passord_hash"] = _hash_passord(nytt, salt)
    lagre_brukar(data)
    return True, "Passord endra"


def init_app(app):
    """Initialiser auth for Flask-appen."""
    app.secret_key = _hent_eller_opprett_secret_key()
    _opprett_standard_brukar()

    @app.before_request
    def sjekk_auth():
        # Single sign-on: kall proxya frå hubben ber X-Hub-Auth med ein delt
        # flåte-nøkkel. Er den gyldig, er brukaren alt autentisert på hubben →
        # autentiser denne node-sesjonen så ein slepp eige node-login. Køyrer
        # FØR unntaka under, slik at /api/auth/status òg ser sesjonen.
        if "brukar" not in session:
            hub_auth = request.headers.get("X-Hub-Auth", "").strip()
            if hub_auth:
                for tk in _delte_token():
                    if secrets.compare_digest(hub_auth, tk):
                        session["brukar"] = "hub-sso"
                        break
        # Unntatt: auth-endepunkt og ikkje-API-ruter (frontend, statiske filer)
        if request.path.startswith("/api/auth/") or not request.path.startswith("/api/"):
            return
        # Unntatt: POST /api/ingest har eigen Bearer-token-auth (kallast frå
        # node-konteinarar bak CGNAT, kan ikkje session-cookies). GET-endepunkt
        # under /api/ingest/ er admin-only (status, data) -> session-auth.
        if request.path == "/api/ingest" and request.method == "POST":
            return
        # Unntatt: hub->node oppdaterings-trigger via Bearer flaate-token.
        # Sjølve token-valideringa skjer i ruta (api_system_oppdater).
        if (request.path in ("/api/system/oppdater", "/api/system/restart")
                and request.method == "POST"
                and request.headers.get("Authorization", "").startswith("Bearer ")):
            return
        if "brukar" not in session:
            return jsonify({"feil": "Ikkje innlogga"}), 401
