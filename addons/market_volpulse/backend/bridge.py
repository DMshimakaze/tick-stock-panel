"""Prepare the official management-plane resources and store the local runtime token."""
import getpass
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .publisher import prepare_tables

BRIDGE_FILE = 'bridge.json'


def local_url(value):
    url = urlsplit(value)
    if (url.scheme not in ('http', 'https') or url.hostname not in ('127.0.0.1', '::1', 'localhost')
            or url.username or url.path not in ('', '/') or url.query or url.fragment):
        raise ValueError('TSP API 地址必须为本机回环地址')
    return value.rstrip('/')


def configure(admin, data_dir, url):
    """Use only the authenticated panel session to provision a dedicated token."""
    url = local_url(url)
    prepare_tables(admin)
    folder = Path(data_dir) / 'user_data' / 'market_volpulse'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / BRIDGE_FILE
    if target.exists():
        raise ValueError('本机发布凭据已配置；更换前请从面板吊销对应旧 Token')
    response = admin.post('/api/settings/api-tokens',
                          json={'name': 'Market VolPulse', 'scopes': ['read:ext', 'write:ext']})
    response.raise_for_status()
    result = response.json()
    try:
        # The permission is set before writing any secret, including on a retry.
        with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w',
                       encoding='utf-8') as file:
            json.dump({'api_base': url, 'api_token': result['plaintext']}, file)
    except OSError:
        admin.delete('/api/settings/api-tokens/' + result['token']['id']).raise_for_status()
        raise


def main():
    from argparse import ArgumentParser

    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--url', required=True)
    parser.add_argument('--session', action='store_true', help='Prompt for the active tf_session cookie')
    args = parser.parse_args()
    url = local_url(args.url)
    cookies = {'tf_session': getpass.getpass('TSP tf_session: ')} if args.session else None
    with httpx.Client(base_url=url, cookies=cookies, trust_env=False, timeout=10) as admin:
        configure(admin, args.data_dir, url)
    print('VolPulse 扩展表及专用发布 Token 已准备。')


if __name__ == '__main__':
    main()
