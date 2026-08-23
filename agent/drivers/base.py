"""Interfaccia comune dei driver di gateway.

Un driver sa parlare con il router di un sito e leggerne le tabelle. Serve
perche' una scansione dalla LAN vede solo chi risponde: lease DHCP,
registration-table WiFi con il segnale e bridge host table stanno **dentro** il
router, e senza chiedergliele l'appliance monitora meno di quanto potrebbe.

Ogni driver espone due metodi soli:

* ``probe()``  - questo router parla la mia lingua? Deve essere veloce e non
  deve mai sollevare eccezioni: un router che non risponde non e' un errore del
  programma, e' un router che non risponde.
* ``collect()`` - leggi tutto quello che sai leggere.

Aggiungere un brand significa aggiungere un file qui dentro e registrarlo. Il
resto dell'agent non cambia: e' il motivo per cui esiste questo modulo invece
di chiamare direttamente le API MikroTik da ``agent.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol, runtime_checkable

from scanner import DiscoveredClient, DiscoveredDevice


@dataclass
class GatewayInfo:
    """Il router stesso: quello che finisce nell'heartbeat come `router`."""

    ip: Optional[str] = None
    identity: Optional[str] = None
    board: Optional[str] = None
    version: Optional[str] = None
    uptime_seconds: Optional[int] = None
    cpu_load: Optional[float] = None
    free_memory: Optional[int] = None
    total_memory: Optional[int] = None
    vendor: Optional[str] = None


@dataclass
class GatewayData:
    """Tutto cio' che un driver e' riuscito a leggere in un ciclo."""

    gateway: Optional[GatewayInfo] = None
    devices: List[DiscoveredDevice] = field(default_factory=list)
    clients: List[DiscoveredClient] = field(default_factory=list)
    # Cosa ha funzionato davvero: serve a distinguere "nessun client WiFi" da
    # "non sono riuscito a leggere la tabella WiFi".
    read_ok: List[str] = field(default_factory=list)
    read_failed: List[str] = field(default_factory=list)


@runtime_checkable
class GatewayDriver(Protocol):
    name: str

    async def probe(self) -> bool: ...

    async def collect(self) -> GatewayData: ...


def signal_to_int(raw) -> Optional[int]:
    """Estrae il numero dal campo del segnale, qualunque forma abbia.

    A seconda di versione e driver RouterOS restituisce ``-38``, ``-38dBm``,
    ``-38@6Mbps`` o ``-38dBm@6.0Mbps``. Invece di elencare i formati si leggono
    segno e cifre iniziali e si butta il resto: e' lo stesso approccio usato
    nello script RouterOS, dove il tentativo di indovinare il formato aveva gia'
    fatto perdere il segnale di tutti i client.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    out = ""
    for index, char in enumerate(text):
        if index == 0 and char == "-":
            out = "-"
        elif char.isdigit():
            out += char
        else:
            break

    if out in ("", "-"):
        return None
    return int(out)
