"""Driver MikroTik via REST API — RouterOS 7.1 e successivi.

Il REST non esiste su RouterOS 6: la' serve `mikrotik_api.py`. La logica di
lettura e fusione delle tabelle e' comune ai due e sta in `mikrotik_tables.py`;
qui c'e' solo il trasporto HTTP.

Requisiti sul router:

    /certificate add name=netmonitor common-name=netmonitor days-valid=3650
    /certificate sign netmonitor
    /ip service set www-ssl certificate=netmonitor disabled=no
    /user add name=netmonitor group=read password=...

Il certificato non e' un dettaglio: senza, `www-ssl` accetta la connessione ma
l'handshake TLS fallisce con "sslv3 alert handshake failure".

Il gruppo ``read`` basta: questo driver non scrive nulla.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from drivers.mikrotik_tables import MikroTikTables

logger = logging.getLogger(__name__)


class MikroTikRestDriver(MikroTikTables):
    name = "mikrotik-rest"

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        timeout: float = 10.0,
        verify_tls: bool = False,
        scheme: str = "https",
        transport=None,
    ) -> None:
        super().__init__()
        self.host = host
        self.base = f"{scheme}://{host}/rest"
        self._auth = (username, password)
        self._timeout = timeout
        self._verify = verify_tls
        # Punto di innesto per i test: permette di far girare il driver contro
        # risposte finte, senza un router sotto mano.
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            auth=self._auth,
            timeout=self._timeout,
            verify=self._verify,
            transport=self._transport,
        )

    async def _get(self, client: httpx.AsyncClient, path: str) -> Optional[Any]:
        """GET di un endpoint. None se non c'e' o non risponde.

        Ogni tabella viene letta separatamente e un fallimento non ferma le
        altre: su un router senza pacchetto wireless la registration-table
        legacy non esiste, e non e' un guasto.
        """
        try:
            resp = await client.get(f"{self.base}{path}")
        except Exception as e:
            # Registrato, non solo scritto a debug: se falliscono tutte le
            # letture questo e' l'unico indizio del perche'.
            self._last_error = f"{type(e).__name__}: {e}"
            logger.debug("MikroTik %s: %s non raggiungibile: %s", self.host, path, e)
            return None
        if resp.status_code == 404:
            return None
        if resp.status_code == 401:
            self._last_error = "401: credenziali rifiutate dal router"
            return None
        if resp.status_code >= 400:
            self._last_error = f"HTTP {resp.status_code} su {path}"
            logger.debug("MikroTik %s: %s ha risposto %s", self.host, path, resp.status_code)
            return None
        try:
            return resp.json()
        except Exception:
            logger.debug("MikroTik %s: %s non ha restituito JSON", self.host, path)
            return None
