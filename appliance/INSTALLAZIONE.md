# Installare NetMonitor in un sito nuovo

Procedura completa, dall'inizio alla verifica. Pensata per essere seguita senza
ricordarsi niente: ogni passo dice cosa fare, cosa deve succedere, e cosa fare
se non succede.

Tempo indicativo: **20 minuti**, di cui metà è attesa di `apt`.

---

## Prima di partire

Da avere sotto mano:

- il **box** con Armbian installato e collegato via cavo a una porta LAN del router
- accesso al **router** del cliente (utente amministrativo)
- il **nome del sito** e la **rete** (es. `192.168.88.0/24`)
- facoltativo: un token di **Cloudflare Tunnel**, per l'accesso remoto

Il box va su una porta LAN, **non** fra modem e router. Se sta sul percorso del
traffico e si blocca, il cliente resta offline e tu perdi anche l'accesso remoto
per ripararlo.

---

## 1. Creare il sito in NetMonitor

Dalla web app: **Siti → Nuovo sito**.

- **Nome**: come lo chiami tu, comparirà in dashboard
- **Rete**: il CIDR reale della LAN del cliente, es. `192.168.88.0/24`

Copia l'**Agent Token** che compare nella pagina del sito. Serve al passo 4.

> Un sito = un collector. Non riusare il token di un altro sito: due collector
> sullo stesso sito si sovrascrivono i dati a vicenda.

---

## 2. Preparare il router

Serve un utente in **sola lettura**. Il monitoraggio funziona anche senza, ma si
ferma a "chi risponde": niente tipo di connessione, niente interfaccia, niente
segnale WiFi. Quei dati stanno solo nelle tabelle del router.

### Se è un MikroTik

Verifica prima la versione, perché cambia tutto:

```
/system resource print
```

**RouterOS 7.x** — c'è l'API REST. Attenzione: `www-ssl` senza certificato
accetta la connessione ma fallisce l'handshake TLS, quindi il certificato non è
opzionale.

```
/certificate add name=netmonitor common-name=netmonitor days-valid=3650
/certificate sign netmonitor
/ip service set www-ssl certificate=netmonitor disabled=no
/user add name=netmonitor group=read password=SceglineUna2026
```

La firma può richiedere qualche minuto su hardware modesto. Verifica con
`/certificate print`: davanti al nome deve comparire la **K**.

**RouterOS 6.x** — il REST non esiste, è arrivato con la 7.1. Si usa l'API
binaria sulla 8728, che non richiede certificati:

```
/ip service set api disabled=no
/user add name=netmonitor group=read password=SceglineUna2026
```

Controlla che il servizio sia attivo — senza `X` davanti:

```
/ip service print
```

> **La password: solo lettere e numeri.** Un `#` iniziale viene interpretato
> come inizio di commento sia dalla console di RouterOS sia da bash, e finisci
> con un utente creato senza la password che credevi di avergli dato. È successo,
> e costa un'ora a capirlo.

### Se è di un'altra marca

Attiva **SNMP v2c** in sola lettura dall'interfaccia web dell'apparato e annota
la community. L'agent la usa per nome, modello e topologia. Su TP-Link,
EnGenius e switch gestiti funziona senza altro.

---

## 3. Preparare gli access point e lo switch

Su ogni antenna e su ogni switch gestito, dall'interfaccia web:

- attiva **SNMP** (community di sola lettura, di solito `public`)
- attiva **LLDP** se c'è l'opzione — è quello che costruisce l'albero
- compila il campo **Location** con il posto reale: `Magazzino`, `Sala`. Arriva
  da solo in dashboard e ti risparmia di rinominare a mano.

> Uno switch **gestito** ti dà l'albero vero, porta per porta, e l'assorbimento
> PoE. Uno **non gestito** è invisibile: le antenne risultano tutte dietro la
> stessa porta del router. È una differenza che vale la pena in fase d'acquisto.

---

## 4. Installare sul box

Collegati al box (monitor e tastiera, o SSH) e:

```bash
git clone https://github.com/PedroCNetwork/dnsarm.git netmonitor
cd netmonitor
```

Prima **a vuoto**, per vedere cosa farebbe senza toccare niente:

```bash
sudo ./appliance/install.sh --dry-run \
  --token <AGENT-TOKEN-DEL-SITO> --site "Nome Cliente"
```

Controlla nell'output la riga **rete**: deve combaciare con quella scritta nel
sito in NetMonitor. Se no, correggi il campo nella web app.

Poi quella vera:

```bash
sudo ./appliance/install.sh \
  --token <AGENT-TOKEN-DEL-SITO> --site "Nome Cliente" \
  --router-user netmonitor
```

La password del router te la chiede a schermo, con l'input nascosto. È anche il
modo più sicuro di darla: sulla riga di comando finirebbe nella cronologia della
shell e sarebbe visibile a chiunque faccia `ps`.

Il passaggio dei pacchetti dura diversi minuti su hardware lento. **Non
interrompere**: `apt` a metà lascia il sistema in uno stato scomodo da sbrogliare.

### 4.1 L'ordine di avvio, finché sei davanti al box

Sulle TV box Rockchip u-boot prova spesso `pxe` e `dhcp` **prima** della eMMC:
con il cavo di rete staccato ogni accensione si porta dietro il timeout della
rete, e su alcune build alla scheda non ci arriva proprio. Dopo un blackout, con
lo switch ancora spento, il box resta al buio e non è un guasto dell'agent.

Guarda com'è messo (non tocca niente):

```bash
sudo netmonitor-boot-mmc
```

Se compare `c'è la rete nell'ordine di avvio`, correggilo **adesso che sei sul
posto**, non da remoto:

```bash
sudo netmonitor-boot-mmc --applica     # oppure install.sh --correggi-avvio
```

Poi provalo davvero: stacca il cavo di rete, spegni e riaccendi. Deve arrivare
al login senza aspettare la rete. Se qualcosa va storto, `--ripristina` rimette
la configurazione precedente dalla copia di sicurezza.

Se lo script dice che manca `/etc/fw_env.config`, si ferma di proposito: quel
file dice a quale offset della eMMC vive l'ambiente di u-boot, e scriverlo
sbagliato non dà errore — sovrascrive il bootloader. In quel caso la correzione
si fa da u-boot, con seriale o tastiera: `setenv boot_targets "mmc0 mmc1 usb0";
saveenv; reset`.

---

## 5. Verificare

```bash
journalctl -u netmonitor-agent -f
```

Entro un minuto devono comparire, in quest'ordine:

```
Heartbeat ok
Driver del router: mikrotik-api su 192.168.88.1
Router (mikrotik-api): N device, M client da system, bridge, neighbor, dhcp, wifi
Ciclo completato: N device, M/M client
```

L'elenco finale dice **quali tabelle** sono state lette davvero: serve a
distinguere "nessun client WiFi" da "non sono riuscito a leggere la tabella WiFi".

In NetMonitor il sito deve passare a **Agent online** e la topologia mostrare il
router con gli apparati sotto.

Il test che vale la pena fare una volta sola, per convincersi: **stacca
l'alimentazione al box**. La rete del cliente deve continuare a funzionare come
se niente fosse.

---

## 6. Accesso remoto (facoltativo)

Si attiva **da NetMonitor**, senza tornare sul box: il token arriva all'appliance
insieme all'heartbeat e viene applicato da sola. Sul box non si digita niente.

### 6.1 Una volta sola per il tuo account

Serve un account Cloudflare gratuito con **Zero Trust** attivato
([one.dash.cloudflare.com](https://one.dash.cloudflare.com)): la prima volta
chiede un nome per l'organizzazione — diventa `<nome>.cloudflareaccess.com` — e
di scegliere il piano **Free** (fino a 50 utenti; chiede una carta ma non
addebita).

### 6.2 Per ogni sito: creare il tunnel e copiare il token

1. **Networks → Tunnels → Create a tunnel** → tipo **Cloudflared** → nome del
   cliente (es. `vcaparm`) → **Save**.
2. Compare la schermata "Install and run a connector" con i comandi per ogni
   sistema. **Non eseguirli**: serve solo la stringa lunga che segue `--token`,
   quella che comincia per `eyJ`. Copiala.
3. La stessa schermata chiede di configurare *Public Hostname* o *Private
   Network*: si può fare dopo, vedi 6.4. Chiudi pure.

> Il token si ritrova quando serve: **Tunnels → il tuo tunnel → Configure →
> Install and run a connector**. Se lo perdi puoi rigenerarlo (*Refresh token*),
> ma il vecchio smette di funzionare e il box va riconfigurato.

### 6.3 Consegnarlo al box

In NetMonitor, pagina del sito → riquadro **Accesso remoto** → incolla il token →
**Attiva accesso remoto**.

Lo stato passa per *In attesa del box* e diventa **Attivo** entro una trentina di
secondi, quando il box conferma che Cloudflare ha registrato la connessione. Non
diventa attivo prima: con un token sbagliato `cloudflared` parte lo stesso e per
qualche secondo sembra vivo, quindi la conferma è la connessione registrata, non
il servizio avviato.

Se resta in attesa o va in errore, il motivo è nella UI e sul box:

```bash
cat /run/netmonitor/tunnel-state
journalctl -u cloudflared -n 30 --no-pager
```

**Un tunnel per sito.** Lo stesso token su due clienti li metterebbe nella stessa
rotta: reti separate, tunnel separati.

### 6.4 Decidere cosa raggiungere

Il tunnel da solo collega il box a Cloudflare: quello che ci passa dentro lo
decidono le regole del tunnel.

**Tutta la LAN del cliente** (è il modello di WiFiman Teleport, e non serve un
dominio):

1. Nel tunnel: **Private Network → Add a private network** → la LAN del cliente,
   es. `192.168.88.0/24`.
2. **Settings → WARP Client → Device settings → Split Tunnels**: includi quella
   rete (in modalità *Exclude* va tolta dalle esclusioni; in *Include* va
   aggiunta).
3. Dal portatile o dal telefono, app **WARP** collegata alla tua organizzazione:
   Winbox, WebFig e le interfacce degli apparati rispondono ai loro IP privati.

**Un singolo servizio via browser**, senza WARP: serve un dominio già su
Cloudflare. Nel tunnel, **Public Hostname → Add**: sottodominio + servizio
(es. `http://192.168.88.1` per WebFig). Proteggilo con una policy di Access,
altrimenti è pubblico.

Attenzione a non sovrapporre reti: due clienti entrambi su `192.168.1.0/24` non
possono stare nella stessa organizzazione WARP con rotte private uguali.

Limite del piano gratuito: **10 GB al mese** di traffico WARP — abbondante per
gestione, stretto per travasare firmware.

### 6.5 Il metodo vecchio, se preferisci

`install.sh --tunnel-token <TOKEN>` funziona ancora e applica il token subito,
senza passare dal cloud. Utile quando sei già davanti al box durante la prima
installazione.

---

## 7. Dare un nome alle cose

Nella topologia, **clicca un nodo** per assegnare nome, tipo, posizione e note.
Serve per tutto ciò che in rete non sa dire come si chiama: stampanti per le
comande, telecamere, dispositivi senza hostname.

Quello che scrivi non viene **mai** sovrascritto dal monitoraggio, e svuotare un
campo fa tornare il valore rilevato automaticamente.

Le note sono il posto per le cose che nessun protocollo ti dirà mai:
*"alimentata dalla presa dietro il bancone"*.

---

## Quando qualcosa non va

| Sintomo | Causa | Rimedio |
|---|---|---|
| L'installer termina **senza output** | un'opzione con valore che inizia per `#`: bash lo legge come commento | metti il valore fra apici singoli, o ometti `--router-pass` e falla chiedere a schermo |
| `Nessun driver riesce a parlare con il router` | credenziali o servizio | lancia la diagnosi qui sotto |
| `sslv3 alert handshake failure` | `www-ssl` senza certificato | genera il certificato (passo 2), oppure usa l'API binaria su RouterOS 6 |
| `/rest` risponde **404** | è un RouterOS 6: il REST non esiste | `/ip service set api disabled=no` |
| `invalid user name or password` | la password sul router non è quella che credi | reimpostala: `/user set netmonitor password=SoloLettereNumeri` |
| `0 dispositivi trovati` | scansione senza permessi | verifica che l'unit abbia `AmbientCapabilities=CAP_NET_RAW`; rilancia l'installer |
| Il servizio **non riparte** dopo un aggiornamento | vecchia versione dell'installer | `git pull` e rilancia: ora fa `systemctl restart` esplicito |
| Un apparato compare **due volte** | riga vecchia a database | si autocorregge al ciclo successivo dopo l'aggiornamento del backend |
| Dopo un **blackout** il sito resta offline | il box è ripartito prima del router: nessun lease DHCP, nessuna rete rilevata, e senza RTC l'orologio sbagliato fa fallire il TLS | ci pensa `netmonitor-riavvio`, che aspetta rete e ora vere e riavvia l'agent; per vedere cosa sa: `sudo netmonitor-riavvio --stato` |
| Il box **non si accende più** dopo un blackout | u-boot prova la rete prima della eMMC | `sudo netmonitor-boot-mmc` (passo 4.1), da fare stando davanti al box |
| `Nessuna rete da scansionare` nel log | l'agent è partito senza rotta di default | si ricorregge da solo al giro di telemetria successivo; se resta, `sudo netmonitor-riavvio --stato` |

### La diagnosi che risponde in dieci secondi

```bash
sudo bash -c 'set -a; . /etc/netmonitor/agent.env; set +a; \
  /opt/netmonitor/venv/bin/python ~/netmonitor/agent/agent.py --check-router'
```

Prova entrambi i trasporti — REST e API binaria — e stampa **l'errore vero**,
non un generico "non riesco".

Per vedere cosa espone un apparato qualsiasi via SNMP, prima di dare per scontato
che non parli:

```bash
/opt/netmonitor/venv/bin/python ~/netmonitor/agent/agent.py --check-snmp <IP> public
```

---

## Aggiornare un box già installato

```bash
cd ~/netmonitor && git pull
sudo ./appliance/install.sh --token <AGENT-TOKEN> --site "Nome Cliente" --router-user netmonitor
```

L'installer è idempotente: rilanciarlo non fa danni, aggiorna il codice e
riavvia il servizio.

---

## File e comandi utili

| Cosa | Dove |
|---|---|
| Configurazione | `/etc/netmonitor/agent.env` |
| Token del tunnel | `/etc/netmonitor/cloudflared.env` |
| Codice dell'agent | `/opt/netmonitor` |
| Log | `journalctl -u netmonitor-agent -f` |
| Riavvio | `sudo systemctl restart netmonitor-agent` |
| Stato | `systemctl status netmonitor-agent` |
| Ripresa dopo il blackout | `sudo netmonitor-riavvio --stato` |
| Controllo periodico | `systemctl list-timers netmonitor-controllo.timer` |
| Ordine di avvio (eMMC, non rete) | `sudo netmonitor-boot-mmc` |

Dopo ogni modifica a `agent.env` serve un riavvio del servizio.
