"""
Oppdateringsfunksjon — Hent nyaste versjon frå GitHub
=====================================================
Lastar ned tarball frå GitHub, pakkar ut, og erstattar
Python-filer i /app/. Konteineren restartar seg sjølv
via Docker restart-policy etter os._exit(0).
"""

import json
import os
import tarfile
import tempfile
import shutil
import urllib.request
import logging

log = logging.getLogger(__name__)

GITHUB_REPO = "sverrekm/PQTech-openDAQ"
GITHUB_BRANCH = "main"
VERSJON_FIL = "/data/konfig/versjon.json"
APP_DIR = "/app"


def les_versjon():
    """Les lagra versjon frå versjon.json."""
    try:
        with open(VERSJON_FIL, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"sha": "ukjend", "melding": "Ikkje sett", "dato": ""}


def sjekk_github():
    """Sjekk GitHub API for nyaste commit på main."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/commits/{GITHUB_BRANCH}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "PQTech-openDAQ-updater",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())

    return {
        "sha": data["sha"][:7],
        "sha_full": data["sha"],
        "melding": data["commit"]["message"].split("\n")[0],
        "dato": data["commit"]["committer"]["date"],
        "forfattar": data["commit"]["committer"]["name"],
    }


def last_ned_og_oppdater():
    """Last ned tarball frå GitHub, pakk ut, og erstatt filer i /app/."""
    log.info("Startar oppdatering frå GitHub...")

    # 1. Hent info om nyaste commit
    info = sjekk_github()
    log.info(f"Nyaste commit: {info['sha']} — {info['melding']}")

    # 2. Last ned tarball
    tarball_url = f"https://api.github.com/repos/{GITHUB_REPO}/tarball/{GITHUB_BRANCH}"
    req = urllib.request.Request(tarball_url, headers={
        "User-Agent": "PQTech-openDAQ-updater",
    })

    tmpdir = tempfile.mkdtemp(prefix="oppdatering_")
    tarball_path = os.path.join(tmpdir, "repo.tar.gz")

    try:
        log.info(f"Lastar ned tarball...")
        with urllib.request.urlopen(req, timeout=60) as resp:
            with open(tarball_path, "wb") as f:
                shutil.copyfileobj(resp, f)

        # 3. Pakk ut tarball
        log.info("Pakkar ut tarball...")
        with tarfile.open(tarball_path, "r:gz") as tar:
            tar.extractall(path=tmpdir)

        # Finn utpakka mappe (GitHub legg til prefix som "sverrekm-PQTech-openDAQ-abc1234")
        utpakka = [
            d for d in os.listdir(tmpdir)
            if os.path.isdir(os.path.join(tmpdir, d)) and d != "repo.tar.gz"
        ]
        if not utpakka:
            raise RuntimeError("Fann ikkje utpakka mappe i tarball")

        # GitHub tarball: filene ligg anten direkte i rot-mappa
        # eller i ei OpenDackoConteiner/-undermappe (eldre repo-struktur)
        repo_rot = os.path.join(tmpdir, utpakka[0], "OpenDackoConteiner")
        if not os.path.isdir(repo_rot):
            repo_rot = os.path.join(tmpdir, utpakka[0])
        if not os.path.isdir(repo_rot):
            raise RuntimeError(f"Fann ikkje filer i tarball")

        # 4. Kopier Python-filer til /app/
        oppdaterte = []
        for filnavn in os.listdir(repo_rot):
            kilde = os.path.join(repo_rot, filnavn)
            if not os.path.isfile(kilde):
                continue

            if filnavn.endswith(".py") or filnavn == "docker-entrypoint.sh":
                maal = os.path.join(APP_DIR, filnavn)
                shutil.copy2(kilde, maal)
                oppdaterte.append(filnavn)
                log.info(f"  Oppdatert: {filnavn}")

        # 5. Kopier frontend/dist/ viss det finst (framtidig)
        frontend_dist = os.path.join(repo_rot, "frontend", "dist")
        if os.path.isdir(frontend_dist):
            maal_dist = os.path.join(APP_DIR, "frontend", "dist")
            if os.path.isdir(maal_dist):
                shutil.rmtree(maal_dist)
            shutil.copytree(frontend_dist, maal_dist)
            oppdaterte.append("frontend/dist/")
            log.info("  Oppdatert: frontend/dist/")

        # 6. Lagre versjon
        os.makedirs(os.path.dirname(VERSJON_FIL), exist_ok=True)
        with open(VERSJON_FIL, "w") as f:
            json.dump({
                "sha": info["sha"],
                "sha_full": info["sha_full"],
                "melding": info["melding"],
                "dato": info["dato"],
            }, f, indent=2)

        log.info(f"Oppdatering fullført: {len(oppdaterte)} filer oppdaterte")

        return {
            "suksess": True,
            "versjon": info["sha"],
            "melding": info["melding"],
            "oppdaterte_filer": oppdaterte,
        }

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
