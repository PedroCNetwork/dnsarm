# Appliance NetMonitor

Un piccolo computer sotto Armbian (TV box, Raspberry, mini PC) che monitora la rete
di un sito, ne diventa il punto di accesso remoto e comanda il firewall del router.

> **È la prima volta?** Segui **[GUIDA.md](GUIDA.md)**: la stessa procedura in
> parole semplici, pensata perché chiunque possa mettere online un sito nuovo.
>
> **Devi installarne uno adesso?** Segui **[INSTALLAZIONE.md](INSTALLAZIONE.md)**:
> è la procedura in ordine, dal sito da creare alla verifica finale, con la tabella
> dei guasti già visti. Questo README spiega invece *perché* le cose stanno così, e
> si legge quando qualcosa non torna.

## Dove si collega

**A una porta LAN del router**, non fra modem e router.

Non è una preferenza estetica: se l'appliance sta sul percorso del traffico e si
blocca, il cliente resta offline *e* tu perdi la VPN che ti serviva per ripararlo.
Devi salire in macchina. Fuori banda, un box morto è un disservizio di monitoraggio,
non di connettività.

Il firewall non si perde: l'appliance non deve **essere** il collo di bottiglia, deve
**comandarlo**. Rileva, decide, e scrive le regole sul router via API (fase A4).

## Cosa serve

- Board ARM a 32 o 64 bit, oppure x86, con Debian/Ubuntu/Armbian e systemd. Provato
  su TV box Rockchip RK322x (ARMv7, 32 bit); le Amlogic S9xx vanno uguale.
- Una porta Ethernet sulla LAN del cliente, con DHCP
- Il token del sito, dalla pagina del sito in NetMonitor
- Facoltativo, per l'accesso remoto: un token di Cloudflare Tunnel

### Sul supporto di boot

La regola breve è "root su SSD USB", ma la ragione conta più della regola: la flash
consumer di una TV box si consuma a **scritture**, e chi scrive di continuo sono i log.

Armbian monta già `/var/log` su zram (`armbian-ramlog`), quindi su un box Armbian
standard il grosso del problema è risolto e l'eMMC va bene. L'installer lo rileva e in
quel caso non aggiunge nulla.

Con i log già in RAM, l'agent e il tunnel scrivono pochissimo: l'eMMC regge. L'SSD USB
torna necessario solo il giorno in cui il box ospiterà qualcosa con un database che
scrive di continuo.

### Vincoli accertati sul box RK322x

Rilevati sul campo, non dedotti dalle schede tecniche:

| Fatto | Conseguenza |
|---|---|
| **ARMv7 Cortex-A7, 32 bit (armhf)** | RK3229 non ha modalità 64 bit: non è una scelta dell'immagine. Ogni dipendenza va verificata per `armhf`, non per `arm64`. |
| Tailscale ha binari armv7 ufficiali | **A2 procede senza modifiche** |
| CrowdSec pubblica solo amd64, arm64, i386 | **A4 va ripensato**: CrowdSec non si installa qui |
| 4 core a ~1,2 GHz, 1 GB di RAM | scansioni e servizi vanno limitati, non lanciati a briglia sciolta |

**Come cambia A4.** CrowdSec non gira sul box, e non c'è un VPS dove spostarlo. Al suo
posto, a costo zero: l'agent scarica blocklist pubbliche e le scrive nell'address-list
del router via API, e il backend ricava le detection semplici dalla telemetria che già
riceve. Copre la maggior parte del valore senza infrastruttura nuova.

**Come cambia A2.** Niente VPN e niente control plane da ospitare: l'accesso remoto
passa dal WebSocket che l'agent tiene già aperto verso il backend, esteso a tunnel
TCP. Winbox e WebFig del cliente arrivano sul tuo PC attraverso NetMonitor.

### Sulla RAM

Questo box ha 1 GB. L'agent ci gira largo, ma la scansione va tenuta a freno: ogni
ping è un processo, e un `/24` senza limiti ne genererebbe 254 insieme. Il tetto è
`SCAN_CONCURRENCY` (default 32); su un `/16` alzarlo non serve, semmai abbassarlo.

La NIC di questa classe è a 100 Mbit. Fuori banda è irrilevante — non è il traffico
del cliente a passare da lì. Sarebbe stato un problema solo nella soluzione inline che
abbiamo scartato.

## Installazione

```bash
git clone <repo> netmonitor && cd netmonitor
sudo ./appliance/install.sh --token <agent-token> --site "Nome Cliente"
```

Prima di toccare qualcosa, per vedere cosa farebbe:

```bash
sudo ./appliance/install.sh --dry-run --token <agent-token> --site "Nome Cliente"
```

Lo script è idempotente: rilanciarlo su un box già configurato aggiorna e basta.

### Cosa fa

1. fotografa l'hardware (scheda, RAM, velocità della NIC, supporto di boot) e lo
   scrive nel log dell'installazione
2. installa i pacchetti, imposta fuso orario e hostname `netmon-<sito>`
3. arma il watchdog hardware, se la board ne ha uno
4. installa l'agent in `/opt/netmonitor` con un venv dedicato
5. scrive il token in `/etc/netmonitor/agent.env`, `0640 root:netmonitor`
6. crea e avvia il servizio `netmonitor-agent`
7. con `--tunnel-token`, installa anche `cloudflared` per l'accesso remoto
8. installa `netmonitor-riavvio` e il suo timer: la ripresa dopo un blackout
9. fa entrare la console da sola al riavvio (`--senza-autologin` per non farlo)
10. verifica che il servizio giri e che il backend risponda

### Cosa NON fa, di proposito

**Non tocca la configurazione SSH.** Irrigidirla prima che esista una seconda via
d'ingresso significa chiudersi fuori da un box headless a casa di un cliente. Si fa
dopo, quando il tunnel funziona e l'hai provato.

**Non cambia l'ordine di avvio senza che glielo si chieda.** Serve `--correggi-avvio`,
e va fatto stando davanti al box: vedi più sotto.

## Quando salta la corrente

Un blackout non è "il box si spegne e si riaccende". È un caso a sé, e nessuna delle
difese già in campo lo copre:

- `Restart=always` riavvia i processi che muoiono. Qui non muore nessuno.
- Il watchdog di systemd abbatte l'agent bloccato. Qui l'agent non è bloccato: gira,
  e a ogni giro riferisce diligentemente che non c'è niente da scansionare.

Quello che succede davvero è che **torna la corrente a tutti insieme**, e il box è
pronto prima del router del cliente. Da lì partono tre guasti distinti:

| Cosa | Perché fa male |
|---|---|
| Nessun lease DHCP all'avvio | l'agent rileva la rete una volta sola, all'avvio: senza rotta di default resta senza rete **per sempre**, con il servizio `active (running)` |
| Orologio sbagliato | la RK322x non ha batteria tampone e riparte alla data dell'immagine: finché l'NTP non sincronizza, ogni handshake TLS verso il backend fallisce perché il certificato "non è ancora valido" |
| u-boot che prova la rete | con il cavo staccato — o lo switch ancora spento — la board cerca di avviarsi da PXE invece che dalla eMMC, e a volte non ci arriva proprio |

I primi due li risolve `netmonitor-riavvio`, installato come unit di avvio più un
timer ogni cinque minuti. All'avvio aspetta che il gateway risponda (fino a cinque
minuti: un MikroTik con un AP dietro ci mette il suo) e che l'orologio sia credibile,
poi riavvia l'agent — **una volta, e solo se serve davvero**. Se l'NTP non ce la fa,
l'ora si prende dall'intestazione `Date` della risposta del backend, in chiaro: in
https il certificato non sarebbe ancora valido, ed è esattamente il problema che si
sta risolvendo.

Il terzo è il bootloader e si corregge una volta sola, con `netmonitor-boot-mmc`.

```bash
sudo netmonitor-riavvio --stato    # cosa sa e cosa farebbe, senza toccare niente
sudo netmonitor-boot-mmc           # diagnosi dell'ordine di avvio
sudo netmonitor-boot-mmc --applica # toglie pxe/dhcp: falla da davanti al box
```

### Il pezzo delicato: quando NON intervenire

Un guardiano che riavvia troppo è peggio del guasto che cura. La regola è che si
interviene solo davanti a una **prova di guasto locale**:

- gateway che non risponde → non si tocca niente. Il router del cliente può essere
  spento: il box è sano, e insistere lo porterebbe a riavviarsi in cerchio.
- heartbeat assente **ma backend irraggiungibile** → non si tocca niente. È la linea
  del cliente, e l'agent sta già facendo la cosa giusta riprovando.
- servizio fermo, agent partito senza rotta, descrittori che crescono → quelli sì.

Il riavvio della macchina arriva solo dopo tre controlli falliti di fila con la LAN
presente (un quarto d'ora), e mai più di uno ogni sei ore. È la stessa scelta fatta
nel watchdog dell'agent, che conferma il giro **fatto** e non il giro **riuscito**.

## Accesso remoto

**La VPN la fa il box, non il router.** È il motivo per cui funziona uguale con un
MikroTik, un TP-Link o la scatola dell'ISP: la marca del router non c'entra.

`cloudflared` apre un tunnel **in uscita** verso Cloudflare, quindi non serve un IP
pubblico dal cliente e non si apre niente sul suo router. Tu ti colleghi con l'app
**WARP**, da portatile o da telefono — l'esperienza è quella di WiFiman Teleport.

### Preparazione, una volta sola

1. Account Cloudflare gratuito, sezione **Zero Trust** (chiede un nome per
   l'organizzazione e il piano *Free*)
2. **Settings → WARP Client → Split Tunnels**: qui includerai le LAN dei clienti

### Per ogni sito

1. **Networks → Tunnels → Create a tunnel**, tipo *Cloudflared*, nome del cliente:
   dalla schermata di installazione copia solo la stringa dopo `--token` (comincia
   per `eyJ`). I comandi mostrati lì non servono.
2. In NetMonitor, pagina del sito → **Accesso remoto** → incolla il token → attiva.
   Il box lo ritira col primo heartbeat e riferisce se il tunnel è salito: sul posto
   non si digita niente.
3. Nel tunnel, **Private Network**: aggiungi la LAN del cliente (es. `192.168.2.0/24`)
   e includila nello Split Tunnel del profilo WARP.

Un tunnel per sito. Usare lo stesso token su due clienti diversi li metterebbe nella
stessa rotta: reti separate, tunnel separati.

`install.sh --tunnel-token <token>` resta valido e applica il token subito, senza
passare dal cloud: comodo quando sei già davanti al box.

### Limiti, detti prima

- **10 GB al mese** di traffico WARP sul piano gratuito. Winbox, WebFig e SSH ci
  stanno larghi; travasare firmware o backup no.
- Piano gratuito fino a 50 utenti, dispositivi illimitati.
- Su ARM a 32 bit non esiste un pacchetto Debian ufficiale: l'installer prende il
  binario dalle release GitHub. Funziona, ma gli aggiornamenti sono a carico nostro
  (`--no-autoupdate` è impostato apposta, così non si aggiorna da solo sotto i piedi).
- Dipendi da Cloudflare, come dipenderesti dai relay MikroTik con Back To Home.

### Se il cliente ha un MikroTik ARM

Attiva **anche** Back To Home sul router: due strade indipendenti. Se si pianta il
box entri dal router, se si pianta il router entri dal box. Attenzione al modello —
serve architettura ARM e RouterOS 7.12+: hAP lite e hAP ac lite sono MIPS e **non**
lo supportano, l'hAP ax lite sì.

**Il monitoraggio non dipende dal tunnel.** Senza `--tunnel-token` il box monitora
esattamente come prima: sono due funzioni separate, e quella che conta di più è la
prima.

## Verifica

```bash
journalctl -u netmonitor-agent -f
```

Entro un minuto devi vedere `Heartbeat ok` e `Ciclo completato: N device, M/M client`,
e il sito deve risultare online in NetMonitor.

Il test che conta davvero, da fare una volta sola per convincersi della scelta fuori
banda: **stacca l'alimentazione al box**. La rete del cliente deve continuare a
funzionare come se niente fosse.

## Configurazione

`/etc/netmonitor/agent.env`. I due campi di rete sono vuoti di default perché vengono
ricavati dalle rotte, non indovinati:

| Campo | Vuoto significa |
|---|---|
| `SCAN_NETWORK` | rete presa dalla rotta di sottorete sull'interfaccia dell'uplink |
| `GATEWAY_IP` | gateway preso dalla rotta di default |

Compilali solo se il rilevamento sbaglia. Attenzione su box con Docker: le bridge
`172.x` non devono essere scambiate per la LAN — l'agent le esclude già scegliendo
l'interfaccia che porta al gateway.

Dopo ogni modifica:

```bash
sudo systemctl restart netmonitor-agent
```

## Leggere le tabelle del router

Una scansione dalla LAN vede solo **chi risponde**. Lease DHCP, registration-table
WiFi con il segnale e bridge host table stanno dentro il router: vanno chieste a lui.

Come si arriva a quelle tabelle dipende dalla versione di RouterOS, e l'agent lo
scopre da solo provando: prima il REST, poi l'API binaria. Non devi dirglielo.

**RouterOS 7** — REST, cifrato. Attenzione: `www-ssl` senza certificato accetta la
connessione ma fallisce l'handshake TLS.

```
/certificate add name=netmonitor common-name=netmonitor days-valid=3650
/certificate sign netmonitor
/ip service set www-ssl certificate=netmonitor disabled=no
/user add name=netmonitor group=read password=SCEGLINE-UNA
```

**RouterOS 6** — il REST non esiste, è arrivato con la 7.1. Si usa l'API binaria
sulla 8728, che non richiede certificati:

```
/ip service set api disabled=no
/user add name=netmonitor group=read password=SCEGLINE-UNA
```

La password: **solo lettere e numeri**. Un `#` iniziale viene letto come inizio di
commento sia dalla console di RouterOS sia da bash, e finisci con un utente creato
senza la password che credevi di avergli dato.

Il canale però non è cifrato: le credenziali viaggiano in chiaro sulla LAN. Per un
utente in sola lettura su una rete che controlli è un compromesso ragionevole.

Il gruppo `read` basta: il driver non scrive nulla. Poi sul box:

```bash
sudo ./appliance/install.sh --token <token-del-sito> --site "Nome" \
  --router-user netmonitor
```

La password te la chiede a schermo, con l'input nascosto. È anche il modo più sicuro
di darla: su `--router-pass` finirebbe nella cronologia della shell e sarebbe visibile
a chiunque faccia `ps`.

Con le credenziali l'agent ottiene gli stessi dati dello script incollato sul
router — tipo di connessione, interfaccia, segnale, attribuzione all'access point —
e il router non ha più bisogno di quello script.

Nel log comparirà:

```
Router (mikrotik-rest): N device, M client da system, bridge, neighbor, dhcp, wifi
```

L'elenco finale dice **quali tabelle** sono state lette davvero: serve a distinguere
"nessun client WiFi" da "non sono riuscito a leggere la tabella WiFi".

Sul certificato: RouterOS ne presenta uno autofirmato, quindi la verifica TLS è
disattivata di default. La connessione resta cifrata e la controparte è un indirizzo
sulla LAN, non un host su Internet. Chi vuole la verifica importa una CA sul router e
mette `ROUTER_VERIFY_TLS=true` in `/etc/netmonitor/agent.env`.

Senza credenziali non cambia nulla di quanto già funziona: l'agent continua con la
sola scansione ARP.

## Cosa vede, e cosa non può vedere

L'appliance sta su una porta LAN, quindi **non** ha accesso a lease DHCP,
registration-table WiFi e bridge host table del router. Un ping sweep trova gli host
vivi e basta.

Oggi (A1) i client vengono dichiarati con sorgente `arp`: hanno risposto, quindi sono
presenti davvero. Mancano tipo di connessione, interfaccia e segnale — li recupererà
il driver del gateway in A3, interrogando il router via API o SNMP.

## Sicurezza

Il box custodisce le credenziali dei router dei clienti e un ingresso nelle loro reti:
è un bersaglio di valore.

- nessuna porta in ascolto verso la LAN del cliente, la VPN esce e basta
- il servizio gira come utente `netmonitor`, non root, con `ProtectSystem=strict`
- il token sta in un file `0640`, separato dal codice
- un token per sito: mai una credenziale buona per tutti i clienti
- se un token trapela, si ruota da NetMonitor con **Ruota token** e si rilancia
  l'installer

Il filtro DNS e la raccolta di dati sui dispositivi (A5) vanno concordati per iscritto
con il cliente. È ciò che distingue il monitoraggio dall'intercettazione.
