"""NetMonitor Universal Agent — reverse WebSocket + push telemetry.

This agent is meant to run on the local network of a site (Raspberry Pi,
OpenWrt router, MikroTik container, small Linux gateway, Windows PC, etc.).
It connects outbound to the cloud backend, so it works behind NAT.
"""
import asyncio
import json
import logging
import sys
from urllib.parse import urlencode

import platform
import socket

import httpx
import websockets

from config import config
from scanner import (
    DiscoveredDevice,
    discover_network,
    get_default_gateway,
    get_local_ip,
    get_local_network,
    get_uptime_seconds,
    sweep,
)

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("netmonitor-agent")

TELEMETRY_URL = f"{config.BACKEND_URL}/api/v1/sites/agent/telemetry"
HEARTBEAT_URL = f"{config.BACKEND_URL}/api/v1/sites/agent/heartbeat"


class Agent:
    def __init__(self) -> None:
        self.ws_url = f"{config.WS_URL}?{urlencode({'token': config.AGENT_TOKEN})}"
        token_qs = urlencode({"agent_token": config.AGENT_TOKEN})
        self.telemetry_url = f"{TELEMETRY_URL}?{token_qs}"
        self.heartbeat_url = f"{HEARTBEAT_URL}?{token_qs}"
        self.running = True
        self.keepalive_task: asyncio.Task | None = None
        self.telemetry_task: asyncio.Task | None = None
        self.presence_task: asyncio.Task | None = None
        # Vuoto in configurazione = ricavato dalle rotte, non indovinato
        self.network = config.SCAN_NETWORK or get_local_network()
        # Riempito dal driver del router, se configurato.
        self.gateway_info = None

    async def run(self) -> None:
        logger.info("NetMonitor Agent starting...")
        logger.info("Backend HTTP: %s", config.BACKEND_URL)
        logger.info("Backend WS:   %s", config.WS_URL)
        logger.info("Token:        %s...", config.AGENT_TOKEN[:8])

        logger.info("Rete:         %s", self.network or "NON RILEVATA")
        logger.info("Gateway:      %s", get_default_gateway() or "non rilevato")

        # Heartbeat e telemetria girano separati, e non per eleganza: se la
        # scansione fallisce o e' lenta, il sito deve restare "online" lo
        # stesso. E' la stessa scelta fatta nello script RouterOS dopo che un
        # payload malformato aveva fatto risultare offline un sito perfettamente
        # funzionante.
        self.presence_task = asyncio.create_task(self._presence_loop())
        self.telemetry_task = asyncio.create_task(self._telemetry_loop())

        while self.running:
            try:
                logger.info("Connecting to backend WebSocket...")
                async with websockets.connect(self.ws_url) as ws:
                    logger.info("WebSocket connected")
                    self.keepalive_task = asyncio.create_task(self._ws_keepalive(ws))
                    await self._receive_loop(ws)
            except websockets.InvalidStatusCode as e:
                logger.error(
                    "WebSocket refused with status %s. Check AGENT_TOKEN.", e.status_code
                )
                await asyncio.sleep(config.RECONNECT_DELAY)
            except websockets.ConnectionClosed:
                logger.warning("WebSocket closed, reconnecting in %ds...", config.RECONNECT_DELAY)
                await asyncio.sleep(config.RECONNECT_DELAY)
            except Exception as e:
                logger.error("WebSocket error: %s", e)
                await asyncio.sleep(config.RECONNECT_DELAY)
            finally:
                if self.keepalive_task:
                    self.keepalive_task.cancel()
                    try:
                        await self.keepalive_task
                    except asyncio.CancelledError:
                        pass
                    self.keepalive_task = None

    async def _presence_loop(self) -> None:
        """Dice al cloud che il collector e' vivo, indipendentemente dai dati."""
        while self.running:
            try:
                await self._send_heartbeat()
            except Exception as e:
                logger.error("Heartbeat fallito: %s", e)
            await asyncio.sleep(config.HEARTBEAT_INTERVAL)

    def heartbeat_payload(self) -> dict:
        """Il corpo dell'heartbeat, separato dall'invio per poterlo verificare.

        `collector` e `router` sono due cose distinte: il collector e' questo
        box su una porta LAN, il router e' il gateway del sito. Coincidono solo
        quando il collector e' lo script che gira sul router stesso.
        """
        payload = {
            "collector": {
                "kind": "agent",
                "version": config.AGENT_VERSION,
                "hostname": socket.gethostname(),
                "ip": get_local_ip(),
                "os": f"{platform.system()} {platform.release()}",
                "uptime_seconds": get_uptime_seconds(),
            }
        }

        gw = getattr(self, "gateway_info", None)
        if gw:
            payload["router"] = {
                "identity": gw.identity,
                "ip": gw.ip,
                "board": gw.board,
                "version": gw.version,
                "uptime_seconds": gw.uptime_seconds,
                "cpu_load": gw.cpu_load,
                "free_memory": gw.free_memory,
                "total_memory": gw.total_memory,
            }
        return payload

    async def _send_heartbeat(self) -> None:
        payload = self.heartbeat_payload()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(self.heartbeat_url, json=payload)
            resp.raise_for_status()
            logger.info("Heartbeat ok")

    def _gateway_driver(self):
        """Il driver del router del sito, se e' configurato e riconosciuto.

        Senza credenziali non c'e' driver e l'agent si limita alla scansione
        ARP: vede chi c'e', ma non sa dire se e' via cavo o radio, su quale
        porta, con che segnale. Quelle informazioni esistono solo dentro il
        router, e vanno chieste a lui.
        """
        if config.ROUTER_DRIVER == "none":
            return None
        if not (config.ROUTER_USER and config.ROUTER_PASSWORD):
            return None

        host = config.GATEWAY_IP or get_default_gateway()
        if not host:
            logger.warning("Nessun gateway rilevato: driver del router non attivabile")
            return None

        from drivers.mikrotik_rest import MikroTikRestDriver

        if config.ROUTER_SCHEME == "http":
            logger.warning(
                "Driver del router su http: utente e password viaggiano in chiaro "
                "sulla LAN. Preferibile assegnare un certificato a www-ssl."
            )

        return MikroTikRestDriver(
            host,
            config.ROUTER_USER,
            config.ROUTER_PASSWORD,
            timeout=config.ROUTER_TIMEOUT,
            verify_tls=config.ROUTER_VERIFY_TLS,
            scheme=config.ROUTER_SCHEME,
        )

    async def _enrich_from_gateway(self, devices, clients):
        """Unisce la scansione con quello che sa il router.

        Le due fonti non sono equivalenti: il router e' autorevole su tipo di
        connessione, interfaccia e segnale, perche' li osserva direttamente. La
        scansione ARP serve a non perdere chi il router non conosce - un
        dispositivo con IP statico e senza lease, per esempio.
        """
        driver = self._gateway_driver()
        if driver is None:
            self.gateway_info = None
            return devices, clients

        try:
            data = await driver.collect()
        except Exception as e:
            logger.error("Lettura dal router fallita (%s): %s", driver.name, e)
            self.gateway_info = None
            return devices, clients

        if data.read_failed:
            logger.warning("Router: tabelle non lette: %s", ", ".join(data.read_failed))
        logger.info(
            "Router (%s): %d device, %d client da %s",
            driver.name,
            len(data.devices),
            len(data.clients),
            ", ".join(data.read_ok) or "nessuna tabella",
        )

        self.gateway_info = data.gateway

        # Device: quelli del router hanno la precedenza, sono identificati.
        by_ip = {d.ip: d for d in devices}
        for d in data.devices:
            by_ip[d.ip] = d
        if data.gateway and data.gateway.ip and data.gateway.ip not in by_ip:
            by_ip[data.gateway.ip] = DiscoveredDevice(
                ip=data.gateway.ip,
                name=data.gateway.identity,
                vendor=data.gateway.vendor,
                model=data.gateway.board,
            )

        # Client: chiave il MAC. Il router sovrascrive, la scansione integra.
        by_mac = {c.mac: c for c in clients if c.mac}
        for c in data.clients:
            if not c.mac:
                continue
            existing = by_mac.get(c.mac)
            if existing and "arp" in existing.sources and "arp" not in c.sources:
                c.sources.append("arp")
            if existing and not c.ip:
                c.ip = existing.ip
            by_mac[c.mac] = c

        # Un apparato di rete non e' anche un client.
        infra = {d.mac for d in by_ip.values() if d.mac}
        merged_clients = [c for mac, c in by_mac.items() if mac not in infra]

        return list(by_ip.values()), merged_clients

    async def _telemetry_loop(self) -> None:
        """Periodically scan the local network and push telemetry to the cloud."""
        # Send first telemetry soon after start
        await asyncio.sleep(5)
        while self.running:
            try:
                await self._send_telemetry()
            except Exception as e:
                logger.error("Telemetry push failed: %s", e)
            await asyncio.sleep(config.TELEMETRY_INTERVAL)

    async def _send_telemetry(self) -> None:
        if not self.network:
            logger.error(
                "Nessuna rete da scansionare: imposta SCAN_NETWORK nel file .env"
            )
            return

        logger.info("Raccolta telemetria per %s...", self.network)
        devices, clients = await sweep(self.network)
        devices, clients = await self._enrich_from_gateway(devices, clients)

        sent_devices = await self._post_batch(
            {
                "devices": [
                    {
                        "ip": d.ip,
                        "mac": d.mac,
                        "name": d.name,
                        "status": "online",
                        "vendor": d.vendor,
                        "model": d.model,
                    }
                    for d in devices
                ],
                "clients": [],
                "batch": {"index": 0, "total": 1, "kind": "devices"},
            },
            "device",
        )

        # I client vanno a lotti: un lotto che fallisce non porta via gli altri.
        size = config.CLIENT_BATCH_SIZE
        chunks = [clients[i : i + size] for i in range(0, len(clients), size)] or []
        sent_clients = 0
        for index, chunk in enumerate(chunks):
            sent_clients += await self._post_batch(
                {
                    "devices": [],
                    "clients": [
                        {
                            "mac": c.mac,
                            "ip": c.ip,
                            "hostname": c.hostname,
                            "connection_type": c.connection_type,
                            "interface_name": c.interface_name,
                            "signal_dbm": c.signal_dbm,
                            "device_ip": c.device_ip,
                            "sources": c.sources,
                        }
                        for c in chunk
                    ],
                    "batch": {"index": index, "total": len(chunks), "kind": "clients"},
                },
                "client",
            )

        logger.info(
            "Ciclo completato: %d device, %d/%d client",
            sent_devices,
            sent_clients,
            len(clients),
        )

    async def _post_batch(self, payload: dict, label: str) -> int:
        """Invia un lotto. Non solleva: un lotto perso non ferma il ciclo."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(self.telemetry_url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return int(data.get("devices" if label == "device" else "clients", 0))
        except Exception as e:
            logger.error("Invio lotto %s fallito: %s", label, e)
            return 0

    async def _receive_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        async for raw in ws:
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Received invalid JSON: %s", raw[:200])
                continue

            action = message.get("action")
            payload = message.get("payload", {})
            logger.info("Received WS command: %s", action)

            if action == "start_discovery":
                network = payload.get("network") or self.network
                asyncio.create_task(self._handle_discovery(ws, network))
            elif action == "ping":
                await self._send_ws(ws, {"action": "pong", "payload": {}})
            else:
                logger.info("Ignoring unknown WS action: %s", action)

    async def _handle_discovery(
        self, ws: websockets.WebSocketClientProtocol, network: str
    ) -> None:
        try:
            devices = await discover_network(network)
            payload = {
                "devices": [
                    {
                        "ip": d.ip,
                        "mac": d.mac,
                        "name": d.name,
                        "vendor": d.vendor,
                        "model": d.model,
                    }
                    for d in devices
                ]
            }
            await self._send_ws(ws, {"action": "discovery_result", "payload": payload})
        except Exception as e:
            logger.exception("Discovery failed: %s", e)
            await self._send_ws(
                ws,
                {
                    "action": "discovery_result",
                    "payload": {"error": str(e), "devices": []},
                },
            )

    async def _ws_keepalive(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Tiene aperto il WebSocket dei comandi. Non e' l'heartbeat del sito."""
        while True:
            try:
                await asyncio.sleep(config.HEARTBEAT_INTERVAL)
                await self._send_ws(ws, {"action": "pong", "payload": {}})
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("Heartbeat failed: %s", e)
                break

    async def _send_ws(self, ws: websockets.WebSocketClientProtocol, message: dict) -> None:
        try:
            await ws.send(json.dumps(message))
        except Exception as e:
            logger.error("Failed to send WS message: %s", e)


def main() -> None:
    if not config.AGENT_TOKEN or "YOUR" in config.AGENT_TOKEN.upper():
        logger.error(
            "AGENT_TOKEN is missing. Copy agent/.env.example to agent/.env and set the token."
        )
        sys.exit(1)

    try:
        asyncio.run(Agent().run())
    except KeyboardInterrupt:
        logger.info("Agent stopped by user")


if __name__ == "__main__":
    main()
