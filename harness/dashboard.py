"""Loopback-only, read-only presentation of existing task and Vault records."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import stat
import threading
from urllib.parse import unquote, urlsplit

from .status import build_status

FINISHED = {'complete', 'verified', 'published', 'merged', 'usable'}
MAX_EVIDENCE_BYTES = 1_000_000


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo and parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _latest(values):
    valid = [value for value in values if _time(value) is not None]
    return max(valid, key=_time) if valid else None


def _label(title, task_id, labels):
    entry = labels.get(task_id, {})
    if (isinstance(entry, dict) and entry.get('source_title') == title
            and isinstance(entry.get('title'), str) and entry['title'].strip()
            and not re.search(r'[\u3040-\u30ff\u3400-\u9fff]', entry['title'])):
        return entry['title']
    if re.search(r'[\u3040-\u30ff\u3400-\u9fff]', title):
        return 'Earlier task ' + str(task_id).removeprefix('task_')[:8]
    return title


class Dashboard:
    def __init__(self, *, agents_root=None, vault_root=None, ui_dir=None, labels=None):
        roots = []
        for value, name in ((agents_root, 'AGENTS_ROOT'), (vault_root, 'AGENTS_VAULT_ROOT')):
            value = value or os.environ.get(name)
            if not value or not Path(value).is_dir():
                raise RuntimeError(f'{name} must point to an existing directory')
            roots.append(Path(value).resolve())
        self.agents_root, self.vault_root = roots
        self.ui_dir = (Path(ui_dir) if ui_dir else
                       Path(__file__).resolve().parents[1] / 'apps/progress-dashboard/dist/client').resolve()
        self.labels = json.loads(Path(labels).read_text()) if labels else {}
        if not isinstance(self.labels, dict):
            raise ValueError('Display labels must be a JSON object')
        self.token = secrets.token_urlsafe(32)
        self._lock = threading.RLock()
        self._snapshot = None
        self._evidence = {}

    def _safe_vault(self, raw):
        try:
            if raw.startswith('vault://'):
                relative = unquote(raw[8:])
                if Path(relative).is_absolute() or '..' in Path(relative).parts:
                    return None
                candidate = self.vault_root / relative
            elif Path(raw).is_absolute():
                candidate = Path(raw)
                if '..' in candidate.parts:
                    return None
            else:
                return None
            candidate = candidate.resolve()
            candidate.relative_to(self.vault_root)
            return candidate
        except (ValueError, OSError, RuntimeError):
            return None

    def _evidence_item(self, raw, registry):
        if not isinstance(raw, str):
            return None
        eid = hashlib.sha256(raw.encode()).hexdigest()[:24]
        item = {'id': eid, 'label': 'Source reference', 'availability': 'unsupported', 'kind': 'file'}
        try:
            parsed = urlsplit(raw)
            if (parsed.scheme == 'https' and parsed.hostname and not parsed.username
                    and not parsed.password and not any(c.isspace() for c in raw)):
                item.update(kind='link', url=raw, availability='available', label=parsed.hostname)
                return item
        except ValueError:
            return item
        if not (raw.startswith('vault://') or raw.startswith('/')):
            return item
        path = self._safe_vault(raw)
        if path is None:
            return item
        item['label'] = _label(path.name, eid, {})
        try:
            item['availability'] = 'available' if path.is_file() else 'missing'
            if item['availability'] == 'available':
                registry[eid] = path
                item['last_modified_at'] = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            item['availability'] = 'missing'
        return item

    def refresh(self):
        with self._lock:
            try:
                report = build_status(agents_root=self.agents_root,
                                      service_db_path=self.agents_root / '.local/service.sqlite3')
                if report.get('sources', {}).get('tasks', {}).get('state') != 'available':
                    raise RuntimeError('Task records unavailable')
                snapshot, registry = self._project(report)
                self._snapshot, self._evidence = snapshot, registry
            except Exception:
                # The source error may contain private paths. Report only the state.
                if self._snapshot is None:
                    return {'generated_at': None, 'tasks': [], 'sources': [
                        {'name': 'tasks', 'state': 'unavailable', 'last_record_at': None}],
                        'warnings': ['Task records are unavailable; current work and decisions are unknown.'],
                        'stale': True}
                previous = json.loads(json.dumps(self._snapshot))
                previous['stale'] = True
                previous['warnings'] = list(dict.fromkeys(previous['warnings'] + [
                    'Refresh failed; showing the last valid snapshot.']))
                return previous
            return json.loads(json.dumps(self._snapshot))

    def _project(self, report):
        now = datetime.now(timezone.utc)
        tasks, registry, seen = [], {}, set()
        ordered = sorted(report.get('tasks', []), key=lambda t: (
            _time(t.get('updated_at')) or datetime.min.replace(tzinfo=timezone.utc),
            str(t.get('task_id', ''))), reverse=True)
        for source in ordered:
            task_id = source.get('task_id')
            if not task_id or task_id in seen:
                continue
            seen.add(task_id)
            title = str(source.get('purpose') or 'Earlier task')
            when = _time(source.get('updated_at'))
            freshness = ('unknown' if when is None else 'future' if when > now else
                         'fresh' if now - when <= timedelta(hours=24) else 'old')
            lifecycle = source.get('lifecycle') or 'unknown'
            category = ('attention' if lifecycle in {'awaiting_user', 'auth_failed'} else
                        'recent' if lifecycle in FINISHED else
                        'closed' if lifecycle in {'cancelled', 'canceled'} else
                        'active' if lifecycle == 'running' and freshness == 'fresh' else 'backlog')
            links = list(source.get('evidence_links', [])) + list(source.get('completion_evidence', []))
            links += [item.get('url') for item in source.get('issues', []) + source.get('prs', [])]
            evidence = [self._evidence_item(link, registry) for link in dict.fromkeys(
                link for link in links if isinstance(link, str))]
            status_label = {
                'running': 'In progress', 'awaiting_user': 'Decision needed',
                'auth_failed': 'Sign-in needed', 'quality_failed': 'Checks need attention',
                'awaiting_verification': 'Verification pending', 'needs_verification': 'Verification pending',
                'planned': 'Planned', 'cancelled': 'Cancelled', 'merged_unsynced': 'Sync pending',
            }.get(lifecycle, lifecycle.replace('_', ' ').capitalize())
            tasks.append({'id': task_id, 'title': _label(title, task_id, self.labels),
                          'original_title': title, 'lifecycle': lifecycle, 'status_label': status_label,
                          'next_action': (None if lifecycle in FINISHED or category == 'closed' else
                                          'Wait for the next recorded update.' if lifecycle == 'running' else
                                          source.get('next_action')),
                          'updated_at': source.get('updated_at'), 'freshness': freshness,
                          'category': category, 'evidence': evidence})
        sources = []
        warnings = []
        for name in ('tasks', 'service'):
            state = report.get('sources', {}).get(name, {}).get('state', 'unknown')
            values = ([task.get('updated_at') for task in report.get('tasks', [])] if name == 'tasks' else
                      [job.get('updated_at') for job in report.get('service', {}).get('jobs', [])])
            sources.append({'name': name, 'state': state, 'last_record_at': _latest(values)})
            if state != 'available':
                warnings.append(f'{name.capitalize()} records are unavailable; their current state is unknown.')
        vault_state = 'available' if self.vault_root.is_dir() else 'missing'
        sources.append({'name': 'vault', 'state': vault_state, 'last_record_at': _latest([
            item.get('last_modified_at') for task in tasks for item in task['evidence']])})
        if vault_state != 'available':
            warnings.append('Vault evidence is unavailable.')
        return {'generated_at': report.get('generated_at'), 'tasks': tasks, 'sources': sources,
                'warnings': warnings, 'stale': False}, registry

    def evidence(self, eid):
        with self._lock:
            original = self._evidence.get(eid)
            if original is None:
                return None
            path = self._safe_vault(str(original))
            if path is None:
                return None
            try:
                # Refuse special files and replaced final symlinks; read a bounded amount.
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_EVIDENCE_BYTES:
                        return None
                    data = stream.read(MAX_EVIDENCE_BYTES + 1)
                if len(data) > MAX_EVIDENCE_BYTES or b'\x00' in data:
                    return None
                return data.decode('utf-8')
            except (OSError, UnicodeError):
                return None


class Handler(BaseHTTPRequestHandler):
    server_version = 'ProgressDashboard/1'

    def log_message(self, *_):
        pass

    def _host_allowed(self):
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if self.server.server_port == 80:
            allowed.update({'127.0.0.1', 'localhost'})
        hosts = self.headers.get_all('Host', [])
        if len(hosts) != 1 or hosts[0] not in allowed:
            return False
        origin = self.headers.get('Origin')
        return origin is None or origin == 'http://' + hosts[0]

    def _reply(self, code, body, content_type='text/plain; charset=utf-8', *, head=False):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_GET(self, head=False):
        if not self._host_allowed():
            return self._reply(403, b'Access denied.', head=head)
        try:
            path = unquote(urlsplit(self.path).path)
            if path == '/favicon.ico':
                return self._reply(204, b'', 'image/x-icon', head=head)
            if path.startswith('/api/'):
                if not secrets.compare_digest(self.headers.get('X-Dashboard-Token', ''), self.server.dashboard.token):
                    return self._reply(403, b'Access denied.', head=head)
                if path == '/api/status':
                    body = json.dumps(self.server.dashboard.refresh(), ensure_ascii=False).encode()
                    return self._reply(200, body, 'application/json; charset=utf-8', head=head)
                if path.startswith('/api/evidence/'):
                    value = self.server.dashboard.evidence(path.removeprefix('/api/evidence/'))
                    if value is not None:
                        return self._reply(200, value.encode(), head=head)
                return self._reply(404, b'Source unavailable or unsupported.', head=head)
            relative = 'index.html' if path == '/' else path.lstrip('/')
            candidate = (self.server.dashboard.ui_dir / relative).resolve()
            if not candidate.is_relative_to(self.server.dashboard.ui_dir) or not candidate.is_file():
                return self._reply(404, b'Not found.', head=head)
            body = candidate.read_bytes()
            if candidate.name == 'index.html':
                body = body.replace(b'__DASHBOARD_TOKEN__', self.server.dashboard.token.encode())
            content_type = mimetypes.guess_type(str(candidate))[0] or 'application/octet-stream'
            return self._reply(200, body, content_type, head=head)
        except (OSError, ValueError, RuntimeError):
            return self._reply(503, b'Dashboard source unavailable.', head=head)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--ui-dir')
    parser.add_argument('--labels')
    args = parser.parse_args(argv)
    try:
        dashboard = Dashboard(ui_dir=args.ui_dir, labels=args.labels)
        if not (dashboard.ui_dir / 'index.html').is_file():
            parser.error('Build the dashboard UI before starting the server.')
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except (OSError, ValueError, RuntimeError):
        parser.error('Dashboard configuration is unavailable or the local port is already in use.')
    server.daemon_threads = True
    server.dashboard = dashboard
    print(f'Dashboard listening on http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
