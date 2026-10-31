"""Conservative numeric migration of the named legacy projectile pack.

Current native records provide references, enum values and newly added fields.
Legacy-only keys are reported and omitted until their current meaning is proved.
"""
import math
from pathlib import Path

from database import SCHEMA, decode, encode
from pack_io import read_pack

BALLISTIC_FILENAME = '@这回英国人不需要信鸽了.pack'
PRIMARY_KEYS = {
    'battle_vortexs_tables': 'vortex_key',
    'projectiles_explosions_tables': 'key',
    'projectiles_tables': 'key',
}
# Explicit reviewed gameplay columns only. Do not infer author intent from a
# difference against today's vanilla: these are retained legacy numeric settings.
NUMERIC_FIELDS = {
    'battle_vortexs_tables': frozenset({
        'change_max_angle','damage','damage_ap','duration','expansion_speed',
        'goal_radius','move_change_freq','movement_speed','start_radius',
        'ignition_amount','detonation_force','height_off_ground','delay',
        'num_vortexes','launch_source_offset','delay_between_vortexes','start_height','goal_height',
    }),
    'projectiles_explosions_tables': frozenset({
        'detonation_radius','detonation_duration','detonation_speed','detonation_damage',
        'fuse_distance_from_target','ignition_amount','detonation_damage_ap',
        'detonation_force','fuse_fixed_time',
    }),
    'projectiles_tables': frozenset({
        'projectile_number','effective_range','minimum_range','max_elevation',
        'muzzle_velocity','marksmanship_bonus','spread','damage','ap_damage',
        'collision_radius','base_reload_time','calibration_distance','calibration_area',
        'bonus_v_infantry','bonus_v_large','shockwave_radius','gravity','burst_size',
        'burst_shot_delay','mass','ignition_amount','fixed_elevation','expiry_range',
        'shots_per_volley','building_damage_multiplier','vegetation_ignore_time','missile_mirror_start_time',
    }),
}
BOOLEAN_FIELDS = {
    'battle_vortexs_tables': frozenset({'affects_allies'}),
    'projectiles_explosions_tables': frozenset({'affects_allies'}),
    'projectiles_tables': frozenset({'can_damage_allies'}),
}
NUMERIC_TYPES = {'F32','F64','I32','I64','I16'}


def safe_ballistic_entries(game, source):
    """Return current-version replacements and an auditable field manifest."""
    tables = set(PRIMARY_KEYS)
    native_files = read_pack(Path(game) / 'data/db.pack',
                             lambda name: name.startswith('db\\') and name.split('\\')[1] in tables)
    native, versions = {}, {}
    for name, data in native_files.items():
        table = name.split('\\')[1]
        version, rows = decode(table, data)
        if table in versions and versions[table] != version:
            raise ValueError('原生弹道表含多个版本：' + table)
        versions[table] = version
        by_key = native.setdefault(table, {})
        for row in rows:
            key = row[PRIMARY_KEYS[table]]
            if key in by_key:
                raise ValueError('原生弹道表含重复键：' + table + '/' + key)
            by_key[key] = row
    if set(native) != tables:
        raise ValueError('当前游戏缺少弹道迁移所需原生表')
    report = dict(policy='current_native_records_with_reviewed_legacy_numeric_settings',
                  author_change_history_available=False,unmigrated_legacy_keys={},tables={},changes=[],preserved_current_fields=[])
    seen, replacements = set(), {t: [] for t in tables}
    for name, data in source.items():
        if name.endswith('.rpfm_reserved'):
            continue
        if not name.startswith('db\\') or name.split('\\')[1] not in tables:
            raise ValueError('弹道参考包包含未审查内容：' + name)
        table = name.split('\\')[1]
        old_version, rows = decode(table, data)
        old_fields = {f['name']: f['type'] for f in SCHEMA[table][str(old_version)]}
        current_fields = {f['name']: f['type'] for f in SCHEMA[table][str(versions[table])]}
        allowed = NUMERIC_FIELDS[table] | BOOLEAN_FIELDS[table]
        for field in allowed & old_fields.keys():
            if field not in current_fields or old_fields[field] != current_fields[field]:
                raise ValueError('弹道字段版本不兼容：' + table + '/' + field)
            expected = {'Boolean'} if field in BOOLEAN_FIELDS[table] else NUMERIC_TYPES
            if current_fields[field] not in expected:
                raise ValueError('弹道白名单字段类型已变化：' + table + '/' + field)
        stats = report['tables'].setdefault(table, dict(legacy_rows=0,native_existing_rows=0,changed_rows=0,current_version=versions[table]))
        for legacy in rows:
            key = legacy[PRIMARY_KEYS[table]]
            identity = (table,key)
            if identity in seen:
                raise ValueError('弹道参考包含重复键：' + table + '/' + key)
            seen.add(identity)
            stats['legacy_rows'] += 1
            if key not in native[table]:
                report['unmigrated_legacy_keys'].setdefault(table, []).append(key)
                continue
            stats['native_existing_rows'] += 1
            current = native[table][key]
            result = dict(current)
            for field in allowed & legacy.keys():
                value = legacy[field]
                if field in NUMERIC_FIELDS[table] and (type(value) not in (int,float) or not math.isfinite(value)):
                    raise ValueError('弹道参数必须为有限数值：' + table + '/' + key + '/' + field)
                result[field] = value
                if value != current[field]:
                    report['changes'].append(dict(table=table,key=key,field=field,native=current[field],legacy=value,result=value))
            for field in legacy.keys() & current.keys() - allowed:
                if legacy[field] != current[field]:
                    report['preserved_current_fields'].append(dict(table=table,key=key,field=field,
                        legacy=legacy[field],result=current[field],reason='keep_current_reference_enum_or_unreviewed_setting'))
            if result != current:
                replacements[table].append(result)
                stats['changed_rows'] += 1
    files = {}
    for table, rows in replacements.items():
        if rows:
            files['db\\' + table + '\\wh3pt_ballistic_current'] = encode(table, versions[table], rows)
    if not files:
        raise ValueError('弹道参考包没有可兼容迁移的现版已有记录')
    return files, report
