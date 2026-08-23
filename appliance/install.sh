#!/usr/bin/env bash
#
# Provisioning dell'appliance NetMonitor su Armbian / Debian / Ubuntu.
#
#   sudo ./install.sh --token <agent-token> [--site "Nome Sito"] [--backend URL]
#   sudo ./install.sh --token <t> --tunnel-token <t>   aggiunge l'accesso remoto
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
CF_ENV_FILE=/etc/netmonitor/cloudflared.env
SERVICE=netmonitor-agent
SERVICE_USER=netmonitor
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TOKEN=""; SITE_NAME=""; BACKEND=""; TUNNEL_TOKEN=""; DRY_RUN=0
while [ $# -gt 0 ]; do
    case "$1" in
        --token)        shift; TOKEN="${1:-}" ;;
        --site)         shift; SITE_NAME="${1:-}" ;;
        --backend)      shift; BACKEND="${1:-}" ;;
        --tunnel-token) shift; TUNNEL_TOKEN="${1:-}" ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help) awk 'NR>1 { if (/^#/) { sub(/^# ?/,""); print } else exit }' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "Opzione sconosciuta: $1" >&2; exit 2 ;;
    esac
    shift
done

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
[ "$RAM_MB" -lt 1500 ] && warn "Sotto 1.5 GB: l'agent gira largo, ma CrowdSec (A4) andra' tenuto d'occhio."
UPLINK="$(awk '$2=="00000000" {print $1; exit}' /proc/net/route 2>/dev/null || true)"
if [ -n "$UPLINK" ]; then
    SPEED="$(cat "/sys/class/net/$UPLINK/speed" 2>/dev/null || echo "?")"
    printf '    uplink:  %s a %s Mbit/s\n' "$UPLINK" "$SPEED"
    [ "$SPEED" = "100" ] && warn "NIC a 100 Mbit: irrilevante fuori banda, sarebbe un problema solo inline."
else
    warn "Nessuna rotta di default: il box non ha ancora rete."
fi

# Il supporto di boot decide la vita del box. La flash consumer di una TV box
# si consuma con i log; non possiamo spostare il rootfs da qui, ma possiamo
# dirlo chiaramente e mitigare con log2ram.
ROOT_SRC="$(findmnt -no SOURCE / 2>/dev/null || echo '')"
ROOT_DISK="$(lsblk -no PKNAME "$ROOT_SRC" 2>/dev/null || echo '')"
ON_FLASH=0
case "$ROOT_DISK" in
    mmcblk*) ON_FLASH=1 ;;
esac
if [ "$ON_FLASH" -eq 1 ]; then
    warn "Root su $ROOT_DISK (eMMC/microSD): flash consumer, si consuma coi log."
    warn "Consigliato reinstallare con root su SSD USB. Intanto attivo log2ram."
else
    ok "Root su $ROOT_DISK, non su flash interna"
fi

# ---------------------------------------------------------------------------
# Pacchetti e impostazioni di base
# ---------------------------------------------------------------------------
step "Pacchetti"
export DEBIAN_FRONTEND=noninteractive
run apt-get update -qq

# I pacchetti essenziali vanno in una apt separata da quelli opzionali: apt e'
# tutto-o-niente, e un solo nome non disponibile fa fallire l'intera chiamata
# senza installare nulla. Con un `|| warn` sembrerebbe pure andata bene, e poi
# la creazione del venv fallirebbe senza un motivo evidente.
PKGS=(python3 python3-venv python3-pip curl ca-certificates iputils-ping unattended-upgrades)
run apt-get install -y -qq "${PKGS[@]}"
ok "pacchetti essenziali installati"

# Log in RAM: Armbian di suo monta /var/log su zram (armbian-ramlog), quindi
# nella maggior parte dei casi non serve aggiungere niente.
LOG_ON_RAM=0
systemctl is-active --quiet armbian-ramlog 2>/dev/null && LOG_ON_RAM=1
case "$(findmnt -no SOURCE /var/log 2>/dev/null)" in
    /dev/zram*) LOG_ON_RAM=1 ;;
esac
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
run "$APP_DIR/venv/bin/pip" install -q --upgrade pip
run "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
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

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/agent.py
Restart=always
RestartSec=10

# Il box custodisce credenziali dei router dei clienti: vale la pena stringere.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$APP_DIR
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true

[Install]
WantedBy=multi-user.target
EOF
fi
run systemctl daemon-reload
run systemctl enable --now "$SERVICE"
ok "servizio $SERVICE attivo"

# ---------------------------------------------------------------------------
# Accesso remoto: cloudflared
#
# Il tunnel esce dall'appliance verso Cloudflare, quindi funziona senza IP
# pubblico e senza aprire nulla sul router del cliente. Ci si collega con l'app
# WARP, da portatile o da telefono.
#
# L'endpoint della VPN e' il box, non il router: e' il motivo per cui questo
# funziona uguale con un MikroTik, un TP-Link o la scatola dell'ISP.
#
# Opzionale: senza --tunnel-token il box monitora e basta.
# ---------------------------------------------------------------------------
if [ -n "$TUNNEL_TOKEN" ]; then
    step "Accesso remoto (cloudflared)"

    # Il nome del binario dipende dall'architettura. Su ARM a 32 bit Cloudflare
    # non pubblica un pacchetto Debian, solo il binario: va installato a mano e
    # aggiornato da noi.
    case "$(uname -m)" in
        armv7l|armv6l) CF_ARCH=arm ;;
        aarch64|arm64) CF_ARCH=arm64 ;;
        x86_64)        CF_ARCH=amd64 ;;
        *) die "Architettura $(uname -m) non gestita per cloudflared." ;;
    esac
    CF_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$CF_ARCH"

    if [ -x /usr/local/bin/cloudflared ]; then
        ok "cloudflared gia' presente ($(/usr/local/bin/cloudflared --version 2>/dev/null | head -1))"
    else
        run curl -fsSL "$CF_URL" -o /tmp/cloudflared
        run install -m 0755 /tmp/cloudflared /usr/local/bin/cloudflared
        run rm -f /tmp/cloudflared
        ok "cloudflared installato per $CF_ARCH"
    fi

    # Il token del tunnel vale quanto una chiave di casa: file a parte, non
    # dentro l'unit di systemd, che e' leggibile da chiunque.
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '    [dry-run] scrittura di %s (0600 root)\n' "$CF_ENV_FILE"
        printf '    [dry-run] scrittura di /etc/systemd/system/cloudflared.service\n'
    else
        umask 077
        printf 'TUNNEL_TOKEN=%s\n' "$TUNNEL_TOKEN" > "$CF_ENV_FILE"
        chown root:root "$CF_ENV_FILE"
        chmod 0600 "$CF_ENV_FILE"

        cat > /etc/systemd/system/cloudflared.service <<EOF
[Unit]
Description=Cloudflare Tunnel (accesso remoto NetMonitor)
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
EnvironmentFile=$CF_ENV_FILE
ExecStart=/usr/local/bin/cloudflared --no-autoupdate tunnel run --token \${TUNNEL_TOKEN}
Restart=always
RestartSec=10
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF
    fi
    run systemctl daemon-reload
    run systemctl enable --now cloudflared
    ok "tunnel attivo, token in $CF_ENV_FILE"
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

    ${YELLOW}SSH non e' stato irrigidito${OFF}: si fa in A2, quando la VPN
    funziona e hai una seconda via d'ingresso.

EOF
