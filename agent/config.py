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

    # Credenziali di sola lettura sul router del sito. Senza, l'agent si limita
    # alla scansione ARP: vede chi risponde, ma non sa il tipo di connessione,
    # l'interfaccia ne' il segnale, che stanno solo nelle tabelle del router.
    ROUTER_USER: str = os.getenv("ROUTER_USER", "")
    ROUTER_PASSWORD: str = os.getenv("ROUTER_PASSWORD", "")
    # auto = prova a riconoscere il router; none = disattiva del tutto
    ROUTER_DRIVER: str = os.getenv("ROUTER_DRIVER", "auto")
    # RouterOS presenta un certificato autofirmato: la verifica va attivata solo
    # dopo aver importato una CA sul router.
    ROUTER_VERIFY_TLS: bool = os.getenv("ROUTER_VERIFY_TLS", "false").lower() == "true"
    # https richiede che al servizio www-ssl sia assegnato un certificato,
    # altrimenti l'handshake TLS fallisce. Dove generarne uno e' scomodo si puo'
    # mettere http, accettando che le credenziali viaggino in chiaro sulla LAN:
    # e' una scelta da fare consapevolmente, non un ripiego automatico.
    ROUTER_SCHEME: str = os.getenv("ROUTER_SCHEME", "https")
    ROUTER_TIMEOUT: float = float(os.getenv("ROUTER_TIMEOUT", "10"))

    # Client per richiesta. Qui non c'e' il limite di /tool fetch di RouterOS,
    # ma spezzare resta utile: un lotto che fallisce non porta via tutto.
    CLIENT_BATCH_SIZE: int = int(os.getenv("CLIENT_BATCH_SIZE", "50"))

    # Secondi prima di ridepositare la stessa richiesta di tunnel. Il cloud la
    # ripete a ogni heartbeat finche' il connettore non risulta attivo: senza
    # questa pausa cloudflared verrebbe riavviato ogni trenta secondi.
    TUNNEL_RETRY: float = float(os.getenv("TUNNEL_RETRY", "300"))

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
    SNMP_TIMEOUT: float = float(os.getenv("SNMP_TIMEOUT", "2.0"))
    # Interrogazione SNMP degli apparati: da' nome, produttore, posizione e
    # l'albero LLDP. Si spegne solo se disturba qualcosa in rete.
    SNMP_ENABLED: bool = os.getenv("SNMP_ENABLED", "true").lower() == "true"
    SNMP_COMMUNITIES: list[str] = os.getenv("SNMP_COMMUNITIES", "public,private").split(",")


config = Config()
