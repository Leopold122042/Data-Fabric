"""Check Doris VPN routes and connectivity without attempting to log in to the VPN."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
from tempfile import NamedTemporaryFile

from management.catalog.doris import DorisSources
from platform_core.storage import DomainError


def route_interface(host):
    try:
        result = subprocess.run(['ip', '-j', 'route', 'get', host], capture_output=True,
                                check=True, text=True, timeout=3)
        routes = json.loads(result.stdout)
        return routes[0].get('dev') if routes else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def tcp_reachable(host, port):
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def inspect_configured(source):
    result = {'source_id': source.id, 'name': source.name, 'transport': source.transport}
    if source.transport == 'vpn':
        result['interface'] = route_interface(source.host)
        try:
            source.check_vpn_route()
        except DomainError:
            return {**result, 'status': 'vpn_route_unavailable'}
    if not tcp_reachable(source.host, source.port):
        return {**result, 'status': 'database_tcp_unreachable'}
    try:
        with source.cursor() as cursor:
            cursor.execute('SELECT 1')
            if cursor.fetchone() != (1,):
                return {**result, 'status': 'database_check_failed'}
    except DomainError as exc:
        return {**result, 'status': 'database_check_failed', 'detail': str(exc)}
    return {**result, 'status': 'database_authenticated'}


def write_status(path, state):
    try:
        previous = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    if (previous.get('sources'), previous.get('config_error')) != (state['sources'], state['config_error']):
        print(json.dumps(state, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description='检查已配置的 Doris VPN 数据源')
    parser.add_argument('--bootstrap-host', type=ipaddress.ip_address)
    parser.add_argument('--bootstrap-port', type=int, default=9030)
    parser.add_argument('--bootstrap-source', default='doris-nju')
    parser.add_argument('--state-file', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.bootstrap_port <= 65535:
        parser.error('--bootstrap-port 超出端口范围')

    # The checker only loads profiles. It does not read or write the catalog.
    registry = DorisSources(store=None)
    with ThreadPoolExecutor(max_workers=8) as pool:
        sources = list(pool.map(inspect_configured, registry.sources.values()))
    if args.bootstrap_host is not None and args.bootstrap_source not in registry.sources:
        host = str(args.bootstrap_host)
        tcp = tcp_reachable(host, args.bootstrap_port)
        sources.append({'source_id': args.bootstrap_source, 'interface': route_interface(host),
                        'status': 'database_profile_missing' if tcp else 'database_tcp_unreachable',
                        'database_profile_configured': False})

    write_status(args.state_file, {'checked_at': datetime.now(timezone.utc).isoformat(),
                                  'sources': sources, 'config_error': registry.config_error})


if __name__ == '__main__':
    main()
