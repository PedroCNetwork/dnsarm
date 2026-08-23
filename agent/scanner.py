"""Primitive di discovery usate dall'agent (appliance Armbian, Raspberry, PC).

Nota importante sul modello dei dati: questo agent sta su una porta LAN, fuori
dal percorso del traffico. Vede quindi molto meno del gateway. In particolare
NON ha accesso a lease DHCP, registration-table WiFi e bridge host table: quelle
si ottengono interrogando il gateway (fase A3, driver del gateway).

Cio' che puo' provare da solo e' la raggiungibilita': un host che risponde a
ICMP e compare in ARP e' presente adesso. Per questo i client scoperti qui
dichiarano la sorgente "arp", che il backend considera prova di presenza reale
al pari di "wifi" e "bridge".
"""
import asyncio
import ipaddress
import logging
import platform
import re
import socket
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

from config import config

logger = logging.getLogger(__name__)
CURRENT_OS = platform.system().lower()

# Porta "discard" (RFC 863): serve solo a dare al kernel un motivo per
# risolvere l'indirizzo fisico. Nessuno deve rispondere, e infatti non risponde.
_DISCARD_PORT = 9


@dataclass
class DiscoveredDevice:
    ip: str
    mac: Optional[str] = None
    name: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None


@dataclass
class DiscoveredClient:
    mac: Optional[str] = None
    ip: Optional[str] = None
    hostname: Optional[str] = None
    connection_type: str = "unknown"
    interface_name: Optional[str] = None
    # Solo il router li conosce: una scansione dalla LAN non puo' saperli.
    signal_dbm: Optional[int] = None
    device_ip: Optional[str] = None
    # Chi lo ha visto: "arp" dalla scansione, "dhcp"/"wifi"/"bridge" dal router.
    sources: List[str] = field(default_factory=list)


async def _ping_host(ip: str) -> bool:
    """Return True if host replies to one ICMP echo request."""
    if CURRENT_OS == "windows":
        cmd = ["ping", "-n", "1", "-w", str(int(config.PING_TIMEOUT * 1000)), ip]
    else:
        cmd = ["ping", "-c", "1", "-W", str(int(config.PING_TIMEOUT)), ip]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=config.PING_TIMEOUT + 1.0)
        return proc.returncode == 0
    except Exception:
        return False


async def ping_usable() -> tuple[bool, str]:
    """Verifica che ping funzioni davvero, prima di fidarsi dei suoi risultati.

    Esiste per un motivo preciso: `_ping_host` restituisce False sia quando
    l'host non risponde sia quando ping non riesce nemmeno a partire. Con
    l'agent avviato da systemd con NoNewPrivileges, ping non puo' acquisire
    CAP_NET_RAW dalla propria file capability e fallisce all'istante: il
    risultato era "0 host attivi", indistinguibile da una rete davvero vuota.
    Un ping verso il loopback separa i due casi in mezzo secondo.
    """
    if CURRENT_OS == "windows":
        return True, ""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", "1", "127.0.0.1",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode == 0:
            return True, ""
        return False, (err or b"").decode(errors="replace").strip()
    except Exception as e:
        return False, str(e)


def _parse_arp_table() -> dict[str, str]:
    """Parse the OS ARP table into a dict {ip: mac}."""
    mapping: dict[str, str] = {}
    try:
        if CURRENT_OS == "windows":
            output = subprocess.check_output(["arp", "-a"], text=True, errors="ignore")
            # Lines like: 192.168.1.1    00-11-22-33-44-55    dynamic
            for line in output.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    ip_candidate = parts[0]
                    mac_candidate = parts[1]
                    if re.match(r"\d+\.\d+\.\d+\.\d+", ip_candidate) and re.match(
                        r"([0-9a-fA-F]{2}[-:]){5}[0-9a-fA-F]{2}", mac_candidate
                    ):
                        mapping[ip_candidate] = mac_candidate.replace("-", ":").lower()
        else:
            # /proc/net/arp: IP, tipo HW, flag, MAC, maschera, interfaccia.
            # Il bit 0x2 dei flag (ATF_COM) dice che la risoluzione e' andata a
            # buon fine. Senza questo controllo si prendono anche le voci
            # "incomplete", cioe' indirizzi interrogati che non hanno mai
            # risposto: sarebbero dispositivi inventati.
            with open("/proc/net/arp", "r", encoding="utf-8") as f:
                next(f)  # salta l'intestazione
                for line in f:
                    parts = line.split()
                    if len(parts) < 4:
                        continue
                    try:
                        complete = int(parts[2], 16) & 0x2
                    except ValueError:
                        complete = 0
                    if complete and parts[3] != "00:00:00:00:00:00":
                        mapping[parts[0]] = parts[3].lower()
    except Exception as e:
        logger.debug("Could not parse ARP table: %s", e)
    return mapping


async def arp_sweep(network_cidr: str) -> dict[str, str]:
    """Scopre i dispositivi via ARP invece che via ping. Ritorna {ip: mac}.

    Perche' ARP e non ICMP: un dispositivo **puo'** ignorare il ping, e molti lo
    fanno di proposito - telefoni, stampanti, Windows col firewall. Ma **non
    puo'** ignorare l'ARP, altrimenti non riuscirebbe a comunicare in rete.
    Misurato sul campo: dove il ping trovava 2 host su 4, ARP li trovava tutti.

    Non serve alcun permesso speciale, e questa e' la parte elegante: si invia
    un datagramma UDP verso una porta scartata e ci pensa il kernel a risolvere
    l'indirizzo fisico prima di spedirlo. Nessun socket raw, nessuna capability.

    Limite noto: una voce ARP di un dispositivo appena scollegato resta valida
    per qualche minuto, quindi un dispositivo puo' comparire poco oltre la sua
    uscita. Ripulire la cache richiederebbe CAP_NET_ADMIN, che non vogliamo; il
    campo `last_active` lato backend copre gia' il caso.
    """
    try:
        net = ipaddress.ip_network(network_cidr, strict=False)
    except ValueError as e:
        logger.error("Rete non valida %s: %s", network_cidr, e)
        return {}

    hosts = [str(h) for h in net.hosts()]
    logger.info("Scansione ARP di %d indirizzi su %s...", len(hosts), network_cidr)

    async def _solicit(targets: List[str], sock) -> List[str]:
        """Tocca ogni indirizzo, ritorna quelli che non e' riuscita a toccare."""
        failed: List[str] = []
        for start in range(0, len(targets), config.ARP_BATCH):
            for ip in targets[start : start + config.ARP_BATCH]:
                try:
                    sock.sendto(b"", (ip, _DISCARD_PORT))
                except OSError:
                    # Tipicamente ENOBUFS: la coda del kernel per gli indirizzi
                    # non ancora risolti e' piena. Non e' un host assente, e'
                    # una richiesta che non e' proprio partita.
                    failed.append(ip)
            await asyncio.sleep(0.05)
        return failed

    # Socket BLOCCANTE, non bloccante. Con il socket non bloccante il buffer di
    # invio si riempiva e sendto restituiva EAGAIN sugli ultimi ~30 indirizzi:
    # scartati in silenzio, e quindi contati come host assenti senza essere mai
    # stati interrogati. Bloccando, il kernel applica la contropressione da se'.
    # I datagrammi sono vuoti e le pause fra i lotti danno tempo di smaltire,
    # quindi in pratica non si blocca mai a lungo.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    still_failed: List[str] = []
    try:
        failed = await _solicit(hosts, sock)
        if failed:
            # Secondo passaggio: la coda nel frattempo si e' svuotata. Senza
            # questo, gli indirizzi caduti verrebbero contati come "assenti"
            # senza essere mai stati interrogati.
            logger.debug("ARP: %d indirizzi da riprovare", len(failed))
            await asyncio.sleep(1.0)
            still_failed = await _solicit(failed, sock)
            if still_failed:
                logger.warning(
                    "ARP: %d indirizzi non interrogati nemmeno al secondo tentativo",
                    len(still_failed),
                )
        sent = len(hosts) - len(still_failed)
    finally:
        sock.close()

    # Le risposte ARP arrivano in pochi millisecondi, ma vanno attese.
    await asyncio.sleep(config.ARP_SETTLE)

    table = _parse_arp_table()
    found = {ip: mac for ip, mac in table.items() if ip in set(hosts)}
    logger.info("ARP: %d indirizzi sollecitati, %d dispositivi trovati", sent, len(found))
    return found


def _resolve_hostname(ip: str) -> Optional[str]:
    try:
        name = socket.gethostbyaddr(ip)[0]
    except Exception:
        return None
    # Alcuni resolver, quando non c'e' un record PTR, restituiscono l'indirizzo
    # stesso. Un "hostname" uguale all'IP non e' un nome: e' rumore in tabella.
    return None if not name or name == ip else name


async def _snmp_get_sysdescr(ip: str) -> Optional[str]:
    """Try to fetch SNMP sysDescr. Requires pysnmp (optional dependency)."""
    try:
        from pysnmp.hlapi.v3arch.asyncio import (
            CommunityData,
            ContextData,
            ObjectIdentity,
            ObjectType,
            SnmpEngine,
            UdpTransportTarget,
            get_cmd,
        )
    except ImportError:
        return None

    for community in config.SNMP_COMMUNITIES:
        community = community.strip()
        if not community:
            continue
        try:
            iterator = get_cmd(
                SnmpEngine(),
                CommunityData(community),
                UdpTransportTarget((ip, 161), timeout=config.SNMP_TIMEOUT, retries=0),
                ContextData(),
                ObjectType(ObjectIdentity("1.3.6.1.2.1.1.1.0")),
            )
            error_indication, error_status, _, var_binds = await iterator
            if not error_indication and not error_status and var_binds:
                value = str(var_binds[0][1])
                if value and value not in ("No Such Object", ""):
                    return value
        except Exception as e:
            logger.debug("SNMP failed for %s community %s: %s", ip, community, e)
    return None


def _parse_vendor_model(sys_descr: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Naive vendor/model extraction from sysDescr."""
    if not sys_descr:
        return None, None
    s = sys_descr.lower()
    vendor = None
    if "mikrotik" in s or "routeros" in s:
        vendor = "mikrotik"
    elif "engenius" in s:
        vendor = "engenius"
    elif "tp-link" in s or "tplink" in s:
        vendor = "tplink"
    elif "tenda" in s:
        vendor = "tenda"
    elif "cisco" in s:
        vendor = "cisco"
    elif "ubiquiti" in s or "unifi" in s:
        vendor = "ubiquiti"
    return vendor, None


def get_default_gateway() -> Optional[str]:
    """IP del gateway di default, cioe' il router del sito.

    Serve a separare l'infrastruttura dai client: il gateway va in `devices`,
    tutto il resto in `clients`. Se configurato a mano, GATEWAY_IP vince.
    """
    if getattr(config, "GATEWAY_IP", ""):
        return config.GATEWAY_IP

    try:
        if CURRENT_OS == "windows":
            output = subprocess.check_output(["route", "print", "0.0.0.0"], text=True, errors="ignore")
            for line in output.splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[0] == "0.0.0.0":
                    return parts[2]
        else:
            # /proc/net/route: destinazione 00000000 = default, gateway in hex little-endian
            with open("/proc/net/route", "r", encoding="utf-8") as f:
                next(f)
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3 and parts[1] == "00000000":
                        raw = int(parts[2], 16)
                        return ".".join(str((raw >> (8 * i)) & 0xFF) for i in range(4))
    except Exception as e:
        logger.debug("Rilevamento gateway fallito: %s", e)
    return None


def get_local_ip() -> Optional[str]:
    """IP con cui il box esce verso la rete, senza inviare nulla."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1: non instradabile, nessun traffico
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return None


def get_local_network() -> Optional[str]:
    """CIDR della rete locale, letto dalle rotte invece che indovinato.

    Un /24 dedotto dall'IP sarebbe sbagliato su ogni rete con maschera diversa,
    e sbaglierebbe in silenzio: la scansione girerebbe a vuoto. Qui si prende
    la rotta di sottorete vera (gateway 00000000 e maschera non nulla).
    """
    try:
        if CURRENT_OS != "windows":
            with open("/proc/net/route", "r", encoding="utf-8") as f:
                rows = [ln.split() for ln in f.readlines()[1:]]
            rows = [r for r in rows if len(r) >= 8]

            # L'interfaccia giusta e' quella che porta al gateway di default.
            # Senza questo vincolo si prende la prima sottorete trovata, che su
            # una macchina con Docker e' un bridge come 172.17.0.0/16: scansione
            # di 65.000 indirizzi inesistenti, e nessun errore a dirlo.
            uplink = next((r[0] for r in rows if r[1] == "00000000"), None)
            if not uplink:
                raise RuntimeError("nessuna rotta di default")

            for r in rows:
                iface, dest, gw, mask = r[0], r[1], r[2], r[7]
                if iface != uplink or gw != "00000000" or mask == "00000000":
                    continue
                net = ".".join(str((int(dest, 16) >> (8 * i)) & 0xFF) for i in range(4))
                prefix = bin(int(mask, 16)).count("1")
                if net != "0.0.0.0" and 8 <= prefix <= 30:
                    return f"{net}/{prefix}"
    except Exception as e:
        logger.debug("Rilevamento rete locale fallito: %s", e)

    # Ripiego: /24 attorno all'IP locale. Dichiarato, non silenzioso.
    ip = get_local_ip()
    if ip:
        logger.warning("Rete locale non rilevata dalle rotte, ripiego su /24 attorno a %s", ip)
        return ".".join(ip.split(".")[:3]) + ".0/24"
    return None


def get_uptime_seconds() -> Optional[int]:
    """Uptime della macchina che ospita il collector."""
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as f:
            return int(float(f.read().split()[0]))
    except Exception:
        return None


async def _ping_sweep(hosts: List[str]) -> List[str]:
    """Ripiego ICMP, usato solo se l'ARP non trova nulla."""
    gate = asyncio.Semaphore(config.SCAN_CONCURRENCY)

    async def _probe(ip: str) -> bool:
        async with gate:
            return await _ping_host(ip)

    results = await asyncio.gather(*[_probe(ip) for ip in hosts])
    return [ip for ip, alive in zip(hosts, results) if alive]


async def discover_network(network_cidr: str) -> List[DiscoveredDevice]:
    """Scopre i dispositivi sulla rete e li arricchisce con hostname e SNMP.

    ARP e' la prova primaria di presenza. ICMP resta come ripiego per reti dove
    l'ARP non e' praticabile - per esempio se il collector non e' sullo stesso
    dominio di broadcast dei dispositivi.
    """
    logger.info("Discovery su %s", network_cidr)

    try:
        net = ipaddress.ip_network(network_cidr, strict=False)
    except ValueError as e:
        logger.error("Rete non valida %s: %s", network_cidr, e)
        return []
    hosts = [str(h) for h in net.hosts()]

    arp_table = await arp_sweep(network_cidr)
    alive_ips = sorted(arp_table, key=lambda ip: tuple(int(p) for p in ip.split(".")))

    if not alive_ips:
        logger.warning("Nessun dispositivo via ARP: provo con ICMP come ripiego.")
        usable, why = await ping_usable()
        if not usable:
            logger.error("Anche ping non e' utilizzabile: %s", why)
            logger.error(
                "Da systemd servono AmbientCapabilities=CAP_NET_RAW e "
                "CapabilityBoundingSet=CAP_NET_RAW nell'unit, altrimenti "
                "NoNewPrivileges impedisce a ping di ottenere CAP_NET_RAW."
            )
            return []
        alive_ips = await _ping_sweep(hosts)
        arp_table = _parse_arp_table()
        logger.info("Ripiego ICMP: %d host attivi", len(alive_ips))

    devices: List[DiscoveredDevice] = []
    for ip in alive_ips:
        mac = arp_table.get(ip)
        name = _resolve_hostname(ip)
        sys_descr = await _snmp_get_sysdescr(ip)
        vendor, model = _parse_vendor_model(sys_descr)
        if not name and sys_descr:
            # Use first line of sysDescr as fallback name
            name = sys_descr.splitlines()[0][:64]

        devices.append(
            DiscoveredDevice(
                ip=ip,
                mac=mac,
                name=name,
                vendor=vendor,
                model=model,
            )
        )

    logger.info("Discovery complete: %d devices", len(devices))
    return devices


async def sweep(network_cidr: str) -> tuple[List[DiscoveredDevice], List[DiscoveredClient]]:
    """Scansiona la rete e separa infrastruttura da client.

    Regola di A1, volutamente grezza: il gateway e' infrastruttura, il resto
    sono client. L'identificazione vera arriva con i driver (A3) e con
    l'Identity Engine (F2); qui conta solo non mescolare le due tabelle.
    """
    discovered = await discover_network(network_cidr)
    gateway_ip = get_default_gateway()

    devices: List[DiscoveredDevice] = []
    clients: List[DiscoveredClient] = []

    for d in discovered:
        if gateway_ip and d.ip == gateway_ip:
            devices.append(d)
            continue
        clients.append(
            DiscoveredClient(
                mac=d.mac,
                ip=d.ip,
                hostname=d.name,
                # Da qui non si puo' sapere se e' cavo o radio: lo dira' il
                # driver del gateway. "unknown" non sovrascrive quanto gia' noto.
                connection_type="unknown",
                sources=["arp"],
            )
        )

    logger.info("Sweep: %d device infrastruttura, %d client", len(devices), len(clients))
    return devices, clients
