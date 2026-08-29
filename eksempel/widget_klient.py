#!/usr/bin/env python3
"""
Eksempelklient for PQTech-hubben sitt eksterne lese-API
========================================================
Køyrer kvar som helst — maskina treng ikkje vere på same nett som hubben,
berre nå han over HTTPS. All autentisering er éin API-nøkkel, oppretta under
Innstillingar → «Deling og integrasjonar» → API-nøklar.

    pip install requests

    # Ein gong, siste verdiar:
    python widget_klient.py --url https://opendac.pqtech.no --nokkel pqt_...

    # Live (Server-Sent Events, éi lang tilkobling):
    python widget_klient.py --url https://opendac.pqtech.no --nokkel pqt_... --straum

    # Berre nokre kanalar:
    python widget_klient.py ... --kanal "Spenning L1" --kanal "Straum L1"

Nøkkelen kan òg liggje i miljøvariabelen PQTECH_API_NOKKEL, så han ikkje
hamnar i shell-historikk eller i ei prosessliste.

Dette er med vilje ei enkelt fil utan avhengnader utover `requests`: bruk han
som han er, eller klipp ut `HubKlient` og legg han bak kva GUI du vil
(tkinter, Qt, tray-ikon, Rainmeter, ...).
"""

import os
import sys
import json
import time
import argparse

import requests


class HubKlient:
    """Tynn klient mot /api/v1 på ein PQTech-hub eller -node."""

    def __init__(self, url: str, nokkel: str, timeout: float = 10.0):
        self.url = url.rstrip("/")
        self.nokkel = nokkel
        self.timeout = timeout
        self.sesjon = requests.Session()
        # Nøkkelen som header, ikkje query-parameter: query-strengar hamnar
        # gjerne i proxy-loggar.
        self.sesjon.headers.update({
            "X-API-Key": nokkel,
            # Cloudflare avviser default-UA-en til python-urllib; requests sin
            # eigen UA slepp gjennom, men vi er eksplisitte for tydelegheit.
            "User-Agent": "pqtech-widget/1.0",
        })

    def info(self) -> dict:
        """Kva hub dette er, og kva nøkkelen har tilgang til."""
        r = self.sesjon.get(f"{self.url}/api/v1/info", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def kanalar(self) -> list:
        """Siste verdi for kvar kanal nøkkelen får sjå."""
        r = self.sesjon.get(f"{self.url}/api/v1/kanalar", timeout=self.timeout)
        r.raise_for_status()
        return r.json().get("kanalar", [])

    def straum(self, intervall: float = 2.0):
        """Generator som gir ei ny kanalliste kvar gong hubben sender ei.

        Server-Sent Events over éi tilkobling. Fell tilbake til å kople opp
        att ved brot — eit widget skal ikkje døy fordi nettet blafra.
        """
        while True:
            try:
                r = self.sesjon.get(
                    f"{self.url}/api/v1/straum",
                    params={"intervall": intervall},
                    stream=True, timeout=(self.timeout, None),
                )
                r.raise_for_status()
                for linje in r.iter_lines(decode_unicode=True):
                    if not linje or linje.startswith(":"):
                        continue          # keep-alive-kommentar
                    if linje.startswith("data: "):
                        yield json.loads(linje[6:])
            except (requests.RequestException, json.JSONDecodeError) as e:
                print(f"[straum] brot: {e} — koplar opp att om 5 s", file=sys.stderr)
                time.sleep(5.0)


def skriv_ut(kanalar: list, filter_namn=None) -> None:
    if filter_namn:
        laag = [f.lower() for f in filter_namn]
        kanalar = [k for k in kanalar if k["namn"].lower() in laag]
    if not kanalar:
        print("  (ingen kanalar)")
        return
    bredde = max(len(f"{k['node']}/{k['namn']}") for k in kanalar)
    for k in kanalar:
        verdi = k["verdi"]
        vis = f"{verdi:>12.3f}" if isinstance(verdi, (int, float)) else f"{str(verdi):>12}"
        merke = "" if k["tilkobla"] else "  (fråkobla)"
        print(f"  {k['node']}/{k['namn']:<{bredde}}  {vis} {k['eining']}{merke}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Les måledata frå ein PQTech-hub.")
    ap.add_argument("--url", default=os.environ.get("PQTECH_URL", "https://opendac.pqtech.no"),
                    help="Hub-URL (default: $PQTECH_URL eller opendac.pqtech.no)")
    ap.add_argument("--nokkel", default=os.environ.get("PQTECH_API_NOKKEL", ""),
                    help="API-nøkkel (default: $PQTECH_API_NOKKEL)")
    ap.add_argument("--straum", action="store_true", help="Live via SSE i staden for eitt oppslag")
    ap.add_argument("--intervall", type=float, default=2.0, help="Sekund mellom oppdateringar (0.5-60)")
    ap.add_argument("--kanal", action="append", help="Vis berre denne kanalen (kan gjentakast)")
    args = ap.parse_args()

    if not args.nokkel:
        print("Manglar API-nøkkel. Bruk --nokkel eller sett PQTECH_API_NOKKEL.", file=sys.stderr)
        return 2

    klient = HubKlient(args.url, args.nokkel)
    try:
        info = klient.info()
    except requests.HTTPError as e:
        kode = e.response.status_code if e.response is not None else "?"
        if kode == 401:
            print("401: nøkkelen er ugyldig, deaktivert eller utgått.", file=sys.stderr)
        else:
            print(f"Feil frå hubben: {e}", file=sys.stderr)
        return 1
    except requests.RequestException as e:
        print(f"Fekk ikkje kontakt med {args.url}: {e}", file=sys.stderr)
        return 1

    print(f"{info['namn']} ({info['modus']}, {info['antal_kanalar']} kanalar tilgjengelege"
          f" for nøkkelen «{info.get('nokkel_namn') or '?'}»)")

    if not args.straum:
        skriv_ut(klient.kanalar(), args.kanal)
        return 0

    print("Live — Ctrl+C for å avslutte.\n")
    try:
        for pakke in klient.straum(args.intervall):
            print(f"\x1b[2J\x1b[H{pakke['tid']}")
            skriv_ut(pakke["kanalar"], args.kanal)
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
