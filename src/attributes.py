"""Editable effect values, sourced from the user's local reference packs."""
from pathlib import Path
import math
import struct
from pack_io import read_pack
from database import decode

TABLE = 'campaign_difficulty_handicap_effects_tables'
# id: label, effect, human, category, maximum, default, unit
ATTRIBUTES = {
    'ward': ('特保', 'wh_main_effect_character_stat_ward_save', True, '部队属性', 100, 33, '效果值'),
    'missile_resistance': ('远程抗性', 'wh_main_effect_character_stat_missile_resistance', True, '部队属性', 100, 33, '效果值'),
    'attack': ('近战攻击加成', 'wh_main_effect_force_stat_melee_attack', True, '部队属性', 500, 20, '效果值'),
    'defence': ('近战防御加成', 'wh_main_effect_force_stat_melee_defence', True, '部队属性', 500, 20, '效果值'),
    'weapon': ('近战武器伤害加成', 'wh2_dlc17_effect_weapon_str_for_all', True, '部队属性', 1000, 20, '效果值'),
    'missile_damage': ('远程武器伤害加成', 'wh_main_effect_force_stat_missile_damage', True, '部队属性', 1000, 20, '效果值'),
    'ammunition': ('弹药量加成', 'wh_main_effect_character_stat_ammunition', True, '部队属性', 1000, 100, '%'),
    'armour': ('护甲加成', 'wh_main_effect_character_stat_armour', True, '部队属性', 1000, 100, '点'),
    'range': ('射程加成', 'wh_main_effect_force_stat_range', True, '部队属性', 1000, 100, '%'),
    'health': ('领主与英雄生命值', 'wh_main_effect_character_stat_unit_health', True, '部队属性', 1000, 100, '%'),
    'speed': ('速度加成', 'wh_main_effect_force_stat_speed', True, '部队属性', 1000, 100, '%'),
    'mass': ('质量加成', 'wh3_main_effect_force_stat_unit_mass_percentage_mod', True, '部队属性', 1000, 100, '%'),
    'reload': ('装填速度加成', 'wh_main_effect_character_stat_reload_time_reduction', True, '部队属性', 1000, 100, '效果值'),
    'order': ('公共秩序加成', 'wh_main_effect_public_order_difficulty_level', True, '战役加成', 500, 20, '效果值'),
    'movement': ('战役移动距离加成', 'wh_main_effect_force_all_campaign_movement_range', True, '战役加成', 1000, 100, '%'),
    'diplomacy': ('外交关系加成', 'wh_main_faction_political_diplomacy_mod', True, '战役加成', 1000, 50, '效果值'),
    'recruitment': ('本地招募格子加成', 'wh_main_effect_unit_recruitment_points', True, '战役加成', 100, 10, '格'),
    'upkeep': ('维护费减免', 'wh_main_effect_force_all_campaign_upkeep', True, '战役加成', 100, 50, '%'),
    'agent_player': ('玩家间谍成功率加成', 'wh_main_effect_agent_action_success_chance', True, '战役加成', 100, 100, '效果值'),
    'agent_ai': ('敌方间谍成功率减益', 'wh_main_effect_agent_action_success_chance', False, '战役加成', 100, 100, '效果值'),
}
NEGATIVE = {'upkeep', 'agent_ai'}

# Native DB examples: Throt's final battle targets character hit points;
# The Dark Elf Black Council dilemma targets local province recruitment.
SCOPE_CORRECTIONS = {
    'health': 'faction_to_character_own',
    'recruitment': 'faction_to_province_own',
}
# Still recognize the supplied old reference pack, which used horde capacity.
REFERENCE_EFFECT_ALIASES = {'wh_main_effect_force_all_recruitment_points': 'recruitment'}


def validate_value(key, value):
    if key not in ATTRIBUTES:
        raise ValueError('未知属性：' + str(key))
    label, _, _, _, maximum, _, _ = ATTRIBUTES[key]
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError(f'{label} 必须在 0～{maximum} 之间')
    if key == 'recruitment' and int(value) != value:
        raise ValueError('招募格子必须为整数')


def attribute_pack(path):
    """Only replace a pure supported attribute pack; never hide bundled features."""
    entries = {n: b for n, b in read_pack(path).items() if not n.endswith('.rpfm_reserved')}
    if not entries or any(not n.startswith('db\\' + TABLE + '\\') for n in entries):
        return {}
    lookup = {(spec[1], spec[2]): key for key, spec in ATTRIBUTES.items()}
    lookup.update({(effect, ATTRIBUTES[key][2]): key for effect, key in REFERENCE_EFFECT_ALIASES.items()})
    groups = {}
    for data in entries.values():
        _, rows = decode(TABLE, data)
        for row in rows:
            key = lookup.get((row['effect'], row['human']))
            if key is None:
                return {}
            groups.setdefault(key, []).append(row)
    for key, rows in groups.items():
        difficulties = {-3, -2, -1, 0, 1} if ATTRIBUTES[key][2] else {-1, 0, 1, 2, 3}
        if len(rows) != 5 or {r['campaign_difficulty_handicap'] for r in rows} != difficulties:
            return {}
        if len({r['effect_value'] for r in rows}) != 1:
            return {}
    return groups


def templates(folder, warnings=None):
    result = {}
    for path in sorted(Path(folder).glob('*.pack')):
        if path.name == 'zz_wh3_personal_trainer.pack':
            continue
        try:
            groups = attribute_pack(path)
        except (ValueError, KeyError, struct.error, IndexError, OSError) as exc:
            if warnings is not None:
                warnings.append('已跳过无法解析的参考包 ' + path.name + '：' + str(exc))
            continue
        for key, rows in groups.items():
            result.setdefault(key, (path, rows))
    return result
