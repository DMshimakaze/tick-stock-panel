"""TSP Ext Data HTTP bridge. The host owns every Parquet read/write."""
import json

from .projection import VIEW_TABLE

SCOPE = 'market.volpulse'
COMMAND_TABLE = 'market_volpulse_command_v1'
RECEIPT_TABLE = 'market_volpulse_receipt_v1'


def table_config(table, label):
    return dict(id=table, label=label, mode='snapshot', market_level=True,
                description='market.volpulse · 独立插件数据交换',
                fields=[dict(name=k, dtype='string', label=k) for k in ('scope', 'payload_json')])


def view_config():
    return table_config(VIEW_TABLE, '大盘量能 · 实时投影')


def prepare(admin_http, configs=None):
    response = admin_http.get('/api/ext-data')
    response.raise_for_status()
    existing = {row['id']: row for row in response.json()['items']}
    for expected in [view_config()] if configs is None else configs:
        current = existing.get(expected['id'])
        if current is None:
            admin_http.post('/api/ext-data', json=expected).raise_for_status()
            continue
        fields = [(f['name'], f['dtype']) for f in current['fields']]
        if (fields != [('scope', 'string'), ('payload_json', 'string')]
                or current['mode'] != 'snapshot' or not current.get('market_level')):
            raise ValueError(f"扩展表 {expected['id']} 的现有结构不兼容")


def prepare_tables(admin_http):
    prepare(admin_http, [
        view_config(),
        table_config(COMMAND_TABLE, '大盘量能 · 页面请求'),
        table_config(RECEIPT_TABLE, '大盘量能 · 处理回执'),
    ])


class Publisher:
    def __init__(self, http):
        self.http = http
        self.last_bytes = 0

    async def put(self, table, payload):
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        row = dict(scope=SCOPE, payload_json=encoded)
        response = await self.http.post(f'/api/ext-data/{table}/ingest', json={'rows': [row]})
        response.raise_for_status()
        if table == VIEW_TABLE:
            self.last_bytes = len(encoded.encode('utf-8'))

    async def command(self):
        response = await self.http.get(f'/api/ext-data/{COMMAND_TABLE}/rows', params={'limit': 1})
        response.raise_for_status()
        row = next(iter(response.json()['rows']), None)
        if row and row.get('scope') == SCOPE:
            payload = json.loads(row['payload_json'])
            if isinstance(payload, dict):
                return payload
        return None

    async def __call__(self, view):
        if view.get('schema_version') != 1:
            raise ValueError('unsupported view version')
        await self.put(VIEW_TABLE, view)
