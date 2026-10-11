# Pulsante aggiorna server

Pagina privata per aggiornare **Aleselva12/serveria1** dal branch `main` tramite
l'updater host già presente nel progetto Cora. Nessuna dipendenza Python aggiuntiva.
Non è un GitHub Action: premere un pulsante su GitHub non raggiungerebbe da solo
il server nella rete Tailscale. La pagina va installata una volta sul server,
poi è utilizzabile da PC e telefono.

## Cosa fa un clic

1. Recupera il branch GitHub configurato tramite l'updater e crea un workspace
   separato, senza modificare il checkout `/srv/cora-server-test`.
2. Fissa il commit recuperato, prepara le immagini API/web ed esegue i test.
3. Dopo esito positivo richiede l'applicazione dello stesso commit. L'updater
   ferma Cora, salva database e `state`, verifica le migrazioni su copia e avvia
   le nuove immagini con controlli di salute.
4. Mostra stato, commit attivo ed eventuali problemi. La pagina rimane attiva
   anche mentre Cora si riavvia; chiudere il browser non annulla il lavoro.

Il clic autorizza l'intera sequenza: nessun aggiornamento periodico, nessun merge
automatico, nessuna richiesta al modello. "Ultima versione" significa il commit
recuperato dal branch all'inizio della richiesta, non PR ancora aperte né commit
pubblicati mentre la build è in corso. I modelli Ollama restano invariati.

## Compatibilità e limiti reali

- Richiede che il servizio `cora-updater` sia già installato e funzionante.
  Il solo avvio dei container Cora **non** installa questo servizio host.
- Mantiene Compose, credenziali, porte, mount NAS e configurazione congelati
  dall'updater. Una modifica a `compose.yml` blocca la preparazione: serve una
  revisione host. Modifiche agli altri override non vengono adottate dal pulsante.
- Il servizio deve puntare ad `Aleselva12/serveria1`, branch `main`. Per mantenere
  invece il branch di test usare `--branch server-test/current-hardware`, purché
  anche l'updater sia configurato per quel branch. Nessun cambio branch silenzioso.
- Il server era inizialmente installato dal branch `server-test/current-hardware`:
  se questo diverge da `main`, l'updater rifiuta l'operazione. Integrare i commit
  necessari su GitHub o fare una migrazione host verificata prima di attivare il
  pulsante verso main. Non usare `reset --hard` per aggirare il problema.
- Se la versione attiva include già il branch, non prepara un rilascio superfluo.
  Una versione locale più avanzata viene conservata; non forza un downgrade.
- Usa lo stesso lock/job engine del Programmatore: un job attivo o uno stato
  `recovery_required` blocca nuovi lavori. Non eseguire in parallelo `git pull`,
  `manage.py up`, restore o altri aggiornamenti host.
- L'updater gestisce il recupero dagli errori di applicazione con il backup
  precedente. Questo progetto non implementa un secondo meccanismo di rollback.
  Consultare `updater/README.md` di ServerIA per errori e recupero manuale.
- Lo snapshot comprende DB Cora e `state`, non NAS esterni e modelli Ollama.
  Richiede spazio per copie, immagini e prova DB. Nessuna pulizia automatica.
- Un riavvio del solo pulsante interrompe la sua sequenza: un job già inviato
  continua nell'updater, ma non viene rilanciata automaticamente una futura
  applicazione. Controllare il job prima di riprovare.
- Il checkout originale non viene avanzato: la versione realmente installata è
  quella in `/srv/cora-updater/current.json` e nelle immagini attive.

## 1. Verifica sul server prima dell'installazione

Eseguire via SSH, senza inviare file di credenziali in chat:

```bash
cd /srv/cora-server-test
git status --short
git branch --show-current
git rev-parse HEAD
systemctl is-active cora-updater
python3 -c 'import json; c=json.load(open("/srv/cora-updater/config.json")); print({k:c.get(k) for k in ("github_repository","github_branch","state_root","project")})'
```

Se `/srv/cora-updater` manca, seguire prima **updater/README.md** nella versione
fidata di ServerIA. Il setup deve registrare il commit effettivamente installato
e tutti gli override realmente usati, compreso `compose.server-test.yml` e
l'eventuale override locale. Non rieseguire setup su una directory esistente.
Configurare l'accesso SSH a GitHub dell'utente updater e verificare la host key;
il pulsante non riceve chiavi GitHub. Non confondere il commit del checkout con
quello installato se nel frattempo le immagini sono state aggiornate altrove.

## 2. Installa la pagina una volta

Con lo stesso utente host dell'updater (nell'esempio `ale`):

```bash
git clone https://github.com/Aleselva12/pulsante-aggiorna.git /srv/pulsante-aggiorna
mkdir -m 700 /srv/pulsante-aggiorna-state
cd /srv/pulsante-aggiorna
python3 -m unittest discover -s tests -v
python3 app.py --state-dir /srv/pulsante-aggiorna-state
```

Se `/srv` non è scrivibile, l'amministratore deve predisporre quelle directory
assegnandole all'utente updater. Per una prova locale aprire
`http://127.0.0.1:9030` sul server oppure con tunnel SSH dal PC.
Non premere il pulsante di prova sul server personale prima del collaudo su
un'installazione sacrificabile del servizio updater.

## 3. Accesso da telefono nella rete privata

Usare una porta HTTPS separata, così non si sostituisce il proxy Cora su 443:

```bash
sudo tailscale serve --bg --https=8443 http://127.0.0.1:9030
tailscale serve status
```

Annotare l'URL mostrato, per esempio `https://serverale.NOME-TAILNET.ts.net:8443`.
Nel servizio sostituire `SERVER.TAILNET.ts.net` con l'hostname reale, conservando
la porta 8443; correggere `User` se diverso da `ale`.
Le richieste con Host/Origin diverso sono rifiutate. Il listener è sempre su
localhost, protetto da controlli di origine e token CSRF. Il token del bridge
resta sul server e non viene consegnato al browser.

L'accesso alla pagina è autorizzato dalla rete Tailscale, non dal login Cora:
chi può raggiungere questo endpoint nel tailnet può richiedere aggiornamenti.
Limitare le regole Tailscale ai propri dispositivi/identità; non attivare Funnel
né un proxy pubblico per questa pagina. Non si verifica qui la configurazione
effettiva del tailnet.

## 4. Avvio persistente

Dopo avere corretto hostname e utente nel file esempio:

```bash
sudo cp pulsante-aggiorna.service.example /etc/systemd/system/pulsante-aggiorna.service
sudo systemctl daemon-reload
sudo systemctl enable --now pulsante-aggiorna
systemctl status pulsante-aggiorna --no-pager
```

Aprire l'URL HTTPS dal telefono con Tailscale attivo. Il pulsante si abilita solo
quando l'updater risponde. Non serve un token GitHub nel telefono.

Documentazione Tailscale: https://tailscale.com/docs/reference/tailscale-cli/serve

## Verifiche eseguite nello sviluppo

Test con bridge simulato e HTTP reale locale: commit fissato, errore test senza
apply, nessuna nuova versione, updater occupato, recupero dopo riavvio, branch
errato, Host/CSRF e doppio clic. Il deployment Docker reale, SSH GitHub, backup,
migrazioni e accesso Tailscale vanno collaudati sul server: questi test non li
certificano. Non è stata effettuata un'installazione sul server personale.
