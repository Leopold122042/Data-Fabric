"""Provision one Doris profile in the operator's private settings file."""

import argparse
from getpass import getpass
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from management.catalog.doris import DorisSource
from platform_core.storage import DomainError


def main():
    parser = argparse.ArgumentParser(description='登记平台专用 Doris 只读连接')
    parser.add_argument('id', help='连接标识，如 nju')
    parser.add_argument('--name')
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=9030)
    parser.add_argument('--user', required=True)
    parser.add_argument('--table', action='append', required=True, metavar='DB.TABLE')
    parser.add_argument('--count-service', action='append', default=[], metavar='DB.TABLE')
    parser.add_argument('--vpn-interface', help='仅在数据库使用 VPN 保护传输时填写，如 utun* 或 wg0')
    parser.add_argument('--ca-file', type=Path)
    parser.add_argument('--time-zone', help='数据源字符串以外的时间字段所用时区')
    args = parser.parse_args()
    path = Path(os.environ.get('MEDFABRIC_DORIS_CONFIG',
                str(Path.home() / '.config' / 'medfabric' / 'doris.json'))).expanduser()
    tables = {}
    for reference in args.table:
        database, dot, table = reference.partition('.')
        if not dot or not database or not table:
            parser.error('--table 需要 DB.TABLE')
        tables.setdefault(database, []).append(table)
    if args.vpn_interface and args.ca_file:
        parser.error('启用数据库 TLS 时不需要 --vpn-interface')
    if not os.isatty(0):
        parser.error('请在终端交互输入密码')
    profile = {'id': args.id, 'name': args.name or args.id, 'host': args.host,
               'port': args.port, 'user': args.user, 'password': getpass('Doris 只读账号密码：'),
               'tables': tables, 'count_services': [value.split('.') for value in args.count_service],
               'transport': 'vpn' if args.vpn_interface else 'tls'}
    if args.vpn_interface:
        profile['vpn_interface'] = args.vpn_interface
    if args.ca_file:
        profile['ca_file'] = str(args.ca_file.expanduser().resolve())
    if args.time_zone:
        profile['time_zone'] = args.time_zone
    temporary = None
    try:
        source = DorisSource(profile)
        if path.exists():
            if path.is_symlink() or (os.name == 'posix' and path.stat().st_mode & 0o077):
                parser.error('配置文件必须是本人可读写的普通文件（权限 600）')
            profiles = json.loads(path.read_text(encoding='utf-8'))
        else:
            profiles = []
        if not isinstance(profiles, list) or any(not isinstance(item, dict) for item in profiles):
            parser.error('现有配置格式无效，原文件保留')
        profiles = [item for item in profiles if item.get('id') != args.id] + [profile]
        if len(profiles) > 16:
            parser.error('本实例最多配置 16 个 Doris 连接')
        snapshot = source.snapshot()
        if set(snapshot['tables']) != set(source.tables):
            parser.error('只读账号未返回全部允许表的元数据，请核对表名和表权限')
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(profiles, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        temporary.replace(path)
    except DomainError as exc:
        parser.error(str(exc))
    except (ValueError, KeyError, OSError):
        parser.error('无法保存 Doris 配置，请核对主机、表名、配置目录及权限')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f'元数据连接成功，已登记 {len(snapshot["tables"])} 张允许表。重启后可在数据目录查看。')


if __name__ == '__main__':
    main()
