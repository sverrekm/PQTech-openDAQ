#!/usr/bin/env python3
"""
Oppdateringsfunksjon - Hent nyaste versjon fraa Git-tarball
===========================================================
Lastar ned tarball fraa eit Gitea- (eller GitHub-) repo, pakkar ut, og
erstattar Python-filer + frontend/dist i /app/. Konteineren restartar seg
sjoelv via Docker restart-policy etter os._exit(0) (gjort i web_ui.py).

Repo-URL, branch og (valfri) access-token er konfigurerbare fraa Admin-GUI-et
og vert lagra i /data/konfig/oppdatering.json. Token vert sendt som
Authorization: token <token>-header (Gitea/GitHub), ALDRI i URL, og ALDRI
returnert til GUI-et.
"""

import json
import os
import tarfile
import tempfile
import shutil
import urllib.request
import logging
from urllib.parse import urlparse

log = logging.getLogger(__name__)

VERSJON_FIL = "/data/konfig/versjon.json"
OPPDATER_KONFIG_FIL = os.environ.get(
    "OPPDATER_KONFIG_STI", "/data/konfig/oppdatering.json")
APP_DIR = "/app"

# Standard repo (kan overstyrast fraa GUI / konfig-fil)
_STANDARD_REPO_URL = os.environ.get(
    "OPPDATER_REPO_URL", "http://192.168.1.58/sverre/pq-tech-opendaq")
_STANDARD_BRANCH = os.environ.get("OPPDATER_BRANCH", "main")


def hent_oppdater_konfig() -> dict:
    """Les oppdaterings-konfig (repo_url, branch, token). Env gir standard,
    fila overstyrer."""
    konfig = {
        "repo_url": _STANDARD_REPO_URL,
        "branch": _STANDARD_BRANCH,
        "token": "",
    }
    try:
        with open(OPPDATER_KONFIG_FIL, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k in ("repo_url", "branch", "token"):
            if isinstance(data.get(k), str):
                konfig[k] = data[k]
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    except Exception as e:
        log.warning(f"Kunne ikkje lese {OPPDATER_KONFIG_FIL}: {e}")
    if not konfig["branch"]:
        konfig["branch"] = "main"
    return konfig


def hent_oppdater_konfig_offentleg() -> dict:
    """Som hent_oppdater_konfig, men lekkar ikkje token til GUI-et."""
    k = hent_oppdater_konfig()
    return {
        "repo_url": k["repo_url"],
        "branch": k["branch"],
        "token_satt": bool(k["token"]),
    }


def lagre_oppdater_konfig(repo_url: str, branch: str = "main",
                          token=None) -> dict:
    """Lagre konfig. token=None => behald eksisterande; tom streng => fjern."""
    eksisterande = hent_oppdater_konfig()
    if token is None:
        token = eksisterande.get("token", "")
    data = {
        "repo_url": (repo_url or "").strip().rstrip("/"),
        "branch": (branch or "main").strip() or "main",
        "token": (token or "").strip(),
    }
    try:
        os.makedirs(os.path.dirname(OPPDATER_KONFIG_FIL), exist_ok=True)
        with open(OPPDATER_KONFIG_FIL, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        return {"suksess": False, "feil": f"Kunne ikkje lagre konfig: {e}"}
    return {
        "suksess": True,
        "melding": "Oppdaterings-konfig lagra.",
        "repo_url": data["repo_url"],
        "branch": data["branch"],
        "token_satt": bool(data["token"]),
    }


def _eigar_repo(repo_url: str):
    """Parse (parsed_url, eigar, repo) fraa repo-URL."""
    p = urlparse(repo_url)
    delar = [d for d in p.path.strip("/").split("/") if d]
    if len(delar) < 2:
        raise RuntimeError(f"Ugyldig repo-URL (manglar eigar/repo): {repo_url}")
    eigar = delar[0]
    repo = delar[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return p, eigar, repo


def _req(url: str, token: str):
    headers = {"User-Agent": "PQTech-openDAQ-updater", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"token {token}"
    return urllib.request.Request(url, headers=headers)


def sjekk_github():
    """Sjekk remote (Gitea eller GitHub) for nyaste commit. Namnet er behalde
    av historiske grunnar - fungerer for begge."""
    konfig = hent_oppdater_konfig()
    repo_url = konfig["repo_url"]
    branch = konfig["branch"]
    token = konfig["token"]
    p, eigar, repo = _eigar_repo(repo_url)

    if p.hostname and p.hostname.endswith("github.com"):
        url = f"https://api.github.com/repos/{eigar}/{repo}/commits/{branch}"
        with urllib.request.urlopen(_req(url, token), timeout=15) as resp:
            data = json.loads(resp.read().decode())
    else:
        base = f"{p.scheme}://{p.netloc}/api/v1/repos/{eigar}/{repo}"
        url = f"{base}/commits?sha={branch}&limit=1"
        with urllib.request.urlopen(_req(url, token), timeout=15) as resp:
            liste = json.loads(resp.read().decode())
        if not liste:
            raise RuntimeError("Tomt repo / ingen commits funne")
        data = liste[0]

    commit = data.get("commit", {})
    committer = commit.get("committer", {}) or {}
    melding = (commit.get("message") or "").split("\n")[0]
    return {
        "sha": data["sha"][:7],
        "sha_full": data["sha"],
        "melding": melding,
        "dato": committer.get("date", ""),
        "forfattar": committer.get("name", ""),
    }


def les_versjon():
    """Les lagra versjon fraa versjon.json."""
    try:
        with open(VERSJON_FIL, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"sha": "ukjend", "melding": "Ikkje sett", "dato": ""}


def _tarball_url(p, eigar, repo, branch):
    if p.hostname and p.hostname.endswith("github.com"):
        return f"https://api.github.com/repos/{eigar}/{repo}/tarball/{branch}"
    return f"{p.scheme}://{p.netloc}/api/v1/repos/{eigar}/{repo}/archive/{branch}.tar.gz"


def last_ned_og_oppdater():
    """Last ned tarball fraa konfigurert repo, pakk ut, og erstatt filer i /app/."""
    konfig = hent_oppdater_konfig()
    repo_url = konfig["repo_url"]
    branch = konfig["branch"]
    token = konfig["token"]
    log.info(f"Startar oppdatering fraa {repo_url} ({branch})...")

    info = sjekk_github()
    log.info(f"Nyaste commit: {info['sha']} - {info['melding']}")

    p, eigar, repo = _eigar_repo(repo_url)
    tarball_url = _tarball_url(p, eigar, repo, branch)

    tmpdir = tempfile.mkdtemp(prefix="oppdatering_")
    tarball_path = os.path.join(tmpdir, "repo.tar.gz")

    try:
        log.info("Lastar ned tarball...")
        with urllib.request.urlopen(_req(tarball_url, token), timeout=120) as resp:
            with open(tarball_path, "wb") as f:
                shutil.copyfileobj(resp, f)

        log.info("Pakkar ut tarball...")
        with tarfile.open(tarball_path, "r:gz") as tar:
            tar.extractall(path=tmpdir)

        utpakka = [
            d for d in os.listdir(tmpdir)
            if os.path.isdir(os.path.join(tmpdir, d)) and d != "repo.tar.gz"
        ]
        if not utpakka:
            raise RuntimeError("Fann ikkje utpakka mappe i tarball")

        # Filene ligg anten i rota, eller i ei OpenDackoConteiner/-undermappe
        repo_rot = os.path.join(tmpdir, utpakka[0], "OpenDackoConteiner")
        if not os.path.isdir(repo_rot):
            repo_rot = os.path.join(tmpdir, utpakka[0])
        if not os.path.isdir(repo_rot):
            raise RuntimeError("Fann ikkje filer i tarball")

        oppdaterte = []
        for filnavn in os.listdir(repo_rot):
            kilde = os.path.join(repo_rot, filnavn)
            if not os.path.isfile(kilde):
                continue
            if filnavn.endswith(".py") or filnavn == "docker-entrypoint.sh":
                shutil.copy2(kilde, os.path.join(APP_DIR, filnavn))
                oppdaterte.append(filnavn)
                log.info(f"  Oppdatert: {filnavn}")

        frontend_dist = os.path.join(repo_rot, "frontend", "dist")
        if os.path.isdir(frontend_dist):
            maal_dist = os.path.join(APP_DIR, "frontend", "dist")
            if os.path.isdir(maal_dist):
                shutil.rmtree(maal_dist)
            shutil.copytree(frontend_dist, maal_dist)
            oppdaterte.append("frontend/dist/")
            log.info("  Oppdatert: frontend/dist/")

        os.makedirs(os.path.dirname(VERSJON_FIL), exist_ok=True)
        with open(VERSJON_FIL, "w") as f:
            json.dump({
                "sha": info["sha"],
                "sha_full": info["sha_full"],
                "melding": info["melding"],
                "dato": info["dato"],
            }, f, indent=2)

        log.info(f"Oppdatering fullfoert: {len(oppdaterte)} filer oppdaterte")
        return {
            "suksess": True,
            "versjon": info["sha"],
            "melding": info["melding"],
            "oppdaterte_filer": oppdaterte,
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
