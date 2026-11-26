"""Data API client for Supabase opaque API keys; backend only."""
import os
import re
import threading
import time
import logging

log = logging.getLogger(__name__)
from dataclasses import dataclass
from urllib.parse import urlsplit, quote

import requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv('SUPABASE_URL', '').strip().rstrip('/').removesuffix('/rest/v1')
SUPABASE_SECRET_KEY = os.getenv('SUPABASE_SECRET_KEY', '').strip()
SUPABASE_PUBLISHABLE_KEY = os.getenv('SUPABASE_PUBLISHABLE_KEY', '').strip()


def configured():
    url = urlsplit(SUPABASE_URL)
    return bool(url.scheme == 'https' and url.hostname and not url.username and not url.password
                and not url.path and not url.query and not url.fragment
                and SUPABASE_SECRET_KEY.startswith('sb_secret_'))


def secret_headers():
    if not configured():
        raise RuntimeError('Configura SUPABASE_URL y SUPABASE_SECRET_KEY (sb_secret_...).')
    return {'apikey': SUPABASE_SECRET_KEY, 'Content-Type': 'application/json',
            'Accept': 'application/json', 'Prefer': 'return=representation'}


def network_error(exc):
    """Classify without exposing request headers, credentials or URLs."""
    chain = [exc]; seen = set(); names = []; errnos = []
    while chain:
        item = chain.pop()
        if id(item) in seen or not isinstance(item, BaseException):
            continue
        seen.add(id(item)); names.append(type(item).__name__)
        number = getattr(item, 'errno', None)
        if isinstance(number, int): errnos.append(number)
        chain.extend(x for x in (item.__cause__, item.__context__, getattr(item, 'reason', None), *item.args) if isinstance(x, BaseException))
    if isinstance(exc, requests.exceptions.SSLError) or any(n in ('SSLError', 'SSLCertVerificationError') for n in names):
        return 'TLS/certificado rechazado', False
    if isinstance(exc, requests.exceptions.ProxyError):
        return 'proxy no disponible', True
    if any(n in ('gaierror', 'NameResolutionError') for n in names):
        return 'DNS: no se pudo resolver el dominio del proyecto', True
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return 'tiempo de espera al conectar', True
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return 'tiempo de espera de respuesta HTTP', True
    if isinstance(exc, requests.exceptions.Timeout):
        return 'tiempo de espera agotado', True
    if isinstance(exc, requests.exceptions.ConnectionError):
        return 'conexión rechazada/interrumpida' + (f' (errno {errnos[0]})' if errnos else ''), True
    return 'fallo de transporte (' + type(exc).__name__ + ')', False


@dataclass
class Result:
    data: list


class RestClient:
    """Small PostgREST adapter implementing the operations used by Nexus."""
    def __init__(self):
        if not configured():
            raise ValueError('Se requiere una URL HTTPS y una clave sb_secret_.')
        self.healthy = None
        self._levels_lock = threading.Lock()

    def table(self, name):
        return Query(self, name)

    def request(self, method, table, params, payload, prefer):
        headers = secret_headers()
        headers['Prefer'] = prefer
        # Only retry reads and idempotent upserts for the APK table. Never
        # replay arbitrary inserts or slow down the legacy synchronous paths.
        retryable = table == 'nexusmusic_updates' and (method == 'GET' or (method == 'POST' and params.get('on_conflict') == 'guild_id' and 'merge-duplicates' in prefer))
        attempts = 3 if retryable else 1
        for attempt in range(1, attempts + 1):
            try:
                response = requests.request(method, f'{SUPABASE_URL}/rest/v1/{quote(table, safe="")}',
                                            headers=headers, params=params, json=payload,
                                            timeout=(5, 15) if retryable else (4, 10), allow_redirects=False)
            except requests.exceptions.RequestException as exc:
                self.healthy = False
                detail, transient = network_error(exc)
                log.warning('Supabase %s %s: %s (intento %s/%s)', method, table, detail, attempt, attempts)
                if transient and attempt < attempts:
                    time.sleep(0.5 * 2 ** (attempt - 1)); continue
                raise RuntimeError(f'Supabase: {detail} ({method} {table}; {attempt} intento(s)).') from None
            if response.status_code in (429, 500, 502, 503, 504) and attempt < attempts:
                log.warning('Supabase %s %s: HTTP %s (intento %s/%s)', method, table, response.status_code, attempt, attempts)
                time.sleep(0.5 * 2 ** (attempt - 1)); continue
            break
        if not 200 <= response.status_code < 300:
            self.healthy = False
            messages = {401: 'clave inválida o revocada', 403: 'permiso denegado',
                        404: 'tabla o ruta no encontrada', 429: 'límite de solicitudes'}
            summary = messages.get(response.status_code, 'consulta rechazada')
            try:
                error = response.json()
            except ValueError:
                error = {}
            if not isinstance(error, dict):
                error = {}
            code = str(error.get('code') or response.headers.get('sb-error-code') or '')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', code):
                code = ''
            # Only report the code for writes (database errors can contain row data).
            if method == 'GET':
                msg = str(error.get('message') or '')
                for key in (SUPABASE_SECRET_KEY, SUPABASE_PUBLISHABLE_KEY):
                    if key:
                        msg = msg.replace(key, '[REDACTADO]')
                msg = re.sub(r'sb_(?:secret|publishable)_[A-Za-z0-9_-]+', '[REDACTADO]', msg)
                msg = re.sub(r'[\x00-\x1f\x7f]', ' ', msg).strip()[:300]
                if msg:
                    summary = msg
            suffix = f' [{code}]' if code else ''
            raise RuntimeError(f'Supabase HTTP {response.status_code}{suffix} ({method} {table}): {summary}')
        self.healthy = True
        if not response.content:
            return Result([])
        data = response.json()
        return Result(data if isinstance(data, list) else [data])

    def probe(self):
        self.table('config').select('guild_id').limit(1).execute()

    def _guild_query(self, table, guild_id, columns):
        query = self.table(table).select(columns).eq('guild_id', str(guild_id))
        # Only levels has an independent numeric primary key in this schema.
        if table == 'levels':
            query.order('id', desc=True)
        return query.limit(1)

    def get_guild_data(self, table, guild_id):
        rows = self._guild_query(table, guild_id, 'data').execute().data
        return rows[0].get('data') if rows else None

    def set_guild_data(self, table, guild_id, data):
        guild_id = str(guild_id)
        payload = {'guild_id': guild_id, 'data': data}
        if table != 'levels':
            # The observed tables use guild_id as their primary key.
            self.table(table).upsert(payload, on_conflict='guild_id').execute()
            return
        # levels has id as its primary key; update the latest row for this guild.
        # Do not delete historical rows or assume guild_id is unique.
        with self._levels_lock:
            rows = self._guild_query(table, guild_id, 'id').execute().data
            if rows:
                result = (self.table(table).update({'data': data})
                          .eq('id', rows[0]['id']).eq('guild_id', guild_id).execute())
                if not result.data:
                    raise RuntimeError('Supabase levels: la fila seleccionada no pudo actualizarse.')
            else:
                self.table(table).insert(payload).execute()


class Query:
    def __init__(self, client, table):
        self.client, self.name = client, table
        self.method, self.params, self.payload = 'GET', {}, None
        self.prefer = 'return=representation'

    def select(self, columns='*'):
        self.params['select'] = columns
        return self

    def eq(self, column, value):
        self.params[column] = f'eq.{value}'
        return self

    def order(self, column, desc=False):
        self.params['order'] = f'{column}.{"desc" if desc else "asc"}'
        return self

    def limit(self, count):
        self.params['limit'] = str(count)
        return self

    def insert(self, payload):
        self.method, self.payload = 'POST', payload
        return self

    def update(self, payload):
        self.method, self.payload = 'PATCH', payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.method, self.payload = 'POST', payload
        self.prefer = 'resolution=merge-duplicates,return=representation'
        if on_conflict:
            self.params['on_conflict'] = on_conflict
        return self

    def execute(self):
        return self.client.request(self.method, self.name, self.params, self.payload, self.prefer)
