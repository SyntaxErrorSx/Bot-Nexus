"""Data API client for Supabase opaque API keys; backend only."""
import os
import re
import threading
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
        try:
            response = requests.request(method, f'{SUPABASE_URL}/rest/v1/{quote(table, safe="")}',
                                        headers=headers, params=params, json=payload,
                                        timeout=(4, 10), allow_redirects=False)
        except requests.exceptions.RequestException as exc:
            self.healthy = False
            if isinstance(exc, requests.exceptions.Timeout):
                raise RuntimeError('Supabase: tiempo de espera agotado.') from None
            raise RuntimeError('Supabase: error de red/DNS/TLS; comprueba URL, DNS y estado del proyecto.') from None
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
