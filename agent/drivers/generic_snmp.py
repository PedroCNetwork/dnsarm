"""Driver SNMP generico: funziona su qualunque apparato, senza sapere che marca sia.

Non e' un driver "per una marca in meno": e' il fondo su cui poggia tutto il
resto. Le MIB standard sono le stesse su uno switch TP-Link, su un access point
EnGenius e su un router che nessuno di noi ha mai visto, quindi con queste si
ottiene identita' e topologia senza scrivere una riga per ogni produttore.

Cosa legge, e da quale standard:

* **SNMPv2-MIB** (RFC 3418) - nome, descrizione, tempo di accensione, contatto e
  posizione. E' quello che trasforma "192.168.88.10" in "EWS330AP - Magazzino".
* **LLDP-MIB** (IEEE 802.1AB) - i vicini annunciati su ogni porta. E' l'albero:
  dice quale apparato sta dietro quale porta, ed e' il motivo per cui uno switch
  gestito vale molto piu' di uno non gestito.
* **POWER-ETHERNET-MIB** (RFC 3621) - stato e assorbimento PoE per porta. Serve
  a distinguere "antenna spenta" da "antenna che non risponde": se la porta non
  eroga piu' corrente il problema e' il cavo o l'alimentazione, non il firmware.

Le letture sono indipendenti: un apparato che non espone LLDP resta comunque
identificato, e uno che non fa PoE non e' un errore.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# --- SNMPv2-MIB: il gruppo "system", presente ovunque -----------------------
OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OID_SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
OID_SYS_CONTACT = "1.3.6.1.2.1.1.4.0"
OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"
OID_SYS_LOCATION = "1.3.6.1.2.1.1.6.0"

# --- LLDP-MIB: i vicini, cioe' l'albero ------------------------------------
OID_LLDP_REM_CHASSIS_ID = "1.0.8802.1.1.2.1.4.1.1.5"
OID_LLDP_REM_PORT_ID = "1.0.8802.1.1.2.1.4.1.1.7"
OID_LLDP_REM_PORT_DESC = "1.0.8802.1.1.2.1.4.1.1.8"
OID_LLDP_REM_SYS_NAME = "1.0.8802.1.1.2.1.4.1.1.9"
OID_LLDP_REM_SYS_DESC = "1.0.8802.1.1.2.1.4.1.1.10"
# lldpRemManAddrTable: l'indirizzo di gestione del vicino. Serve perche' un
# albero costruito sui nomi e' fragile - due apparati usciti di fabbrica hanno
# lo stesso nome - mentre l'IP identifica una riga del database.
OID_LLDP_REM_MAN_ADDR = "1.0.8802.1.1.2.1.4.2.1.3"

# --- POWER-ETHERNET-MIB: PoE per porta -------------------------------------
OID_PETH_PORT_DETECTION = "1.3.6.1.2.1.105.1.1.1.6"
OID_PETH_PORT_POWER = "1.3.6.1.2.1.105.1.1.1.11"

# Stati di rilevamento PoE (pethPsePortDetectionStatus, RFC 3621)
PETH_STATES = {
    1: "disabled",
    2: "searching",
    3: "deliveringPower",
    4: "fault",
    5: "test",
    6: "otherFault",
}


@dataclass
class SnmpNeighbour:
    """Un vicino annunciato via LLDP: un ramo dell'albero."""

    local_port: str
    sys_name: Optional[str] = None
    sys_desc: Optional[str] = None
    chassis_id: Optional[str] = None
    port_id: Optional[str] = None
    # Indirizzo di gestione annunciato dal vicino: e' cio' che permette di
    # collegarlo alla riga giusta del database invece che al nome giusto.
    mgmt_ip: Optional[str] = None


@dataclass
class SnmpPoePort:
    port: str
    state: Optional[str] = None
    power_mw: Optional[int] = None


@dataclass
class SnmpDeviceInfo:
    """Cio' che si riesce a sapere di un apparato parlando solo standard."""

    ip: str
    name: Optional[str] = None
    descr: Optional[str] = None
    object_id: Optional[str] = None
    uptime_seconds: Optional[int] = None
    location: Optional[str] = None
    contact: Optional[str] = None
    vendor: Optional[str] = None
    neighbours: List[SnmpNeighbour] = field(default_factory=list)
    poe_ports: List[SnmpPoePort] = field(default_factory=list)
    read_ok: List[str] = field(default_factory=list)
    read_failed: List[str] = field(default_factory=list)


# Enterprise number IANA -> produttore. sysObjectID comincia sempre con
# 1.3.6.1.4.1.<numero>, e quel numero identifica il produttore in modo
# inequivocabile: e' molto piu' affidabile che cercare parole dentro sysDescr.
ENTERPRISE_VENDORS = {
    "14988": "MikroTik",
    "11863": "TP-Link",
    "171": "D-Link",
    "10002": "EnGenius",
    "4413": "Broadcom",
    "9": "Cisco",
    "41112": "Ubiquiti",
    "2011": "Huawei",
    "890": "Zyxel",
    "4526": "Netgear",
    "674": "Dell",
    "311": "Microsoft",
    "8072": "net-snmp (Linux)",
}


# Enterprise che non identificano un produttore: sono stack SNMP generici usati
# da mezzo mondo. Un access point EnGenius standalone si annuncia cosi', e
# fermarsi qui vorrebbe dire scrivere "net-snmp (Linux)" al posto di "EnGenius".
GENERIC_ENTERPRISES = {"8072", "2021", "311"}

# Prefissi MAC. E' il ripiego quando SNMP non identifica il produttore: il MAC
# lo dichiara sempre. Elenco breve e mirato a cio' che si incontra davvero;
# l'importazione del registro IEEE completo e' un lavoro a parte.
OUI_VENDORS = {
    "88:DC:96": "EnGenius", "00:02:6F": "EnGenius", "00:19:70": "EnGenius",
    "E4:8D:8C": "MikroTik", "74:4D:28": "MikroTik", "48:8F:5A": "MikroTik",
    "08:55:31": "MikroTik", "64:D1:54": "MikroTik", "C4:AD:34": "MikroTik",
    "30:DE:4B": "TP-Link", "50:C7:BF": "TP-Link", "AC:15:A2": "TP-Link",
    "9C:A6:15": "TP-Link", "B0:95:75": "TP-Link", "F0:A7:31": "TP-Link",
    "C8:3A:35": "Tenda", "08:40:F3": "Tenda", "50:0F:F5": "Tenda",
    "24:5A:4C": "Ubiquiti", "FC:EC:DA": "Ubiquiti", "78:8A:20": "Ubiquiti",
    "00:1D:AA": "D-Link", "1C:BD:B9": "D-Link",
}


def vendor_from_mac(mac: Optional[str]) -> Optional[str]:
    """Produttore dal prefisso MAC. Sempre disponibile, sempre dichiarato."""
    if not mac or len(mac) < 8:
        return None
    return OUI_VENDORS.get(mac.replace("-", ":").upper()[:8])


def vendor_from_object_id(object_id: Optional[str]) -> Optional[str]:
    """Produttore dedotto dall'enterprise number, non da parole nel testo.

    None anche per gli enterprise generici: dire "net-snmp" quando si voleva
    sapere la marca e' peggio che non dire niente, perche' impedisce al
    chiamante di provare il prefisso MAC.
    """
    if not object_id:
        return None
    prefix = "1.3.6.1.4.1."
    text = str(object_id).strip()
    if not text.startswith(prefix):
        return None
    enterprise = text[len(prefix):].split(".")[0]
    if enterprise in GENERIC_ENTERPRISES:
        return None
    return ENTERPRISE_VENDORS.get(enterprise, f"enterprise {enterprise}")


# Valori che gli apparati mettono quando il campo non e' stato compilato.
# Trattarli come dati riempirebbe la dashboard di etichette "Unknown".
SEGNAPOSTO = {
    "", "unknown", "none", "n/a", "not set", "<private>",
    "sitting on the dock of the bay",  # il classico default di net-snmp
}


def valore_reale(testo: Optional[str]) -> Optional[str]:
    """None se il campo contiene un segnaposto invece di un valore."""
    if not testo:
        return None
    return None if testo.strip().lower() in SEGNAPOSTO else testo.strip()


def role_from_descr(descr: Optional[str]) -> Optional[str]:
    """Ruolo da cio' che l'apparato dice di essere.

    Non e' euristica sui nomi: sysDescr e' il campo in cui l'apparato dichiara
    la propria natura. L'EnGenius del salotto scrive "Wireless Access Point".
    """
    if not descr:
        return None
    t = descr.lower()
    if "access point" in t or "wireless ap" in t:
        return "access_point"
    if "switch" in t:
        return "switch"
    if "router" in t or "routeros" in t:
        return "gateway"
    if "printer" in t:
        return "printer"
    if "camera" in t or "ipcam" in t:
        return "camera"
    return None


def ticks_to_seconds(ticks) -> Optional[int]:
    """sysUpTime e' in centesimi di secondo, non in secondi."""
    try:
        return int(ticks) // 100
    except (TypeError, ValueError):
        return None


def parse_lldp(
    nomi: Dict[str, str],
    descrizioni: Dict[str, str],
    porte: Dict[str, str],
    indirizzi: Optional[Dict[str, str]] = None,
) -> List[SnmpNeighbour]:
    """Costruisce l'elenco dei vicini dalle tre tabelle LLDP.

    L'indice di lldpRemTable e' ``<timeMark>.<localPortNum>.<remIndex>``: la
    porta locale e' il **secondo** campo, non il primo. Sbagliarlo produce un
    albero plausibile e completamente falso, che e' il tipo di errore peggiore
    perche' non si nota. Funzione separata apposta per poterla verificare.
    """
    vicini: List[SnmpNeighbour] = []
    for oid, nome in sorted(nomi.items()):
        suffisso = oid[len(OID_LLDP_REM_SYS_NAME) + 1:]
        parti = suffisso.split(".")
        porta_locale = parti[1] if len(parti) >= 2 else "?"
        vicini.append(
            SnmpNeighbour(
                local_port=porta_locale,
                sys_name=nome or None,
                sys_desc=descrizioni.get(f"{OID_LLDP_REM_SYS_DESC}.{suffisso}") or None,
                port_id=porte.get(f"{OID_LLDP_REM_PORT_ID}.{suffisso}") or None,
                mgmt_ip=(indirizzi or {}).get(suffisso),
            )
        )
    return vicini


def parse_lldp_mgmt_addr(tabella: Dict[str, str]) -> Dict[str, str]:
    """Estrae {chiave-vicino: indirizzo IP} da lldpRemManAddrTable.

    L'indirizzo non sta nel valore: sta **dentro l'indice** dell'OID, che ha
    forma ``<timeMark>.<localPort>.<remIndex>.<subtype>.<lunghezza>.<ottetti>``.
    Con subtype 1 e lunghezza 4 gli ultimi quattro numeri sono l'IPv4.

    La chiave restituita e' ``timeMark.localPort.remIndex``, cioe' la stessa
    che indicizza lldpRemTable: serve a incollare l'indirizzo al vicino giusto.
    """
    per_vicino: Dict[str, str] = {}
    for oid in tabella:
        suffisso = oid[len(OID_LLDP_REM_MAN_ADDR) + 1:]
        parti = suffisso.split(".")
        # 3 dell'indice + subtype + lunghezza + almeno un ottetto
        if len(parti) < 6:
            continue
        chiave = ".".join(parti[:3])
        try:
            subtype, lunghezza = int(parti[3]), int(parti[4])
        except ValueError:
            continue
        if subtype != 1 or lunghezza != 4:
            continue  # non IPv4: ignorato, non inventato
        ottetti = parti[5:5 + 4]
        if len(ottetti) != 4:
            continue
        per_vicino[chiave] = ".".join(ottetti)
    return per_vicino


def parse_poe(stati: Dict[str, str], potenze: Dict[str, str]) -> List[SnmpPoePort]:
    """Stato e assorbimento PoE per porta.

    L'indice e' ``<gruppo>.<porta>``: si tiene intero, perche' su uno chassis
    con piu' moduli la sola porta non basta a identificarla.
    """
    porte: List[SnmpPoePort] = []
    for oid, stato in sorted(stati.items()):
        suffisso = oid[len(OID_PETH_PORT_DETECTION) + 1:]
        try:
            stato_nome = PETH_STATES.get(int(stato), str(stato))
        except (TypeError, ValueError):
            stato_nome = str(stato)
        potenza = potenze.get(f"{OID_PETH_PORT_POWER}.{suffisso}")
        porte.append(
            SnmpPoePort(
                port=suffisso,
                state=stato_nome,
                power_mw=int(potenza) if potenza and str(potenza).isdigit() else None,
            )
        )
    return porte


def _chiudi_engine(engine) -> None:
    """Chiude il dispatcher dell'engine SNMP.

    Ogni SnmpEngine apre un socket UDP alla prima richiesta e non lo chiude da
    se': senza questa riga il processo perde un descrittore per ogni get e per
    ogni walk. Su un box che interroga due apparati ogni minuto e mezzo il
    limite arriva in poche ore, e da li' in poi non si apre piu' niente - ne' i
    socket HTTP dell'heartbeat, ne' i file dei MIB. E' cosi' che l'agent e'
    rimasto "active (running)" per una notte senza mandare piu' un dato.
    """
    try:
        engine.close_dispatcher()
    except Exception as e:  # un engine mai aperto non ha nulla da chiudere
        logger.debug("SNMP: chiusura del dispatcher fallita: %s", e)


class SnmpClient:
    """GET e WALK su SNMP v2c. Non solleva: un apparato muto non e' un errore."""

    def __init__(
        self,
        host: str,
        community: str = "public",
        *,
        port: int = 161,
        timeout: float = 2.0,
        retries: int = 1,
    ) -> None:
        self.host = host
        self.community = community
        self.port = port
        self.timeout = timeout
        self.retries = retries

    async def _target(self):
        from pysnmp.hlapi.v3arch.asyncio import UdpTransportTarget

        return await UdpTransportTarget.create(
            (self.host, self.port), timeout=self.timeout, retries=self.retries
        )

    async def get(self, *oids: str) -> Dict[str, str]:
        """Legge piu' OID in una richiesta sola. Ritorna {oid: valore}."""
        from pysnmp.hlapi.v3arch.asyncio import (
            CommunityData,
            ContextData,
            ObjectIdentity,
            ObjectType,
            SnmpEngine,
            get_cmd,
        )

        engine = SnmpEngine()
        try:
            error_indication, error_status, _, var_binds = await get_cmd(
                engine,
                CommunityData(self.community),
                await self._target(),
                ContextData(),
                *[ObjectType(ObjectIdentity(o)) for o in oids],
            )
        except Exception as e:
            logger.debug("SNMP %s: get fallita: %s", self.host, e)
            return {}
        finally:
            _chiudi_engine(engine)

        if error_indication or error_status:
            logger.debug("SNMP %s: %s %s", self.host, error_indication, error_status)
            return {}

        risultato = {}
        for oid, value in var_binds:
            testo = str(value)
            # pysnmp restituisce questi segnaposto quando l'OID non esiste
            if testo in ("", "No Such Object currently exists at this OID",
                         "No Such Instance currently exists at this OID"):
                continue
            risultato[str(oid)] = testo
        return risultato

    async def walk(self, base_oid: str, limit: int = 256) -> Dict[str, str]:
        """Percorre una tabella. Ritorna {oid completo: valore}."""
        from pysnmp.hlapi.v3arch.asyncio import (
            CommunityData,
            ContextData,
            ObjectIdentity,
            ObjectType,
            SnmpEngine,
            bulk_walk_cmd,
        )

        risultato: Dict[str, str] = {}
        engine = SnmpEngine()
        try:
            target = await self._target()
            async for error_indication, error_status, _, var_binds in bulk_walk_cmd(
                engine,
                CommunityData(self.community),
                target,
                ContextData(),
                0,
                20,
                ObjectType(ObjectIdentity(base_oid)),
                lexicographicMode=False,
            ):
                if error_indication or error_status:
                    break
                for oid, value in var_binds:
                    risultato[str(oid)] = str(value)
                if len(risultato) >= limit:
                    break
        except Exception as e:
            logger.debug("SNMP %s: walk di %s fallita: %s", self.host, base_oid, e)
        finally:
            _chiudi_engine(engine)
        return risultato


async def read_device(
    host: str, community: str = "public", *, timeout: float = 2.0
) -> Optional[SnmpDeviceInfo]:
    """Interroga un apparato e ritorna cio' che ha voluto dire.

    None solo se non risponde affatto: in quel caso non parla SNMP, oppure la
    community e' sbagliata - dal punto di vista del protocollo sono
    indistinguibili, ed e' bene saperlo quando si legge un log.
    """
    client = SnmpClient(host, community, timeout=timeout)

    system = await client.get(
        OID_SYS_DESCR, OID_SYS_OBJECT_ID, OID_SYS_UPTIME,
        OID_SYS_CONTACT, OID_SYS_NAME, OID_SYS_LOCATION,
    )
    if not system:
        return None

    info = SnmpDeviceInfo(
        ip=host,
        descr=system.get(OID_SYS_DESCR),
        object_id=system.get(OID_SYS_OBJECT_ID),
        uptime_seconds=ticks_to_seconds(system.get(OID_SYS_UPTIME)),
        contact=valore_reale(system.get(OID_SYS_CONTACT)),
        name=valore_reale(system.get(OID_SYS_NAME)),
        location=valore_reale(system.get(OID_SYS_LOCATION)),
    )
    info.vendor = vendor_from_object_id(info.object_id)
    info.read_ok.append("system")

    # --- LLDP: l'albero ---
    nomi = await client.walk(OID_LLDP_REM_SYS_NAME)
    if nomi:
        info.read_ok.append("lldp")
        descrizioni = await client.walk(OID_LLDP_REM_SYS_DESC)
        porte = await client.walk(OID_LLDP_REM_PORT_ID)
        indirizzi = parse_lldp_mgmt_addr(await client.walk(OID_LLDP_REM_MAN_ADDR))
        info.neighbours = parse_lldp(nomi, descrizioni, porte, indirizzi)
    else:
        info.read_failed.append("lldp")

    # --- PoE per porta ---
    stati = await client.walk(OID_PETH_PORT_DETECTION)
    if stati:
        info.read_ok.append("poe")
        potenze = await client.walk(OID_PETH_PORT_POWER)
        info.poe_ports = parse_poe(stati, potenze)
    else:
        info.read_failed.append("poe")

    return info
