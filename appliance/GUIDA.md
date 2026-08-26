# Guida facile: mettere online un sito nuovo

Questa guida serve a chi non l'ha mai fatto. Non devi sapere niente di computer:
devi solo saper copiare e incollare, e leggere quello che compare sullo schermo.

Se segui i passi in ordine, in mezz'ora il negozio del cliente compare in
NetMonitor e tu vedi da casa cosa succede nella sua rete.

> Nel dubbio, **fermati e chiedi**. Nessun passo di questa guida è urgente, e
> un box montato male costa un viaggio.

---

## Cosa stai per fare, in tre righe

Attacchi una scatolina alla rete del cliente. La scatolina guarda chi c'è
collegato e lo racconta a NetMonitor, il sito web. Tu apri NetMonitor da
qualsiasi posto e vedi se va tutto bene.

La scatolina **non** fa passare il traffico del cliente: se si rompe o la
staccano, il negozio continua a lavorare come prima. Guarda e basta.

---

## Le parole che uso in questa guida

| Parola | Vuol dire |
|---|---|
| **il box** | la scatolina nera. È un piccolo computer. |
| **il router** | la scatola del cliente da cui esce internet, quella con le lucine |
| **NetMonitor** | il sito web dove compaiono i clienti |
| **il token** | una password lunghissima. Serve al box per dire "sono io" |
| **il terminale** | la finestra nera dove si scrivono i comandi |
| **un comando** | una riga che scrivi nel terminale e poi premi Invio |

Quando scrivo un comando lo trovi in un riquadro. **Copialo tutto**, incollalo
nel terminale, premi Invio. Se una riga finisce con `\`, vuol dire che il
comando continua sotto: copia tutto il blocco insieme.

---

## Cosa ti serve sul tavolo

- [ ] il **box** con il suo alimentatore
- [ ] un **cavo di rete** (quello con lo spinotto quadrato che fa clic)
- [ ] il **router del cliente**, con una presa libera
- [ ] un **monitor con presa HDMI** e una **tastiera** — oppure un portatile,
      se sai già collegarti al box da lontano
- [ ] la password di amministratore **del router del cliente**
- [ ] il tuo accesso a **NetMonitor**

---

## Passo 1 — Crea il sito in NetMonitor

Apri NetMonitor sul computer e vai su **Siti → Nuovo sito**.

Scrivi due cose:

- **Nome**: come vuoi chiamare il cliente. Lo vedrai tu in dashboard.
- **Rete**: i numeri della rete del cliente, per esempio `192.168.88.0/24`.
  Se non li sai, li scoprirai al passo 5 e potrai correggerli dopo.

Salva. Nella pagina del sito compare una **stringa lunghissima di lettere e
numeri**: è il token. **Copiala e tienila da parte**, ti serve al passo 6.

> Un token per ogni cliente. Non riusare quello di un altro sito: i due box si
> cancellerebbero i dati a vicenda.

---

## Passo 2 — Chiedi al router il permesso di guardare

Il box riesce a vedere qualcosa anche senza questo passo, ma vede poco: sa
*chi* c'è, non *come* è collegato. Con questo passo vede tutto.

Devi creare, dentro il router, un utente che può **solo leggere**.

### Se il router è un MikroTik

Collegati al router con Winbox o WebFig, apri il suo terminale e scrivi:

```
/system resource print
```

Guarda la riga `version`. Il primo numero è quello che conta.

**Se comincia per 7:**

```
/certificate add name=netmonitor common-name=netmonitor days-valid=3650
/certificate sign netmonitor
/ip service set www-ssl certificate=netmonitor disabled=no
/user add name=netmonitor group=read password=SceglineUna2026
```

La seconda riga può metterci qualche minuto. Aspetta che finisca.

**Se comincia per 6:**

```
/ip service set api disabled=no
/user add name=netmonitor group=read password=SceglineUna2026
```

> **La password: solo lettere e numeri.** Niente `#`, niente spazi, niente
> simboli strani. Il carattere `#` viene letto come "da qui in poi ignora
> tutto", e ti ritrovi un utente senza la password che credevi di aver messo.
> È già successo, e per capirlo ci vuole un'ora.

**Scrivi su un foglio la password che hai scelto.** Ti serve al passo 6.

### Se il router è di un'altra marca

Entra nella sua pagina web e cerca **SNMP**. Attivalo in sola lettura e annota
la parola che ti chiede (di solito è già scritto `public`).

### Già che ci sei: antenne e switch

Su ogni antenna WiFi e su ogni switch che ha una pagina web:

- attiva **SNMP** (di solito basta lasciare `public`)
- attiva **LLDP** se lo trovi: è quello che disegna l'albero dei collegamenti
- nel campo **Location** scrivi dove si trova davvero: `Sala`, `Magazzino`.
  Quel nome arriva da solo in NetMonitor e ti risparmia di riscriverlo a mano.

---

## Passo 3 — Attacca il box

1. Infila il cavo di rete in una **presa libera del router** e l'altro capo nel
   box.
2. Attacca l'alimentatore.
3. Se hai monitor e tastiera, collegali adesso.

> **Importante:** il cavo va in una presa normale del router, quelle numerate.
> **Non** fra il modem e il router. Il box deve stare di lato a guardare, non in
> mezzo alla strada: se si blocca stando in mezzo, il cliente resta senza
> internet e tu resti senza il collegamento che ti serviva per ripararlo.

Accendi. Aspetta un paio di minuti la prima volta.

---

## Passo 4 — Entra nel box

Sullo schermo compare una riga che chiede nome utente e password: è il box che
ti sta chiedendo chi sei.

- **Nome utente e password** sono quelli dell'immagine Armbian già installata.
  Se il box è nuovo, la prima volta ti obbliga a cambiare la password: falla
  semplice ma scrivila su un foglio.
- Se preferisci lavorare dal tuo portatile, collegati con SSH. Se non sai cosa
  vuol dire, usa monitor e tastiera: è la stessa cosa, più semplice.

Da qui in poi tutto quello che scrivi lo scrivi **nel box**.

> Dopo l'installazione questa richiesta di password sparisce: il box entrerà da
> solo ogni volta che si riaccende. È voluto — vedi il passo 7.

---

## Passo 5 — Scarica il programma

Copia e incolla, una riga alla volta:

```bash
git clone https://github.com/PedroCNetwork/dnsarm.git netmonitor
cd netmonitor
```

La prima riga scarica il programma da internet. La seconda ti sposta dentro la
cartella appena scaricata.

**Se il box ce l'ha già** (lo stai reinstallando), usa invece:

```bash
cd ~/netmonitor
git pull
```

`git pull` vuol dire "scarica gli aggiornamenti". Se risponde `Già aggiornato`,
va bene: vuol dire che hai già l'ultima versione.

### Prima la prova a vuoto

```bash
sudo ./appliance/install.sh --dry-run --token x
```

Questo comando **non tocca niente**: mostra soltanto cosa farebbe. Serve a
leggere una riga in particolare:

```
rete:    192.168.88.0/24
```

Quella è la rete vera del cliente. Confrontala con quella che hai scritto in
NetMonitor al passo 1: **devono essere identiche**. Se sono diverse, correggi
il campo *Rete* nella pagina del sito.

Se invece compare `Nessuna rotta di default: il box non ha ancora rete`, il box
non sta ricevendo internet: controlla il cavo e le lucine della presa.

---

## Passo 6 — Installa davvero

Adesso ti servono le due cose che hai messo da parte: il **token** del passo 1 e
la **password del router** del passo 2.

```bash
sudo ./appliance/install.sh \
  --token INCOLLA-QUI-IL-TOKEN \
  --site "Nome del Cliente" \
  --router-user netmonitor
```

Al posto di `INCOLLA-QUI-IL-TOKEN` incolla la stringa lunga. Al posto di
`Nome del Cliente` scrivi il nome, **fra virgolette**.

Poi ti chiede la password del router. **Scrivila e premi Invio: non vedrai
comparire niente mentre digiti.** È normale, è fatto apposta perché nessuno la
legga da sopra la spalla.

Adesso aspetta. Su questi box **ci mette diversi minuti**, e per qualche minuto
sembra bloccato. Non è bloccato: sta scaricando. **Non chiudere, non staccare
la corrente, non premere Ctrl+C.**

Alla fine deve comparire `Fatto.` insieme a un riassunto. Se compare una riga
rossa che comincia per `KO`, leggila: dice cosa manca. La tabella in fondo a
questa guida spiega le più comuni.

---

## Passo 7 — Sistema l'accensione

Questi box hanno un difetto di fabbrica: quando si accendono **provano a
partire dalla presa di rete** invece che dalla loro memoria. Se il cavo è
staccato, o se lo switch è ancora spento dopo un blackout, il box può restare
al buio e non accendersi mai.

Si sistema una volta sola, e va fatto **adesso che sei davanti al box**.

Prima guarda com'è messo — questo comando legge e basta, non cambia niente:

```bash
sudo ./appliance/netmonitor-boot-mmc
```

Se fra le righe compare **`c'è la rete nell'ordine di avvio`**, allora
sistemalo:

```bash
sudo ./appliance/netmonitor-boot-mmc --applica
```

Se invece dice che è già a posto, non fare altro.

### Provalo davvero, adesso

1. Stacca il cavo di rete dal box.
2. Stacca e riattacca la corrente.
3. Il box deve arrivare da solo alla schermata di lavoro, **senza aspettare**
   e **senza chiedere la password**.
4. Riattacca il cavo di rete.

Se non si accende, riattacca il cavo di rete e riaccendi: tornerà su. Poi
lancia `sudo ./appliance/netmonitor-boot-mmc --ripristina`, che rimette le cose
com'erano prima, e riprova un'altra volta con calma.

> Se lo script ti dice che **manca un file** e che non vuole indovinare, non
> insistere: è una protezione. Su quel box la sistemazione va fatta in un altro
> modo, e serve qualcuno che sappia farla. Il resto funziona lo stesso.

---

## Passo 8 — Controlla che sia tutto verde

Nel box:

```bash
journalctl -u netmonitor-agent -f
```

Questo comando mostra in diretta quello che il box sta facendo. Entro un minuto
devono comparire queste righe:

```
Heartbeat ok
Ciclo completato: 5 device, 12/12 client
```

- **`Heartbeat ok`** vuol dire "il box parla con NetMonitor".
- **`Ciclo completato`** vuol dire "il box ha guardato la rete e ha trovato
  roba". I numeri cambiano da cliente a cliente.

Per uscire da questa schermata premi **Ctrl+C**. Non spegne niente: chiude solo
la finestra dei messaggi.

Poi apri NetMonitor sul computer: il sito deve essere passato a **Agent
online**, e nella mappa devi vedere il router con sotto le antenne.

### L'ultima prova, quella che convince

**Stacca la corrente al box.** La rete del cliente deve continuare a funzionare
come se niente fosse. Riattacca, e dopo qualche minuto il sito torna online da
solo.

Hai finito. Il cliente è online.

---

## Cosa succede quando manca la corrente

Non devi fare niente: il box si rimette in piedi da solo. Vale la pena sapere
come, perché è la domanda che ti farai il giorno del primo blackout.

Quando torna la corrente si riaccende tutto insieme, e il box è pronto prima del
router del cliente. Il box quindi aspetta: guarda se il router risponde, guarda
se l'orologio è giusto (questi box non hanno la pila, quindi ripartono con la
data sbagliata) e solo quando le due cose tornano riparte a lavorare.

Poi continua a controllarsi da solo ogni cinque minuti. Se qualcosa si è
inceppato lo sistema; se invece è la linea del cliente a essere giù, **non tocca
niente**, perché il box non ha nessuna colpa e riavviarlo non servirebbe.

Se vuoi vedere cosa sa in questo momento:

```bash
sudo netmonitor-riavvio --stato
```

Ti risponde con un elenco in italiano: se il servizio è attivo, se il router
risponde, se NetMonitor è raggiungibile, se l'orologio è giusto.

---

## Entrare nella rete del cliente da casa (facoltativo)

Serve se vuoi aprire Winbox o la pagina del router del cliente stando altrove.
Si attiva **da NetMonitor**: sul box non si scrive niente.

1. Una volta sola, per te: fatti un account gratuito su Cloudflare e attiva
   **Zero Trust** su [one.dash.cloudflare.com](https://one.dash.cloudflare.com).
   Ti chiede un nome per l'organizzazione e di scegliere il piano **Free**.
2. Per ogni cliente: **Networks → Tunnels → Create a tunnel**, tipo
   **Cloudflared**, dai il nome del cliente, **Save**.
3. Compare una schermata con dei comandi da installare: **non eseguirli**. Serve
   solo la stringa lunga che comincia per `eyJ`. Copiala.
4. In NetMonitor, pagina del sito → riquadro **Accesso remoto** → incolla la
   stringa → **Attiva accesso remoto**.

Lo stato passa per *In attesa del box* e diventa **Attivo** dopo una trentina di
secondi. Se resta in attesa, aspetta ancora un minuto: il box ritira il token
quando fa il giro successivo.

> Un tunnel per ogni cliente. Lo stesso token su due clienti li mette nella
> stessa strada e si pestano i piedi.

---

## Dare i nomi alle cose

Nella mappa di NetMonitor **clicca su un pallino** e puoi scrivere nome, tipo e
posizione. Serve per tutto quello che in rete non sa dire come si chiama:
stampanti delle comande, telecamere, casse.

Quello che scrivi tu non viene mai cancellato dal monitoraggio. Se svuoti un
campo, torna il nome trovato automaticamente.

Nelle note scrivi le cose che nessun programma potrà mai sapere:
*"alimentata dalla presa dietro il bancone"*.

---

## Se qualcosa non va

| Cosa vedi | Cosa vuol dire | Cosa fare |
|---|---|---|
| Il comando si chiude subito senza dire niente | c'è un simbolo strano in un valore | rimetti il valore fra virgolette singole: `'valore#strano'` |
| `Nessun driver riesce a parlare con il router` | nome utente o password sbagliati | rifai il passo 2 e riprova con la password giusta |
| `invalid user name or password` | la password del router non è quella che credi | rimettila da capo sul router, solo lettere e numeri |
| `0 dispositivi trovati` | il box non riesce a guardare la rete | rilancia l'installazione del passo 6 |
| Il sito resta **offline** dopo un blackout | il box è ripartito prima del router | non fare niente per cinque minuti: si sistema da solo. Poi `sudo netmonitor-riavvio --stato` |
| Il box **non si accende più** dopo un blackout | quel difetto di fabbrica del passo 7 | attacca il cavo di rete e riaccendi, poi fai il passo 7 |
| `comando non trovato` | il box non ha ancora quel programma | `cd ~/netmonitor && git pull`, poi rifai il passo 6 |
| `Già aggiornato` ma manca qualcosa | l'aggiornamento non è mai stato pubblicato | scrivi a chi gestisce il programma: non è colpa del box |
| Il sito non compare online | il token è di un altro cliente | ricontrolla il token nella pagina del sito e rifai il passo 6 |

### Il comando che risponde in dieci secondi

Se il box non riesce a parlare col router, questo dice **perché**:

```bash
sudo bash -c 'set -a; . /etc/netmonitor/agent.env; set +a; \
  /opt/netmonitor/venv/bin/python ~/netmonitor/agent/agent.py --check-router'
```

---

## Aggiornare un box già installato

Quando esce una versione nuova, sul box:

```bash
cd ~/netmonitor
git pull
sudo ./appliance/install.sh --token IL-TOKEN-DI-QUESTO-SITO --site "Nome del Cliente"
```

**Le due righe insieme, sempre.** `git pull` da solo scarica il programma nuovo
ma non lo mette al lavoro: è l'installazione che lo sostituisce davvero.

Fai **un box alla volta**, e controlla che sia tornato online prima di toccare
il successivo. Se qualcosa va storto su uno solo, hai ancora tutti gli altri
funzionanti e un solo posto dove guardare.

---

## Il foglietto da tenere in tasca

| Cosa voglio | Comando |
|---|---|
| Vedere cosa sta facendo il box | `journalctl -u netmonitor-agent -f` |
| Vedere se sta bene | `sudo netmonitor-riavvio --stato` |
| Farlo ripartire | `sudo systemctl restart netmonitor-agent` |
| Sistemare l'accensione | `sudo netmonitor-boot-mmc` |
| Aggiornarlo | `cd ~/netmonitor && git pull` poi l'installazione |
| Uscire da una schermata che scorre | premi `Ctrl+C` |
