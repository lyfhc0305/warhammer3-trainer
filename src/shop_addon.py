"""Convert the legacy shop pack into Kislev prayer rewards only.

The source pack also changes vanilla rituals, Khorne and global localisation.
None of those overrides belong to this add-on's supported reward feature.
"""
from pathlib import Path
import struct

from database import decode, encode

SHOP_FILENAME = '@锤3神器商店.pack'
SHOP_SCRIPT = 'script\\campaign\\mod\\wh3pt_shop_reward.lua'
REMOVED_MAGIC_EFFECT = 'wh3_main_effect_winds_of_magic_pooled_resource_change_positive_mod'
ITEMS = (
    'wh2_dlc15_anc_enchanted_item_star_dragon_special1',
    'wh2_main_anc_armour_war_crown_of_saphery1',
    'wh_main_anc_talisman_the_eye_of_sheerian1',
    'wh2_dlc10_anc_weapon_the_widowmaker_31',
)
TABLE_FIELDS = {
    'ancillaries_tables': 'key',
    'ancillary_info_tables': 'ancillary',
    'ancillary_to_effects_tables': 'ancillary',
}


def decode_loc(data):
    if data[:10] != b'\xff\xfeLOC\0\1\0\0\0':
        raise ValueError('神器名称本地化格式不受支持')
    count = struct.unpack_from('<I', data, 10)[0]
    pos, rows = 14, []
    for _ in range(count):
        values = []
        for _ in range(2):
            length = struct.unpack_from('<H', data, pos)[0]
            pos += 2
            raw = data[pos:pos + length * 2]
            if len(raw) != length * 2:
                raise ValueError('神器名称本地化被截断')
            values.append(raw.decode('utf-16-le'))
            pos += length * 2
        values.append(struct.unpack_from('<?', data, pos)[0])
        pos += 1
        rows.append(tuple(values))
    if pos != len(data):
        raise ValueError('神器名称本地化长度不匹配')
    return rows


def encode_loc(rows):
    result = bytearray(b'\xff\xfeLOC\0\1\0\0\0' + struct.pack('<I', len(rows)))
    for key, value, tooltip in rows:
        for field in (key, value):
            raw = field.encode('utf-16-le')
            result.extend(struct.pack('<H', len(raw) // 2))
            result.extend(raw)
        result.extend(struct.pack('<?', tooltip))
    return bytes(result)


def safe_shop_entries(source):
    """Return a strict allowlist; never pass the legacy ritual payload through."""
    files, names = {}, {}
    for table, key_field in TABLE_FIELDS.items():
        matched, version = [], None
        for name, data in source.items():
            if name.startswith('db\\' + table + '\\'):
                row_version, rows = decode(table, data)
                if version is not None and version != row_version:
                    raise ValueError('神器参考包存在多个表版本：' + table)
                version = row_version
                matched.extend(r for r in rows if r[key_field] in ITEMS)
        if table == 'ancillary_to_effects_tables':
            # This legacy percentage effect is absent from all current native
            # effect tables. The remaining *_positive effect uses base_amount,
            # so substituting it would silently grant a different flat bonus.
            matched = [r for r in matched if r['effect'] != REMOVED_MAGIC_EFFECT]
        if {r[key_field] for r in matched} != set(ITEMS):
            raise ValueError('神器参考包缺少四件祈祷奖励的数据：' + table)
        if table != 'ancillary_to_effects_tables' and len(matched) != len(ITEMS):
            raise ValueError('神器参考包含重复物品：' + table)
        files['db\\' + table + '\\wh3pt_kislev_prayer_rewards'] = encode(table, version, matched)
    for name, data in source.items():
        if name.endswith('.loc'):
            for key, value, tooltip in decode_loc(data):
                if any(key.endswith('_' + item) for item in ITEMS):
                    row = (key, value, tooltip)
                    if key in names and names[key] != row:
                        raise ValueError('神器参考包含冲突名称：' + key)
                    names[key] = row
    if any('ancillaries_onscreen_name_' + item not in names for item in ITEMS):
        raise ValueError('神器参考包缺少奖励名称')
    files['text\\db\\wh3pt_kislev_prayer_rewards.loc'] = encode_loc(list(names.values()))
    files[SHOP_SCRIPT] = (Path(__file__).parent / 'ingame/shop_reward.lua').read_bytes()
    return files
