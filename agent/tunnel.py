"""Ponte fra l'agent e la parte che deve girare da root.

L'agent non puo' installare o riavviare servizi: gira senza privilegi, con
`NoNewPrivileges=true` e `RestrictSUIDSGID=true`, quindi nemmeno sudo lo
eleverebbe. E va bene cosi': un processo che parla con il cloud e interroga
router altrui non deve poter eseguire comandi da amministratore.

Percio' qui non si esegue niente. L'agent deposita una richiesta in
/run/netmonitor e un'unit systemd `.path` la raccoglie ed esegue da root
`/usr/local/sbin/netmonitor-tunnel`. Il confine e' stretto per costruzione: da
questa parte si puo' solo chiedere "applica questo token" o "togli il tunnel",
non far eseguire una riga di comando.
"""
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("netmonitor-agent")

# Sovrascrivibile per i test: la cartella vera e' un tmpfs creato da tmpfiles.d.
SPOOL = Path(os.getenv("NETMONITOR_SPOOL", "/run/netmonitor"))
RICHIESTA = SPOOL / "tunnel-request"
STATO = SPOOL / "tunnel-state"

AZIONI = ("applica", "rimuovi")


def supportato() -> bool:
    """Vero se su questo box esiste la parte privilegiata.

    Un'appliance installata prima che il tunnel remoto esistesse non ha ne' la
    cartella ne' l'unit: la richiesta cadrebbe nel vuoto, e il cloud aspetterebbe
    per sempre una conferma che nessuno scrivera'. Meglio dirlo subito.
    """
    return SPOOL.is_dir()


def stato() -> dict:
    """Ultimo esito scritto dalla parte privilegiata.

    Chi non ha mai avuto un tunnel non ha il file: 'assente' e' la risposta
    giusta, non un errore.
    """
    try:
        dati = json.loads(STATO.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"stato": "assente", "messaggio": None}
    except Exception as e:
        logger.debug("Stato del tunnel illeggibile: %s", e)
        return {"stato": "errore", "messaggio": f"stato locale illeggibile: {e}"}

    if not isinstance(dati, dict) or dati.get("stato") not in ("attivo", "errore", "assente"):
        return {"stato": "errore", "messaggio": "stato locale malformato"}
    return {"stato": dati["stato"], "messaggio": dati.get("messaggio")}


def richiedi(azione: str, token: Optional[str] = None, hostname: Optional[str] = None) -> bool:
    """Deposita una richiesta per la parte privilegiata. True se e' stata scritta.

    Scrittura atomica: l'unit `.path` scatta sulla comparsa del file, e un file
    letto mentre lo si sta ancora scrivendo sarebbe un token troncato applicato
    per davvero.
    """
    if azione not in AZIONI:
        raise ValueError(f"azione sconosciuta: {azione}")
    if azione == "applica" and not token:
        raise ValueError("applica senza token")
    if not supportato():
        return False

    corpo = json.dumps(
        {"azione": azione, "token": token, "hostname": hostname, "chiesto": time.time()}
    )
    try:
        fd, tmp = tempfile.mkstemp(dir=str(SPOOL), prefix=".tunnel-request.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(corpo)
            os.chmod(tmp, 0o640)
            os.replace(tmp, RICHIESTA)
        except Exception:
            os.unlink(tmp)
            raise
    except Exception as e:
        logger.error("Richiesta di tunnel non depositata: %s", e)
        return False

    logger.info("Richiesta di tunnel depositata: %s", azione)
    return True
