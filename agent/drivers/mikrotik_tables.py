"""Lettura delle tabelle di RouterOS, indipendente dal trasporto.

Le stesse tabelle si raggiungono in due modi diversi a seconda della versione:
REST su RouterOS 7, API binaria sulla 8728 su RouterOS 6. Cambia come si chiede,
non cosa si ottiene ne' come va interpretato - quindi la parte che conta vive
qui una volta sola, e i due driver forniscono soltanto il proprio `_fetch`.
"""
from __future__ import annotations

import logging
import re
from typing import Any, List, Optional

from drivers.base import GatewayData, GatewayInfo, signal_to_int
from scanner import DiscoveredClient, DiscoveredDevice

logger = logging.getLogger(__name__)


class MikroTikTables:
    """Logica di lettura e fusione delle tabelle. Il trasporto lo mette chi eredita."""

    name = "mikrotik"

    def __init__(self) -> None:
        self.host = ""
        self._last_error: Optional[str] = None

    async def probe(self) -> bool:
        async with self._client() as client:
            return await self._get(client, "/system/identity") is not None

    async def collect(self) -> GatewayData:
        data = GatewayData()
        self._last_error = None
        try:
            return await self._collect(data)
        except Exception as e:
            # Il trasporto puo' fallire prima ancora della prima lettura: sulla
            # API binaria e' il login, sul REST la connessione TLS. Va detto
            # con il motivo, non lasciato uscire come eccezione anonima.
            self._last_error = f"{type(e).__name__}: {e}"
            logger.error(
                "MikroTik %s: connessione fallita. Motivo: %s", self.host, self._last_error
            )
            return data

    async def _collect(self, data: GatewayData) -> GatewayData:
        async with self._client() as client:
            gateway = await self._read_gateway(client, data)
            data.gateway = gateway

            iface_by_mac = await self._read_bridge_hosts(client, data)
            data.port_by_mac = dict(iface_by_mac)
            neighbours, ap_by_iface, infra_macs = await self._read_neighbours(
                client, data, iface_by_mac
            )
            data.devices = neighbours

            data.clients = await self._read_clients(
                client, data, iface_by_mac, ap_by_iface, infra_macs
            )

        # Se non e' passata nemmeno una tabella non e' un router "vuoto": e' un
        # router che non stiamo raggiungendo. Va detto per intero, con il
        # motivo e con la verifica da fare a mano.
        if not data.read_ok:
            reason = self._last_error or "nessuna risposta"
            logger.error("MikroTik %s: nessuna tabella letta. Motivo: %s", self.host, reason)
            if "401" in reason:
                logger.error(
                    "Le credenziali sono rifiutate: controlla ROUTER_USER e "
                    "ROUTER_PASSWORD in /etc/netmonitor/agent.env."
                )
            else:
                logger.error(
                    "Sul router serve il servizio REST attivo:  "
                    "/ip service enable www-ssl"
                )
                logger.error(
                    "Verifica a mano con:  curl -k -u UTENTE:PASSWORD %s/system/identity",
                    self.base,
                )
        return data

    # ------------------------------------------------------------------
    # Letture
    # ------------------------------------------------------------------
    async def _read_gateway(self, client, data: GatewayData) -> Optional[GatewayInfo]:
        resource = await self._get(client, "/system/resource")
        identity = await self._get(client, "/system/identity")
        if resource is None and identity is None:
            data.read_failed.append("system")
            return None
        data.read_ok.append("system")

        resource = resource or {}
        uptime = resource.get("uptime")

        return GatewayInfo(
            ip=self.host,
            vendor="mikrotik",
            identity=(identity or {}).get("name"),
            board=resource.get("board-name"),
            version=resource.get("version"),
            uptime_seconds=_routeros_uptime_to_seconds(uptime),
            cpu_load=_as_number(resource.get("cpu-load")),
            free_memory=_as_number(resource.get("free-memory")),
            total_memory=_as_number(resource.get("total-memory")),
        )

    async def _read_bridge_hosts(self, client, data: GatewayData) -> dict[str, str]:
        """Mappa MAC -> porta del bridge. Dice dietro quale porta sta ogni MAC."""
        rows = await self._get(client, "/interface/bridge/host")
        if rows is None:
            data.read_failed.append("bridge")
            return {}
        data.read_ok.append("bridge")

        mapping: dict[str, str] = {}
        for row in rows:
            # Le voci "local" sono le interfacce del router stesso, non client.
            if str(row.get("local", "false")).lower() == "true":
                continue
            mac = _norm_mac(row.get("mac-address"))
            iface = row.get("interface")
            if mac and iface:
                mapping[mac] = iface
        return mapping

    async def _read_neighbours(
        self, client, data: GatewayData, iface_by_mac: dict[str, str]
    ) -> tuple[List[DiscoveredDevice], dict[str, str], set]:
        """Vicini MNDP/CDP/LLDP: access point, switch, altri apparati."""
        rows = await self._get(client, "/ip/neighbor")
        if rows is None:
            data.read_failed.append("neighbor")
            return [], {}, set()
        data.read_ok.append("neighbor")

        devices: List[DiscoveredDevice] = []
        ap_by_iface: dict[str, str] = {}
        infra_macs: set = set()

        for row in rows:
            ip = row.get("address")
            mac = _norm_mac(row.get("mac-address"))
            if not ip or mac in infra_macs:
                continue
            infra_macs.add(mac)

            name = row.get("identity") or row.get("board") or row.get("platform") or f"device {ip}"
            devices.append(
                DiscoveredDevice(
                    ip=ip,
                    mac=mac,
                    name=name,
                    vendor=row.get("platform"),
                    model=row.get("board"),
                )
            )
            # Dietro quale porta sta questo apparato? Serve ad attribuire i
            # client di quella porta all'access point giusto.
            iface = iface_by_mac.get(mac or "")
            if iface:
                ap_by_iface[iface] = ip

        return devices, ap_by_iface, infra_macs

    async def _read_clients(
        self,
        client,
        data: GatewayData,
        iface_by_mac: dict[str, str],
        ap_by_iface: dict[str, str],
        infra_macs: set,
    ) -> List[DiscoveredClient]:
        """Unisce lease DHCP, registration-table WiFi e bridge host per MAC.

        L'unione avviene qui, non nel backend: ogni sorgente conosce solo una
        parte dei campi, e un upsert separato per sorgente farebbe azzerare a
        vicenda i dati - e' l'errore che aveva reso inutile la tabella client
        nella prima versione dello script RouterOS.
        """
        merged: dict[str, DiscoveredClient] = {}

        def entry(mac: str) -> DiscoveredClient:
            if mac not in merged:
                merged[mac] = DiscoveredClient(mac=mac)
            return merged[mac]

        # --- lease DHCP: danno IP e hostname ---
        leases = await self._get(client, "/ip/dhcp-server/lease")
        if leases is None:
            data.read_failed.append("dhcp")
        else:
            data.read_ok.append("dhcp")
            for row in leases:
                if str(row.get("status", "")).lower() != "bound":
                    continue
                mac = _norm_mac(row.get("mac-address"))
                if not mac or mac in infra_macs:
                    continue
                item = entry(mac)
                item.ip = row.get("address") or item.ip
                item.hostname = row.get("host-name") or item.hostname
                _add_source(item, "dhcp")

        # --- registration-table: tipo, interfaccia e segnale reale ---
        for path, signal_keys, label in (
            ("/interface/wireless/registration-table", ("signal-strength", "signal-strength-ch0"), "wifi"),
            ("/interface/wifi/registration-table", ("signal", "signal-strength"), "wifi-new"),
        ):
            rows = await self._get(client, path)
            if rows is None:
                # Normale: su un router c'e' l'uno o l'altro driver, non entrambi.
                continue
            data.read_ok.append(label)
            for row in rows:
                mac = _norm_mac(row.get("mac-address"))
                if not mac:
                    continue
                item = entry(mac)
                item.connection_type = "wifi"
                item.interface_name = row.get("interface") or item.interface_name
                for key in signal_keys:
                    value = signal_to_int(row.get(key))
                    if value is not None:
                        item.signal_dbm = value
                        break
                _add_source(item, "wifi")

        # --- bridge host: porta di ogni client, e a quale AP appartiene ---
        radio_ifaces = await self._radio_interfaces(client, data)
        for mac, iface in iface_by_mac.items():
            if mac in infra_macs:
                continue
            item = entry(mac)
            if not item.interface_name:
                item.interface_name = iface
            via = ap_by_iface.get(iface)
            if via:
                item.device_ip = via
                if item.connection_type == "unknown":
                    item.connection_type = "wifi"
            elif item.connection_type == "unknown":
                # Se la porta e' una radio il client e' wireless anche se non
                # compare (piu') nella registration-table: le voci del bridge
                # sopravvivono qualche minuto alla disconnessione.
                item.connection_type = "wifi" if iface in radio_ifaces else "ethernet"
            _add_source(item, "bridge")

        return list(merged.values())

    async def _radio_interfaces(self, client, data: GatewayData) -> set:
        names: set = set()
        for path in ("/interface/wireless", "/interface/wifi"):
            rows = await self._get(client, path)
            for row in rows or []:
                name = row.get("name")
                if name:
                    names.add(name)
        return names


# ----------------------------------------------------------------------
# Helper
# ----------------------------------------------------------------------
def _add_source(item: DiscoveredClient, source: str) -> None:
    if source not in item.sources:
        item.sources.append(source)


def _norm_mac(value) -> Optional[str]:
    if not value:
        return None
    return str(value).strip().upper()


def _as_number(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


_UNIT_SECONDS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}


def _routeros_uptime_to_seconds(value) -> Optional[int]:
    """Converte l'uptime di RouterOS in secondi.

    RouterOS mescola due notazioni nella stessa stringa: le unita' con lettera
    per settimane e giorni, e l'orologio hh:mm:ss per il resto. Esempi reali:
    ``4w2d13:45:12``, ``2d13:45:12``, ``13:45:12``, ``1h2m3s``, ``45s``.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None

    total = 0
    matched = False

    for amount, unit in re.findall(r"(\d+)([wdhms])", text):
        total += int(amount) * _UNIT_SECONDS[unit]
        matched = True

    clock = re.search(r"(\d+):(\d+):(\d+)", text)
    if clock:
        hours, minutes, seconds = (int(g) for g in clock.groups())
        total += hours * 3600 + minutes * 60 + seconds
        matched = True

    return total if matched else None
