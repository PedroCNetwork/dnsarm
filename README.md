# NetMonitor — appliance

Codice che gira sull'appliance di sito: monitoraggio della rete del cliente e,
se richiesto, accesso remoto via Cloudflare Tunnel.

Generato da `scripts/publish-appliance.sh` del repo principale: **non modificare
qui**, le modifiche verrebbero sovrascritte alla prossima pubblicazione.

## Installazione sul box

```bash
git clone https://github.com/PedroCNetwork/dnsarm.git netmonitor
cd netmonitor
sudo ./appliance/install.sh --dry-run --token <token-del-sito> --site "Nome Cliente"
```

Il `--dry-run` mostra cosa farebbe senza toccare niente. Se torna, si rilancia
senza `--dry-run`.

Aggiornamenti successivi:

```bash
cd ~/netmonitor && git pull && sudo ./appliance/install.sh --token <token> --site "Nome"
```

Documentazione completa in [appliance/README.md](appliance/README.md).
