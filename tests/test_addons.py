"""Regressions for the legacy shop's unsafe vanilla overrides and reward scope."""
from pathlib import Path
import hashlib
import tempfile
import unittest

from lupa.lua51 import LuaRuntime
from build_ingame import build_ingame
from database import decode, encode
from pack_io import read_pack
from shop_addon import ITEMS, SHOP_FILENAME, SHOP_SCRIPT, TABLE_FIELDS, REMOVED_MAGIC_EFFECT, decode_loc, safe_shop_entries
from trainer_core import GAME, Options, build
from ballistic_addon import BALLISTIC_FILENAME, PRIMARY_KEYS, NUMERIC_FIELDS, BOOLEAN_FIELDS, safe_ballistic_entries

from tests import ROOT, SRC, REFERENCE


def unpack(payload):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'test.pack'
        path.write_bytes(payload)
        return read_pack(path)


class AddonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = (REFERENCE / SHOP_FILENAME).read_bytes()
        cls.source = read_pack(REFERENCE / SHOP_FILENAME)
        cls.safe = safe_shop_entries(cls.source)

    def test_strict_allowlist_keeps_four_items_without_vanilla_overrides(self):
        self.assertEqual({n.split('\\')[1] for n in self.safe if n.startswith('db\\')}, set(TABLE_FIELDS))
        self.assertIn(SHOP_SCRIPT, self.safe)
        for name, data in self.safe.items():
            self.assertFalse(any(x in name for x in ('ritual', 'streak', 'uniqueness_groupings')))
            if name.startswith('db\\'):
                table = name.split('\\')[1]
                _, rows = decode(table, data)
                self.assertEqual({r[TABLE_FIELDS[table]] for r in rows}, set(ITEMS))
                self.assertEqual(len(rows), 19 if table == 'ancillary_to_effects_tables' else 4)

    def test_removed_magic_effect_is_dropped_without_a_different_flat_bonus(self):
        table = 'ancillary_to_effects_tables'
        rows_for = lambda files: [r for n, b in files.items() if n.startswith('db\\' + table + '\\') for r in decode(table, b)[1]]
        before, after = rows_for(self.source), rows_for(self.safe)
        removed = [r for r in before if r['effect'] == REMOVED_MAGIC_EFFECT]
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]['ancillary'], 'wh2_main_anc_armour_war_crown_of_saphery1')
        self.assertEqual(after, [r for r in before if r['effect'] != REMOVED_MAGIC_EFFECT])
        effects = [r for n,b in read_pack(Path(GAME)/'data/db.pack',lambda n:n.startswith('db\\effects_tables\\')).items()
                   for r in decode('effects_tables',b)[1]]
        self.assertNotIn(REMOVED_MAGIC_EFFECT, {r['effect'] for r in effects})
        self.assertNotIn('wh3_main_effect_winds_of_magic_pooled_resource_change_positive', {r['effect'] for r in after})

    def test_only_item_localisation_survives(self):
        locs = [b for n, b in self.safe.items() if n.endswith('.loc')]
        self.assertEqual(len(locs), 1)
        rows = decode_loc(locs[0])
        self.assertEqual({r[0] for r in rows}, {'ancillaries_onscreen_name_' + k for k in ITEMS})
        self.assertTrue(all(r[1] for r in rows))
        self.assertFalse(any(n.startswith('ui\\') for n in self.safe))

    def test_static_and_ingame_build_share_the_same_safe_conversion(self):
        static = unpack(build(GAME, Options(local_mods=[SHOP_FILENAME]), local_dir=REFERENCE)[0])
        ingame = unpack(build_ingame(addons={'shop': True}, local_dir=REFERENCE)[0])
        for files in (static, ingame):
            self.assertFalse(any(n.startswith(('db\\ritual', 'db\\campaign_streak', 'db\\ancillary_uniqueness')) for n in files))
            self.assertEqual(files[SHOP_SCRIPT], self.safe[SHOP_SCRIPT])
            self.assertEqual(files['text\\db\\wh3pt_kislev_prayer_rewards.loc'], self.safe['text\\db\\wh3pt_kislev_prayer_rewards.loc'])
        relevant = lambda files: {n: b for n, b in files.items() if n == SHOP_SCRIPT or n.startswith('text\\db\\wh3pt_kislev') or any(n.startswith('db\\' + t + '\\') for t in TABLE_FIELDS)}
        self.assertEqual(relevant(static), relevant(ingame))

    def test_missing_reward_data_fails_instead_of_loading_legacy_overrides(self):
        incomplete = {n: b for n, b in self.source.items() if not n.startswith('db\\ancillary_info_tables\\')}
        with self.assertRaisesRegex(ValueError, '缺少四件'):
            safe_shop_entries(incomplete)

    def test_duplicate_reward_definitions_are_rejected(self):
        duplicate = dict(self.source)
        name = next(n for n in duplicate if n.startswith('db\\ancillaries_tables\\'))
        version, rows = decode('ancillaries_tables', duplicate[name])
        duplicate[name] = encode('ancillaries_tables', version, rows + [rows[0]])
        with self.assertRaisesRegex(ValueError, '重复物品'):
            safe_shop_entries(duplicate)

    def test_native_start_event_rewards_legacy_and_current_prayers_to_human_kislev(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute('''
            grants={}; logs={}; listeners={}
            function out(s) logs[#logs+1]=s end
            core={add_listener=function(self,name,event,condition,callback,persistent)
                listeners[#listeners+1]={event=event,condition=condition,callback=callback,persistent=persistent}
            end}
            cm={add_ancillary_to_faction=function(self,faction,item,suppress)
                grants[#grants+1]={faction=faction:name(),item=item,suppress=suppress}
            end}
            function trigger(event,key,human,subculture)
                local faction={is_human=function() return human end,
                    subculture=function() return subculture end,name=function() return 'test_faction' end}
                local context={performing_faction=function() return faction end,
                    ritual=function() return {ritual_key=function() return key end} end}
                for _,listener in ipairs(listeners) do
                    if listener.event==event and listener.condition(context) then listener.callback(context) end
                end
            end
        ''')
        lua.execute(self.safe[SHOP_SCRIPT].decode('utf-8'))
        g = lua.globals()
        self.assertEqual(len(g.listeners), 1)
        self.assertEqual(g.listeners[1].event, 'RitualStartedEvent')
        self.assertTrue(g.listeners[1].persistent)
        for god, item in zip(('dazh', 'salyak', 'tor', 'ursun'), ITEMS):
            for suffix in ('', '_upgraded', '_force', '_province'):
                key = 'wh3_main_ritual_ksl_winter_' + god + suffix
                before = len(g.grants)
                g.trigger('RitualStartedEvent', key, True, 'wh3_main_sc_ksl_kislev')
                self.assertEqual(len(g.grants), before + 1)
                self.assertEqual(g.grants[len(g.grants)].item, item)
                self.assertFalse(g.grants[len(g.grants)].suppress)
                # Completion must not duplicate the immediate reward.
                g.trigger('RitualCompletedEvent', key, True, 'wh3_main_sc_ksl_kislev')
                self.assertEqual(len(g.grants), before + 1)
        for key, human, culture in (
            ('wh3_main_ritual_ksl_winter_dazh', False, 'wh3_main_sc_ksl_kislev'),
            ('wh3_main_ritual_ksl_winter_dazh', True, 'wh3_main_sc_kho_khorne'),
            ('wh3_main_ritual_kho_the_skull_throne', True, 'wh3_main_sc_kho_khorne'),
            ('unrelated_ritual', True, 'wh3_main_sc_ksl_kislev'),
        ):
            g.trigger('RitualStartedEvent', key, human, culture)
        self.assertEqual(len(g.grants), 16)

    def test_event_and_grant_api_match_current_native_scripts(self):
        scripts = read_pack(Path(GAME) / 'data/data_script.pack', lambda n: n.endswith(('wh3_narrative_khorne.lua', 'wh2_dlc12_tehenhauin.lua')))
        khorne = next(b.decode('utf-8') for n, b in scripts.items() if n.endswith('wh3_narrative_khorne.lua'))
        sotek = next(b.decode('utf-8') for n, b in scripts.items() if n.endswith('wh2_dlc12_tehenhauin.lua'))
        self.assertIn('"RitualStartedEvent"', khorne)
        self.assertIn('context:performing_faction():name()', khorne)
        self.assertIn('context:ritual():ritual_key()', khorne)
        self.assertIn('cm:add_ancillary_to_faction(context:performing_faction(), rand_anc, false)', sotek)

    def test_current_kislev_force_and_province_ritual_keys_exist(self):
        native = read_pack(Path(GAME) / 'data/db.pack',
                           lambda n: n.startswith('db\\rituals_tables\\'))
        keys = {r['key'] for blob in native.values()
                for r in decode('rituals_tables', blob)[1]}
        for god in ('dazh', 'salyak', 'tor', 'ursun'):
            for suffix in ('_force', '_province'):
                self.assertIn('wh3_main_ritual_ksl_winter_' + god + suffix, keys)
        scripts = read_pack(Path(GAME) / 'data/data_script.pack',
                            lambda n: n.endswith('wh3_campaign_kislev_devotion.lua'))
        source = next(iter(scripts.values())).decode('utf-8')
        self.assertIn('"MOTHERLAND_RITUAL_FORCE"', source)
        self.assertIn('"MOTHERLAND_RITUAL_PROVINCE"', source)
        self.assertIn('cm:set_public_order_of_province_for_region', source)

    def test_original_reference_pack_is_preserved(self):
        self.assertEqual(hashlib.sha256((REFERENCE / SHOP_FILENAME).read_bytes()).digest(), hashlib.sha256(self.original).digest())


class StaticRecoveryTests(unittest.TestCase):
    def test_resurrection_without_healing_rejected_before_pack_creation(self):
        options = Options(resurrection=True)
        with self.assertRaisesRegex(ValueError, '复活需要同时开启.*持续回血'):
            options.validate()
        with self.assertRaisesRegex(ValueError, '复活需要同时开启.*持续回血'):
            build(GAME, options, local_dir=REFERENCE)

    def test_resurrection_with_configured_healing_keeps_ammo_disabled(self):
        files = unpack(build(GAME, Options(regeneration=True, resurrection=True, heal_percent=2.5), local_dir=REFERENCE)[0])
        phases = [r for n, b in files.items() if n.startswith('db\\special_ability_phases_tables\\')
                  for r in decode('special_ability_phases_tables', b)[1]]
        self.assertEqual(len(phases), 1)
        self.assertAlmostEqual(phases[0]['heal_amount'], .025)
        self.assertTrue(phases[0]['resurrect'])
        self.assertEqual(phases[0]['replenish_ammo'], 0)

    def test_static_ammo_cycles_without_healing_and_mixed_mode_keeps_original_phase(self):
        rows_for=lambda files,table:[r for n,b in files.items() if n.startswith('db\\'+table+'\\') for r in decode(table,b)[1]]
        for options in [Options(ammunition=True,ammo_percent=20),
                        Options(ammunition=True,ammo_percent=20,regeneration=True,heal_percent=5),
                        Options(ammunition=True,ammo_percent=20,reload=True,reload_value=37)]:
            with self.subTest(options=options):
                files=unpack(build(GAME,options,local_dir=REFERENCE)[0])
                separate=options.regeneration or options.reload
                pulse_key='wh3pt_static_ammo' if separate else 'blood'
                phase=next(r for r in rows_for(files,'special_ability_phases_tables') if r['id']==pulse_key)
                ability=next(r for r in rows_for(files,'unit_special_abilities_tables') if r['key']==pulse_key)
                self.assertEqual(phase['duration'],1)
                self.assertAlmostEqual(phase['replenish_ammo'],.2)
                self.assertEqual(phase['heal_amount'],0)
                self.assertFalse(phase['resurrect'])
                self.assertEqual((ability['active_time'],ability['recharge_time'],ability['initial_recharge'],ability['num_uses']),(1,0,0,-1))
                self.assertEqual(ability['effect_range'],0)
                junction=next(r for r in rows_for(files,'special_ability_to_special_ability_phase_junctions_tables') if r['special_ability']==pulse_key)
                self.assertTrue(junction['target_self']);self.assertFalse(junction['target_friends']);self.assertFalse(junction['target_enemies'])
                if separate:
                    original=next(r for r in rows_for(files,'special_ability_phases_tables') if r['id']=='blood')
                    self.assertEqual(original['duration'],-1)
                    self.assertEqual(original['replenish_ammo'],0)
                    self.assertAlmostEqual(original['heal_amount'],.05 if options.regeneration else 0)
                    grants=rows_for(files,'effect_bonus_value_unit_ability_junctions_tables')
                    self.assertEqual({r['unit_ability'] for r in grants if r['effect']=='blood_returning'},{'blood',pulse_key})
                    stats=rows_for(files,'special_ability_phase_stat_effects_tables')
                    self.assertTrue(all(r['phase']=='blood' for r in stats))


class BallisticMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = (REFERENCE / BALLISTIC_FILENAME).read_bytes()
        cls.source = read_pack(REFERENCE / BALLISTIC_FILENAME)
        cls.safe, cls.report = safe_ballistic_entries(GAME, cls.source)
        cls.native = {}
        cls.legacy = {}
        cls.result = {}
        cls.native_versions = {}
        native = read_pack(Path(GAME)/'data/db.pack', lambda n:n.startswith('db\\') and n.split('\\')[1] in PRIMARY_KEYS)
        for entries, target in [(native, cls.native), (cls.source, cls.legacy), (cls.safe, cls.result)]:
            for name, data in entries.items():
                if not name.startswith('db\\'): continue
                table = name.split('\\')[1]
                version, rows = decode(table, data)
                if entries is native: cls.native_versions[table] = version
                target.setdefault(table, {}).update({r[PRIMARY_KEYS[table]]:r for r in rows})

    def test_existing_rows_keep_all_current_references_enums_and_new_fields(self):
        for table, rows in self.result.items():
            allowed = NUMERIC_FIELDS[table] | BOOLEAN_FIELDS[table]
            for key, result in rows.items():
                current, old = self.native[table][key], self.legacy[table][key]
                self.assertEqual(set(result), set(current))
                for field in current:
                    expected = old[field] if field in allowed and field in old else current[field]
                    self.assertEqual(result[field], expected, f'{table}/{key}/{field}')
        self.assertTrue(self.report['preserved_current_fields'])

    def test_stale_homing_is_replaced_by_its_current_native_value(self):
        table, key = 'projectiles_tables', 'wh2_dlc13_weapon_abilities_amber_bow'
        self.assertNotEqual(self.legacy[table][key]['homing_params'], self.native[table][key]['homing_params'])
        self.assertEqual(self.result[table][key]['homing_params'], self.native[table][key]['homing_params'])
        rocket = self.result[table]['wh3_main_cth_rocket']
        self.assertEqual(rocket['effective_range'], 360)
        self.assertEqual(self.native[table]['wh3_main_cth_rocket']['effective_range'], 400)
        self.assertFalse(rocket['can_damage_allies'])

    def test_legacy_only_keys_are_reported_and_not_fabricated(self):
        self.assertEqual({t:len(v) for t,v in self.report['unmigrated_legacy_keys'].items()},
                         {'projectiles_tables':4, 'projectiles_explosions_tables':2})
        for table, keys in self.report['unmigrated_legacy_keys'].items():
            self.assertTrue(set(keys).isdisjoint(self.result[table]))
            self.assertTrue(set(keys).isdisjoint(self.native[table]))
        self.assertFalse(self.report['author_change_history_available'])

    def test_static_and_dynamic_use_identical_current_version_replacements(self):
        static, report = build(GAME, Options(local_mods=[BALLISTIC_FILENAME]), local_dir=REFERENCE)
        dynamic, _ = build_ingame(addons={'ballistic':True}, local_dir=REFERENCE)
        tables = lambda files: {n.split('\\')[1]:(decode(n.split('\\')[1],b)) for n,b in files.items()
                                if n.startswith('db\\') and n.split('\\')[1] in PRIMARY_KEYS}
        self.assertEqual(tables(unpack(static)), tables(unpack(dynamic)))
        self.assertEqual({t:v[0] for t,v in tables(unpack(static)).items()}, self.native_versions)
        self.assertEqual(len(report['ballistic_migrations']), 1)
        self.assertTrue(any('6 个' in w for w in report['warnings']))
        self.assertEqual((REFERENCE / BALLISTIC_FILENAME).read_bytes(), self.original)

    def test_nonfinite_legacy_parameter_is_rejected(self):
        bad = dict(self.source)
        name = next(n for n in bad if n.startswith('db\\projectiles_tables\\'))
        version, rows = decode('projectiles_tables', bad[name])
        row = next(r for r in rows if r['key'] in self.native['projectiles_tables'])
        row['muzzle_velocity'] = float('nan')
        bad[name] = encode('projectiles_tables', version, rows)
        with self.assertRaisesRegex(ValueError, '有限数值'):
            safe_ballistic_entries(GAME, bad)


    def test_duplicate_vortex_key_is_rejected(self):
        bad = dict(self.source)
        name = next(n for n in bad if n.startswith('db\\battle_vortexs_tables\\'))
        version, rows = decode('battle_vortexs_tables', bad[name])
        bad[name] = encode('battle_vortexs_tables', version, rows + [dict(rows[0])])
        with self.assertRaisesRegex(ValueError, '重复键'):
            safe_ballistic_entries(GAME, bad)


class PanelStringCompatibilityTests(unittest.TestCase):
    def test_ascii_dispatch_and_literal_search_with_standard_or_native_replacements(self):
        files = unpack(build_ingame()[0])
        panel = files['script\\campaign\\mod\\wh3pt_panel.lua'].decode('utf-8')
        for native_replacements in [False, True]:
            with self.subTest(native_replacements=native_replacements):
                lua = LuaRuntime(unpack_returned_tuples=True)
                lua.execute((ROOT/'tests/runtime_mock.lua').read_text(encoding='utf-8'))
                if native_replacements:
                    lua.execute('native_sub_calls=0; string.sub=function(...) native_sub_calls=native_sub_calls+1; error("native substring must not parse trainer IDs") end')
                else:
                    lua.execute('string.find=string.find_lua; string.find_lua=nil; string.sub_lua=nil; native_sub_calls=0')
                api = lua.execute(panel)
                g = lua.globals()
                g.first_tick()
                # One ordinary localized action and malformed short/prefix-only
                # events exercise routing without invoking native UTF-8 slicing.
                lua.execute("core:trigger_event('ComponentLClickUp',{string='x'}); core:trigger_event('ComponentLClickUp',{string='wh3pt_'}); core:trigger_event('ComponentLClickUp',{string='wh3pt_tab_战斗'})")
                self.assertEqual(g.widgets['wh3pt_tab_战斗'].state, 'selected')
                api.click('tab_战斗'); api.click('search_open')
                def rows():
                    return [w.id for w in g.widgets.values() if w.id.startswith('wh3pt_row_') and not w.dead
                            and not w.id.startswith('wh3pt_row_section_')]
                g.widgets['wh3pt_search'].text = '['
                api.click('search_go')
                self.assertEqual(rows(), [])
                g.widgets['wh3pt_search'].text = '生命'
                api.click('search_go')
                self.assertEqual(len(rows()), 1)
                self.assertEqual(g.native_sub_calls, 0)

if __name__ == '__main__':
    unittest.main()
