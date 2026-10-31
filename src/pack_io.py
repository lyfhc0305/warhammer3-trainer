"""Strict PFH5 reader/writer. No third-party game assets are bundled."""
from pathlib import Path
import struct
import time


def read_pack(path, select=lambda name: True):
    result = {}
    with Path(path).open('rb') as f:
        header = f.read(28)
        if len(header) != 28 or header[:4] != b'PFH5':
            raise ValueError(f'不支持的 Pack 格式：{path}')
        flags, deps, dep_size, count, index_size, _ = struct.unpack('<6I', header[4:])
        if flags & ~0x4f or count > 200000 or index_size > 64000000:
            raise ValueError('不支持的 Pack 标记或索引过大')
        if deps or dep_size:
            raise ValueError('当前不支持带强制依赖的 Pack')
        index = f.read(index_size)
        if len(index) != index_size:
            raise ValueError('Pack 索引被截断')
        pos, offset = 0, 28 + index_size
        total = Path(path).stat().st_size
        seen = set()
        for _ in range(count):
            if pos + 5 > len(index):
                raise ValueError('Pack 条目被截断')
            size = struct.unpack_from('<I', index, pos)[0]
            pos += 4
            if flags & 64:
                pos += 4
            compressed = index[pos]
            pos += 1
            end = index.index(0, pos)
            name = index[pos:end].decode('utf-8').replace('/', '\\')
            pos = end + 1
            parts = name.split('\\')
            if any(p in ('', '.', '..') or ':' in p for p in parts) or name.lower() in seen:
                raise ValueError('非法或重复 Pack 路径：' + name)
            seen.add(name.lower())
            if offset + size > total or compressed not in (0, 1):
                raise ValueError('Pack 数据被截断或压缩标记错误')
            if select(name):
                f.seek(offset)
                data = f.read(size)
                if compressed:
                    from compression import zstd
                    expected = struct.unpack_from('<I', data)[0]
                    if expected > 128000000:
                        raise ValueError('解压大小超出限制')
                    data = zstd.decompress(data[4:])
                    if len(data) != expected:
                        raise ValueError('解压长度不匹配')
                result[name] = data
            offset += size
        if pos != len(index) or offset != total:
            raise ValueError('Pack 长度不匹配')
    return result


def write_pack(files, pack_type=4):
    index, payload = bytearray(), bytearray()
    for name, data in sorted(files.items(), key=lambda kv: kv[0].lower()):
        index += struct.pack('<IB', len(data), 0) + name.encode('utf-8') + b'\0'
        payload += data
    return b'PFH5' + struct.pack('<6I', pack_type, 0, 0, len(files), len(index), int(time.time())) + index + payload


def db_header(data):
    pos, version = 0, 0
    if data[:4] == b'\xfd\xfe\xfc\xff':
        length = struct.unpack_from('<H', data, 4)[0]
        pos = 6 + length * 2
    if data[pos:pos+4] == b'\xfc\xfd\xfe\xff':
        version = struct.unpack_from('<I', data, pos + 4)[0]
        pos += 8
    if data[pos:pos+1] != b'\x01':
        raise ValueError('不支持的 DB 表头')
    rows = struct.unpack_from('<I', data, pos + 1)[0]
    return version, rows, pos + 5
