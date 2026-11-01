from pathlib import Path
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, asdict, field
from pack_io import read_pack, write_pack, db_header
from database import decode, encode, SCHEMA
from game_compat import MANIFEST, make_manifest
from attributes import ATTRIBUTES, NEGATIVE, SCOPE_CORRECTIONS, templates, validate_value
from shop_addon import SHOP_FILENAME, safe_shop_entries
from ballistic_addon import BALLISTIC_FILENAME, safe_ballistic_entries

VERSION = '2.12.0'
GAME = r'D:\Program Files (x86)\Steam\steamapps\common\Total War WARHAMMER III'
PACK_NAME = 'zz_wh3_personal_trainer.pack'
CAP_VALUE_RANGE = (0, 100000000)  # healing_percentage_cap, as a multiple of unit health
ROOT = Path(__file__).parent
FROZEN = getattr(sys, 'frozen', False)
# Frozen: beside the exe, with bundled data unpacked to ROOT. From source: the repository.
HOME = Path(sys.executable).parent if FROZEN else ROOT.parent
REFERENCE = HOME / 'reference'
OUTPUT = HOME / 'output'
BUNDLE = (ROOT if FROZEN else HOME) / 'output/ingame' / PACK_NAME
MODS = {
    'building': ('2789869874', 'cpecific_building_quick_upgrade.pack'),
    'cap': ('2789900649', 'no regen cap.pack'),
    'ai': ('2799316652', 'aigeneral_iii_pancake.pack'),
    'blood': ('2935493705', '@!!!blood_returning+12.5.pack'),
}
EFFECTS = {
    'vigour': 'wh2_dlc10_effect_attribute_enable_perfect_vigour',
    'unbreakable': 'wh_main_effect_attribute_enable_unbreakable',
    'vanguard': 'wh_main_effect_attribute_enable_vanguard_deployment',
    'attrition': 'wh_main_effect_force_army_campaign_attrition_all_immunity',
    'foreign_replenishment': 'wh2_twa03_effect_blood_voyage_foreign_replenishment',
    'replenishment_cap': 'wh_main_ignore_replenishment_cap',
}
# These exact source->target layouts were inspected against the local game.
REVIEWED_MIGRATIONS = frozenset({
    ('special_ability_phases_tables', 41, 44),
    ('special_ability_phases_tables', 40, 44),
    ('unit_special_abilities_tables', 66, 74),
    ('rituals_tables', 0, 4),
    ('battle_vortexs_tables', 17, 20),
    ('projectiles_tables', 51, 53),
})


def additive_change(table, old, new):
    """Only new columns: every old column survives with its type, so rows carry over."""
    current = {f['name']: f['type'] for f in SCHEMA[table][str(new)]}
    return all(current.get(f['name']) == f['type'] for f in SCHEMA[table][str(old)])


def self_restoration_rows(table, rows, ability_key):
    """Remove inherited aura/CP/recharge side effects for one known ability."""
    if table == 'unit_special_abilities_tables':
        key_field = 'key'
        changes = dict(effect_range=0, num_effected_friendly_units=0,
                       update_targets_every_frame=False, additional_melee_cp=0)
    elif table == 'special_ability_phases_tables':
        key_field, changes = 'id', dict(ability_recharge_change=0)
    elif table == 'special_ability_to_special_ability_phase_junctions_tables':
        key_field, changes = 'special_ability', dict(target_friends=False)
    else:
        return
    for row in rows:
        if row.get(key_field) == ability_key:
            row.update(changes)


def repeating_ammo_rows(table, rows, ability_key, interval):
    """Ammo is granted on phase application, so an infinite phase grants once.

    Native Endless Drum/Unending Volley use finite passive activations and
    unlimited uses. A finite self phase with no cooldown repeats the grant;
    hp_change_frequency controls healing and cannot schedule ammunition.
    """
    self_restoration_rows(table, rows, ability_key)
    if table == 'unit_special_abilities_tables':
        for row in rows:
            if row.get('key') == ability_key:
                row.update(active_time=interval, recharge_time=0, initial_recharge=0,
                           num_uses=-1, passive=True)
    elif table == 'special_ability_phases_tables':
        for row in rows:
            if row.get('id') == ability_key:
                row.update(duration=interval, heal_amount=0, resurrect=False,
                           unbreakable=False, freeze_fatigue=False, freeze_recharge=False,
                           fatigue_change_ratio=0, inspiration_aura_range_mod=0,
                           mana_regen_mod=0, mana_max_depletion_mod=0, damage_amount=0,
                           barrier_heal_amount=0, execute_ratio=0)
    elif table == 'unit_abilities_to_additional_ui_effects_juncs_tables':
        for row in rows:
            if row.get('ability') == ability_key:
                row['effect'] = 'wh2_dlc11_all_replenish_ammo'


def separate_repeating_ammo(files, ability_key, ammo_key, interval, unique_id):
    """Keep healing/stat phases infinite; add one independently cycling ammo ability.

    The existing enable effect grants both abilities, preserving human-only
    handicap scopes and all original preset effects without a second campaign
    effect or a Lua battle timer.
    """
    identities = {
        'unit_special_abilities_tables': 'key',
        'special_ability_phases_tables': 'id',
        'special_ability_to_special_ability_phase_junctions_tables': 'special_ability',
        'unit_abilities_tables': 'key',
        'unit_abilities_to_additional_ui_effects_juncs_tables': 'ability',
        'effect_bonus_value_unit_ability_junctions_tables': 'unit_ability',
    }
    for name, data in list(files.items()):
        if not name.startswith('db\\'):
            continue
        table = name.split('\\')[1]
        identity = identities.get(table)
        if not identity:
            continue
        version, rows = decode(table, data)
        clones = []
        for row in rows:
            if row.get(identity) != ability_key:
                continue
            clone = {key: ammo_key if value == ability_key else value for key, value in row.items()}
            if table == 'special_ability_phases_tables':
                row['replenish_ammo'] = 0
            elif table == 'unit_special_abilities_tables':
                clone['unique_id'] = unique_id
            elif table == 'unit_abilities_tables':
                clone['requires_effect_enabling'] = True
            clones.append(clone)
        if clones:
            repeating_ammo_rows(table, clones, ammo_key, interval)
            files[name] = encode(table, version, rows + clones)

@dataclass
class Options:
    building: bool = False
    ai: bool = False
    cap: bool = False
    cap_value: float = 50.0
    regeneration: bool = False
    heal_percent: float = 12.5
    resurrection: bool = False
    ammunition: bool = False
    reload: bool = False
    vigour: bool = False
    unbreakable: bool = False
    vanguard: bool = False
    attrition: bool = False
    foreign_replenishment: bool = False
    replenishment_cap: bool = False
    local_mods: list = field(default_factory=list)
    attributes: dict = field(default_factory=dict)
    ammo_percent: float = 50.0
    reload_value: float = 20.0
    foreign_value: float = 100.0

    def validate(self):
        for name, low, high in [('cap_value', *CAP_VALUE_RANGE), ('heal_percent', 0.01, 100), ('ammo_percent', 0, 100), ('reload_value', 0, 500), ('foreign_value', 0, 500)]:
            value = getattr(self, name)
            if not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f'{name} 必须在 {low}～{high} 之间')
        for name, value in asdict(self).items():
            if name not in ('cap_value', 'heal_percent', 'local_mods', 'attributes', 'ammo_percent', 'reload_value', 'foreign_value') and type(value) is not bool:
                raise ValueError(f'{name} 必须是开关')
        if self.resurrection and not self.regeneration:
            raise ValueError('复活需要同时开启“持续回血”，请勾选持续回血并设置回复比例')
        if not isinstance(self.local_mods, list) or any(not isinstance(n, str) or Path(n).name != n or '/' in n or '\\' in n for n in self.local_mods):
            raise ValueError('非法本地 Mod 名称')
        if not isinstance(self.attributes, dict):
            raise ValueError('属性配置必须是字典')
        for key, value in self.attributes.items():
            validate_value(key, value)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def game_path(path):
    path = Path(path).resolve()
    if not (path / 'Warhammer3.exe').is_file() or not (path / 'data/db.pack').is_file():
        raise ValueError('请选择包含 Warhammer3.exe 和 data/db.pack 的游戏目录')
    return path


def discover(game, workshop=None, local_dir=None):
    game = game_path(game)
    root = Path(workshop) if workshop else game.parent.parent / 'workshop/content/1142710'
    local = Path(local_dir) if local_dir else REFERENCE
    found = {}
    for key, (ident, name) in MODS.items():
        candidates = [root / ident / name, game / 'data' / name, local / 'workshop' / name]
        found[key] = next((p for p in candidates if p.is_file()), None)
    return found


def build(game, options, workshop=None, local_dir=None):
    options.validate()
    game = game_path(game)
    local_dir = Path(local_dir) if local_dir else REFERENCE
    found = discover(game, workshop, local_dir)
    files, sources, migrations, warnings, ballistic_migrations = {}, [], [], [], []
    loaded = {}
    def source(key):
        if key not in loaded:
            if not found[key]:
                raise ValueError('缺少本地旧 Mod：' + MODS[key][1])
            data = found[key].read_bytes()
            loaded[key] = read_pack(found[key])
            sources.append({'path': str(found[key]), 'sha256': sha(data)})
        return loaded[key]
    for key in ('building', 'ai'):
        if getattr(options, key):
            files.update({n: b for n, b in source(key).items() if not n.endswith('.rpfm_reserved')})
    ability = options.regeneration or options.resurrection or options.ammunition or options.reload
    if ability or any(getattr(options, key) for key in EFFECTS):
        for name, data in source('blood').items():
            if not name.startswith('db\\'):
                continue
            table = name.split('\\')[1]
            if table == '_kv_unit_ability_scaling_rules_tables':
                continue  # cap is independently selected; never merge two conflicting rows
            version, rows = decode(table, data)
            if table == 'campaign_difficulty_handicap_effects_tables':
                keep = {effect for key, effect in EFFECTS.items() if getattr(options, key)}
                if ability:
                    keep.add('blood_returning')
                rows = [r for r in rows if r['human'] and r['effect'] in keep]
                for row in rows:
                    if row['effect'] == EFFECTS['foreign_replenishment']:
                        row['effect_value'] = options.foreign_value
            elif not ability:
                continue
            if table == 'special_ability_phases_tables':
                for row in rows:
                    row.update(heal_amount=options.heal_percent / 100 if options.regeneration else 0,
                               resurrect=options.resurrection, replenish_ammo=options.ammo_percent / 100 if options.ammunition else 0,
                               freeze_fatigue=False, unbreakable=False)
            self_restoration_rows(table, rows, 'blood')
            if table == 'special_ability_phase_stat_effects_tables' and not options.reload:
                continue
            if table == 'special_ability_phase_stat_effects_tables':
                for row in rows:
                    if row['stat'] == 'stat_reloading':
                        row['value'] = options.reload_value
            if rows:
                files['db\\' + table + '\\zz_wh3_personal_trainer'] = encode(table, version, rows)
        if options.ammunition:
            if options.regeneration or options.reload:
                separate_repeating_ammo(files, 'blood', 'wh3pt_static_ammo', 1, 1910401)
            else:
                for name, data in list(files.items()):
                    if name.startswith('db\\'):
                        table = name.split('\\')[1]
                        version, rows = decode(table, data)
                        repeating_ammo_rows(table, rows, 'blood', 1)
                        files[name] = encode(table, version, rows)
    if options.cap:
        for name, data in source('cap').items():
            if name.startswith('db\\'):
                table = name.split('\\')[1]
                version, rows = decode(table, data)
                if len(rows) != 1 or rows[0].get('key') != 'healing_percentage_cap':
                    raise ValueError('回血上限 Mod 内容已变化，请重新检查')
                rows[0]['value'] = options.cap_value
                files['db\\' + table + '\\zz_wh3_personal_trainer'] = encode(table, version, rows)
    if options.attributes:
        available = templates(local_dir, warnings)
        edited_rows = []
        for key, value in options.attributes.items():
            if key not in available:
                raise ValueError('缺少属性参考包：' + ATTRIBUTES[key][0])
            path, rows = available[key]
            sources.append({'path': str(path), 'sha256': sha(path.read_bytes()), 'attribute': key})
            edited_rows.extend(dict(row, effect=ATTRIBUTES[key][1], effect_value=-value if key in NEGATIVE else value,
                                    effect_scope=SCOPE_CORRECTIONS.get(key, row['effect_scope'])) for row in rows)
        files['db\\campaign_difficulty_handicap_effects_tables\\zz_custom_attributes'] = encode('campaign_difficulty_handicap_effects_tables', 0, edited_rows)
    for filename in options.local_mods:
        path = local_dir / filename
        if not path.is_file() or path.suffix.lower() != '.pack':
            raise ValueError('找不到所选本地 Mod：' + filename)
        entries = read_pack(path)
        sources.append({'path': str(path), 'sha256': sha(path.read_bytes())})
        if filename == SHOP_FILENAME:
            entries = safe_shop_entries(entries)
            warnings.append('神器附加模块仅为人类基斯里夫四神祈祷奖励，支持现版军队／行省及旧版入口；保留当前游戏的仪式、消耗、冷却与祈祷效果，不包含恐虐或通用商店；已移除旧包在当前游戏中不存在的魔风加成。')
        if filename == BALLISTIC_FILENAME:
            entries, ballistic_report = safe_ballistic_entries(game, entries)
            ballistic_migrations.append(ballistic_report)
            skipped = sum(len(v) for v in ballistic_report['unmigrated_legacy_keys'].values())
            warnings.append(f'弹道包仅向当前原生已有记录移植白名单数值与友伤开关；保留当前合法引用/枚举/新增字段；{skipped} 个未核验旧独有键未合并，可能同时影响敌方，实际游戏表现待验证。')
        for name, data in entries.items():
            if name.endswith('.rpfm_reserved'):
                continue
            if name.startswith('db\\'):
                table = name.split('\\')[1]
                # An explicitly selected cap overrides the bundled cap parameter.
                if table == '_kv_unit_ability_scaling_rules_tables' and options.cap:
                    continue
                if table == 'projectiles_tables' and filename != BALLISTIC_FILENAME:
                    warnings.append(filename + '：全局弹道/爆炸/漩涡覆盖，可能同时影响敌方；保留旧 Mod 数值，游戏效果待验证。')
                unique = sha(filename.encode())[:12]
                name = 'db\\' + table + '\\zz_local_' + unique
            if name in files and files[name] != data:
                raise ValueError('所选 Mod 包含冲突文件：' + name)
            files[name] = data
    # Merge handicap rows by their actual composite key. Reject conflicting presets.
    handicap = 'campaign_difficulty_handicap_effects_tables'
    merged, handicap_version = {}, 0
    for name in list(files):
        if name.startswith('db\\' + handicap + '\\'):
            handicap_version, rows = decode(handicap, files.pop(name))
            for row in rows:
                key = tuple((k, v) for k, v in sorted(row.items()) if k != 'effect_value')
                if key in merged and merged[key]['effect_value'] != row['effect_value']:
                    raise ValueError('效果数值冲突，请只保留一种强度：' + row['effect'])
                merged[key] = row
    if merged:
        files['db\\' + handicap + '\\zz_wh3_personal_trainer'] = encode(handicap, handicap_version, list(merged.values()))
    if not files:
        raise ValueError('请至少选择一项功能；停用全部功能请使用“卸载本修改器”')
    tables = {n.split('\\')[1] for n in files if n.startswith('db\\')}
    base = read_pack(game / 'data/db.pack', lambda n: n.startswith('db\\') and n.split('\\')[1] in tables)
    versions = {}
    for name, data in base.items():
        table = name.split('\\')[1]
        version = db_header(data)[0]
        if table in versions and versions[table] != version:
            raise ValueError('基础表存在多个版本：' + table)
        versions[table] = version
    for name in list(files):
        if not name.startswith('db\\'):
            continue
        table = name.split('\\')[1]
        old, rows = decode(table, files[name])
        current = versions.get(table)
        if current is None or str(current) not in SCHEMA[table]:
            raise ValueError('当前游戏表版本尚不支持：' + table + ' ' + str(current))
        if old != current:
            if (table, old, current) not in REVIEWED_MIGRATIONS:
                if not additive_change(table, old, current):
                    raise ValueError(f'需要人工迁移：{table} {old} → {current}')
                warnings.append(f'{table} {old} → {current}：已自动补齐新增字段，尚未人工核对。')
            migrations.append(f'{table}: {old} → {current}')
            # Preserve new fields on vanilla rows instead of resetting them to defaults.
            base_rows = []
            for base_name, base_data in base.items():
                if base_name.startswith('db\\' + table + '\\'):
                    base_rows.extend(decode(table, base_data)[1])
            key_name = 'key' if rows and 'key' in rows[0] else 'id'
            by_key = {r.get(key_name): r for r in base_rows if key_name in r}
            old_fields = {f['name'] for f in SCHEMA[table][str(old)]}
            for row in rows:
                vanilla = by_key.get(row.get(key_name), {})
                for f in SCHEMA[table][str(current)]:
                    if f['name'] not in old_fields and f['name'] in vanilla:
                        row[f['name']] = vanilla[f['name']]
        files[name] = encode(table, current, rows)
        decode(table, files[name])
    # Detect cross-pack DB key collisions, including differently named preset files.
    table_keys = {}
    for name, data in files.items():
        if not name.startswith('db\\'):
            continue
        table = name.split('\\')[1]
        _, rows = decode(table, data)
        if table == handicap:
            continue
        for row in rows:
            identity = row.get('key', row.get('id'))
            if identity is not None:
                key = (table, identity)
                if key in table_keys:
                    raise ValueError('重复的预设数据，请只选择一种：' + table + '/' + str(identity))
                table_keys[key] = name
    # Marker belongs only to this application and is checked before replacing/removing a pack.
    files['wh3_personal_trainer_marker.txt'] = b'WH3-PERSONAL-TRAINER-v1'
    files[MANIFEST] = make_manifest(game, tables, VERSION)
    payload = write_pack(files)
    return payload, {'options': asdict(options), 'sources': sources, 'migrations': migrations,
                     'files': sorted(files), 'sha256': sha(payload), 'warnings': warnings,
                     'ballistic_migrations': ballistic_migrations,
                     'validation': 'Offline only; in-game behavior not verified'}


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.wh3-trainer-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def running():
    if os.name != 'nt':
        return False
    result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Warhammer3.exe', '/FO', 'CSV', '/NH'],
                            capture_output=True, creationflags=0x08000000, check=True)
    return b'warhammer3.exe' in result.stdout.lower()


def owned(path):
    entries = read_pack(path, lambda n: n == 'wh3_personal_trainer_marker.txt')
    if entries.get('wh3_personal_trainer_marker.txt') != b'WH3-PERSONAL-TRAINER-v1':
        raise ValueError('目标同名文件不属于本修改器，已拒绝覆盖或删除')


def install(game, payload, history):
    game = game_path(game)
    if running():
        raise ValueError('请先退出游戏，再安装或更新功能')
    target = game / 'data' / PACK_NAME
    if target.is_symlink() or (game / 'data').is_symlink():
        raise ValueError('安装目标不能是目录链接')
    if target.exists():
        owned(target)
        old = target.read_bytes()
        atomic_write(Path(history) / (sha(old) + '.pack'), old)
    # Validate payload and ownership using an isolated local file before touching game data.
    with tempfile.TemporaryDirectory() as tmp:
        check = Path(tmp) / PACK_NAME; check.write_bytes(payload); owned(check)
    atomic_write(target, payload)
    if sha(target.read_bytes()) != sha(payload):
        raise IOError('安装回读不一致')
    return target


def uninstall(game, history):
    game = game_path(game)
    if running():
        raise ValueError('请先退出游戏')
    target = game / 'data' / PACK_NAME
    if not target.exists():
        return False
    if target.is_symlink() or (game / 'data').is_symlink():
        raise ValueError('卸载目标不能是目录链接')
    owned(target)
    old = target.read_bytes()
    atomic_write(Path(history) / (sha(old) + '.pack'), old)
    target.unlink()
    return True
