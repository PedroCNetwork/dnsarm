#!/usr/bin/env bash
#
# Provisioning dell'appliance NetMonitor su Armbian / Debian / Ubuntu.
#
#   sudo ./install.sh --token <agent-token> [--site "Nome Sito"] [--backend URL]
#   sudo ./install.sh --token <t> --tunnel-token <t>   aggiunge l'accesso remoto
#   sudo ./install.sh --token <t> --router-user netmonitor --router-pass <pw>
#       legge le tabelle del router (lease DHCP, WiFi, bridge): senza, il
#       monitoraggio si ferma a "chi risponde", senza tipo ne' segnale.
#   sudo ./install.sh --dry-run --token x        mostra cosa farebbe, non tocca nulla
#
# --tunnel-token e' il token del Cloudflare Tunnel, preso dalla dashboard Zero
# Trust. Senza, il box monitora e basta: il monitoraggio non dipende dal tunnel.
#
# Idempotente: rilanciarlo su un box gia' configurato aggiorna e basta.
#
# COSA NON FA, DI PROPOSITO:
# non tocca la configurazione SSH. L'irrigidimento (solo chiave, solo dalla VPN)
# arriva in A2, quando la mesh funziona: applicarlo adesso su un box headless a
# casa di un cliente significa restare chiusi fuori senza via di rientro.

set -euo pipefail

APP_DIR=/opt/netmonitor
ENV_FILE=/etc/netmonitor/agent.env
SERVICE=netmonitor-agent
SERVICE_USER=netmonitor
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TOKEN=""; SITE_NAME=""; BACKEND=""; TUNNEL_TOKEN=""; DRY_RUN=0
ROUTER_USER=""; ROUTER_PASS=""; ROUTER_PASS_GIVEN=0; ROUTER_SCHEME="https"

# Un'opzione senza valore deve dirlo. Prima lo `shift` di troppo faceva uscire
# lo script in silenzio (set -e), e il caso classico non e' la distrazione: e'
# una password che comincia per '#', che la shell interpreta come inizio di
# commento e mangia insieme a tutto il resto della riga.
_need_value() {
    [ "$2" -ge 2 ] || {
        printf 'L opzione %s richiede un valore.\n' "$1" >&2
        printf 'Se il valore contiene # $ spazi o apici, racchiudilo fra apici singoli:\n' >&2
        printf "    %s '%s'\n" "$1" "valore#con-simboli" >&2
        exit 2
    }
}

while [ $# -gt 0 ]; do
    case "$1" in
        --token)        _need_value "$1" $#; TOKEN="$2"; shift ;;
        --site)         _need_value "$1" $#; SITE_NAME="$2"; shift ;;
        --backend)      _need_value "$1" $#; BACKEND="$2"; shift ;;
        --tunnel-token) _need_value "$1" $#; TUNNEL_TOKEN="$2"; shift ;;
        --router-user)  _need_value "$1" $#; ROUTER_USER="$2"; shift ;;
        --router-pass)  _need_value "$1" $#; ROUTER_PASS="$2"; ROUTER_PASS_GIVEN=1; shift ;;
        --router-scheme) _need_value "$1" $#; ROUTER_SCHEME="$2"; shift ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help) awk 'NR>1 { if (/^#/) { sub(/^# ?/,""); print } else exit }' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "Opzione sconosciuta: $1" >&2; exit 2 ;;
    esac
    shift
done

# Password richiesta a video se manca. E' anche il modo piu' sicuro di darla:
# sulla riga di comando finisce nella cronologia della shell e resta visibile a
# chiunque faccia `ps` sul box finche' lo script gira.
if [ -n "$ROUTER_USER" ] && [ "$ROUTER_PASS_GIVEN" -eq 0 ] && [ "$DRY_RUN" -eq 0 ]; then
    printf 'Password di %s sul router (input nascosto): ' "$ROUTER_USER"
    read -rs ROUTER_PASS
    printf '\n'
fi

if [ -t 1 ]; then
    RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; OFF=$'\033[0m'
else
    RED=; GREEN=; YELLOW=; BOLD=; OFF=
fi
step() { printf '\n%s==> %s%s\n' "$BOLD" "$1" "$OFF"; }
ok()   { printf '    %sOK%s   %s\n' "$GREEN" "$OFF" "$1"; }
warn() { printf '    %sATT%s  %s\n' "$YELLOW" "$OFF" "$1"; }
die()  { printf '\n    %sKO%s   %s\n\n' "$RED" "$OFF" "$1" >&2; exit 1; }
run()  { if [ "$DRY_RUN" -eq 1 ]; then printf '    [dry-run] %s\n' "$*"; else "$@"; fi; }

# ---------------------------------------------------------------------------
# Requisiti
# ---------------------------------------------------------------------------
step "Requisiti"
[ "$DRY_RUN" -eq 1 ] || [ "$(id -u)" -eq 0 ] || die "Serve root: rilancia con sudo."
command -v systemctl >/dev/null || die "Serve systemd."
command -v apt-get   >/dev/null || die "Questo script assume Debian/Ubuntu/Armbian."
[ -n "$TOKEN" ] || die "Manca --token. Lo trovi nella pagina del sito in NetMonitor."
# La password finisce nel file di configurazione fra apici singoli, che systemd
# preserva verbatim. L'unico carattere che non puo' contenere e' l'apice
# singolo stesso: meglio dirlo adesso che scoprirlo da un 401 misterioso.
case "$ROUTER_PASS" in
    *"'"*) die "La password del router non puo' contenere apici singoli. Cambiala sul router." ;;
esac
[ -d "$REPO_DIR/agent" ] || die "Cartella agent/ non trovata accanto a install.sh."
ok "root, systemd, apt, token e sorgenti presenti"

# ---------------------------------------------------------------------------
# Fotografia dell'hardware: finisce nel log dell'installazione, cosi' quando
# fra sei mesi un box fa i capricci sai su cosa stai lavorando senza andarci.
# ---------------------------------------------------------------------------
step "Hardware"
if [ -r /proc/device-tree/model ]; then
    BOARD="$(tr -d '\0' < /proc/device-tree/model)"
else
    BOARD="sconosciuta (non e' una board ARM con device-tree)"
fi
printf '    scheda:  %s\n' "$BOARD"
printf '    arch:    %s\n' "$(uname -m)"
printf '    kernel:  %s\n' "$(uname -r)"
RAM_MB="$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo)"
printf '    RAM:     %s MB\n' "$RAM_MB"
[ "$RAM_MB" -lt 1500 ] && warn "Sotto 1.5 GB: l'agent ci sta largo, ma non caricare il box di altri servizi."
UPLINK="$(awk '$2=="00000000" {print $1; exit}' /proc/net/route 2>/dev/null || true)"
if [ -n "$UPLINK" ]; then
    SPEED="$(cat "/sys/class/net/$UPLINK/speed" 2>/dev/null || echo "?")"
    printf '    uplink:  %s a %s Mbit/s\n' "$UPLINK" "$SPEED"
    [ "$SPEED" = "100" ] && warn "NIC a 100 Mbit: irrilevante fuori banda, sarebbe un problema solo inline."

    # La rete che l'agent scansionera' davvero. Va stampata qui perche' il campo
    # "Rete" del sito in NetMonitor si compila a mano e puo' non combaciare:
    # l'agent usa questa, il pulsante "Scansiona rete" usa quella. Vederle
    # entrambe evita mezz'ora di dubbi.
    GW="$(awk '$2=="00000000" {print $3; exit}' /proc/net/route 2>/dev/null || true)"
    if [ -n "$GW" ]; then
        GW_IP="$(printf '%d.%d.%d.%d' \
            "$((0x${GW:6:2}))" "$((0x${GW:4:2}))" "$((0x${GW:2:2}))" "$((0x${GW:0:2}))")"
        printf '    gateway: %s\n' "$GW_IP"
    fi
    # Calcolata dalle rotte, non con ipcalc: su Armbian minimale non c'e'.
    # Si prende la rotta di sottorete sull'interfaccia dell'uplink, cosi' su un
    # box con Docker non si finisce per annunciare un bridge 172.x.
    SUBNET_HEX="$(awk -v ifc="$UPLINK" \
        '$1==ifc && $3=="00000000" && $8!="00000000" {print $2, $8; exit}' \
        /proc/net/route 2>/dev/null || true)"
    if [ -n "$SUBNET_HEX" ]; then
        read -r NETHEX MASKHEX <<< "$SUBNET_HEX"
        NET_IP="$(printf '%d.%d.%d.%d' \
            "$((0x${NETHEX:6:2}))" "$((0x${NETHEX:4:2}))" "$((0x${NETHEX:2:2}))" "$((0x${NETHEX:0:2}))")"
        PREFIX=0; MASKBITS=$((0x$MASKHEX))
        while [ "$MASKBITS" -ne 0 ]; do
            PREFIX=$((PREFIX + (MASKBITS & 1)))
            MASKBITS=$((MASKBITS >> 1))
        done
        printf '    rete:    %s/%s\n' "$NET_IP" "$PREFIX"
        printf '             ^ il campo "Rete" del sito in NetMonitor deve dire questo\n'
    fi
else
    warn "Nessuna rotta di default: il box non ha ancora rete."
fi

# Il supporto di boot decide la vita del box: la flash consumer si consuma a
# scritture, e chi scrive di continuo sono i log. Se pero' i log stanno gia' in
# RAM il problema e' risolto, e allarmare lo stesso sarebbe solo rumore.
ROOT_SRC="$(findmnt -no SOURCE / 2>/dev/null || echo '')"
ROOT_DISK="$(lsblk -no PKNAME "$ROOT_SRC" 2>/dev/null || echo '')"
ON_FLASH=0
case "$ROOT_DISK" in
    mmcblk*) ON_FLASH=1 ;;
esac

LOG_ON_RAM=0
systemctl is-active --quiet armbian-ramlog 2>/dev/null && LOG_ON_RAM=1
case "$(findmnt -no SOURCE /var/log 2>/dev/null)" in
    /dev/zram*) LOG_ON_RAM=1 ;;
esac

if [ "$ON_FLASH" -eq 0 ]; then
    ok "Root su $ROOT_DISK, non su flash interna"
elif [ "$LOG_ON_RAM" -eq 1 ]; then
    ok "Root su $ROOT_DISK (flash), ma i log stanno in RAM: va bene cosi'"
else
    warn "Root su $ROOT_DISK (eMMC/microSD) e log su flash: si consuma."
    warn "Provo ad attivare log2ram; valuta comunque root su SSD USB."
fi

# ---------------------------------------------------------------------------
# Pacchetti e impostazioni di base
# ---------------------------------------------------------------------------
step "Pacchetti"
export DEBIAN_FRONTEND=noninteractive
# Niente -qq: su una TV box questo passaggio dura minuti, e un comando muto per
# minuti sembra un comando bloccato. Meglio rumoroso che ambiguo.
warn "Questo passaggio puo' richiedere diversi minuti su hardware lento. Non interrompere."
run apt-get update -q

# I pacchetti essenziali vanno in una apt separata da quelli opzionali: apt e'
# tutto-o-niente, e un solo nome non disponibile fa fallire l'intera chiamata
# senza installare nulla. Con un `|| warn` sembrerebbe pure andata bene, e poi
# la creazione del venv fallirebbe senza un motivo evidente.
PKGS=(python3 python3-venv python3-pip curl ca-certificates iputils-ping unattended-upgrades)
run apt-get install -y -q "${PKGS[@]}"
ok "pacchetti essenziali installati"

# Armbian di suo monta /var/log su zram (armbian-ramlog): nella maggior parte
# dei casi non serve aggiungere niente. LOG_ON_RAM e' gia' stato rilevato sopra.
if [ "$LOG_ON_RAM" -eq 1 ]; then
    ok "/var/log gia' in RAM: la flash non viene consumata dai log"
elif [ "$ON_FLASH" -eq 1 ]; then
    run apt-get install -y -qq log2ram \
        || warn "log2ram non disponibile nei repo: i log scrivono sulla flash interna"
fi

step "Sistema"
run timedatectl set-timezone Europe/Rome || warn "timezone non impostata"
if [ -n "$SITE_NAME" ]; then
    SLUG="$(echo "$SITE_NAME" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-//; s/-$//')"
    run hostnamectl set-hostname "netmon-$SLUG"
    ok "hostname netmon-$SLUG"
fi
run systemctl enable --now unattended-upgrades >/dev/null 2>&1 || warn "unattended-upgrades non attivato"

# Watchdog: una TV box che si pianta a casa di un cliente deve riavviarsi da
# sola, altrimenti l'intervento e' un viaggio in macchina.
if [ -e /dev/watchdog ]; then
    if ! grep -q '^RuntimeWatchdogSec=' /etc/systemd/system.conf 2>/dev/null; then
        run sh -c 'printf "RuntimeWatchdogSec=30\nRebootWatchdogSec=5min\n" >> /etc/systemd/system.conf'
    fi
    ok "watchdog hardware presente e armato"
else
    warn "Nessun /dev/watchdog: il box non si riavvia da solo se si pianta."
fi

# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
step "Agent"
id "$SERVICE_USER" >/dev/null 2>&1 || run useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
run install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0755 "$APP_DIR"
run cp -a "$REPO_DIR/agent/." "$APP_DIR/"
run rm -rf "$APP_DIR/__pycache__" "$APP_DIR/.env"
run chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

if [ ! -x "$APP_DIR/venv/bin/python" ]; then
    run python3 -m venv "$APP_DIR/venv"
fi
run "$APP_DIR/venv/bin/pip" install --upgrade pip

# --prefer-binary: su ARM a 32 bit alcune dipendenze non hanno una ruota
# precompilata e pip proverebbe a compilarle. Se succede e manca il
# compilatore, invece di morire si installano gli strumenti e si riprova una
# volta sola: sono ~200 MB, quindi non si mettono se non servono davvero.
if [ "$DRY_RUN" -eq 1 ]; then
    printf '    [dry-run] pip install -r %s/requirements.txt\n' "$APP_DIR"
elif ! "$APP_DIR/venv/bin/pip" install --prefer-binary -r "$APP_DIR/requirements.txt"; then
    warn "Installazione fallita: probabilmente serve compilare. Installo gli strumenti e riprovo."
    apt-get install -y -q build-essential python3-dev \
        || die "Impossibile installare build-essential: controlla la connessione."
    "$APP_DIR/venv/bin/pip" install --prefer-binary -r "$APP_DIR/requirements.txt" \
        || die "Installazione delle dipendenze fallita anche dopo l'installazione del compilatore."
fi
run chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"
ok "agent in $APP_DIR"

# Il token e' un segreto: file separato dal codice, leggibile solo dal servizio.
step "Configurazione"
BACKEND="${BACKEND:-https://web-production-4bcf49.up.railway.app}"
WS_URL="$(echo "$BACKEND" | sed 's|^http|ws|')/ws/agent"
run install -d -m 0750 -o root -g "$SERVICE_USER" /etc/netmonitor
if [ "$DRY_RUN" -eq 1 ]; then
    printf '    [dry-run] scrittura di %s (0640 root:%s)\n' "$ENV_FILE" "$SERVICE_USER"
else
    umask 027
    cat > "$ENV_FILE" <<EOF
# Generato da install.sh. Contiene il token del sito: non committare, non copiare.
BACKEND_URL=$BACKEND
WS_URL=$WS_URL
AGENT_TOKEN=$TOKEN
LOG_LEVEL=INFO
TELEMETRY_INTERVAL=60
HEARTBEAT_INTERVAL=30
# Vuoti = rilevati dalle rotte. Compilare solo se il rilevamento sbaglia.
SCAN_NETWORK=
GATEWAY_IP=
# Credenziali di sola lettura sul router. Senza, l'agent vede chi c'e' ma non
# sa se e' via cavo o radio, su quale porta, con che segnale: quei dati
# stanno solo nelle tabelle del router.
ROUTER_USER=$ROUTER_USER
ROUTER_PASSWORD='$ROUTER_PASS'
ROUTER_DRIVER=auto
ROUTER_VERIFY_TLS=false
# https richiede un certificato assegnato a www-ssl sul router. Mettere http
# solo consapevolmente: le credenziali viaggerebbero in chiaro sulla LAN.
ROUTER_SCHEME=$ROUTER_SCHEME
EOF
    chown root:"$SERVICE_USER" "$ENV_FILE"
    chmod 0640 "$ENV_FILE"
fi
ok "$ENV_FILE scritto, leggibile solo dal servizio"

step "Servizio"
if [ "$DRY_RUN" -eq 1 ]; then
    printf '    [dry-run] scrittura di /etc/systemd/system/%s.service\n' "$SERVICE"
else
    cat > "/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=NetMonitor appliance agent
After=network-online.target
Wants=network-online.target
# Un limite di avvii qui e' solo un modo per non tornare piu' su: dopo qualche
# riavvio ravvicinato systemd smetterebbe di provarci e il box resterebbe
# offline finche' qualcuno non ci mette le mani. Il box e' remoto: si riprova
# sempre.
StartLimitIntervalSec=0

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/agent.py
Restart=always
RestartSec=10

# Restart=always copre il processo che muore, non quello che resta vivo senza
# lavorare piu'. L'agent conferma il watchdog solo finche' il ciclo di presenza
# gira: se si blocca, systemd lo abbatte e lo riavvia invece di lasciarlo
# "active (running)" a fare niente.
WatchdogSec=180
NotifyAccess=main

# Il box custodisce credenziali dei router dei clienti: vale la pena stringere.
#
# CAP_NET_RAW e' indispensabile: ping ottiene il permesso di aprire socket ICMP
# da una file capability, e NoNewPrivileges impedisce a un figlio di acquisire
# privilegi in quel modo. Senza queste due righe la scansione fallisce in
# silenzio e il box riporta "0 host" come se la rete fosse vuota.
# Concessa dal padre invece che guadagnata dal binario, quindi NoNewPrivileges
# resta attivo. CapabilityBoundingSet toglie tutto il resto.
AmbientCapabilities=CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_RAW
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
# /run/netmonitor e' la buca delle lettere verso la parte privilegiata: l'agent
# ci deposita le richieste di tunnel e ci legge l'esito. Senza questa riga
# ProtectSystem=strict la renderebbe di sola lettura e l'accesso remoto non si
# attiverebbe mai, in silenzio.
ReadWritePaths=$APP_DIR /run/netmonitor
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true

[Install]
WantedBy=multi-user.target
EOF
fi
run systemctl daemon-reload
run systemctl enable "$SERVICE"
# restart, non `enable --now`: se il servizio e' gia' in esecuzione `--now` non
# fa niente, e una reinstallazione lascerebbe in giro il processo vecchio con
# la unit e il codice vecchi. Sembra riuscita e non ha cambiato nulla.
run systemctl restart "$SERVICE"
ok "servizio $SERVICE riavviato"

# ---------------------------------------------------------------------------
# Accesso remoto: cloudflared, pilotabile dal cloud
#
# Il tunnel esce dall'appliance verso Cloudflare, quindi funziona senza IP
# pubblico e senza aprire nulla sul router del cliente. Ci si collega con l'app
# WARP, da portatile o da telefono.
#
# La parte che conta: il token NON deve piu' passare per forza da qui. L'agent
# gira senza privilegi e non puo' installare servizi, quindi deposita una
# richiesta in /run/netmonitor e questa unit .path la fa eseguire da root. Cosi'
# un tunnel si attiva da NetMonitor, su un box gia' installato e chiuso dentro
# un locale, senza che nessuno vada a digitarci un comando.
#
# --tunnel-token resta valido: e' la stessa strada, percorsa subito.
# ---------------------------------------------------------------------------
step "Accesso remoto"

# La buca delle lettere: root scrive e legge, il servizio anche. Sta in /run,
# quindi e' un tmpfs che si azzera a ogni riavvio - un token non ci resta.
if [ "$DRY_RUN" -eq 1 ]; then
    printf '    [dry-run] /etc/tmpfiles.d/netmonitor.conf e /run/netmonitor\n'
else
    printf 'd /run/netmonitor 0770 root %s -\n' "$SERVICE_USER" > /etc/tmpfiles.d/netmonitor.conf
    systemd-tmpfiles --create /etc/tmpfiles.d/netmonitor.conf
fi

run install -m 0755 "$REPO_DIR/appliance/netmonitor-tunnel" /usr/local/sbin/netmonitor-tunnel

if [ "$DRY_RUN" -eq 1 ]; then
    printf '    [dry-run] unit netmonitor-tunnel.path e .service\n'
else
    cat > /etc/systemd/system/netmonitor-tunnel.service <<'EOF'
[Unit]
Description=Applica la configurazione del tunnel NetMonitor
# Una richiesta alla volta: due cloudflared riavviati insieme non aiutano nessuno.

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/netmonitor-tunnel
EOF

    cat > /etc/systemd/system/netmonitor-tunnel.path <<'EOF'
[Unit]
Description=Sorveglia le richieste di tunnel depositate dall'agent

[Path]
PathExists=/run/netmonitor/tunnel-request
Unit=netmonitor-tunnel.service

[Install]
WantedBy=multi-user.target
EOF
fi
run systemctl daemon-reload
run systemctl enable netmonitor-tunnel.path
run systemctl restart netmonitor-tunnel.path
ok "accesso remoto pilotabile dal cloud"

# Con --tunnel-token il tunnel si attiva subito, senza aspettare il cloud.
if [ -n "$TUNNEL_TOKEN" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '    [dry-run] applicazione immediata del token del tunnel\n'
    else
        printf '%s' "$TUNNEL_TOKEN" | /usr/local/sbin/netmonitor-tunnel --applica --token-stdin \
            || warn "il tunnel non si e' attivato: guarda 'journalctl -u cloudflared'"
    fi
    ok "token del tunnel applicato"
fi

# ---------------------------------------------------------------------------
# Verifica
# ---------------------------------------------------------------------------
step "Verifica"
if [ "$DRY_RUN" -eq 1 ]; then
    printf '    [dry-run] nessuna verifica eseguita\n\n'
    exit 0
fi
sleep 5
systemctl is-active --quiet "$SERVICE" && ok "servizio in esecuzione" || die "Il servizio non parte: journalctl -u $SERVICE -n 50"
curl -fsS --max-time 15 "$BACKEND/health" >/dev/null 2>&1 && ok "backend raggiungibile" || warn "Backend non raggiungibile da qui: controlla DNS e rotta"

cat <<EOF

    ${BOLD}Fatto.${OFF} Segui l'avvio con:

        journalctl -u $SERVICE -f

    Attesi entro un minuto: "Heartbeat ok" e "Ciclo completato".
    Il sito deve comparire online in NetMonitor.

    L'accesso remoto ora si attiva da NetMonitor, senza tornare qui: incolla
    il token del connettore Cloudflare nella pagina del sito. Il box lo ritira
    al primo heartbeat e riferisce se il tunnel e' salito.

    ${YELLOW}SSH non e' stato irrigidito${OFF}: si fa in A2, quando la VPN
    funziona e hai una seconda via d'ingresso.

EOF
