"""Native recurring-income effects; never modify transactions or expenses."""
from pathlib import Path
from hashlib import sha256
from pack_io import read_pack
from database import decode, encode, table_version

SOURCE_TABLES = frozenset({'effects_tables', 'pooled_resource_factor_junctions_tables',
                           'effect_bonus_value_pooled_resource_factor_junctions_tables'})


def native_income_files(game, strategic):
    wanted = SOURCE_TABLES
    tables = {k: [] for k in wanted}
    versions = {}
    for name, data in read_pack(Path(game) / 'data/db.pack', lambda n: n.startswith('db\\') and n.split('\\')[1] in wanted).items():
        table = name.split('\\')[1]
        version, rows = decode(table, data)
        versions[table] = version
        tables[table].extend(rows)
    effects = {r['effect']: r for r in tables['effects_tables']}
    assert 'wh_main_effect_economy_gdp_mod_all' in effects, 'wh_main_effect_economy_gdp_mod_all'
    factors = {r['unique_id']: r for r in tables['pooled_resource_factor_junctions_tables']}
    recurring = {}
    for row in tables['effect_bonus_value_pooled_resource_factor_junctions_tables']:
        factor = factors[row['resource_factor']]
        # Positive recurring base production only: exclude battle rewards,
        # refunds, recruitment/construction payments and mixed-sign upkeep.
        if row['bonus_value_id'] == 'base_amount' and factor['minimum'] >= 0 and factor['maximum'] > 0:
            recurring.setdefault(factor['resource'], set()).add(row['resource_factor'])
    new_effects, junctions = [], []
    for resource in strategic['resources']:
        factor_keys = recurring.get(resource['id'], set())
        if not factor_keys:
            continue
        key = 'wh3pt_income_' + sha256(resource['id'].encode()).hexdigest()[:16]
        resource['income_effect'] = key
        resource['income_scope'] = 'faction_to_region_own_regions' if resource.get('region_group') else 'faction_to_faction_own'
        new_effects.append(dict(effects['wh_main_effect_economy_gdp_mod_all'], effect=key))
        for factor_key in sorted(factor_keys):
            junctions.append(dict(bonus_value_id='percentage_multiplier_post_mod', effect=key, resource_factor=factor_key))
    bundle = dict(key='wh3pt_income', localised_title='每回合收入倍率', localised_description='',
                  bundle_target='faction', priority=0, ui_icon='', is_global_effect=False,
                  show_in_3d_space=False, owner_only=True)
    return {
        'db\\effects_tables\\wh3pt_income': encode('effects_tables', versions['effects_tables'], new_effects),
        'db\\effect_bonus_value_pooled_resource_factor_junctions_tables\\wh3pt_income': encode(
            'effect_bonus_value_pooled_resource_factor_junctions_tables', versions['effect_bonus_value_pooled_resource_factor_junctions_tables'], junctions),
        'db\\effect_bundles_tables\\wh3pt_income': encode('effect_bundles_tables', table_version(game, 'effect_bundles_tables'), [bundle]),
    }
