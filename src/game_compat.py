"""Tie a generated Pack to the game data it was built from.

The Pack is a Movie pack: the game always loads it and the launcher cannot turn
it off, so stale tables have to be caught here, before the game runs on them.
"""
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re

from pack_io import db_header, read_pack

MANIFEST = 'wh3pt_build.json'


def steam_build(game):
    """Steam build id of this install, or None outside a normal Steam library."""
    manifest = Path(game).parent.parent / 'appmanifest_1142710.acf'
    try:
        match = re.search(r'"buildid"\s+"(\d+)"', manifest.read_text(encoding='utf-8', errors='replace'))
    except OSError:
        return None
    return match[1] if match else None


def game_version(game):
    """Warhammer3.exe version such as 9.0.4, or None where it cannot be read."""
    path = str(Path(game) / 'Warhammer3.exe')
    try:
        import ctypes
        api = ctypes.windll.version
        size = api.GetFileVersionInfoSizeW(path, None)
        data = ctypes.create_string_buffer(size)
        info, length = ctypes.c_void_p(), ctypes.c_uint()
        if not size or not api.GetFileVersionInfoW(path, 0, size, data) \
                or not api.VerQueryValueW(data, '\\', ctypes.byref(info), ctypes.byref(length)):
            return None
        # VS_FIXEDFILEINFO: signature, struct version, file version MS, file version LS.
        high, low = (ctypes.c_uint32 * 4).from_address(info.value)[2:4]
        return f'{high >> 16}.{high & 0xffff}.{low >> 16}'
    except (AttributeError, OSError):
        return None


def table_fingerprints(game, tables):
    """Version and content digest of native tables, across all their fragments."""
    wanted = set(tables)
    entries = read_pack(Path(game) / 'data/db.pack', lambda n: n.startswith('db\\') and n.split('\\')[1] in wanted)
    result = {}
    for name in sorted(entries):
        table = name.split('\\')[1]
        record = result.setdefault(table, {'version': db_header(entries[name])[0], 'digest': hashlib.sha256()})
        record['digest'].update(name.encode('utf-8') + b'\0' + entries[name])
    return {t: {'version': r['version'], 'sha256': r['digest'].hexdigest()} for t, r in result.items()}


def database_digest(game):
    return hashlib.sha256((Path(game) / 'data/db.pack').read_bytes()).hexdigest()


def make_manifest(game, tables, version):
    """What this Pack was generated from, stored inside the Pack."""
    return json.dumps({
        'trainer_version': version,
        'game_version': game_version(game),
        'steam_build': steam_build(game),
        'database_sha256': database_digest(game),
        'tables': table_fingerprints(game, tables),
    }, ensure_ascii=False, indent=2, sort_keys=True).encode('utf-8')


def read_manifest(pack):
    data = read_pack(pack, lambda n: n == MANIFEST).get(MANIFEST)
    if data is None:
        return None
    try:
        manifest = json.loads(data)
    except ValueError:
        return None
    return manifest if isinstance(manifest, dict) and 'tables' in manifest else None


@dataclass
class Compatibility:
    state: str  # current, compatible (game patched, our tables intact), stale or unknown
    manifest: dict = field(default_factory=dict)
    changes: list = field(default_factory=list)

    @property
    def usable(self):
        return self.state in ('current', 'compatible')

    def built_for(self):
        version = self.manifest.get('game_version') or '未知版本'
        build = self.manifest.get('steam_build')
        return f'{version}（Steam 构建 {build}）' if build else version

    def describe(self):
        if self.state == 'unknown':
            return '这个修改器包没有适配记录，无法确认它与当前游戏匹配。'
        if self.usable:
            return f'修改器包按游戏 {self.built_for()} 生成，依赖的数据与当前游戏一致。'
        shown = '、'.join(self.changes[:4]) + (f' 等 {len(self.changes)} 处' if len(self.changes) > 4 else '')
        return f'修改器包按游戏 {self.built_for()} 生成，当前游戏数据已变化：{shown}。'


def check(game, pack):
    """Compare a Pack's manifest with the game as it is installed right now."""
    manifest = read_manifest(pack)
    if manifest is None:
        return Compatibility('unknown')
    if manifest.get('database_sha256') == database_digest(game):
        return Compatibility('current', manifest)
    live = table_fingerprints(game, manifest['tables'])
    changes = []
    for table, built in sorted(manifest['tables'].items()):
        now = live.get(table)
        if now is None:
            changes.append(f'{table} 已不存在')
        elif now['version'] != built['version']:
            changes.append(f"{table} v{built['version']} → v{now['version']}")
        elif now['sha256'] != built['sha256']:
            changes.append(f'{table} 内容变化')
    return Compatibility('stale' if changes else 'compatible', manifest, changes)
