import json
from pathlib import Path
import struct
from pack_io import db_header, read_pack

SCHEMA = json.loads(Path(__file__).with_name('schema.json').read_text(encoding='utf-8'))
FORMATS = {'F32': '<f', 'F64': '<d', 'I32': '<i', 'I64': '<q', 'I16': '<h', 'Boolean': '<?'}


def fields_of(table, version):
    try:
        return SCHEMA[table][str(version)]
    except KeyError:
        raise ValueError(f'schema.json 缺少 {table} v{version}：游戏表结构已更新，'
                         '请运行 tools/extract_schema.py --download 后重新构建') from None


def table_version(game, table):
    """Version of a native table, so generated rows follow the installed game."""
    entries = read_pack(Path(game) / 'data/db.pack', lambda n: n.startswith('db\\' + table + '\\'))
    versions = {db_header(data)[0] for data in entries.values()}
    if len(versions) != 1:
        raise ValueError('无法确定游戏表版本：' + table)
    return versions.pop()


def decode(table, data):
    version, count, pos = db_header(data)
    fields = fields_of(table, version)
    rows = []
    def read(kind):
        nonlocal pos
        if kind.startswith('Optional'):
            present = data[pos]; pos += 1
            if present not in (0, 1):
                raise ValueError('非法可选字段')
            return read(kind[8:]) if present else None
        if kind in ('StringU8', 'StringU16'):
            length = struct.unpack_from('<H', data, pos)[0]; pos += 2
            size = length * (2 if kind == 'StringU16' else 1)
            value = data[pos:pos+size].decode('utf-16-le' if kind == 'StringU16' else 'utf-8')
            pos += size
            return value
        fmt = FORMATS[kind]
        value = struct.unpack_from(fmt, data, pos)[0]; pos += struct.calcsize(fmt)
        return value
    for _ in range(count):
        rows.append({f['name']: read(f['type']) for f in fields})
    if pos != len(data):
        raise ValueError(f'{table} 字段长度不匹配：{pos}/{len(data)}')
    return version, rows


def default_value(field):
    kind, value = field['type'], field['default']
    if kind.startswith('Optional'):
        return None
    if kind.startswith('String'):
        return value or ''
    if kind == 'Boolean':
        return value == 'true'
    return float(value or 0) if kind.startswith('F') else int(value or 0)


def encode(table, version, rows):
    fields = fields_of(table, version)
    def write(kind, value):
        if kind.startswith('Optional'):
            return b'\0' if value is None else b'\1' + write(kind[8:], value)
        if kind in ('StringU8', 'StringU16'):
            raw = value.encode('utf-16-le' if kind == 'StringU16' else 'utf-8')
            return struct.pack('<H', len(raw) // (2 if kind == 'StringU16' else 1)) + raw
        return struct.pack(FORMATS[kind], value)
    header = (b'\xfc\xfd\xfe\xff' + struct.pack('<I', version)) if version else b''
    return header + b'\1' + struct.pack('<I', len(rows)) + b''.join(
        write(f['type'], row.get(f['name'], default_value(f))) for row in rows for f in fields)
