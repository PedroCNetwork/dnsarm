# Il box non parte dopo l'installazione col Multitool

Sintomo: hai seguito la procedura di jock — Multitool su microSD, «Burn image to
flash» su `mmcblk2`, spegni, togli la scheda, riaccendi — e invece del login
arriva questo:

```
U-Boot 2026.04_armbian-... (Aug 19 2026 - 16:32:01 +0000)

Model: Generic Rockchip RK322x TV Box board
Net:   eth0: ethernet@30200000

starting USB...
...
        scanning usb for storage devices... 0 Storage Device(s) found
Hit any key to stop autoboot: 0
=>
```

**Il box non è danneggiato e non serve la procedura di unbrick.** Quel prompt
`=>` è U-Boot, ed è la prova che metà del lavoro è riuscita: la BootROM del
Rockchip ha trovato il bootloader sulla eMMC e lo ha caricato. Se la scrittura
fosse fallita davvero, lo schermo resterebbe nero.

---

## 0. Prima di tutto: stacca la tastiera e riaccendi

**Fallo prima di qualunque diagnosi.** È costata una notte.

Quel `Hit any key to stop autoboot` fa esattamente quello che dice: **un tasto
qualsiasi ferma l'avvio** e lascia il prompt `=>`. Una tastiera USB difettosa —
o un hub, o un ricevitore wireless — che emette caratteri da sola interrompe
l'avvio **a ogni accensione**, per sempre. Il box è sanissimo e sembra rotto.

I due indizi, entrambi visibili nella schermata:

| Indizio | Cosa significa |
|---|---|
| Caratteri comparsi da soli sul prompt (`OQ`, `OQQQQQ...`) senza aver premuto niente | La tastiera sta scrivendo da sola: **è lei** |
| `ERROR: USB-error: NOT ACCESSED` in mezzo all'output | Un dispositivo USB risponde male — di solito lo stesso colpevole |

Quei caratteri sono le code delle sequenze di escape dei tasti freccia. Se li
vedi comparire **senza toccare niente**, hai già finito la diagnosi.

**Stacca la tastiera, togli e rimetti corrente, e non toccare niente.** Se
arriva al login, era quello: non c'era nessun problema di avvio, e non c'è
niente da correggere. Riattacca la tastiera solo dopo, o usane un'altra.

Solo se il box si ferma al prompt **con la tastiera staccata**, allora c'è
davvero un problema di destinazioni di avvio, e vale il resto di questa guida.

---

## Prima di digitare

Al prompt di U-Boot **non usare le frecce**: producono caratteri come
`OQQQQQ...` che sporcano la riga e fanno rispondere `Unknown command`. Se una
riga è già sporca, premi **Invio** una volta per pulirla e riscrivi.

Attenzione ai refusi: `mmc lio` non è un errore riconoscibile, U-Boot ti
risponde con l'intera pagina di aiuto del comando `mmc` e sembra che qualcosa
sia andato storto. È `mmc list`, con la **t**.

E prima di dare per buono un `boot_targets` letto a schermo, ricordati che
`printenv` mostra il valore **in memoria**: se in questa sessione hai già dato
un `setenv`, stai rileggendo quello che hai scritto tu, non il valore di
fabbrica. Per vedere quello vero, riavvia e leggilo **prima** di toccare
qualsiasi cosa.

---

## 1. C'è la eMMC?

```
mmc list
```

**Cosa deve succedere:**

```
mmc@30000000: 0
mmc@30020000: 1
```

Due controller: uno è lo slot microSD, l'altro la eMMC interna. Se li vedi
entrambi, il device tree è giusto e la eMMC esiste — il che esclude subito
l'ipotesi peggiore, cioè una board con NAND al posto della eMMC, su cui Armbian
non può girare dall'interno.

**Se risponde `No MMC device available`** o non stampa niente: salta alla
sezione «Quando la eMMC non risponde».

---

## 2. Quale dei due è la eMMC

Non darlo per scontato dalla numerazione: verificalo.

```
mmc dev 1
mmc dev 0
```

Su una board provata, con la microSD **fuori**, il risultato è stato:

```
=> mmc dev 1
switch to partitions #0, OK
mmc1(part 0) is current device          ← questa è la eMMC: risponde

=> mmc dev 0
Card did not respond to voltage select! : -110   ← questo è lo slot microSD, vuoto
```

| Risposta | Significato |
|---|---|
| `switch to partitions #0, OK` | È la eMMC, e si inizializza. |
| `Card did not respond to voltage select! : -110` | Slot microSD vuoto. **Con la scheda fuori è la risposta giusta, non un guasto.** |

Se rispondono entrambi, quello che risponde con la scheda estratta è la eMMC.

---

## 3. Guardare se il sistema è davvero installato

Usando il numero della eMMC trovato sopra (nell'esempio, `1`):

```
ls mmc 1:1 /boot
```

**Cosa deve succedere:** compaiono `boot.scr`, un `Image` o `zImage`, e una
cartella `dtb`. Allora il Multitool ha fatto il suo lavoro e manca solo che
U-Boot ci arrivi.

**Se la partizione non si legge**, l'immagine sulla eMMC è incompleta o non
adatta a questa board: vedi «Quando la eMMC non risponde», vale la stessa cura.

---

## 4. La cura, provata prima di scriverla

```
setenv boot_targets mmc1
boot
```

`setenv` **senza** `saveenv` vale solo per questa accensione: se qualcosa va
storto basta togliere e rimettere corrente e sei al punto di prima. Provare
prima di scrivere sul bootloader è la differenza fra un tentativo e un box da
riportare a casa.

**Cosa deve succedere:** parte il kernel e dopo un po' arriva il prompt di
login. **Il primo avvio è lento** — due o tre minuti — perché ridimensiona il
filesystem: è normale, non è bloccato.

Al primo accesso Armbian chiede la password di root e la creazione di un utente,
poi si passa a `rk322x-config` — dove però le scelte predefinite non sono quelle
giuste per un'appliance. Vedi il passo 5.

---

## 5. rk322x-config, con le scelte giuste per un'appliance

```bash
sudo rk322x-config
```

Conferma che la diagnosi era giusta: fra le *Detected board features* leggerai
`Internal flash: eMMC`. Poi fa tre domande, e per un box che deve stare acceso
da solo per mesi a casa di un cliente le risposte non sono quelle istintive.

### Tipo di SoC → **scegli la frequenza più bassa**

Il rilevamento dice `Chip type: RK3228A/B`: **non riesce a distinguere A da B**,
e le due varianti hanno limiti diversi (1,2 GHz contro 1,4 GHz). Le conseguenze
non sono simmetriche:

| Scelta | Se il chip era davvero l'altro |
|---|---|
| **RK3228A (1,2 GHz)** su un B | Perdi 200 MHz e non te ne accorgi. |
| RK3228B (1,4 GHz) su un A | Lo fai girare oltre il suo limite: blocchi casuali sotto carico o col caldo, cioè dopo giorni. |

Scegli **RK3228A (max 1.2Ghz)**. Duecento megahertz su un box che interroga un
router ogni sessanta secondi non valgono il rischio di una trasferta.

### Ottimizzazioni eMMC → **non selezionarne nessuna**

`emmc-pins`, `emmc-ddr-ph45`, `emmc-ddr-ph180`, `emmc-hs200`: lasciale tutte
vuote e premi OK, anche quella marcata *"Suggested to use"*.

Cambiano il modo in cui il controller parla con la memoria **da cui il box si
avvia**. La schermata stessa avverte che possono causare *"errors and
misdetections"*: se una la fa sbagliare, il riavvio successivo ti riporta al
prompt `=>`. E quello che guadagni è velocità su una macchina che quasi non
scrive — Armbian tiene già `/var/log` su zram, e fra agent e tunnel il traffico
su disco è irrisorio.

Ci si torna solo se compaiono errori di I/O nel log (`mmc1: error -110`,
`I/O error, dev mmcblk1` in `dmesg`), e in quel caso **una opzione alla volta**,
riavviando dopo ognuna.

### WiFi → **lascialo disattivato**

Il box sta su cavo. Il chip di questi TV box (South Silicon Valley 6051p) ha un
driver fuori dal kernel mainline: attivarlo aggiunge un pezzo fragile senza dare
niente in cambio.

---

## 6. Renderlo permanente

**È il passo che si dimentica**, proprio perché a questo punto sembra tutto a
posto. Il `setenv` del passo 4 vale per un avvio solo: se riavvii adesso, torni
al prompt `=>`. Ora che il sistema parte, la correzione si fa **da
Linux**, con lo strumento che il progetto ha già:

```bash
sudo netmonitor-boot-mmc            # diagnosi: dice cosa c'è e cosa farebbe
sudo netmonitor-boot-mmc --applica  # scrive l'ordine giusto
```

Su un box appena installato quello script non c'è ancora: arriva con
`install.sh`. Finché non hai installato NetMonitor, fai la correzione a mano
tornando una volta a U-Boot (il riquadro qui sotto).

Imposta `boot_targets` a `mmc0 mmc1 usb0`: niente `pxe`, niente `dhcp`. Tenere
`mmc0` (la microSD) **prima** della eMMC è voluto — così un domani basta
infilare una scheda per scavalcare un sistema che non parte più, senza smontare
niente.

Se lo script dice che manca `/etc/fw_env.config` si ferma di proposito, perché
scrivere quel file con l'offset sbagliato sovrascriverebbe il bootloader. In
quel caso torna a U-Boot e fai la cosa a mano, **una volta sola**:

```
setenv boot_targets "mmc0 mmc1 usb0"
saveenv
reset
```

**`saveenv` può fallire, e non è colpa tua.** Su queste immagini U-Boot salva
l'ambiente così:

```
Saving Environment to EXT4... Card did not respond to voltage select! : -110
** Bad device specification mmc 0 **
Failed (1)
```

Sta cercando di scrivere su **`mmc 0`**, cioè lo slot della microSD — che con la
scheda estratta è vuoto. La destinazione è decisa quando l'immagine viene
compilata e non si cambia da riga di comando. Su un box che gira da eMMC con lo
slot vuoto, `saveenv` **non può funzionare**: se ti serve davvero rendere
permanente un cambiamento dell'ambiente, la strada è la B (`boot.cmd`) oppure
far girare il box da microSD.

Poi provalo davvero: stacca il cavo di rete, spegni e riaccendi. Deve arrivare
al login senza aspettare la rete.

---

## Quando la eMMC non risponde

Se `mmc list` non elenca niente, oppure `mmc dev` fallisce su entrambi i
dispositivi con la scheda fuori, la eMMC non si inizializza. Su queste board è
un problema di modalità o velocità, e si sistema con `rk322x-config` — che però
gira solo da un sistema già avviato. Il giro si chiude così:

1. Scrivi la **stessa immagine Armbian** (non il Multitool: proprio il `.img`)
   su una microSD, con Balena o `dd`.
2. Infilala e accendi. La board preferisce la microSD alla eMMC quando c'è.
3. Se parte, hai un box funzionante: `sudo rk322x-config`, imposta le
   caratteristiche della board, e poi eventualmente rifai l'installazione su
   eMMC col Multitool.
4. Se **non parte nemmeno da microSD**, l'immagine non è adatta a questa
   variante di board. Cambia ramo: usa la stessa che gira sui box che hai già in
   produzione — oggi `current`, kernel `6.18.x`.

> È il consiglio che dà jock stesso in fondo alle istruzioni: *«I always
> recommend to first test that your device boots Armbian images from SD Card»*.
> Saltarlo fa risparmiare dieci minuti quando funziona e ne costa un'ora quando
> no.

**Restare su microSD non è una sconfitta.** Si perde lo slot, ma un box che si
ripristina cambiando una scheda invece che con una trasferta, per una macchina a
casa di un cliente, vale più di quello che costa.

---

## Perché succede

**Nella maggior parte dei casi: non succede.** Il box parte benissimo, e
qualcosa preme un tasto durante il conto alla rovescia. Su un box provato quel
qualcosa era una tastiera USB guasta, e la diagnosi è costata una notte perché
tutti gli indizi combaciavano anche con l'altra spiegazione: nessuna riga `mmc`
a schermo, USB scansionato, prompt `=>`. È esattamente quello che si vede quando
l'avvio viene interrotto — e lo si scambia volentieri per «U-Boot non prova la
eMMC», che è più interessante e quasi sempre falso.

Quando invece è davvero l'ordine di avvio: il Multitool scrive l'immagine e il
bootloader agli offset giusti, ma l'ambiente di U-Boot non sempre contiene la
eMMC fra le destinazioni. `boot_targets` arriva con `usb`, `pxe` e `dhcp` e
senza `mmc`, e U-Boot fa quello che gli è stato detto. È una riga di
configurazione, e va corretta una volta sola per box.

**La morale operativa:** prima di credere a una diagnosi elegante, elimina la
causa stupida. Costa dieci secondi — staccare un cavo — contro un'ora.
