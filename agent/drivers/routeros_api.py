"""Client dell'API binaria di MikroTik (porta 8728).

Serve per RouterOS 6, dove il REST non esiste: e' stato introdotto in RouterOS
7.1. Sul campo la 6.x e' ancora diffusissima - un hAP ac lite del 2020 con 64 MB
di RAM non passa a RouterOS 7 con leggerezza - quindi questo non e' un ripiego
per un caso raro, e' la strada normale per una fetta grande del parco macchine.

Il protocollo e' semplice e ben definito:

* un **messaggio** e' una sequenza di parole, chiusa da una parola vuota;
* ogni parola e' preceduta dalla sua lunghezza, codificata in 1-5 byte a seconda
  di quanto e' grande (i valori piccoli occupano un byte solo);
* si invia il comando (``/ip/dhcp-server/lease/print``) seguito dagli attributi
  (``=key=value``);
* si ricevono messaggi che iniziano per ``!re`` (un record), ``!done`` (fine),
  ``!trap`` (errore applicativo) o ``!fatal``.

Login: da RouterOS 6.43 in poi si manda utente e password in chiaro dentro il
comando ``/login``. Le versioni precedenti usavano una sfida MD5; qui e'
implementata anche quella, perche' un parco macchine vecchio ha spesso anche
qualche 6.42.
"""
from __future__ import annotations

import asyncio
import binascii
import hashlib
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8728


class RouterOSApiError(Exception):
    """Errore applicativo restituito dal router (!trap o !fatal)."""


def encode_length(length: int) -> bytes:
    """Codifica la lunghezza di una parola secondo le regole dell'API.

    I primi bit dicono quanti byte occupa il numero. E' l'unica parte non
    ovvia del protocollo, ed e' anche l'unica dove si sbaglia davvero.
    """
    if length < 0x80:
        return bytes([length])
    if length < 0x4000:
        length |= 0x8000
        return length.to_bytes(2, "big")
    if length < 0x200000:
        length |= 0xC00000
        return length.to_bytes(3, "big")
    if length < 0x10000000:
        length |= 0xE0000000
        return length.to_bytes(4, "big")
    return b"\xf0" + length.to_bytes(4, "big")


def encode_word(word: str) -> bytes:
    raw = word.encode("utf-8")
    return encode_length(len(raw)) + raw


class RouterOSApi:
    """Connessione all'API binaria. Un'istanza per ciclo di raccolta."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = DEFAULT_PORT,
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self._username = username
        self._password = password
        self._timeout = timeout
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    # ------------------------------------------------------------------
    # Connessione
    # ------------------------------------------------------------------
    async def __aenter__(self) -> "RouterOSApi":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout=self._timeout
        )
        await self._login()

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    async def _login(self) -> None:
        # RouterOS >= 6.43: password in chiaro nel comando di login.
        reply = await self.talk(["/login", f"=name={self._username}", f"=password={self._password}"])

        challenge = None
        for kind, attrs in reply:
            if kind == "!done" and "ret" in attrs:
                challenge = attrs["ret"]

        if challenge is None:
            return  # login moderno riuscito

        # RouterOS <= 6.42: sfida MD5. Il router ha risposto con un nonce.
        digest = hashlib.md5()
        digest.update(b"\x00")
        digest.update(self._password.encode("utf-8"))
        digest.update(binascii.unhexlify(challenge))
        await self.talk(
            [
                "/login",
                f"=name={self._username}",
                "=response=00" + digest.hexdigest(),
            ]
        )

    # ------------------------------------------------------------------
    # Protocollo
    # ------------------------------------------------------------------
    async def _read_length(self) -> int:
        first = (await self._read_exactly(1))[0]
        if first & 0x80 == 0:
            return first
        if first & 0xC0 == 0x80:
            rest = await self._read_exactly(1)
            return ((first & ~0xC0) << 8) + rest[0]
        if first & 0xE0 == 0xC0:
            rest = await self._read_exactly(2)
            return ((first & ~0xE0) << 16) + int.from_bytes(rest, "big")
        if first & 0xF0 == 0xE0:
            rest = await self._read_exactly(3)
            return ((first & ~0xF0) << 24) + int.from_bytes(rest, "big")
        rest = await self._read_exactly(4)
        return int.from_bytes(rest, "big")

    async def _read_exactly(self, count: int) -> bytes:
        assert self._reader is not None
        return await asyncio.wait_for(self._reader.readexactly(count), timeout=self._timeout)

    async def _read_word(self) -> str:
        length = await self._read_length()
        if length == 0:
            return ""
        return (await self._read_exactly(length)).decode("utf-8", errors="replace")

    async def talk(self, words: List[str]) -> List[tuple]:
        """Invia un messaggio e raccoglie le risposte fino a !done.

        Ritorna una lista di ``(tipo, attributi)``, dove tipo e' "!re", "!done"
        o "!trap".
        """
        assert self._writer is not None
        payload = b"".join(encode_word(w) for w in words) + b"\x00"
        self._writer.write(payload)
        await self._writer.drain()

        replies: List[tuple] = []
        current_kind: Optional[str] = None
        current_attrs: Dict[str, str] = {}

        while True:
            word = await self._read_word()

            if word == "":
                # Fine del messaggio corrente
                if current_kind is not None:
                    replies.append((current_kind, current_attrs))
                    if current_kind in ("!done", "!fatal"):
                        break
                current_kind, current_attrs = None, {}
                continue

            if word.startswith("!"):
                current_kind, current_attrs = word, {}
            elif word.startswith("="):
                # "=key=value", dove value puo' contenere altri "="
                body = word[1:]
                key, _, value = body.partition("=")
                current_attrs[key] = value

        for kind, attrs in replies:
            if kind == "!trap":
                raise RouterOSApiError(attrs.get("message", "errore dal router"))
            if kind == "!fatal":
                raise RouterOSApiError(attrs.get("message", "connessione chiusa dal router"))

        return replies

    async def get_all(self, path: str) -> List[Dict[str, str]]:
        """Legge una tabella intera. `path` come "/ip/dhcp-server/lease"."""
        replies = await self.talk([f"{path}/print"])
        return [attrs for kind, attrs in replies if kind == "!re"]
