"""Read the installed game's faction resources and Regiments of Renown."""
from pathlib import Path
import struct
from database import decode, encode, table_version
from pack_io import read_pack

CATALOG_TABLES = frozenset({
    'pooled_resources_tables', 'pooled_resource_factor_junctions_tables', 'main_units_tables', 'mercenary_pools_tables',
    'mercenary_pool_to_groups_junctions_tables', 'mercenary_unit_groups_tables', 'faction_to_mercenary_set_junctions_tables',
    'land_units_tables', 'unit_variants_tables', 'melee_weapons_tables', 'ui_unit_groupings_tables', 'ui_unit_group_parents_tables',
})
SOURCE_TABLES = CATALOG_TABLES | {'unit_set_to_unit_junctions_tables', 'campaign_mercenary_unit_character_level_restrictions_tables'}


def chinese_labels(game):
    labels = {}
    path = Path(game) / 'data/local_cn.pack'
    if not path.is_file():
        return labels
    for data in read_pack(path, lambda n: n.endswith('.loc')).values():
        if data[:6] != b'\xff\xfeLOC\x00':
            raise ValueError('Unsupported localisation header')
        pos = 14
        for _ in range(struct.unpack_from('<I', data, 10)[0]):
            fields = []
            for field in range(2):
                size = struct.unpack_from('<H', data, pos)[0] * 2
                pos += 2
                fields.append(data[pos:pos+size].decode('utf-16-le'))
                pos += size
            pos += 1
            labels[fields[0]] = fields[1]
        if pos != len(data):
            raise ValueError('Localisation length mismatch')
    return labels


def native_image_paths(game):
    # Read only Pack indexes; game textures stay in the game's own archives.
    names = set()
    for path in (Path(game) / 'data').glob('*ui*.pack'):
        with path.open('rb') as stream:
            header = stream.read(28)
            if header[:4] != b'PFH5':
                continue
            flags, _, dependencies_size, count, index_size, _ = struct.unpack('<6I', header[4:])
            stream.seek(dependencies_size, 1)
            index = stream.read(index_size)
            pos = 0
            for _ in range(count):
                pos += 5 + (4 if flags & 64 else 0)
                end = index.index(0, pos)
                names.add(index[pos:end].decode('utf-8').replace('\\', '/'))
                pos = end + 1
    return names


def strategic_catalog(game):
    tables = {t: [] for t in CATALOG_TABLES}
    for name, data in read_pack(Path(game) / 'data/db.pack', lambda n: n.startswith('db\\') and n.split('\\')[1] in CATALOG_TABLES).items():
        tables[name.split('\\')[1]].extend(decode(name.split('\\')[1], data)[1])
    labels = chinese_labels(game)
    images = native_image_paths(game)
    factors = {}
    for row in tables['pooled_resource_factor_junctions_tables']:
        if not row['specific_faction_set']:
            factors.setdefault(row['resource'], []).append(row)
    resources = []
    for row in tables['pooled_resources_tables']:
        regional = row['key'] == 'wh3_dlc29_nag_necromantic_energy'
        label = labels.get('pooled_resources_display_name_' + row['key'], '')
        if not label or '{{' in label or not row['optional_icon_path']:
            continue
        if (row['scope'] != 'FACTION' and not regional) or row['reset_before_income']:
            continue
        candidates = factors.get(row['key'], [])
        # Use a real resource/factor junction, never an invented universal factor.
        candidates.sort(key=lambda r: (not (r['minimum'] < 0 < r['maximum']), r['factor'] != row['default_factor'], r['factor'] not in ('other', 'events'), r['factor']))
        if not candidates:
            continue
        factor = candidates[0]
        resources.append(dict(id=row['key'], factor=factor['factor'], factor_min=factor['minimum'], factor_max=factor['maximum'], min=row['minimum'], max=row['maximum'],
                              loc='pooled_resources_display_name_' + row['key'], label=label,
                              region_group='land_of_the_dead' if regional else None,
                              faction='wh3_dlc29_nag_host_of_nagash' if regional else None,
                              icon='ui/skins/default/' + row['optional_icon_path']))
    sigil_icon = 'ui/skins/default/dlc29_nag_black_pyramid/icon_mystic_sigil.png'
    assert sigil_icon in images, 'Missing native mystic sigil icon'
    resources.append(dict(id='wh3_dlc29_pyramid_initiative_set', kind='sigils',
                          label='可强化符印', min=0, max=10000,
                          faction='wh3_dlc29_nag_host_of_nagash', icon=sigil_icon))
    groups = {r['key']: r['unit_record'] for r in tables['mercenary_unit_groups_tables']}
    pools = {r['key'] for r in tables['mercenary_pools_tables'] if r['ui_recruitment_info'] == 'renown'}
    pool_factions = {}
    for r in tables['faction_to_mercenary_set_junctions_tables']:
        pool_factions.setdefault(r['mercenary_set'], []).append(r['faction'])
    eligibility = {}
    for r in tables['mercenary_pool_to_groups_junctions_tables']:
        unit = groups.get(r['group'])
        if not unit or r['pool'] not in pools:
            continue
        rules = eligibility.setdefault(unit, [])
        if r['faction_requirement'] or r['subculture_requirement']:
            rules.append(dict(faction=r['faction_requirement'], subculture=r['subculture_requirement']))
        else:
            rules.extend(dict(faction=f) for f in pool_factions.get(r['pool'], []))
    land = {r['key']: r for r in tables['land_units_tables']}
    ui_groups = {r['key']: r for r in tables['ui_unit_groupings_tables']}
    ui_parents = {r['key']: r for r in tables['ui_unit_group_parents_tables']}
    weapons = {r['key']: r for r in tables['melee_weapons_tables']}
    variants = {}
    for r in tables['unit_variants_tables']:
        if not r['faction'] or r['unit'] not in variants:
            variants[r['unit']] = r['unit_card']
    units = []
    for row in tables['main_units_tables']:
        if row['is_renown'] and row['land_unit'] in variants and not row['is_naval'] and row['caste'] not in ('lord', 'hero'):
            detail = land[row['land_unit']]
            # The battle UI grouping is distinct from land_units.category:
            # many monsters are internally inf_melee. Follow the real UI join.
            ui_group = row['ui_unit_group_land']
            parent_key = ui_groups.get(ui_group, {}).get('parent_group', 'other')
            parent = ui_parents.get(parent_key, {})
            weapon = weapons.get(detail['primary_melee_weapon'], {})
            candidates = ['ui/units/icons/' + variants.get(row['land_unit'], row['land_unit']) + '.png',
                          'ui/units/minspec_portholes/' + row['unit'] + '.png',
                          'ui/units/minspec_portholes/' + row['land_unit'] + '.png']
            icon = next((n for n in candidates if n in images), '')
            description = labels.get('unit_description_short_texts_text_' + detail['short_description_text'], '')
            stats = ('基础属性（不含领主、科技及面板加成）\n'
                     f"领导力 {detail['morale']}　近攻 {detail['melee_attack']}　近防 {detail['melee_defence']}\n"
                     f"冲锋 {detail['charge_bonus']}　武器伤害 {weapon.get('damage', 0)} + 破甲 {weapon.get('ap_damage', 0)}\n"
                     f"弹药 {detail['primary_ammo']}　基础维护费 {row['upkeep_cost']}\n")
            units.append(dict(recruitment_cost=row['recruitment_cost'], upkeep_cost=row['upkeep_cost'], id=row['unit'], native_key='wh3pt_ror_' + row['unit'], icon=icon, details=description + '\n' + stats, loc='land_units_onscreen_name_' + row['land_unit'],
                              label=labels.get('land_units_onscreen_name_' + row['land_unit'], ''),
                              category=parent_key,
                              category_label=labels.get('ui_unit_group_parents_onscreen_name_' + parent_key, '其他'),
                              category_order=parent.get('order', 999),
                              unit_type=labels.get('ui_unit_groupings_onscreen_' + ui_group, ''),
                              eligibility=eligibility.get(row['unit'], [])))
    return dict(resources=sorted(resources, key=lambda r: r['id']), units=sorted(units, key=lambda r: r['id']))


def native_recruitment_files(game, strategic):
    """Player-only pool entries, sharing native land units, art and unit sets.

    Separate main-unit keys avoid the original unit's rank/ritual/campaign caps
    without overriding global vanilla rows or granting anything to an army.
    Preserve DLC masks, cost, upkeep and all combat data.
    """
    keys = {u['id']: u['native_key'] for u in strategic['units']}
    # unit_variants.unit references LAND units. Our main units reuse the native
    # land_unit, so their native variants are already available; do not clone them.
    wanted = {'main_units_tables', 'unit_set_to_unit_junctions_tables'}
    result, grouped = {}, {}
    for name, data in read_pack(Path(game) / 'data/db.pack', lambda n: n.startswith('db\\') and n.split('\\')[1] in wanted).items():
        table = name.split('\\')[1]
        version, rows = decode(table, data)
        cloned = []
        for row in rows:
            field = 'unit_record' if table == 'unit_set_to_unit_junctions_tables' else 'unit'
            if row[field] not in keys:
                continue
            copy = dict(row)
            copy[field] = keys[row[field]]
            if table == 'main_units_tables':
                copy.update(campaign_cap=-1, additional_building_requirement=None, resource_requirement=None)
            cloned.append(copy)
        if table in grouped:
            assert grouped[table][0] == version, 'Conflicting native table versions'
            grouped[table][1].extend(cloned)
        else:
            grouped[table] = (version, cloned)
    for table, (version, cloned) in grouped.items():
        result['db\\' + table + '\\wh3pt_native_ror'] = encode(table, version, cloned)
    result['db\\mercenary_unit_groups_tables\\wh3pt_native_ror'] = encode('mercenary_unit_groups_tables', table_version(game, 'mercenary_unit_groups_tables'), [
        dict(key=k, unit_record=k, chance_to_replenish=0, max_count=0,
             max_replenish_per_turn=0, use_partial_replenishment=False, ui_order=i)
        for i, k in enumerate(keys.values())])
    # Explicit rank 0 for our entries; original rank/faction overrides stay intact.
    result['db\\campaign_mercenary_unit_character_level_restrictions_tables\\wh3pt_native_ror'] = encode(
        'campaign_mercenary_unit_character_level_restrictions_tables',
        table_version(game, 'campaign_mercenary_unit_character_level_restrictions_tables'),
        [dict(unit=k, character_level=0, faction_override=None) for k in keys.values()])
    validate_native_recruitment_files(game, result)
    return result


def validate_native_recruitment_files(game, files):
    """Check foreign keys in every recruitment table we generate, before packing.

    Binary round trips alone cannot detect main-unit vs land-unit reference errors.
    Unchanged native fields retain the source game's references.
    """
    wanted = {'main_units_tables', 'land_units_tables', 'unit_variants_tables'}
    base = {t: [] for t in wanted}
    for name, data in read_pack(Path(game) / 'data/db.pack', lambda n: n.startswith('db\\') and n.split('\\')[1] in wanted).items():
        base[name.split('\\')[1]].extend(decode(name.split('\\')[1], data)[1])
    generated = {}
    for name, data in files.items():
        if name.startswith('db\\'):
            table = name.split('\\')[1]
            generated.setdefault(table, []).extend(decode(table, data)[1])
    main = {r['unit'] for r in base['main_units_tables'] + generated.get('main_units_tables', [])}
    land = {r['key'] for r in base['land_units_tables']}
    variants = {r['unit'] for r in base['unit_variants_tables'] if not r['faction']}
    links = {
        'main_units_tables': ('land_unit', land),
        'unit_variants_tables': ('unit', land),
        'unit_set_to_unit_junctions_tables': ('unit_record', main),
        'mercenary_unit_groups_tables': ('unit_record', main),
        'campaign_mercenary_unit_character_level_restrictions_tables': ('unit', main),
    }
    for table, (field, targets) in links.items():
        for row in generated.get(table, []):
            value = row[field]
            if value is not None and value not in targets:
                raise ValueError(f'{table}.{field}: missing referenced record {value}')
    for row in generated.get('main_units_tables', []):
        if row['land_unit'] not in variants:
            raise ValueError('Missing native default land-unit variant: ' + row['land_unit'])
