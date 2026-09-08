#!/usr/bin/env python3
"""Real BB/Codex recovery while an isolated Apple check stays offline.

Run with Python 3.14. Uses only disposable state and a loopback Responses peer.
Apple uses the existing simulation flag. BB receives an isolated CLI environment
and endpoint discovery receives the fixture Codex home. Detection, scheduling,
connection probes, BB retry, and output confirmation run unchanged.
Generated BB state may contain machine credentials; the run directory stays private.
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
APP = Path('/Applications/bb.app/Contents/Resources/app.asar.unpacked/node_modules/bb-app')
NODE = '/Applications/bb.app/Contents/MacOS/bb'
sys.path.insert(0, str(REPO / 'docs/experiments/0019-codex-recovery'))
from recovery_e2e import completed_events


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Peer(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self.server.events.append({'type': 'probe', 'restored': self.server.restored})
        self.send_response(200 if self.server.restored else 503)
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"models":[]}')

    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.server.events.append({'type': 'model_request', 'restored': self.server.restored})
        if not self.server.restored:
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        created = {'type': 'response.created', 'response': {
            'id': 'resp_recovered', 'status': 'in_progress', 'output': [], 'model': 'gpt-6-astra'}}
        for event in [created, *completed_events('resp_recovered')]:
            self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
        self.wfile.flush()
        self.server.events.append({'type': 'output_sent'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', default=shutil.which('codex'), help='Path to the Codex executable')
    args = parser.parse_args()
    if not args.codex:
        parser.error('Codex is unavailable. Specify --codex with an executable path.')
    codex = Path(args.codex).resolve(strict=True)
    run = Path(tempfile.mkdtemp(prefix='immortal-provider-e2e-')).resolve()
    run.chmod(0o700)
    for name in ('workspace', 'codex-home', 'bin', 'watcher'):
        (run / name).mkdir()
    peer = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Peer)
    peer.daemon_threads, peer.restored, peer.events = True, False, []
    threading.Thread(target=peer.serve_forever, daemon=True).start()
    endpoint = f'http://127.0.0.1:{peer.server_port}/v1'
    (run / 'codex-home/config.toml').write_text(f'''model_provider = "loopback_recovery"
[model_providers.loopback_recovery]
name = "Isolated recovery fixture"
base_url = "{endpoint}"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false
stream_max_retries = 0
request_max_retries = 0
stream_idle_timeout_ms = 1000
''')
    shim = run / 'bin/codex'
    shim.write_text('#!/usr/bin/python3\nimport os,sys\n'
                   + f"os.environ['CODEX_HOME'] = {str(run / 'codex-home')!r}\n"
                   + f'os.execv({str(codex)!r}, [{str(codex)!r}, *sys.argv[1:]])\n')
    shim.chmod(0o755)
    ports = [free_port(), free_port()]
    while ports[0] == ports[1]:
        ports[1] = free_port()
    env = {key: os.environ[key] for key in ('USER', 'LOGNAME', 'LANG', 'TMPDIR') if key in os.environ}
    env.update(PATH=str(run / 'bin') + ':' + os.environ.get('PATH', '/usr/bin:/bin'),
               SHELL='/bin/sh', ELECTRON_RUN_AS_NODE='1', BB_DATA_DIR=str(run / 'data'),
               BB_SERVER_BIND_HOST='127.0.0.1', BB_SERVER_PORT=str(ports[0]),
               BB_SERVER_URL=f'http://127.0.0.1:{ports[0]}', BB_HOST_DAEMON_PORT=str(ports[1]),
               BB_TELEMETRY='false', CODEX_HOME=str(run / 'codex-home'),
               WATCHER_STATE_DIR=str(run / 'watcher'), WATCHER_ONCE='1',
               BB_BIN=str(APP / 'dist/bb.js'), IMMORTAL_NODE=NODE,
               BB_CODEX_BRIDGE_APP_SERVER_COMMAND=str(shim),
               BB_CODEX_BRIDGE_APP_SERVER_ARGS='["app-server"]')
    subprocess.run(['git', 'init', '-q', str(run / 'workspace')], env=env, check=True)
    digest = hashlib.sha256(codex.read_bytes()).hexdigest()
    report = {'run_dir': str(run), 'binary': str(codex), 'endpoint': endpoint,
              'bb_version': json.loads((APP / 'package.json').read_text())['version'],
              'source_sha256': {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
                  for name in ('watcher.py', 'revive.py', 'immortal/core/ready.py',
                               'immortal/core/provider_endpoint.py', 'immortal/hosts/bb.py')}}
    process = None

    def cli(*args):
        result = subprocess.run([NODE, str(APP / 'dist/bb.js'), *args, '--json'],
                                env=env, cwd=run / 'workspace', capture_output=True, text=True, timeout=45)
        if result.returncode:
            raise RuntimeError(f'BB {args[:2]} failed: {result.stderr[-1000:]}')
        return json.loads(result.stdout)

    def events():
        return cli('thread', 'log', report['thread_id'], '--all')

    try:
        with (run / 'launcher.log').open('w') as log:
            process = subprocess.Popen([NODE, str(APP / 'dist/bb-app.js'), '--data-dir', str(run / 'data'),
                '--server-bind-host', '127.0.0.1', '--server-port', str(ports[0]),
                '--host-daemon-port', str(ports[1])], env=env, cwd=run / 'workspace',
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        print(json.dumps({'run_dir': str(run), 'phase': 'starting_isolated_bb'}), flush=True)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                status = cli('status')
                if status.get('dataDir') == str(run / 'data'):
                    break
            except (RuntimeError, ValueError):
                pass
            if process.poll() is not None:
                raise RuntimeError('Isolated BB exited during startup')
            time.sleep(.5)
        else:
            raise RuntimeError('Isolated BB startup timed out')
        assert not cli('keep-awake', 'status').get('enabled'), 'Unexpected keep-awake enabled'
        project = cli('project', 'create', '--name', 'Provider recovery E2E', '--root', str(run / 'workspace'))
        cli('provider', 'models', 'codex')
        thread = cli('thread', 'spawn', '--project', project['id'], '--provider', 'codex',
                     '--model', 'gpt-6-astra', '--reasoning-level', 'medium', '--permission-mode', 'accept-edits',
                     '--title', 'Disposable provider recovery', '--prompt', 'Reply RECOVERY_OK. Do not call tools.')
        report['thread_id'] = thread.get('id') or thread['thread']['id']
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            shown = cli('thread', 'show', report['thread_id'])
            if shown['thread']['status'] == 'error':
                break
            time.sleep(1)
        else:
            raise RuntimeError('Codex never reached final error')
        failed_events = events()
        (run / 'failed-events.json').write_text(json.dumps(failed_events, indent=2))
        final_error = next(ev for ev in reversed(failed_events)
                           if ev['type'] == 'provider/error' and not ev['data'].get('willRetry'))
        report['final_error'] = final_error['data']
        report['failed_at_ms'] = final_error['createdAt']
        print(json.dumps({'phase': 'final_error_observed', 'error': report['final_error']}), flush=True)
        # Preserve the real 120-second eligibility and 30-second provider scan.
        # The simulation flag affects only this watcher's disposable state.
        flag = {'offline': True, 'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()}
        (run / 'watcher/simulated_outage.json').write_text(json.dumps(flag))
        (run / 'watcher/bb_runtime.json').write_text(json.dumps({'node': NODE, 'bb': str(APP / 'dist/bb.js')}))
        sys.path.insert(0, str(REPO))
        with patch.dict(os.environ, env, clear=True):
            import watcher
            from immortal.core import bb_runtime, provider_endpoint, ready
            from immortal.hosts import bb
            original_resolver = provider_endpoint.recovery_endpoint
            def resolve(provider, history, **kwargs):
                return original_resolver(provider, history, codex_home=run / 'codex-home')
            with patch.object(bb_runtime, 'watcher_env', return_value=env), \
                    patch.object(provider_endpoint, 'recovery_endpoint', side_effect=resolve):
                target = next(t for t in bb.list_targets() if t['id'] == report['thread_id'])
                report['resolved_endpoint'] = bb.recovery_endpoint(target)
                assert report['resolved_endpoint'] == endpoint + '/responses', report['resolved_endpoint']
                deadline = time.monotonic() + 330
                restored = False
                while time.monotonic() < deadline:
                    watcher.loop()
                    state = json.loads((run / 'watcher/state.json').read_text())
                    assert state['online'] is False
                    history = events()
                    retries = [ev for ev in history if ev['type'] == 'client/turn/requested'
                               and ev['data'].get('retryAttempt', 1) > 1]
                    if not restored:
                        assert not retries, 'Watcher retried before provider returned'
                        logs = [json.loads(line) for line in (run / 'watcher/watcher.log').read_text().splitlines()]
                        if any(row.get('event') == 'endpoint_probe' and row.get('status') == 'unreachable' for row in logs):
                            assert not any(entry.get('tries', 0) for entry in state.get('revived', {}).values())
                            peer.restored, restored = True, True
                            report['restored_at'] = time.time()
                            print(json.dumps({'phase': 'provider_restored_apple_still_offline'}), flush=True)
                    output = [ev for ev in history if ev['type'] == 'item/completed'
                              and ev['data'].get('item', {}).get('type') == 'agentMessage'
                              and ev['data']['item'].get('text') == 'RECOVERY_OK']
                    logs = [json.loads(line) for line in (run / 'watcher/watcher.log').read_text().splitlines()]
                    confirmed = [row for row in logs if row['event'] == 'revive_confirmed']
                    if output and confirmed:
                        assert len(retries) == len(output) == len(confirmed) == 1
                        accepted = [ev for ev in history if ev['type'] == 'turn/input/accepted'
                                    and ev['data'].get('clientRequestId') == retries[0]['data']['requestId']]
                        assert len(accepted) == 1, 'Retry input was not accepted exactly once'
                        assert retries[0]['createdAt'] - report['failed_at_ms'] >= 120000
                        report.update(retry_count=len(retries), accepted_count=len(accepted), output_count=len(output),
                                      confirmed_count=len(confirmed), apple_online=state['online'],
                                      retry_delay_secs=(retries[0]['createdAt'] - report['failed_at_ms']) / 1000)
                        (run / 'recovered-events.json').write_text(json.dumps(history, indent=2))
                        break
                    time.sleep(5)
                else:
                    raise RuntimeError('Watcher failed to confirm one recovery within bounded deadline')
                ready.close_endpoints()
        cli('thread', 'stop', report['thread_id'])
        report['success'] = True
    except (Exception, KeyboardInterrupt) as exc:
        report.update(success=False, error=f'{type(exc).__name__}: {exc}')
    finally:
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        peer.shutdown()
        peer.server_close()
        report['fixture_events'] = peer.events
        report['binary_unchanged'] = digest == hashlib.sha256(codex.read_bytes()).hexdigest()
        report['ports_closed'] = []
        for port in [*ports, peer.server_port]:
            with socket.socket() as sock:
                sock.settimeout(.25)
                report['ports_closed'].append(sock.connect_ex(('127.0.0.1', port)) != 0)
        report['success'] = report.get('success', False) and report['binary_unchanged'] and all(report['ports_closed'])
        (run / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report), flush=True)
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
