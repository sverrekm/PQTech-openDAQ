#!/usr/bin/env python3
"""
Modbus Manager — felles polling-lag for direkte- og hub-modus
==============================================================
Handterer ein eller fleire Modbus-nodar (FjernNode med type=modbus_tcp).
Ein polling-tråd per node, med auto-reconnect og cache av siste verdiar.

Bruk:
    manager = ModbusManager()
    manager.start([node1, node2])
    verdiar = manager.hent_verdiar()   # {(node_id, reg_addr): verdi}
    manager.stop()
"""

import logging
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Callable

from hub_konfig import FjernNode, NODE_TYPE_MODBUS_TCP
import modbus_klient

log = logging.getLogger('modbus_manager')


class ModbusManager:
    """Handterer fleire modbus-nodar med polling-trådar per node."""

    def __init__(self, status_callback: Optional[Callable] = None):
        self._klientar: Dict[str, modbus_klient.ModbusKlient] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._verdiar: Dict[Tuple[str, int], float] = {}
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._nodar: List[FjernNode] = []
        self._status: Dict[str, dict] = {}   # node_id -> status dict
        self._status_callback = status_callback  # kallast når status endrar seg

    def start(self, nodar: List[FjernNode]):
        """Start polling for alle modbus-type nodar i lista. Ikkje-modbus vert ignorert."""
        self._stop.clear()
        self._nodar = [n for n in nodar if n.type == NODE_TYPE_MODBUS_TCP and n.aktivert]
        for node in self._nodar:
            self._start_node(node)
        log.info(f"ModbusManager starta: {len(self._nodar)} nodar")

    def stop(self):
        """Stopp alle polling-trådar og lukk tilkoblingar."""
        self._stop.set()
        with self._lock:
            klientar = list(self._klientar.values())
            self._klientar.clear()
        for k in klientar:
            try:
                k.lukk()
            except Exception:
                pass
        self._threads.clear()
        with self._lock:
            self._verdiar.clear()
        log.info("ModbusManager stoppa")

    def restart(self, nodar: List[FjernNode]):
        """Stopp + start med ny node-liste."""
        self.stop()
        self.start(nodar)

    def hent_verdiar(self) -> Dict[Tuple[str, int], float]:
        """Returner snapshot av cached verdiar."""
        with self._lock:
            return dict(self._verdiar)

    def hent_status(self) -> Dict[str, dict]:
        """Returner per-node status."""
        with self._lock:
            return {k: dict(v) for k, v in self._status.items()}

    def er_tilkobla(self, node_id: str) -> bool:
        with self._lock:
            return self._status.get(node_id, {}).get("tilkobla", False)

    def _oppdater_status(self, node_id: str, **kwargs):
        with self._lock:
            if node_id not in self._status:
                self._status[node_id] = {
                    "tilkobla": False, "feil": None,
                    "sist_sett": None, "tilkobla_sidan": None,
                }
            self._status[node_id].update(kwargs)
            snap = dict(self._status[node_id])
        if self._status_callback:
            try:
                self._status_callback(node_id, snap)
            except Exception as e:
                log.warning(f"status_callback feilet: {e}")

    def _start_node(self, node: FjernNode):
        """Opprett klient + spawn polling-tråd for ein node."""
        klient = modbus_klient.ModbusKlient(
            host=node.adresse,
            port=node.port,
            unit_id=node.modbus_unit_id,
            timeout_ms=node.modbus_timeout_ms,
            base_adresse=node.modbus_base_adresse,
        )
        ok = klient.koble_til()
        with self._lock:
            self._klientar[node.id] = klient

        self._oppdater_status(
            node.id,
            tilkobla=ok,
            feil=klient.siste_feil,
            sist_sett=datetime.now().isoformat() if ok else None,
            tilkobla_sidan=datetime.now().isoformat() if ok else None,
        )
        if ok:
            log.info(f"Modbus '{node.namn}' {node.adresse}:{node.port} — "
                     f"{len(node.modbus_registers)} register")

        node_id = node.id
        node_namn = node.namn
        registers = list(node.modbus_registers)
        poll_hz = max(0.1, node.modbus_poll_hz)
        intervall = 1.0 / poll_hz

        def poll_loop():
            reconnect_pause = 5.0
            siste_ok = ok
            while not self._stop.is_set():
                with self._lock:
                    k = self._klientar.get(node_id)
                if k is None:
                    return

                if not k.tilkobla:
                    if siste_ok:
                        log.warning(f"Modbus '{node_namn}' fråkobla: {k.siste_feil}")
                    siste_ok = False
                    k.koble_til()
                    if k.tilkobla:
                        log.info(f"Modbus '{node_namn}' rekobla")
                        siste_ok = True
                        self._oppdater_status(
                            node_id, tilkobla=True, feil=None,
                            tilkobla_sidan=datetime.now().isoformat(),
                        )
                    else:
                        self._oppdater_status(
                            node_id, tilkobla=False, feil=k.siste_feil,
                        )
                        if self._stop.wait(reconnect_pause):
                            return
                        continue

                # Les alle register
                for reg in registers:
                    if self._stop.is_set():
                        return
                    v = k.les_register(reg)
                    if v is not None:
                        with self._lock:
                            self._verdiar[(node_id, reg.adresse)] = v

                if k.tilkobla:
                    self._oppdater_status(
                        node_id, sist_sett=datetime.now().isoformat(),
                    )

                if self._stop.wait(intervall):
                    return

        thread = threading.Thread(target=poll_loop, daemon=True,
                                  name=f"modbus_{node.id}")
        thread.start()
        self._threads[node.id] = thread

    def fjern_node(self, node_id: str):
        """Stopp polling for ein enkelt node (brukt ved fjern_node_api)."""
        with self._lock:
            klient = self._klientar.pop(node_id, None)
            self._status.pop(node_id, None)
            for key in list(self._verdiar.keys()):
                if key[0] == node_id:
                    self._verdiar.pop(key, None)
        if klient is not None:
            try:
                klient.lukk()
            except Exception:
                pass
        self._threads.pop(node_id, None)

    def legg_til_node(self, node: FjernNode) -> bool:
        """Legg til ein ny modbus-node i køyrande manager."""
        if node.type != NODE_TYPE_MODBUS_TCP:
            return False
        self.fjern_node(node.id)  # sikre at ingen gammal finst
        self._start_node(node)
        return True

    def rekoble_node(self, node_id: str) -> bool:
        """Tving rekobling av ein node."""
        with self._lock:
            klient = self._klientar.get(node_id)
        if klient is None:
            return False
        klient.lukk()
        return klient.koble_til()
