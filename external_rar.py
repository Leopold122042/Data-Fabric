"""Bounded RAR/ZIP inspection in an isolated subprocess; never writes archive paths."""
import base64
import hashlib
import json
import io
import stat
import zipfile
from pathlib import Path, PurePosixPath
import subprocess
import sys

MAX_FILE = 20 * 1024 * 1024
MAX_TOTAL = 80 * 1024 * 1024
MAX_ENTRIES = 1000


def inspect_rar(payload, member=None, archive_format='rar'):
    from external_sources import SourceError
    if len(payload) > MAX_FILE:
        raise SourceError('压缩包 文件超过 20 MB。')
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), member or '', archive_format],
                                input=payload, capture_output=True, timeout=30, check=False)
        if result.returncode:
            raise SourceError('压缩包 读取失败，可能已损坏、加密、分卷不完整或当前解码器不支持。')
        data = json.loads(result.stdout)
        if 'error' in data: raise SourceError(data['error'])
        if 'content' in data: data['content'] = base64.b64decode(data['content'])
        data['digest'] = hashlib.sha256(payload).hexdigest()
        return data
    except subprocess.TimeoutExpired:
        raise SourceError('压缩包 处理超过 30 秒，已停止。') from None
    except SourceError:
        raise
    except (OSError, ValueError):
        raise SourceError('压缩包 解码环境不可用，请安装 requirements-external.txt 依赖及系统 libarchive。') from None


def scan(payload, member=None):
    import libarchive
    from external_sources import SUPPORTED_FORMATS
    if not (payload.startswith(b'Rar!\x1a\x07\x00') or payload.startswith(b'Rar!\x1a\x07\x01\x00')):
        raise ValueError('文件不是有效的 RAR 压缩包。')
    items, seen, total, content = [], set(), 0, None
    with libarchive.memory_reader(payload, format_name='rar' if payload[6] == 0 else 'rar5') as archive:
        for entry in archive:
            name = entry.pathname
            if not isinstance(name,str) or len(name)>2000:
                raise ValueError('RAR 文件名过长或编码不受支持。')
            parts = name.rstrip('/').split('/')
            if name.startswith('/') or any(p in ('','.', '..') for p in parts) or '\\' in name or ':' in name or any(ord(c)<32 for c in name):
                raise ValueError('RAR 包含不安全的文件路径，已拒绝读取。')
            if name in seen: raise ValueError('RAR 包含重复文件名，无法唯一选择。')
            seen.add(name)
            if len(seen)>MAX_ENTRIES: raise ValueError('RAR 超过 1000 个条目。')
            if entry.issym or entry.islnk or not (entry.isfile or entry.isdir):
                raise ValueError('RAR 包含链接或特殊文件，已拒绝读取。')
            size = entry.size
            if size is None or size<0: raise ValueError('RAR 文件大小无效。')
            total += size
            if total>MAX_TOTAL: raise ValueError('RAR 声明的解压总大小超过 80 MB。')
            supported = entry.isfile and PurePosixPath(name).suffix.lower().lstrip('.') in SUPPORTED_FORMATS and size<=MAX_FILE
            items.append({'name':name,'size':size,'directory':bool(entry.isdir),'supported':bool(supported)})
            if member == name:
                if not supported: raise ValueError('所选条目不支持解析或超过 20 MB；不递归解压嵌套压缩包。')
                chunks, actual = [], 0
                for block in entry.get_blocks():
                    actual += len(block)
                    if actual>MAX_FILE: raise ValueError('选中文件解压后超过 20 MB。')
                    chunks.append(block)
                content = b''.join(chunks)
    if member is not None and content is None: raise ValueError('压缩包中找不到所选文件。')
    result = {'items':items,'total_size':total}
    if content is not None: result['content'] = base64.b64encode(content).decode('ascii')
    return result


def inspect_zip(payload, member=None):
    return inspect_rar(payload, member, 'zip')


def scan_zip(payload, member=None):
    from external_sources import SUPPORTED_FORMATS
    items, seen, total, content = [], set(), 0, None
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries = archive.infolist()
        if len(entries)>MAX_ENTRIES: raise ValueError('ZIP 超过 1000 个条目。')
        for entry in entries:
            name = entry.filename
            parts = name.rstrip('/').split('/')
            if (len(name)>2000 or name != entry.orig_filename or name.startswith('/') or
                any(p in ('', '.', '..') for p in parts) or '\\' in name or ':' in name or
                any(ord(c)<32 for c in name)):
                raise ValueError('ZIP 包含不安全的文件路径，已拒绝读取。')
            if name in seen: raise ValueError('ZIP 包含重复文件名，无法唯一选择。')
            seen.add(name)
            kind = stat.S_IFMT(entry.external_attr >> 16)
            if kind not in (0,stat.S_IFREG,stat.S_IFDIR):
                raise ValueError('ZIP 包含链接或特殊文件，已拒绝读取。')
            if entry.flag_bits & 1: raise ValueError('暂不支持加密 ZIP，请使用未加密的压缩包。')
            total += entry.file_size
            if total>MAX_TOTAL: raise ValueError('ZIP 声明的解压总大小超过 80 MB。')
            supported = (not entry.is_dir() and entry.file_size<=MAX_FILE and
                         PurePosixPath(name).suffix.lower().lstrip('.') in SUPPORTED_FORMATS)
            items.append({'name':name,'size':entry.file_size,'directory':entry.is_dir(),'supported':supported})
        if member is not None:
            selected = next((e for e in items if e['name']==member),None)
            if selected is None: raise ValueError('压缩包中找不到所选文件。')
            if not selected['supported']: raise ValueError('所选条目不支持解析或超过 20 MB；不递归解压嵌套压缩包。')
            with archive.open(member) as stream:
                content = stream.read(MAX_FILE+1)
                if len(content)>MAX_FILE: raise ValueError('选中文件解压后超过 20 MB。')
    result = {'items':items,'total_size':total}
    if content is not None: result['content'] = base64.b64encode(content).decode('ascii')
    return result


if __name__ == '__main__':
    try:
        payload = sys.stdin.buffer.read(MAX_FILE+1)
        if len(payload)>MAX_FILE: raise ValueError('RAR 文件超过 20 MB。')
        decoder = scan_zip if len(sys.argv)>2 and sys.argv[2]=='zip' else scan
        result = decoder(payload, sys.argv[1] or None)
    except ValueError as exc:
        result = {'error':str(exc)}
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError):
        result = {'error':'ZIP 已损坏、加密或压缩方式不受支持。'}
    except ImportError:
        result = {'error':'请安装 libarchive-c 和系统 libarchive 后重试。'}
    except Exception:
        result = {'error':'压缩包读取失败：加密包、缺失分卷、损坏文件或不支持的压缩方式。'}
    sys.stdout.write(json.dumps(result,ensure_ascii=True))
