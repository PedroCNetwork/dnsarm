"""Driver MikroTik via API binaria — RouterOS 6.

Stesse tabelle del driver REST, stesso risultato, trasporto diverso: sulla 6.x
il REST non esiste e si passa dalla porta 8728. La logica di lettura e fusione
sta in `mikrotik_tables.py` ed e' condivisa.

Requisiti sul router:

    /ip service enable api
    /user add name=netmonitor group=read password=...

Nessun certificato da generare, nessun TLS da negoziare: e' il motivo per cui su
RouterOS 6 questa strada e' anche la meno faticosa. Il canale pero' **non e'
cifrato**: utente e password viaggiano in chiaro sulla LAN. Su RouterOS 6.43 e
successivi il login manda la password nel comando, sulle precedenti c'e' una
sfida MD5 - in nessuno dei due casi il traffico e' protetto. Per un utente in
sola lettura su una rete che si controlla e' un compromesso ragionevole; per
qualcosa di piu' c'e' `api-ssl` sulla 8729, con gli stessi grattacapi di
certificato del REST.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from drivers.mikrotik_tables import MikroTikTables
from drivers.routeros_api import DEFAULT_PORT, RouterOSApi, RouterOSApiError

logger = logging.getLogger(__name__)

# Risorse che descrivono una cosa sola. Il REST le restituisce come oggetto,
# l'API come elenco di un elemento: qui si uniformano, cosi' la logica comune
# non deve sapere da dove arrivano i dati.
_SINGLE_OBJECT = {"/system/resource", "/system/identity"}


class MikroTikApiDriver(MikroTikTables):
    name = "mikrotik-api"

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = DEFAULT_PORT,
        timeout: float = 10.0,
    ) -> None:
        super().__init__()
        self.host = host
        self._username = username
        self._password = password
        self._port = port
        self._timeout = timeout

    def _client(self) -> RouterOSApi:
        return RouterOSApi(
            self.host,
            self._username,
            self._password,
            port=self._port,
            timeout=self._timeout,
        )

    async def _get(self, client: RouterOSApi, path: str) -> Optional[Any]:
        """Legge una tabella. None se non esiste o non risponde.

        Una tabella assente non e' un errore: su un router senza pacchetto
        wireless `/interface/wireless` non esiste e il router risponde con un
        trap. Va trattato come "non c'e'", non come guasto.
        """
        try:
            rows = await client.get_all(path)
        except RouterOSApiError as e:
            # Il router ha risposto, ma quel comando non esiste o e' negato.
            logger.debug("MikroTik %s: %s rifiutato: %s", self.host, path, e)
            return None
        except Exception as e:
            self._last_error = f"{type(e).__name__}: {e}"
            logger.debug("MikroTik %s: %s non raggiungibile: %s", self.host, path, e)
            return None

        if path in _SINGLE_OBJECT:
            return rows[0] if rows else {}
        return rows

    async def probe(self) -> bool:
        try:
            async with self._client() as client:
                return await self._get(client, "/system/identity") is not None
        except Exception as e:
            self._last_error = f"{type(e).__name__}: {e}"
            return False
