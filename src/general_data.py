"""What the battle commander knows about units and abilities.

Read from the installed game at build time, so a patch or DLC brings its own units
along. The classification is CA's: ai_usage_group for units, ai_usage for abilities.
"""
from pathlib import Path
import re

from database import decode
from pack_io import read_pack

# Role flags, one bit each; battle's general.lua spells out the same names.
FLAGS = {'ranged': 1, 'hybrid': 2, 'mage': 4, 'flanker': 8, 'shock': 16, 'anti_large': 32,
         'high': 64, 'arty': 128, 'large': 256, 'shield': 512, 'harasser': 1024, 'stalker': 2048,
         'melee': 4096}
_ROLE_TOKENS = {'ranged': 'ranged', 'hybrid': 'hybrid', 'mage': 'mage', 'flanker': 'flanker',
                'flankers': 'flanker', 'shock': 'shock', 'highvalue': 'high', 'arty': 'arty',
                'harasser': 'harasser', 'stalkers': 'stalker', 'melee': 'melee'}
_ROLE_PHRASES = {'anti_large': 'anti_large'}

# Abilities a commander has no business firing: they move or remove the caster, or
# exist only for scripted and purchased situations.
_SKIPPED_SOURCES = {'army', 'upgrades', 'replenishments', 'cataclysm', 'quest'}
_SKIPPED_BEHAVIOURS = {'teleport_move', 'changeling_transformation', 'captures_unit',
                       'dig_yourself_out', 'go_underground', 'terrify_target'}

# What an ability is for, by CA's ai_usage. Order matters: first match wins.
_USES = (
    ('heal', r'^(healing|augment_melee_healing)'),
    ('ammo', r'^ammo_replenish'),
    ('rally', r'^augment_morale'),
    ('shooters', r'^augment_ranged'),
    ('cover', r'^augment_defense_ranged'),
    ('self', r'^(augment_melee_self|augment_charge_self|augment_self_area|augment_support_self)'),
    ('bless', r'^(augment_melee|augment_defense_melee|augment_charge)'),
    ('panic', r'^(threatened|being_charged)'),
    ('hex_shooters', r'^hex_ranged(_area)?$'),
    ('hex', r'^hex_(melee|armour|morale|speed|flying)'),
    ('burst', r'^aoe_centred_on_self'),
    ('snipe', r'^(projectile_homing_single|projectile_single|projectile_large|direct_damage_single_entity$)'),
    ('nuke', r'^(vortex|aoe|projectile_explosive|direct_damage)'),
    ('always', r'^always_use_only_land'),
)
# Used when ai_usage says nothing useful.
_TYPE_USES = {
    'wh_type_summon_unit': 'summon', 'wh_type_augment': 'bless', 'wh_type_area_of_augments': 'bless',
    'wh_type_hex': 'hex', 'wh_type_area_of_hexes': 'hex', 'wh_type_regeneration': 'heal',
    'wh_type_area_of_regeneration': 'heal', 'wh_type_ward_save': 'bless',
    'wh_type_area_of_ward_save': 'bless',
}
USES = ('heal', 'ammo', 'rally', 'shooters', 'cover', 'self', 'bless', 'panic', 'hex_shooters', 'hex',
        'burst', 'snipe', 'nuke', 'always', 'summon')

TARGET_SELF, TARGET_FRIEND, TARGET_ENEMY, TARGET_GROUND = 1, 2, 4, 8
# Metres around the aim point that have to be clear of our own men. A moving vortex is
# only followed so far: past that a spell on anything near the front would never fire.
_SAFETY, _MAX_TRAVEL, _BOMBARDMENT_DANGER, _DEFAULT_DANGER = 14, 60, 50, 32


def _rows(game, table):
    entries = read_pack(Path(game) / 'data/db.pack', lambda n: n.startswith('db\\' + table + '\\'))
    return [row for data in entries.values() for row in decode(table, data)[1]]


def _suffix(key):
    """armour and shield keys end in their value: wh2_main_plate_90, wh_missile_block_35_wood."""
    numbers = re.findall(r'\d+', key or '')
    return int(numbers[-1]) if numbers else 0


def role_flags(group, ground, shield, category='artillery'):
    flags = 0
    tokens = (group or '').split('_')
    for token in tokens:
        name = _ROLE_TOKENS.get(token)
        if name:
            flags |= FLAGS[name]
    for phrase, name in _ROLE_PHRASES.items():
        if phrase in (group or ''):
            flags |= FLAGS[name]
    # Vehicles carrying a gun are in CA's "arty" group too, but they drive.
    if category != 'artillery':
        flags &= ~FLAGS['arty']
    if 'large' in (ground or ''):
        flags |= FLAGS['large']
    if shield and shield != 'none':
        flags |= FLAGS['shield']
    return flags


def unit_profiles(game):
    """land unit key -> [flags, value, armour, ap%, bonus v large, bonus v infantry, missile block%]."""
    weapons = {r['key']: r for r in _rows(game, 'melee_weapons_tables')}
    worth = {}
    for row in _rows(game, 'main_units_tables'):
        cost = row['multiplayer_cost'] or row['recruitment_cost'] or int((row['melee_cp'] or 0) + (row['missile_cp'] or 0))
        worth[row['land_unit']] = max(worth.get(row['land_unit'], 0), cost)
    result = {}
    for row in _rows(game, 'land_units_tables'):
        weapon = weapons.get(row['primary_melee_weapon']) or {}
        damage, ap = weapon.get('damage') or 0, weapon.get('ap_damage') or 0
        total = damage + ap
        result[row['key']] = [
            role_flags(row['ai_usage_group'], row['ground_stat_effect_group'], row['shield'], row['category']),
            worth.get(row['key'], 0), _suffix(row['armour']),
            int(round(100 * ap / total)) if total else 0,
            weapon.get('bonus_v_large') or 0, weapon.get('bonus_v_infantry') or 0,
            _suffix(row['shield']) if row['shield'] != 'none' else 0,
        ]
    return result


def ability_use(row, kind):
    usage = row['ai_usage'] or ''
    if kind == 'wh_type_summon_unit' or row['spawned_unit']:
        return 'summon'
    for use, pattern in _USES:
        if re.match(pattern, usage):
            return use
    return _TYPE_USES.get(kind)


def blast_size(row, vortices):
    """(radius of the effect, how far from the aim point it endangers our own men)."""
    vortex = vortices.get(row['vortex'])
    if vortex:
        radius = max(vortex['goal_radius'], vortex['start_radius'])
        if not vortex['affects_allies']:
            return radius, 0
        travel = (vortex['movement_speed'] or 0) * (vortex['duration'] or 0)
        return radius, radius + min(travel, _MAX_TRAVEL) + _SAFETY
    return row['effect_range'], _BOMBARDMENT_DANGER if row['bombardment'] else _DEFAULT_DANGER


def ability_catalog(game):
    """ability key -> [use, target mask, range, area, mana, recharge, uses, wind up, duration, danger]."""
    kinds = {r['key']: r for r in _rows(game, 'unit_abilities_tables')}
    vortices = {r['vortex_key']: r for r in _rows(game, 'battle_vortexs_tables')}
    result = {}
    for row in _rows(game, 'unit_special_abilities_tables'):
        info = kinds.get(row['key'])
        if row['passive'] or not info or info['source_type'] in _SKIPPED_SOURCES:
            continue
        if row['spawn_is_transformation'] or row['behaviour'] in _SKIPPED_BEHAVIOURS:
            continue
        # Breath from the caster's own front would go through the line in front of it.
        vortex = vortices.get(row['vortex'])
        if vortex and vortex['launch_source'] == 'activator_front' and vortex['affects_allies']:
            continue
        use = ability_use(row, info['type'])
        if not use:
            continue
        mask = (TARGET_SELF if row['target_self'] else 0) | (TARGET_FRIEND if row['target_friends'] else 0) \
            | (TARGET_ENEMY if row['target_enemies'] else 0) \
            | (TARGET_GROUND if row['target_ground'] or row['target_ground_under_allies'] or row['target_ground_under_enemies'] else 0)
        area, danger = blast_size(row, vortices)
        result[row['key']] = [
            USES.index(use) + 1, mask, max(0, round(row['target_intercept_range'])), max(0, round(area)),
            round(row['mana_cost']), max(0, round(row['recharge_time'])), row['num_uses'],
            round(row['wind_up_time'], 1), max(0, round(row['active_time'])), round(danger),
        ]
    return result


def general_data(game):
    return {'flags': FLAGS, 'uses': list(USES), 'units': unit_profiles(game), 'abilities': ability_catalog(game)}
