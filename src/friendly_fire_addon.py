"""Takes friendly fire out of the game: missiles, explosions and vortices stop hurting allies.

Only the one flag changes, no number does. The flags have no owner, so the enemy's shots
stop hurting the enemy too. Whatever reaches allies to heal or strengthen them keeps working.
"""
from pathlib import Path

from database import decode, encode
from pack_io import read_pack

FRAGMENT = 'wh3pt_friendly_fire'
# Table -> its key, the flag, and the column naming the effect a hit has on a unit.
TABLES = {
    'battle_vortexs_tables': ('vortex_key', 'affects_allies', 'contact_effect'),
    'projectiles_explosions_tables': ('key', 'affects_allies', 'contact_phase_effect'),
    'projectiles_tables': ('key', 'can_damage_allies', 'contact_stat_effect'),
}


def _rows(game, table):
    entries = read_pack(Path(game) / 'data/db.pack', lambda n: n.startswith('db\\' + table + '\\'))
    versions, rows = set(), []
    for data in entries.values():
        version, part = decode(table, data)
        versions.add(version)
        rows.extend(part)
    if len(versions) != 1:
        raise ValueError('友军伤害所需的原生表异常：' + table)
    return versions.pop(), rows


def ally_helpers(native, phases):
    """The rows that reach allies to help them, by table: they need the flag."""
    helping = {row['id'] for row in phases if row['affects_allies'] and row['effect_type'] == 'positive'}
    kept = {table: {key for key, row in native[table].items() if row[flag] and row[effect] in helping}
            for table, (_, flag, effect) in TABLES.items()}
    # A shell only carries its explosion or vortex there, so it has to reach allies as well.
    kept['projectiles_tables'] |= {
        key for key, row in native['projectiles_tables'].items() if row['can_damage_allies']
        and (row['explosion_type'] in kept['projectiles_explosions_tables']
             or row['spawned_vortex'] in kept['battle_vortexs_tables'])}
    return kept


def friendly_fire_entries(game, files):
    """Table files for a pack that already holds `files`.

    Of two rows with one key only one reaches the game, so a row the pack already
    overrides is changed where it stands, and only the others go into a new file.
    """
    versions, native = {}, {}
    for table, (key, _, _) in TABLES.items():
        versions[table], rows = _rows(game, table)
        native[table] = {row[key]: row for row in rows}
    kept = ally_helpers(native, _rows(game, 'special_ability_phases_tables')[1])
    result = {}
    for table, (key, flag, _) in TABLES.items():
        prefix = 'db\\' + table + '\\'
        overridden = set()
        for name in [n for n in files if n.startswith(prefix)]:
            version, rows = decode(table, files[name])
            for row in rows:
                row[flag] = row[key] in kept[table]
                overridden.add(row[key])
            result[name] = encode(table, version, rows)
        rows = [dict(row, **{flag: False}) for name, row in native[table].items()
                if row[flag] and name not in kept[table] and name not in overridden]
        if rows:
            result[prefix + FRAGMENT] = encode(table, versions[table], rows)
    return result
