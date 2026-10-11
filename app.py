"""One explicit click delegates prepare/apply to the existing Cora host updater."""
import argparse
import fcntl
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler


BUSY = {'queued', 'running', 'recovery_required'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Redirect updater rifiutato.')


class Bridge:
    def __init__(self, root, branch):
        config = json.loads((root / 'config.json').read_text())
        if config.get('github_repository') != 'Aleselva12/serveria1':
            raise ValueError('Updater deve puntare ad Aleselva12/serveria1.')
        if config.get('github_branch', 'main') != branch:
            raise ValueError('Branch updater diverso: revisionare la configurazione host.')
        listen = config['listen']
        if listen in {'0.0.0.0', '::'}:
            listen = '127.0.0.1'
        self.url = 'http://' + ('[' + listen + ']' if ':' in listen else listen) + ':' + str(config['port'])
        self.token = (root / 'bridge.token').read_text().strip()
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, path, data=None):
        body = None if data is None else json.dumps(data).encode()
        request = Request(self.url + path, data=body, headers={
            'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=180) as response:
                return json.load(response)
        except HTTPError as error:
            raise RuntimeError('Updater ha rifiutato la richiesta (HTTP ' + str(error.code) + '). Controllare i log host.') from None


class Controller:
    def __init__(self, bridge, state, branch, interval=3):
        self.bridge, self.state, self.branch, self.interval = bridge, state, branch, interval
        self.lock = threading.Lock()
        self.row = json.loads(state.read_text()) if state.exists() else {'status': 'idle', 'message': 'Pronto.'}
        if self.row['status'] == 'running':
            self.row.update(status='interrupted', message='Pulsante riavviato. Controllare i job updater prima di riprovare; nessuna applicazione rilanciata.')
            self.save()

    def save(self):
        temporary = self.state.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.row, ensure_ascii=False))
        temporary.chmod(0o600)
        temporary.replace(self.state)

    def change(self, **values):
        with self.lock:
            self.row.update(values)
            self.save()

    def status(self):
        with self.lock:
            row = dict(self.row)
        try:
            status = self.bridge.request('/status')
            row.update(available=True, active_commit=status['active_commit'],
                       jobs=status.get('jobs', [])[:5], branch=self.branch)
        except Exception:
            row.update(available=False, branch=self.branch)
        return row

    def start(self):
        with self.lock:
            if self.row['status'] == 'running':
                raise ValueError('Aggiornamento già richiesto.')
            self.row = {'status': 'running', 'message': 'Recupero versione GitHub…', 'branch': self.branch}
            self.save()
            threading.Thread(target=self.run, daemon=True).start()

    def wait_job(self, identifier, expected):
        deadline = time.monotonic() + 7200
        while time.monotonic() < deadline:
            job = self.bridge.request('/jobs/' + identifier)
            self.change(job_id=identifier, phase=job.get('phase', ''), message='Operazione updater: ' + job.get('phase', job['status']))
            if job['status'] == expected:
                return job
            if job['status'] not in {'running', 'queued'}:
                raise RuntimeError('Job ' + identifier + ': ' + job['status'] + '. Consultare Programma o i log updater.')
            time.sleep(self.interval)
        raise RuntimeError('Attesa scaduta. Il job updater può continuare: controllarne lo stato prima di riprovare.')

    def run(self):
        try:
            status = self.bridge.request('/status')
            if any(job['status'] in BUSY for job in status.get('jobs', [])):
                raise RuntimeError('Updater occupato o recupero richiesto. Controllare Programma.')
            workspace = self.bridge.request('/workspaces', {'title': 'Pulsante: aggiornamento GitHub ' + self.branch})
            if workspace['source_commit'] == status['active_commit']:
                self.change(status='completed', message='La versione attiva include già il branch GitHub configurato.', commit=status['active_commit'])
                return
            commit = self.bridge.request('/workspaces/' + workspace['id'] + '/commit', {'message': 'Versione GitHub per aggiornamento manuale'})
            if commit['commit'] != workspace['source_commit']:
                raise RuntimeError('Workspace cambiato durante il lavoro: aggiornamento annullato.')
            self.change(commit=commit['commit'])
            job = self.bridge.request('/jobs', {'kind': 'prepare', 'workspace_id': workspace['id'], 'commit': commit['commit']})
            ready = self.wait_job(job['id'], 'ready')
            if ready['commit'] != commit['commit']:
                raise RuntimeError('Commit del rilascio non corrispondente.')
            job = self.bridge.request('/jobs', {'kind': 'apply', 'release_id': ready['id'], 'commit': commit['commit']})
            self.wait_job(job['id'], 'completed')
            current = self.bridge.request('/status')['active_commit']
            if current != commit['commit']:
                raise RuntimeError('Versione attiva diversa da quella richiesta.')
            self.change(status='completed', message='Aggiornamento completato. Puoi riaprire Cora.')
        except Exception as error:
            self.change(status='failed', message=str(error) if isinstance(error, (RuntimeError, ValueError)) else 'Collegamento updater non disponibile. Controllare il servizio host.')


PAGE = '''<!doctype html><html lang="it"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Aggiorna Cora</title>
<style>body{margin:0;background:#0d1423;color:#eef3ff;font:17px system-ui;display:grid;min-height:100vh;place-items:center}main{max-width:620px;padding:32px}h1{font-size:36px}p{line-height:1.6;color:#b9c8df}button{background:#73d3ba;color:#0d1423;border:0;border-radius:12px;padding:18px 26px;font:700 18px system-ui;cursor:pointer}button:disabled{opacity:.45}pre{white-space:pre-wrap;word-break:break-word;background:#19243a;padding:20px;border-radius:12px;font:14px system-ui}</style>
<main><small>MANUTENZIONE CORA</small><h1>Aggiorna il server</h1><p>Installa la versione più recente del branch configurato di ServerIA. Il clic autorizza build, test, backup e installazione. Cora sarà temporaneamente indisponibile durante il riavvio.</p><button id="go" disabled>Aggiorna da GitHub</button><pre id="status">Collegamento…</pre><p>Questa pagina resta disponibile durante l’aggiornamento. Le modifiche non ancora unite al branch configurato non vengono installate.</p></main>
<script>const button=document.getElementById('go'),out=document.getElementById('status');let pending=false;async function refresh(){try{const r=await fetch('/status',{cache:'no-store'});if(!r.ok)throw Error();const s=await r.json();out.textContent='Branch: '+s.branch+'\\nVersione attiva: '+(s.active_commit||'non disponibile')+'\\n'+s.message+(s.commit?'\\nVersione richiesta: '+s.commit:'')+(s.jobs?.length?'\\nUltimo job updater: '+s.jobs[0].status+' / '+s.jobs[0].phase:'');button.disabled=pending||!s.available||s.status==='running'||s.jobs?.some(j=>['running','queued','recovery_required'].includes(j.status));}catch(e){out.textContent='Servizio non raggiungibile.';button.disabled=true;}}button.onclick=async()=>{pending=true;button.disabled=true;try{const r=await fetch('/update',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':'__CSRF__'},body:'{}'});if(!r.ok){out.textContent=(await r.json()).message;return;}await refresh();}catch(e){out.textContent='Richiesta non confermata. Controllare lo stato prima di riprovare.';}finally{pending=false;}};refresh();setInterval(refresh,3000);</script></html>'''


def handler(controller, origins, csrf):
    hosts = {urlsplit(origin).netloc for origin in origins}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, value, html=False):
            data = value.encode() if html else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(code)
            for key, val in {'Content-Type': 'text/html; charset=utf-8' if html else 'application/json',
                             'Content-Length': str(len(data)), 'Cache-Control': 'no-store',
                             'X-Frame-Options': 'DENY', 'X-Content-Type-Options': 'nosniff',
                             'Referrer-Policy': 'no-referrer',
                             'Content-Security-Policy': "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}.items():
                self.send_header(key, val)
            self.end_headers()
            self.wfile.write(data)

        def valid_host(self):
            return self.headers.get('Host', '') in hosts

        def do_GET(self):
            if not self.valid_host():
                return self.reply(403, {'message': 'Host non autorizzato.'})
            if self.path == '/':
                return self.reply(200, PAGE.replace('__CSRF__', csrf), True)
            if self.path == '/status':
                return self.reply(200, controller.status())
            self.reply(404, {'message': 'Pagina non trovata.'})

        def do_POST(self):
            if not self.valid_host() or self.headers.get('Origin') not in origins or not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), csrf):
                return self.reply(403, {'message': 'Richiesta non autorizzata.'})
            if self.path != '/update':
                return self.reply(404, {'message': 'Operazione non disponibile.'})
            if self.headers.get('Content-Type') != 'application/json' or self.headers.get('Content-Length') != '2' or self.rfile.read(2) != b'{}':
                return self.reply(400, {'message': 'Richiesta non valida.'})
            try:
                controller.start()
                self.reply(202, {'message': 'Aggiornamento richiesto.'})
            except ValueError as error:
                self.reply(409, {'message': str(error)})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--updater-root', type=Path, default=Path('/srv/cora-updater'))
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--branch', default='main')
    parser.add_argument('--port', type=int, default=9030)
    parser.add_argument('--origin', action='append', default=[], help='Exact browser origin, e.g. https://server.tailnet.ts.net:8443')
    args = parser.parse_args()
    origins = args.origin or ['http://127.0.0.1:' + str(args.port)]
    for origin in origins:
        parsed = urlsplit(origin)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
            parser.error('Origin deve contenere soltanto schema e host/porta.')
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.state_dir / 'service.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        controller = Controller(Bridge(args.updater_root, args.branch), args.state_dir / 'status.json', args.branch)
        ThreadingHTTPServer(('127.0.0.1', args.port), handler(controller, set(origins), secrets.token_hex(32))).serve_forever()


if __name__ == '__main__':
    main()
