"""Agent configuration loaded from environment variables."""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Cloud backend URLs (Railway, etc.)
    BACKEND_URL: str = os.getenv("BACKEND_URL", "http://localhost:8000")
    WS_URL: str = os.getenv("WS_URL", "ws://localhost:8000/ws/agent")
    AGENT_TOKEN: str = os.getenv("AGENT_TOKEN", "")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Versione dell'agent, riportata nell'heartbeat: serve a sapere da remoto
    # quale codice sta girando su ogni appliance senza collegarsi.
    AGENT_VERSION: str = "1.0.0"

    # Polling / telemetry
    TELEMETRY_INTERVAL: int = int(os.getenv("TELEMETRY_INTERVAL", "60"))
    SCAN_NETWORK: str = os.getenv("SCAN_NETWORK", "")

    # Gateway del sito. Vuoto = rilevato dalla rotta di default.
    GATEWAY_IP: str = os.getenv("GATEWAY_IP", "")

    # Client per richiesta. Qui non c'e' il limite di /tool fetch di RouterOS,
    # ma spezzare resta utile: un lotto che fallisce non porta via tutto.
    CLIENT_BATCH_SIZE: int = int(os.getenv("CLIENT_BATCH_SIZE", "50"))

    # WS settings
    HEARTBEAT_INTERVAL: int = int(os.getenv("HEARTBEAT_INTERVAL", "30"))
    RECONNECT_DELAY: int = int(os.getenv("RECONNECT_DELAY", "5"))

    # Scanner settings
    # Indirizzi sollecitati per lotto nella scansione ARP: la coda del kernel
    # per gli indirizzi non ancora risolti e' limitata, e riversarci dentro un
    # /24 intero ne fa cadere una parte in silenzio.
    ARP_BATCH: int = int(os.getenv("ARP_BATCH", "64"))
    # Secondi di attesa perche' le risposte ARP arrivino e la tabella si popoli.
    ARP_SETTLE: float = float(os.getenv("ARP_SETTLE", "2.0"))

    # Ping simultanei, solo per il ripiego ICMP. Ogni ping e' un processo:
    # senza limite un /24 ne genera 254 insieme, che su un quad Cortex-A7 con
    # 1 GB di RAM significa swap e scansione piu' lenta di quella limitata.
    SCAN_CONCURRENCY: int = int(os.getenv("SCAN_CONCURRENCY", "32"))
    PING_TIMEOUT: float = float(os.getenv("PING_TIMEOUT", "0.8"))
    SNMP_TIMEOUT: float = float(os.getenv("SNMP_TIMEOUT", "1.0"))
    SNMP_COMMUNITIES: list[str] = os.getenv("SNMP_COMMUNITIES", "public,private").split(",")


config = Config()
