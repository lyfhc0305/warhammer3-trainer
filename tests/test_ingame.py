from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from lupa.lua51 import LuaRuntime
from tests import ROOT, SRC, REFERENCE
from build_ingame import build_ingame, mock_font_lua
from trainer_core import GAME
from game_compat import game_version
from pack_io import read_pack
from database import decode
from ingame_installer import verify_game
from ui_engine import FONT_SIZES, compensate, engine_rgb

RESOLUTIONS = [(1280, 720), (1920, 1080), (2560, 1440)]
TEXT_SCALES = [1.0, 1.5, 2.0]
VIEWS = ['常用', '指令', '战役', '部队', '特殊', '收藏', '全部', 'sheet', 'preset', 'collapsed']


class InGameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload,cls.report=build_ingame()
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'test.pack'; p.write_bytes(cls.payload)
            cls.files=read_pack(p)
            assert verify_game(GAME,p)==cls.payload

    def setUp(self):
        self.lua=LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute((ROOT/'tests/runtime_mock.lua').read_text(encoding='utf-8'))
        self.lua.globals().known_bundle_keys=self.lua.table_from({r['key']:True for n,d in self.files.items() if n.startswith('db\\effect_bundles_tables\\') for r in decode('effect_bundles_tables',d)[1]})
        self.lua.execute(mock_font_lua())
        self.api=self.lua.execute(self.files['script\\campaign\\mod\\wh3pt_panel.lua'].decode('utf-8'))
        self.g=self.lua.globals()
        self.g.first_tick()
        self.assertEqual(len(self.g.logs),0)

    def tearDown(self):
        self.assertEqual(list(self.g.stale_ui_calls.values()),[])

    def feature(self,key):
        return next(v for v in self.api.catalog.values() if v.id==key)

    def strategic_fixture(self):
        self.lua.execute("""
            resource_value, reject_resource, reject_recruit, army_count = 20, false, false, 1
            recruited = {}
            local f = cm:get_faction('player')
            function f:subculture() return 'wh3_main_sc_ksl_kislev' end
            function f:pooled_resource_manager()
                return {resource=function(_, key)
                    return {is_null_interface=function() return key ~= 'wh3_main_ksl_devotion' end,
                        value=function() return resource_value end}
                end}
            end
            function cm:faction_add_pooled_resource(name,key,factor,delta)
                assert(name=='player' and key=='wh3_main_ksl_devotion')
                if not reject_resource then resource_value=resource_value+delta end
            end
            army={is_null_interface=function() return false end,is_armed_citizenry=function() return false end}
            function army:unit_list()
                return {num_items=function() return army_count end,item_at=function(_,i)
                    return {unit_key=function() return recruited[i] or 'general' end}
                end}
            end
            function characters[1]:character_type(kind) return kind=='general' end
        """.replace("function characters[1]:character_type(kind) return kind=='general' end",
                    "characters[1].character_type=function(_,kind) return kind=='general' end"))
        self.lua.execute("""
            characters[1].military_force=function() return army end
            characters[2].character_type=function() return false end
            function cm:grant_unit_to_character(lookup,key)
                assert(lookup=='character_cqi:101')
                if not reject_recruit then recruited[army_count]=key; army_count=army_count+1 end
            end
            core:trigger_event('CharacterSelected', {character=function() return characters[1] end})
        """)

    def test_special_resource_set_readback_and_invalid_requests(self):
        self.strategic_fixture()
        index=next(i for i,d in self.api.strategic.resources.items() if d.id=='wh3_main_ksl_devotion')
        self.api.set_resource(index,'1000'); self.g.flush()
        self.assertEqual(self.g.resource_value,1000)
        for raw in ['nan','1.5','-101','2147483648']:
            with self.assertRaises(Exception): self.api.set_resource(index,raw)
        self.g.reject_resource=True
        self.api.set_resource(index,'2000'); self.g.flush()
        self.assertTrue(any('实际 1000' in line for line in self.g.logs.values()))
        self.g.multiplayer=True
        with self.assertRaises(Exception): self.api.set_resource(index,'3000')

    def native_flush(self):
        self.g.flush(); self.g.flush()

    def test_native_pool_toggle_refill_cross_faction_and_disable(self):
        self.strategic_fixture()
        index=next(i for i,d in self.api.strategic.units.items() if '_tmb_' in d.id)
        unit=self.api.strategic.units[index]
        self.api.native_toggle(True); self.native_flush()
        owned=[d for d in self.api.strategic.units.values() if self.api.regiment_belongs(d,self.g.cm.get_faction(self.g.cm,'player'))]
        self.assertGreater(len(owned),0)
        self.assertEqual(len(self.g.native_pool),0)  # map has no Lua array entries
        self.assertEqual(len(list(self.g.native_pool.keys())),len(owned))
        self.assertIsNone(self.g.native_pool[unit.native_key])
        self.api.native_select(index); self.native_flush()
        self.assertEqual(self.g.native_pool[unit.native_key].count,20)
        self.assertEqual(self.g.army_count,1)
        # Repeated native recruitment events replenish stock, without creating duplicates.
        count=len(list(self.g.native_pool.keys()))
        self.g.native_pool[unit.native_key].count=1
        self.lua.execute("core:trigger_event('UnitTrained', {})")
        self.native_flush()
        self.assertEqual(self.g.native_pool[unit.native_key].count,20)
        self.assertEqual(len(list(self.g.native_pool.keys())),count)
        self.assertIsNotNone(self.g.bundles['wh3pt_native_ror'])
        self.api.native_select(index); self.native_flush()
        self.assertEqual(self.g.native_pool[unit.native_key].count,0)
        self.api.clear(); self.native_flush(); self.g.flush()
        self.assertTrue(all(p.count==0 and p.max==0 for p in self.g.native_pool.values()))
        self.assertIsNone(self.g.bundles['wh3pt_native_ror'])
        self.assertEqual(self.g.army_count,1)
        self.assertEqual(len(self.g.logs),0)
        self.g.multiplayer=True
        with self.assertRaises(Exception): self.api.native_toggle(True)

    def test_ror_cap_requires_real_bundle_definition_and_effect_readback(self):
        self.strategic_fixture()
        self.assertTrue(self.g.known_bundle_keys['wh3pt_native_ror'])
        self.api.native_toggle(True);self.native_flush()
        effects=self.g.bundles['wh3pt_native_ror'].entries
        self.assertTrue(any(e.key()=='wh3_main_effect_recruitment_cap_ror_units' and e.value()>=9999 for e in effects.values()))
        self.g.known_bundle_keys['wh3pt_native_ror']=None
        self.api.native_sync(False);self.native_flush()
        self.assertTrue(any('缺少精英上限效果定义' in line for line in self.g.logs.values()))
        self.assertFalse(any("set_duration" in line for line in self.g.logs.values()))

    def test_shared_land_unit_elites_are_in_catalogue(self):
        ids={d.id for d in self.api.strategic.units.values()}
        self.assertIn('wh2_dlc17_kho_mon_ghorgon_ror_0',ids)
        self.assertIn('wh3_dlc27_bst_mon_chimera_ror',ids)
        self.assertIn('wh3_dlc29_bst_mon_giant_spined_chaos_beast_ror',ids)

    def test_duplicate_native_cards_collapsed_and_restored(self):
        self.strategic_fixture()
        unit=next(d for d in self.api.strategic.units.values() if self.api.regiment_belongs(d,self.g.cm.get_faction(self.g.cm,'player')))
        self.g.test_original=unit.id;self.g.test_unlocked=unit.native_key
        self.lua.execute("""
            local holder=root:CreateComponent('mercenary_display','ui/wh3pt/label')
            original_card=holder:CreateComponent(test_original,'ui/wh3pt/label')
            unrelated_card=holder:CreateComponent('unrelated_unit','ui/wh3pt/label')
            function find_uicomponent(...) return holder end
        """)
        self.api.native_toggle(True);self.native_flush()
        self.assertTrue(self.g.original_card.visible)  # no counterpart yet
        self.lua.execute("unlocked_card=original_card.parent:CreateComponent(test_unlocked,'ui/wh3pt/label')")
        self.api.native_filter()
        self.assertFalse(self.g.original_card.visible)
        self.assertTrue(self.g.unlocked_card.visible)
        self.assertTrue(self.g.unrelated_card.visible)
        badge=self.g.unlocked_card.Find(self.g.unlocked_card,'wh3pt_unlocked_badge')
        self.assertEqual(badge.text,'已解锁')
        self.assertTrue(badge.visible)
        self.api.native_filter()
        self.assertEqual(self.g.unlocked_card.ChildCount(self.g.unlocked_card),1)
        self.api.native_toggle(False);self.native_flush()
        self.assertTrue(self.g.original_card.visible)
        self.assertFalse(badge.visible)

    def test_native_dedup_uses_record_context_outside_scroll_clip(self):
        self.strategic_fixture()
        unit=next(d for d in self.api.strategic.units.values() if self.api.regiment_belongs(d,self.g.cm.get_faction(self.g.cm,'player')))
        self.g.test_original=unit.id;self.g.test_unlocked=unit.native_key
        self.lua.execute("""
            local holder=root:CreateComponent('mercenary_display','ui/wh3pt/label')
            local list=holder:CreateComponent('list_box','ui/wh3pt/label')
            original_card=list:CreateComponent('generated_original_entry','ui/wh3pt/label')
            unlocked_card=list:CreateComponent('generated_unlocked_entry','ui/wh3pt/label')
            local original_icon=original_card:CreateComponent('unit_icon','ui/wh3pt/label')
            local unlocked_icon=unlocked_card:CreateComponent('unit_icon','ui/wh3pt/label')
            local function record(key) return {Call=function(_,field) assert(field=='Key');return key end} end
            function original_icon:GetContextObject(kind) assert(kind=='CcoMainUnitRecord');return record(test_original) end
            function unlocked_icon:GetContextObject(kind) assert(kind=='CcoMainUnitRecord');return record(test_unlocked) end
            function unlocked_card:Visible(check_clip) return not check_clip end
            function find_uicomponent(...) return holder end
        """)
        self.api.native_toggle(True);self.native_flush()
        self.assertFalse(self.g.original_card.visible)
        self.assertTrue(self.g.unlocked_card.visible)
        self.assertEqual(self.g.unlocked_card.Find(self.g.unlocked_card,'wh3pt_unlocked_badge').text,'已解锁')
        self.api.native_toggle(False);self.native_flush()
        self.assertTrue(self.g.original_card.visible)

    def test_cross_faction_sisters_addition_does_not_read_invalidated_pool_objects(self):
        self.strategic_fixture()
        self.api.native_toggle(True);self.native_flush()
        index=next(i for i,d in self.api.strategic.units.items() if 'everqueens_court_guards' in d.id)
        self.api.native_select(index);self.native_flush()
        self.assertEqual(self.g.native_pool[self.api.strategic.units[index].native_key].count,20)
        self.assertFalse(any('stale native pool interface' in s for s in self.g.logs.values()))
        self.api.native_toggle(False);self.native_flush()
        self.assertTrue(all(p.count==0 for p in self.g.native_pool.values()))

    def test_native_duplicate_restored_when_replacement_disappears(self):
        self.strategic_fixture()
        unit=next(d for d in self.api.strategic.units.values() if self.api.regiment_belongs(d,self.g.cm.get_faction(self.g.cm,'player')))
        self.g.test_original=unit.id;self.g.test_unlocked=unit.native_key
        self.lua.execute("""
            local holder=root:CreateComponent('mercenary_display','ui/wh3pt/label')
            original_card=holder:CreateComponent(test_original,'ui/wh3pt/label')
            unlocked_card=holder:CreateComponent(test_unlocked,'ui/wh3pt/label')
            unlocked_card:SetVisible(false)
            function find_uicomponent(...) return holder end
        """)
        self.api.native_toggle(True);self.native_flush()
        self.assertTrue(self.g.original_card.visible)  # invisible replacement is insufficient
        self.g.unlocked_card.SetVisible(self.g.unlocked_card,True)
        self.api.native_filter()
        self.assertFalse(self.g.original_card.visible)
        self.g.unlocked_card.SetVisible(self.g.unlocked_card,False)
        self.api.native_filter()
        self.assertTrue(self.g.original_card.visible)
        self.g.unlocked_card.SetVisible(self.g.unlocked_card,True)
        self.api.native_filter()
        self.assertFalse(self.g.original_card.visible)
        self.lua.execute("unlocked_card.id='removed_record'")
        self.api.native_filter()
        self.assertTrue(self.g.original_card.visible)

    def test_native_dedup_does_not_restore_cards_hidden_by_game(self):
        self.strategic_fixture()
        unit=next(d for d in self.api.strategic.units.values() if self.api.regiment_belongs(d,self.g.cm.get_faction(self.g.cm,'player')))
        self.g.test_original=unit.id;self.g.test_unlocked=unit.native_key
        self.lua.execute("""
            local holder=root:CreateComponent('mercenary_display','ui/wh3pt/label')
            original_card=holder:CreateComponent(test_original,'ui/wh3pt/label')
            original_card:SetVisible(false)
            holder:CreateComponent(test_unlocked,'ui/wh3pt/label')
            function find_uicomponent(...) return holder end
        """)
        self.api.native_toggle(True);self.native_flush()
        self.api.native_toggle(False);self.native_flush()
        self.assertFalse(self.g.original_card.visible)

    def test_native_card_rebuild_does_not_touch_old_ui_handles(self):
        self.strategic_fixture()
        unit=next(d for d in self.api.strategic.units.values() if self.api.regiment_belongs(d,self.g.cm.get_faction(self.g.cm,'player')))
        self.g.test_original=unit.id;self.g.test_unlocked=unit.native_key
        self.lua.execute("""
            holder=root:CreateComponent('mercenary_display','ui/wh3pt/label')
            original_card=holder:CreateComponent(test_original,'ui/wh3pt/label')
            holder:CreateComponent(test_unlocked,'ui/wh3pt/label')
            function find_uicomponent(...) return holder end
        """)
        self.api.native_toggle(True);self.native_flush()
        self.lua.execute("""
            function original_card:SetVisible(v) stale_touched=true;error('destroyed UI handle') end
            holder=root:CreateComponent('mercenary_display','ui/wh3pt/label')
            fresh_original=holder:CreateComponent(test_original,'ui/wh3pt/label')
            holder:CreateComponent(test_unlocked,'ui/wh3pt/label')
        """)
        self.api.native_filter()
        self.assertFalse(self.g.stale_touched)
        self.assertFalse(self.g.fresh_original.visible)
        self.api.native_toggle(False);self.native_flush()
        self.assertTrue(self.g.fresh_original.visible)
        self.assertFalse(self.g.stale_touched)

    def test_all_native_mortarch_reward_regiments_have_unrestricted_counterparts(self):
        # Read the game's actual lock list instead of maintaining a shorter,
        # hand-picked unit list which could silently omit conditional rewards.
        native=read_pack(Path(GAME)/'data/data_script.pack',lambda n:n.endswith('wh3_dlc29_nagash_rors.lua'))
        self.assertEqual(len(native),1)
        self.lua.execute(next(iter(native.values())).decode('utf-8-sig'))
        config=self.g.nagash_rors
        self.assertEqual(len(list(config.rituals.keys())),3)
        expected={'wh2_dlc09_tmb_followers_of_nagash':8,
                  'wh_main_vmp_vampire_counts':9,'wh2_dlc11_cst_vampire_coast':9}
        catalog={d.id:d for d in self.api.strategic.units.values()}
        main={r['unit']:r for n,d in self.files.items() if n.startswith('db\\main_units_tables\\') for r in decode('main_units_tables',d)[1]}
        ranks={r['unit']:r for n,d in self.files.items() if n.startswith('db\\campaign_mercenary_unit_character_level_restrictions_tables\\') for r in decode('campaign_mercenary_unit_character_level_restrictions_tables',d)[1]}
        self.strategic_fixture()
        self.lua.execute("test_nag_faction=cm:get_faction('player'); function test_nag_faction:subculture() return 'wh3_dlc29_sc_nag_undead_legions' end")
        self.api.native_toggle(True);self.native_flush()
        all_keys=[]
        for group,units in config.unit_locks.items():
            self.assertEqual(len(units),expected[group])
            for key in units.values():
                all_keys.append(key)
                definition=catalog[key]
                self.assertTrue(self.api.regiment_belongs(definition,self.g.test_nag_faction),key)
                row=main[definition.native_key]
                self.assertEqual(row['campaign_cap'],-1,key)
                self.assertIsNone(row['additional_building_requirement'],key)
                self.assertIsNone(row['resource_requirement'],key)
                self.assertEqual(ranks[definition.native_key]['character_level'],0,key)
                self.assertEqual(self.g.native_pool[definition.native_key].count,20,key)
        self.assertEqual(len(set(all_keys)),26)
        self.assertIn('wh3_dlc29_tmb_mon_khemric_titan_ror',all_keys)

    def test_native_pool_readback_failure_and_saved_reload(self):
        self.strategic_fixture(); self.g.reject_pool=True
        self.api.native_toggle(True); self.native_flush()
        self.assertTrue(any('招募池回读不一致' in line for line in self.g.logs.values()))
        self.g.reject_pool=False
        self.api.native_toggle(True); self.native_flush()
        index=next(i for i,d in self.api.strategic.units.items() if '_tmb_' in d.id)
        self.api.native_select(index); self.native_flush()
        key=self.api.strategic.units[index].native_key
        self.g.native_pool[key].count=0
        self.g.first_tick(); self.native_flush()
        self.assertEqual(self.g.native_pool[key].count,20)

    def test_native_recruitment_foreign_key_validation_rejects_missing_land_unit(self):
        from strategic_data import validate_native_recruitment_files
        from database import encode
        files={n:d for n,d in self.files.items() if n.endswith('wh3pt_native_ror')}
        validate_native_recruitment_files(GAME,files)
        native_variants=next(iter(read_pack(Path(GAME)/'data/db.pack',lambda n:n.startswith('db\\unit_variants_tables\\')).values()))
        version,rows=decode('unit_variants_tables',native_variants)
        broken=dict(rows[0],unit=self.api.strategic.units[1].native_key)
        files['db\\unit_variants_tables\\bad_link']=encode('unit_variants_tables',version,[broken])
        with self.assertRaisesRegex(ValueError,'unit_variants_tables.unit: missing'):
            validate_native_recruitment_files(GAME,files)
        del files['db\\unit_variants_tables\\bad_link']
        path='db\\main_units_tables\\wh3pt_native_ror'
        version,rows=decode('main_units_tables',files[path])
        rows[0]['land_unit']='missing_land_unit'
        files[path]=encode('main_units_tables',version,rows)
        with self.assertRaisesRegex(ValueError,'main_units_tables.land_unit: missing'):
            validate_native_recruitment_files(GAME,files)

    def test_home_resources_stay_with_gold_at_supported_scales(self):
        self.strategic_fixture()
        for width,height in RESOLUTIONS:
            for scale in TEXT_SCALES:
                self.g.screen_width,self.g.screen_height,self.g.engine_text_scale=width,height,scale
                self.g.first_tick();self.api.click('open');self.api.click('tab_常用')
                panel=self.g.widgets['wh3pt_panel'];gold=self.g.widgets['wh3pt_gold_val']
                resource=self.g.widgets['wh3pt_home_resource_card']
                self.assertGreaterEqual(resource.y,gold.y+gold.h)
                self.assertLessEqual(resource.y+resource.h,panel.y+panel.h)
                value=self.g.widgets['wh3pt_home_resource_value']
                value.SetStateText(value,'123')
                index=next(i for i,d in self.api.strategic.resources.items() if d.id=='wh3_main_ksl_devotion')
                self.api.click('resource_do_'+str(index));self.g.flush()
                self.assertEqual(self.g.resource_value,123)

    def test_native_records_keep_unit_art_stats_dlc_and_do_not_override_vanilla(self):
        table=lambda t:decode(t,self.files['db\\'+t+'\\wh3pt_native_ror'])[1]
        main=table('main_units_tables')
        self.assertNotIn('db\\unit_variants_tables\\wh3pt_native_ror',self.files)
        self.assertEqual(len(main),len(self.api.strategic.units))
        self.assertGreater(len(main),250)
        self.assertTrue(all(r['unit'].startswith('wh3pt_ror_') and r['campaign_cap']==-1 for r in main))
        self.assertTrue(all(r['character_level']==0 for r in table('campaign_mercenary_unit_character_level_restrictions_tables')))
        original={r['unit']:r for d in read_pack(Path(GAME)/'data/db.pack',lambda n:n.startswith('db\\main_units_tables\\')).values() for r in decode('main_units_tables',d)[1]}
        for r in main:
            source=original[r['unit'].removeprefix('wh3pt_ror_')]
            for field in ['land_unit','special_edition_mask','recruitment_cost','upkeep_cost','is_renown']:
                self.assertEqual(r[field],source[field])
        script=self.files['script\\campaign\\mod\\wh3pt_panel.lua'].decode('utf-8')
        self.assertNotIn('grant_unit_to_character',script)

    def test_strategic_pages_stay_bounded_and_filter_owned_resources(self):
        self.strategic_fixture()
        self.api.click('open'); self.api.click('tab_常用')
        self.assertIn('虔信',self.g.widgets['wh3pt_home_resource_name'].text)
        self.assertIsNone(self.g.widgets['wh3pt_tab_资源'])
        self.assertGreater(self.g.widgets['wh3pt_home_resource_card'].y,self.g.widgets['wh3pt_gold_val'].y)
        self.api.click('tab_精英'); self.api.click('strategy_filter')
        self.assertIn(str(len(self.api.strategic.units))+' 项',self.g.widgets['wh3pt_strategy_pages'].text)
        first=self.g.widgets['wh3pt_strategy_name_1'].text
        self.api.click('strategy_next')
        self.assertNotEqual(first,self.g.widgets['wh3pt_strategy_name_1'].text)
        panel=self.g.widgets['wh3pt_panel']
        for w in self.live():
            if w.id.startswith('wh3pt_strategy_'):
                self.assertGreaterEqual(w.y,panel.y)
                self.assertLessEqual(w.y+w.h,panel.y+panel.h)

    def test_regiment_categories_follow_native_ui_not_land_combat_category(self):
        units={u.id:u for u in self.api.strategic.units.values()}
        self.assertEqual(units['wh2_dlc09_tmb_mon_necrosphinx_ror'].category,'monster_beasts')
        sisters=next(u for u in units.values() if 'everqueens_court_guards' in u.id)
        self.assertEqual(sisters.category,'missile_infantry')
        self.assertEqual(sisters.category_label,'远程步兵')
        self.assertEqual(units['wh2_dlc11_cst_mon_necrofex_colossus_ror_0'].category,'missile_monster_beasts')
        self.assertTrue(all(u.category and u.category!='other' and u.category_label!='其他' for u in units.values()))

    def test_regiment_category_filter_search_and_recruitment_keep_original_index(self):
        self.strategic_fixture();self.api.click('open');self.api.click('tab_精英')
        self.api.click('strategy_filter');self.api.click('strategy_categories')
        count=sum(u.category=='missile_infantry' for u in self.api.strategic.units.values())
        self.assertIn(str(count),self.g.widgets['wh3pt_strategycat_missile_infantry'].text)
        self.api.click('strategycat_missile_infantry')
        self.assertIn(str(count)+' 项',self.g.widgets['wh3pt_strategy_pages'].text)
        for w in self.live():
            if w.id.startswith('wh3pt_strategy_do_'):
                self.assertEqual(self.api.strategic.units[int(w.id.rsplit('_',1)[1])].category,'missile_infantry')
        self.api.click('strategy_next')
        self.g.widgets['wh3pt_search'].SetStateText(self.g.widgets['wh3pt_search'],'阿瓦隆')
        self.api.click('search_go')
        self.assertIn('1/1',self.g.widgets['wh3pt_strategy_pages'].text)
        index=next(i for i,u in self.api.strategic.units.items() if 'everqueens_court_guards' in u.id)
        self.assertIn('阿瓦隆',self.g.widgets['wh3pt_strategy_name_1'].text)
        self.api.native_toggle(True);self.native_flush()
        self.api.click('strategy_do_'+str(index));self.native_flush()
        self.assertEqual(self.g.native_pool[self.api.strategic.units[index].native_key].count,20)
        self.api.click('strategycat_monster_beasts')
        self.assertIn('0 项',self.g.widgets['wh3pt_strategy_pages'].text)
        self.api.click('search_clear')
        self.assertNotIn('0 项',self.g.widgets['wh3pt_strategy_pages'].text)
        self.api.click('strategy_filter')
        self.assertIn('全部类别',self.g.widgets['wh3pt_strategy_categories'].text)

    def test_regiment_cost_sort_descending_and_upkeep_sort_keep_native_indices(self):
        self.strategic_fixture();self.api.click('open');self.api.click('tab_精英');self.api.click('strategy_filter')
        for field in ('recruitment_cost','upkeep_cost'):
            cards=sorted((w for w in self.live() if w.id.startswith('wh3pt_strategy_do_')),key=lambda w:w.y)
            actual=[self.api.strategic.units[int(w.id.rsplit('_',1)[1])][field] for w in cards]
            expected=sorted((u[field] for u in self.api.strategic.units.values()),reverse=True)[:5]
            self.assertEqual(actual,expected)
            self.assertIn('招募',self.g.widgets['wh3pt_strategy_info_1'].text)
            self.assertIn('维护',self.g.widgets['wh3pt_strategy_info_1'].text)
            self.api.click('strategy_sort')

    def test_regiment_all_groups_cover_every_unit_once_and_menu_fits(self):
        self.strategic_fixture();self.api.click('open');self.api.click('tab_精英');self.api.click('strategy_filter')
        self.api.click('strategy_sort');self.api.click('strategy_sort')
        found=[]
        import math
        for _ in range(math.ceil(len(self.api.strategic.units)/5)):
            cards=sorted((w for w in self.live() if w.id.startswith('wh3pt_strategy_do_')),key=lambda w:w.y)
            page=[int(w.id.rsplit('_',1)[1]) for w in cards]
            found.extend(page)
            self.api.click('strategy_next')
        self.assertEqual(len(found),len(self.api.strategic.units))
        self.assertEqual(set(found),set(self.api.strategic.units.keys()))
        order=[self.api.strategic.units[i].category_order for i in found]
        self.assertEqual(order,sorted(order))
        self.api.click('strategy_categories')
        panel=self.g.widgets['wh3pt_panel']
        for w in self.live():
            if w.id.startswith('wh3pt_strategycat_'):
                self.assertGreaterEqual(w.x,panel.x)
                self.assertLessEqual(w.x+w.w,panel.x+panel.w)
                self.assertLessEqual(w.y+w.h,panel.y+panel.h)
                self.assertEqual(w.priority,230)

    def test_nagash_uses_native_shared_regiments_and_localised_resource_pool(self):
        self.strategic_fixture()
        self.lua.execute("""
            nag_faction=cm:get_faction('player')
            function nag_faction:name() return 'wh3_dlc29_nag_host_of_nagash' end
            function nag_faction:subculture() return 'wh3_dlc29_sc_nag_undead_legions' end
            energy=29
            energy_resource={is_null_interface=function() return false end,value=function() return energy end}
            energy_manager={is_null_interface=function() return false end,resource=function(_,key)
                assert(key=='wh3_dlc29_nag_necromantic_energy');return energy_resource end}
            function cm:model() return {world=function() return {
                region_group_pooled_resource_managers_system=function() return {
                    get_dynamic_manager_for_region_group_id=function(_,key)
                        assert(key=='land_of_the_dead');return energy_manager end} end} end} end
            function cm:pooled_resource_factor_transaction(resource,factor,delta)
                assert(resource==energy_resource);energy=energy+delta
            end
        """)
        owned=[d for d in self.api.strategic.units.values() if self.api.regiment_belongs(d,self.g.nag_faction)]
        self.assertEqual(len(owned),27)
        for race in ['_tmb_','_vmp_','_cst_']:
            self.assertTrue(any(race in d.id for d in owned))
        index=next(i for i,d in self.api.strategic.resources.items() if d.id=='wh3_dlc29_nag_necromantic_energy')
        self.assertEqual(self.api.strategic.resources[index].label,'亡灵术能量')
        self.api.set_resource(index,'5000');self.g.flush()
        self.assertEqual(self.g.energy,5000)
        self.api.click('open'); self.api.click('tab_精英')
        self.assertIn('27 项',self.g.widgets['wh3pt_strategy_pages'].text)
        self.assertNotIn('wh2_',self.g.widgets['wh3pt_strategy_name_1'].text)
        icon=self.g.widgets['wh3pt_strategy_icon_1']
        self.assertTrue(icon.image_path.startswith('ui/units/'))
        self.assertIn('基础属性',icon.tooltip)
        self.assertIn('近攻',icon.tooltip)
        self.api.click('tab_常用')
        labels=[w.text for w in self.live() if w.id=='wh3pt_home_resource_name']
        self.assertIn('亡灵术能量',labels)
        self.assertFalse(any('soft_cap' in x or 'confidence' in x for x in labels))

    def sigil_fixture(self):
        self.lua.execute('''
            sigil_base, sigil_spent = 23, 8
            sigil_faction=cm:get_faction('player')
            function sigil_faction:name() return 'wh3_dlc29_nag_host_of_nagash' end
            function cm:model() return {world=function() return {
                region_group_pooled_resource_managers_system=function() return {
                    get_dynamic_manager_for_region_group_id=function() return {
                        is_null_interface=function() return true end} end} end} end} end
            function sigil_faction:points_limit_for_faction_initiative_set(key)
                assert(key=='wh3_dlc29_pyramid_initiative_set')
                local b=bundles.wh3pt_sigils
                return sigil_base+(b and not reject_sigils and b.entries[1]:value() or 0)
            end
            cco=nil
            function sigil_faction:command_queue_index() return 47 end
            common={}
            function common.get_context_value(kind,cqi,expression)
                assert(kind=='CcoCampaignFaction' and cqi==47)
                    if sigil_bad_read then return nil end
                    local path='InitiativeManager().InitiativeSetList()'
                    if expression==path..'.Size()' then return 2 end
                    if expression==path..'.At(0).InitiativeSetContext().Key()' then return 'unrelated' end
                    if expression==path..'.At(1).InitiativeSetContext().Key()' then return 'wh3_dlc29_pyramid_initiative_set' end
                    if expression==path..'.At(1).PointsAllocated()' then return sigil_spent end
                    error(expression)
            end
        ''')
        return next(i for i,d in self.api.strategic.resources.items() if d.kind=='sigils')

    def test_mystic_sigils_set_remaining_preserves_spent_and_native_sources(self):
        index=self.sigil_fixture()
        definition=self.api.strategic.resources[index]
        resource=self.api.resource_interface(definition)
        self.assertEqual(resource.value(),15)
        self.api.set_resource(index,'100');self.g.flush()
        self.assertEqual(resource.value(),100)
        self.assertEqual(self.g.sigil_spent,8)
        self.assertEqual(self.g.sigil_base,23)
        self.api.set_resource(index,'0');self.g.flush()
        self.assertEqual(resource.value(),0)
        self.assertEqual(self.g.sigil_faction.points_limit_for_faction_initiative_set(
            self.g.sigil_faction,definition.id),8)
        # Native gains stack after setting; this is not a lock or recurring income.
        self.g.sigil_base=28
        self.assertEqual(resource.value(),5)
        self.api.set_resource(index,'30');self.g.flush()
        self.assertEqual(resource.value(),30)
        self.api.click('open');self.api.click('tab_常用')
        self.assertEqual(self.g.widgets['wh3pt_home_resource_name'].text,'可强化符印')
        self.assertIn('保留已强化',self.g.widgets['wh3pt_home_resource_icon'].tooltip)
        self.assertEqual(len(self.g.logs),0)
        self.lua.execute("function sigil_faction:name() return 'player' end")
        self.assertIsNone(self.api.resource_interface(definition))

    def test_mystic_sigils_readback_failure_and_input_guards(self):
        index=self.sigil_fixture()
        for invalid in ['-1','10001','1.5','nan','']:
            with self.assertRaises(Exception): self.api.set_resource(index,invalid)
        self.assertIsNone(self.g.bundles.wh3pt_sigils)
        self.api.set_resource(index,'40');self.g.flush()
        previous=self.g.bundles.wh3pt_sigils.entries[1].value()
        self.g.reject_sigils=True
        self.api.set_resource(index,'80');self.g.flush()
        self.assertEqual(self.g.bundles.wh3pt_sigils.entries[1].value(),previous)
        self.assertTrue(any('目标 80' in x for x in self.g.logs.values()))
        self.g.reject_sigils=False
        self.api.set_resource(index,'80')
        self.g.sigil_bad_read=True
        self.g.flush()
        self.assertEqual(self.g.bundles.wh3pt_sigils.entries[1].value(),previous)
        with self.assertRaises(Exception): self.api.set_resource(index,'90')
        self.g.sigil_bad_read=False;self.g.multiplayer=True
        with self.assertRaises(Exception): self.api.set_resource(index,'90')

    def test_strategic_switch_clears_stale_search(self):
        self.strategic_fixture();self.api.click('open');self.api.click('tab_精英')
        self.g.widgets['wh3pt_search'].SetStateText(self.g.widgets['wh3pt_search'],'nonexistent')
        self.api.click('search_go');self.api.click('tab_常用');self.api.click('tab_精英')
        self.assertEqual(self.g.widgets['wh3pt_search'].text,'')
        self.assertNotIn('0 项',self.g.widgets['wh3pt_strategy_pages'].text)

    def test_current_player_switch_isolates_resources_elites_and_pending_work(self):
        self.strategic_fixture()
        self.lua.execute("""
            local first=cm:get_faction('player')
            active_name='player'
            local second=setmetatable({}, {__index=first})
            function second:name() return 'wh_main_dwf_dwarfs' end
            function second:subculture() return 'wh_main_sc_dwf_dwarfs' end
            function second:pooled_resource_manager()
                return {resource=function(_,key) return {
                    is_null_interface=function() return key~='dwf_oathgold' end,
                    value=function() return 77 end} end}
            end
            switch_factions={player=first,wh_main_dwf_dwarfs=second}
            switch_pools={player={},wh_main_dwf_dwarfs={}}
            switch_bundles={player={},wh_main_dwf_dwarfs={}}
            function cm:get_local_faction_name() return active_name end
            function cm:get_faction(key) return assert(switch_factions[key]) end
            for name,f in pairs(switch_factions) do
                local owner=name
                function f:mercenary_pool()
                    local entries={}
                    for key,p in pairs(switch_pools[owner]) do
                        local k,v=key,p
                        entries[#entries+1]={unit_record=function() return {key=function() return k end} end,
                            unit_count=function() return v.count end,current_max_units=function() return v.max end}
                    end
                    return {is_null_interface=function() return false end,mercenary_pool_units=function()
                        return {num_items=function() return #entries end,item_at=function(_,i) return entries[i+1] end} end}
                end
                function f:effect_bundles()
                    local entries={};for _,b in pairs(switch_bundles[owner]) do entries[#entries+1]=b end
                    return {num_items=function() return #entries end,item_at=function(_,i) return entries[i+1] end}
                end
            end
            function cm:add_unit_to_faction_mercenary_pool(f,key,source,count,chance,max,per_turn,restriction)
                assert(f==switch_factions[active_name] and restriction==active_name)
                switch_pools[active_name][key]={count=count,max=max}
            end
            function cm:apply_custom_effect_bundle_to_faction(b,f)
                assert(f==switch_factions[active_name]);switch_bundles[active_name][b:key()]=b
            end
            function cm:remove_effect_bundle(key,name) assert(name==active_name);switch_bundles[name][key]=nil end
        """)
        self.api.click('open')
        self.assertEqual(self.g.widgets['wh3pt_home_resource_name'].text,'虔信')
        value=self.g.widgets['wh3pt_home_resource_value']
        value.SetStateText(value,'777')
        self.api.click('tab_精英')
        self.api.native_toggle(True)  # queued Kislev work must never target Dwarfs
        self.g.active_name='wh_main_dwf_dwarfs'
        self.api.click('tab_常用'); self.api.click('tab_常用')
        self.native_flush()
        self.assertEqual(len(list(self.g.switch_pools['wh_main_dwf_dwarfs'].keys())),0)
        self.assertNotEqual(self.g.widgets['wh3pt_home_resource_name'].text,'虔信')
        self.assertEqual(self.g.widgets['wh3pt_home_resource_value'].text,'77')
        self.api.native_toggle(True);self.native_flush()
        dwarf_keys={d.native_key for d in self.api.strategic.units.values() if self.api.regiment_belongs(d,self.g.switch_factions['wh_main_dwf_dwarfs'])}
        self.assertGreater(len(dwarf_keys),0)
        self.assertEqual(set(self.g.switch_pools['wh_main_dwf_dwarfs'].keys()),dwarf_keys)
        cross=next(i for i,d in self.api.strategic.units.items() if '_tmb_' in d.id)
        self.api.native_select(cross);self.native_flush()
        self.assertEqual(self.g.switch_pools['wh_main_dwf_dwarfs'][self.api.strategic.units[cross].native_key].count,20)
        self.g.active_name='player'
        self.api.click('tab_常用');self.native_flush()
        self.assertEqual(self.g.widgets['wh3pt_home_resource_name'].text,'虔信')
        self.assertEqual(self.g.widgets['wh3pt_home_resource_value'].text,'777')
        self.assertIsNone(self.g.switch_pools['player'][self.api.strategic.units[cross].native_key])
        kislev_keys={d.native_key for d in self.api.strategic.units.values() if self.api.regiment_belongs(d,self.g.switch_factions['player'])}
        self.assertEqual(set(self.g.switch_pools['player'].keys()),kislev_keys)
        self.assertEqual(len(self.g.logs),0)

    def test_native_ui_initializes_and_registers_entry_handlers(self):
        self.assertIsNone(self.g.widgets['wh3pt_open'].SetPriority)
        self.assertEqual(self.g.widgets['wh3pt_open'].priority,200)
        self.assertEqual(self.g.widgets['wh3pt_panel'].priority,200)
        self.assertIsNotNone(self.g.core.listeners['wh3pt_click'])
        self.assertIsNotNone(self.g.core.listeners['wh3pt_hotkey'])
        self.lua.execute("core:trigger_event('ComponentLClickUp', {string='wh3pt_open'})")
        self.assertTrue(self.g.widgets['wh3pt_panel'].visible)
        self.assertEqual(self.g.widgets['wh3pt_lock_vigour'].priority,200)

    def test_initialization_errors_are_written_to_mod_log(self):
        self.lua.execute("root.CreateComponent = function() error('native UI unavailable') end")
        self.g.first_tick()
        self.assertEqual(len(self.g.logs),1)
        self.assertIn('native UI unavailable',self.g.logs[1])
        self.assertIn('stack traceback',self.g.logs[1])

    def test_reopening_panel_keeps_resource_input_handles_fresh(self):
        self.strategic_fixture()
        for _ in range(3): self.api.click('open')
        self.assertEqual(list(self.g.stale_ui_calls.values()),[])
        self.assertFalse(self.g.widgets['wh3pt_home_resource_value'].dead)

    def test_failed_first_render_keeps_entry_handlers_and_other_pages(self):
        self.lua.execute("""
            core.listeners={}
            local f=cm:get_faction('player')
            function f:pooled_resource_manager()
                return {resource=function()
                    return {is_null_interface=function() return false end,
                        value=function() error('native context not ready') end}
                end}
            end
        """)
        self.g.first_tick()
        self.assertEqual(len(self.g.logs),1)
        self.assertIn('native context not ready',self.g.logs[1])
        for name in ['wh3pt_click','wh3pt_moved','wh3pt_hotkey','wh3pt_char_select']:
            self.assertIsNotNone(self.g.core.listeners[name],name)
        self.lua.execute("core:trigger_event('ComponentLClickUp', {string='wh3pt_tab_战役'})")
        self.assertFalse(self.g.widgets['wh3pt_card_order'].dead)

    def status_after_start(self,build_number):
        self.g.build_number=build_number
        self.lua.execute("""
            common={get_context_value=function(kind,id,expression)
                assert(kind=='CcoGameCore' and id=='' and expression=='BuildNumberShort()')
                if build_number=='fail' then error('context unavailable') end
                return build_number
            end}
        """)
        self.g.first_tick()
        return self.g.widgets['wh3pt_status'].text

    def test_panel_warns_only_about_a_clearly_different_game_version(self):
        self.assertIn('游戏已更新到 99.1.2',self.status_after_start('99.1.2'))
        self.assertEqual(self.g.widgets['wh3pt_status_dot'].state,'danger_dot')
        for number in (game_version(GAME),'Build50791','fail'):
            with self.subTest(number=number):
                self.assertNotIn('游戏已更新',self.status_after_start(number))

    def test_every_feature_apply_read_modify_disable(self):
        for defn in self.api.catalog.values():
            with self.subTest(feature=defn.id):
                self.api.apply(defn.id,True,str(defn.default)); self.drain_income()
                self.assertEqual(self.api.read(defn),(True,defn.default))
                if not defn.toggle:
                    value=1 if defn.choices else 17
                    self.api.apply(defn.id,True,str(value)); self.drain_income()
                    self.assertEqual(self.api.read(defn),(True,value))
                self.api.apply(defn.id,False,'invalid ignored'); self.g.flush()
                self.assertEqual(self.api.read(defn),(False,None))

    def drain_income(self):
        for _ in range(40): self.g.flush()

    def income_turn_end(self):
        self.lua.execute("core:trigger_event('FactionTurnEnd', {faction=function() return cm:get_faction('player') end})")

    def test_native_income_prediction_refresh_and_reload_without_cash_grants(self):
        self.api.apply('income',True,'100');self.drain_income()
        f=self.g.cm.get_faction(self.g.cm,'player')
        self.assertEqual(f.income(f),2000)
        self.assertEqual(self.g.treasury_balance,5000)
        self.income_turn_end();self.drain_income();self.api.income.start()
        self.lua.execute("core:trigger_event('FactionTurnStart', {faction=function() return cm:get_faction('player') end})")
        self.drain_income()
        self.assertEqual(f.income(f),2000)
        self.assertEqual(self.g.treasury_balance,5000)
        self.g.regular_income=2300
        self.assertIsNone(self.g.campaign_timers.wh3pt_income_prediction)
        self.assertEqual(f.income(f),4600)
        self.api.apply('income',False,None)
        self.assertEqual(f.income(f),2300)

    def test_native_income_invalid_values_failure_and_no_stale_bonus(self):
        for value in ['0','0.99','1.5','10001','nan','1e309','-5']:
            with self.assertRaises(Exception):self.api.apply('income',True,value)
        self.g.ignore_apply=True
        self.api.apply('income',True,'400');self.drain_income()
        self.assertEqual(self.api.income.read(),(False,None))
        self.assertIsNone(self.g.bundles.wh3pt_income)
        self.assertEqual(self.g.treasury_balance,5000)
        self.g.ignore_apply=False
        self.api.apply('income',True,'50');self.drain_income()

    def test_income_refreshes_native_pooled_resource_binding_not_replacement_text(self):
        self.lua.execute("""
            local holder=root:CreateComponent('resources_bar_holder','ui/wh3pt/card')
            local label=holder:CreateComponent('dy_income','ui/wh3pt/label')
            label.text='+199';native_refreshes=0
            local context={Call=function(_,query) assert(query=='Key()');return 'energy' end}
            function label:GetContextObjectId(kind) assert(kind=='CcoCampaignPooledResource');return 'regional_energy' end
            function label:GetContextObject(kind) assert(kind=='CcoCampaignPooledResource');return context end
            function label:SetContextObject(value)
                assert(value==context)
                native_refreshes=native_refreshes+1
                self.text=bundles.wh3pt_income and '+597' or '+199'
            end
            function label:SetStateText() error('Do not fake native income labels') end
            function find_uicomponent(parent,id) if id=='resources_bar_holder' then return holder end end
        """)
        self.api.apply('income',True,'200');self.drain_income()
        self.assertEqual(self.g.widgets.dy_income.text,'+597')
        self.api.apply('income',False,None);self.drain_income()
        self.assertEqual(self.g.widgets.dy_income.text,'+199')
        self.assertEqual(self.g.native_refreshes,3)  # Bundle readback, forecast change, removal.

    def test_income_home_shortcut_presets_and_custom_input(self):
        self.api.click('open');self.api.click('income_open')
        self.assertEqual(self.list_rows(),{'income'})
        self.api.click('incomequick_500');self.drain_income()
        self.assertEqual(self.api.income.read(),(True,500))
        control=self.g.widgets['wh3pt_input_income']
        control.SetStateText(control,'275')
        self.api.click('apply_income');self.drain_income()
        self.assertEqual(self.api.income.read(),(True,275))
        self.api.click('tab_常用')
        self.assertIn('+275%',self.g.widgets['wh3pt_income_open'].text)
        self.assertEqual(self.g.treasury_balance,5000)

    def test_invalid_values_mutual_exclusion_and_busy(self):
        self.assertIsNone(self.g.math.huge)
        for key,value in [('ward','nan'),('ward','1e309'),('ward','-1e309'),('ward','101'),('recruitment','2.5'),('healing','0')]:
            with self.assertRaises(Exception): self.api.apply(key,True,value)
        self.api.apply('order',True,'20')
        with self.assertRaisesRegex(Exception,'等待'): self.api.apply('ward',True,'50')
        self.g.flush()
        self.api.apply('healing',True,'3')
        with self.assertRaisesRegex(Exception,'另一项'): self.api.apply('revive',True,'3')

    def test_read_failure_and_rejected_mutation_not_reported_as_success(self):
        self.g.ignore_apply=True
        self.api.apply('order',True,'50'); self.g.flush()
        self.assertIn('回读不一致',self.g.widgets['wh3pt_status'].text)
        self.assertIsNone(self.g.saved['wh3pt_player_order_value'])
        self.g.fail_read=True
        with self.assertRaisesRegex(Exception,'read unavailable'): self.api.read(self.feature('order'))
        self.api.click('refresh')
        self.api.click('tab_战役')
        self.assertTrue(self.g.widgets['wh3pt_toggle_order'].disabled)

    def test_ui_input_search_favourites_paging_states_and_cleanup(self):
        # Test DawnOfMan-style Home page (常用): lock tiles, treasury quick-add, active list & collapse
        self.api.click('lock_vigour'); self.g.flush()
        self.assertEqual(self.api.read(self.feature('vigour')),(True,1))
        self.api.click('gold_10000')
        self.assertEqual(self.g.treasury_balance,15000)
        self.api.click('off_vigour'); self.g.flush()
        self.assertEqual(self.api.read(self.feature('vigour')),(False,None))
        self.api.click('collapse')
        self.assertEqual(self.g.widgets['wh3pt_panel'].w,68)
        self.assertIsNotNone(self.g.widgets['wh3pt_collapse_rail'])
        self.api.click('tab_部队')
        self.assertEqual(self.g.widgets['wh3pt_panel'].w,460)
        self.assertEqual(self.g.widgets['wh3pt_apply_ward'].priority,200)
        self.assertEqual(self.g.widgets['wh3pt_apply_ward'].text,'应用')
        self.assertEqual(self.g.widgets['wh3pt_tab_部队'].state,'selected')
        self.assertEqual(self.g.widgets['wh3pt_tab_战役'].state,'ghost')
        self.assertEqual(self.g.widgets['wh3pt_toggle_ward'].text,'○ 关')
        self.g.widgets['wh3pt_input_ward'].SetStateText(self.g.widgets['wh3pt_input_ward'],'47.5')
        self.api.click('apply_ward'); self.g.flush()
        self.assertEqual(self.api.read(self.feature('ward')),(True,47.5))
        self.assertEqual(self.g.widgets['wh3pt_card_ward'].state,'enabled')
        self.assertEqual(self.g.widgets['wh3pt_apply_ward'].text,'应用')
        self.assertEqual(self.g.widgets['wh3pt_toggle_ward'].state,'enabled')
        self.assertEqual(self.g.widgets['wh3pt_toggle_ward'].text,'● 开')
        self.assertIn('47.5',self.g.widgets['wh3pt_val_ward'].text)
        self.assertIn('1 项',self.g.widgets['wh3pt_rail_count'].text)
        self.api.click('fav_ward')
        self.assertEqual(self.g.widgets['wh3pt_fav_ward'].state,'star_lit')
        self.api.click('tab_收藏')
        self.assertEqual(self.list_rows(),{'ward'})
        self.api.click('plus_ward')
        self.assertEqual(self.g.widgets['wh3pt_input_ward'].text,'52.5')
        self.api.click('tab_特殊')
        self.assertNotIn('· 100',self.g.widgets['wh3pt_info_attrition'].text)
        self.assertIn('全军免疫战役非战斗损耗',self.g.widgets['wh3pt_info_attrition'].text)
        self.api.click('tab_全部')
        self.assertEqual(len(self.list_rows()),35)
        self.g.widgets['wh3pt_search'].text='不存在的功能'
        self.api.click('search_go')
        self.assertEqual(self.list_rows(),set())
        self.assertIn('未找到',self.g.widgets['wh3pt_empty_t1'].text)
        self.g.widgets['wh3pt_search'].text='['
        self.api.click('search_go')
        self.assertEqual(self.list_rows(),set())
        self.api.click('empty_clear')
        self.assertEqual(self.g.widgets['wh3pt_search'].text,'')
        self.g.widgets['wh3pt_search'].text='attack'
        self.api.click('search_go')
        self.assertEqual(self.list_rows(),{'attack'})
        self.api.click('search_clear')
        self.assertEqual(self.g.widgets['wh3pt_search'].text,'')
        self.assertEqual(len(self.list_rows()),35)
        self.api.click('tab_收藏')
        # Clear favourites for empty-state check, then restore via go_all
        self.api.click('fav_ward')
        self.assertIn('收藏夹还是空的',self.g.widgets['wh3pt_empty_t1'].text)
        self.api.click('go_all')
        self.assertEqual(self.g.widgets['wh3pt_tab_全部'].state,'selected')
        self.api.click('rail_count')
        self.assertIn('正在生效',self.g.widgets['wh3pt_sheet_title'].text)
        self.api.click('sheet_close')
        self.api.clear(); self.g.flush()
        self.assertEqual(self.api.read(self.feature('ward')),(False,None))

    def test_commands_presets_and_shortcuts(self):
        self.api.click('tab_指令')
        self.assertIn('指令',self.g.widgets['wh3pt_title'].text)
        self.assertIn('未选中',self.g.widgets['wh3pt_target_info'].text)
        self.g.widgets['wh3pt_cinput_treasury'].SetStateText(self.g.widgets['wh3pt_cinput_treasury'],'80000')
        self.api.click('cmd_treasury')
        self.assertEqual(self.g.treasury_balance,85000)
        self.api.click('cmd_move_all')
        self.assertEqual(len(self.g.replenished_characters),2)
        self.api.click('cmd_xp_all')
        self.assertEqual(self.g.character_xp['character_cqi:101'],10000)
        self.assertEqual(self.g.character_xp['character_cqi:102'],10000)
        self.lua.execute("core:trigger_event('CharacterSelected', {character=function() return characters[2] end})")
        self.api.click('cmd_xp_selected')
        self.assertEqual(self.g.character_xp['character_cqi:101'],10000)
        self.assertEqual(self.g.character_xp['character_cqi:102'],20000)
        self.api.click('preset_menu')
        self.api.click('preset_campaign'); self.g.flush()
        self.assertEqual(self.api.read(self.feature('order')),(True,50))
        self.assertEqual(self.api.read(self.feature('growth')),(True,500))
        self.assertEqual(self.api.read(self.feature('construction')),(True,500))
        self.api.click('preset_menu')
        self.api.click('preset_battle'); self.g.flush()
        self.assertEqual(self.api.read(self.feature('ward')),(True,33))
        self.assertEqual(self.api.read(self.feature('attack')),(True,50))
        self.assertFalse(self.g.widgets['wh3pt_panel'].visible)
        self.lua.execute("core:trigger_event('ShortcutTriggered', {string='camera_bookmark_view0'})")
        self.assertTrue(self.g.widgets['wh3pt_panel'].visible)
        self.lua.execute("core:trigger_event('ShortcutTriggered', {string='camera_bookmark_view0'})")
        self.assertFalse(self.g.widgets['wh3pt_panel'].visible)

    def test_target_command_page_and_clear_all(self):
        self.api.click('tab_指令')
        self.api.click('command_next')
        self.assertIn('2/2', self.g.widgets['wh3pt_command_page'].text)
        self.assertEqual(self.g.widgets['wh3pt_cinput_unit_rank'].text, '9')
        self.api.click('cmd_free_skills')
        self.assertTrue(self.api.targets().enabled())
        self.assertEqual(self.g.widgets['wh3pt_cmd_free_skills'].text, '● 开')
        self.api.clear(); self.g.flush()
        self.assertFalse(self.api.targets().enabled())
        self.assertIn('script\\battle\\mod\\wh3pt_battle.lua', self.files)

    def test_mortarch_building_command_is_player_scoped_and_dispatches_native_unlock(self):
        self.api.click('tab_指令');self.api.click('command_next')
        self.assertTrue(self.g.widgets['wh3pt_cmd_mortarch_buildings'].disabled)
        self.lua.execute("""
            local f=cm:get_faction('player')
            function f:name() return 'wh3_dlc29_nag_host_of_nagash' end
            nag_mortarchs_config={locked_buildings_ritual={
                wh3_dlc29_nag_mortarchs_neferata='wh3_dlc29_nag_necropolis_military_melee_5',
                wh3_dlc29_nag_mortarchs_walach='wh3_dlc29_nag_necropolis_military_cavalry_5'}}
            building_calls={}
            function cm:remove_event_restricted_building_record_for_faction(building,owner)
                assert(owner=='wh3_dlc29_nag_host_of_nagash');building_calls[building]=true
            end
        """)
        self.api.click('command_prev');self.api.click('command_next')
        self.assertFalse(self.g.widgets['wh3pt_cmd_mortarch_buildings'].disabled)
        self.api.click('cmd_mortarch_buildings')
        self.assertEqual(len(list(self.g.building_calls.keys())),2)

    def test_manual_battle_preferences_do_not_affect_campaign_or_autoresolve(self):
        for key, value in [('attack', 50), ('range', 100), ('revive', 3)]:
            self.api.apply(key, True, str(value)); self.g.flush()
            self.assertEqual(self.api.read(self.feature(key)), (True, value))
            self.assertEqual(self.api.read_model(self.feature(key)), (False, None))
        self.lua.execute("core:trigger_event('PendingBattle', {})")
        self.assertIsNone(self.g.bundles['wh3pt_attack'])
        self.lua.execute("core:trigger_event('BattleBeingFought', {is_autoresolved=function() return true end})")
        self.assertIsNone(self.g.bundles['wh3pt_attack'])
        self.assertTrue(any('autoresolve: no combat' in msg for msg in self.g.logs.values()))
        self.lua.execute("core:trigger_event('BattleBeingFought', {is_autoresolved=function() return false end})")
        self.assertEqual(self.api.read_model(self.feature('attack')), (True, 50))
        self.assertEqual(self.api.read_model(self.feature('range')), (True, 100))
        self.assertEqual(self.g.svr.wh3pt_revive_level, '3')
        self.lua.execute("core:trigger_event('BattleConflictFinished', {})")
        self.assertEqual(self.api.read_model(self.feature('attack')), (False, None))
        self.assertEqual(self.api.read(self.feature('attack')), (True, 50))

    def test_campaign_magic_switches_publish_only_for_manual_battle(self):
        for key in ('battle_magic', 'battle_cooldown'):
            self.api.apply(key, True, '1')
            self.assertEqual(self.api.read(self.feature(key)), (True, 1))
            self.assertIsNone(self.g.bundles['wh3pt_' + key])
        self.api.start()  # saved preferences survive rebuilding the campaign panel
        self.assertEqual(self.api.read(self.feature('battle_magic')), (True, 1))
        self.api.click('open'); self.api.click('tab_特殊')
        for key in ('battle_magic', 'battle_cooldown'):
            self.assertIsNotNone(self.g.widgets['wh3pt_toggle_' + key])
        self.api.click('toggle_battle_magic')
        self.assertEqual(self.api.read(self.feature('battle_magic')), (False, None))
        self.api.click('toggle_battle_magic')
        self.lua.execute("core:trigger_event('BattleBeingFought', {is_autoresolved=function() return true end})")
        self.assertFalse(self.g.svr.wh3pt_magic_pending)
        self.lua.execute("core:trigger_event('BattleBeingFought', {is_autoresolved=function() return false end})")
        self.assertTrue(self.g.svr.wh3pt_magic_pending)
        self.assertEqual(self.g.svr.wh3pt_magic_owner, 'player')
        self.assertTrue(self.g.svr.wh3pt_magic_enabled)
        self.assertTrue(self.g.svr.wh3pt_cooldown_enabled)
        self.assertIsNone(self.g.bundles['wh3pt_battle_magic'])
        self.lua.execute("core:trigger_event('BattleConflictFinished', {})")
        self.assertFalse(self.g.svr.wh3pt_magic_pending)
        self.api.apply('battle_magic', False, None)
        self.lua.execute("core:trigger_event('BattleBeingFought', {is_autoresolved=function() return false end})")
        self.assertFalse(self.g.svr.wh3pt_magic_enabled)
        self.assertTrue(self.g.svr.wh3pt_cooldown_enabled)

    def test_all_manual_features_commit_and_cleanup_through_campaign_events(self):
        for definition in self.api.catalog.values():
            if not definition.manual_battle:
                continue
            with self.subTest(feature=definition.id):
                self.api.apply(definition.id, True, str(definition.default))
                self.assertEqual(self.api.read_model(definition), (False, None))
                self.lua.execute("core:trigger_event('BattleBeingFought', {is_autoresolved=function() return true end})")
                self.assertEqual(self.api.read_model(definition), (False, None))
                self.lua.execute("core:trigger_event('BattleBeingFought', {is_autoresolved=function() return false end})")
                if not definition.battle_setting:
                    self.assertEqual(self.api.read_model(definition), (True, definition.default))
                self.lua.execute("core:trigger_event('BattleCompleted', {})")
                self.assertEqual(self.api.read_model(definition), (False, None))
                self.assertEqual(self.api.read(definition), (True, definition.default))
                self.api.apply(definition.id, False, None)

    def test_manual_settings_work_with_campaign_timers_paused(self):
        self.api.apply('attack', True, '50')
        self.assertIn('已保存', self.g.widgets['wh3pt_status'].text)
        self.api.apply('attack', False, None)  # no flush: pre-battle timers paused
        self.assertIn('已关闭', self.g.widgets['wh3pt_status'].text)
        self.api.apply('range', True, '100')
        self.assertEqual(self.api.read(self.feature('range')), (True, 100))
        self.assertEqual(self.api.read_model(self.feature('range')), (False, None))
        self.api.command('preset_battle', '1')
        self.api.apply('attack', False, None)  # the all-battle preset also finishes synchronously
        self.assertEqual(self.api.read(self.feature('attack')), (False, None))

    def test_manual_migration_preserves_old_values_and_does_not_reenable_disabled(self):
        self.lua.execute('''
            saved.wh3pt_player_attack_manual_enabled=nil
            local b=cm:create_new_custom_effect_bundle('wh3pt_attack')
            b:add_effect('wh_main_effect_force_stat_melee_attack','faction_to_force_own',77)
            cm:apply_custom_effect_bundle_to_faction(b,cm:get_faction('player'))
        ''')
        self.api.start()
        self.assertEqual(self.api.read(self.feature('attack')), (True, 77))
        self.assertEqual(self.api.read_model(self.feature('attack')), (False, None))
        self.api.apply('attack', False, None); self.g.flush()
        self.api.start()
        self.assertEqual(self.api.read(self.feature('attack')), (False, None))

    def test_manual_begin_rolls_back_native_failure_and_unknown_decision(self):
        self.api.apply('attack', True, '50'); self.g.flush()
        self.g.ignore_apply = True
        self.lua.execute("core:trigger_event('BattleBeingFought', {is_autoresolved=function() return false end})")
        self.assertIsNone(self.g.bundles['wh3pt_attack'])
        self.assertTrue(any('手动战斗加成回读失败' in msg for msg in self.g.logs.values()))
        self.g.ignore_apply = False
        self.lua.execute("core:trigger_event('BattleBeingFought', {})")
        self.assertIsNone(self.g.bundles['wh3pt_attack'])

    def test_campaign_bonuses_survive_manual_battle_cleanup(self):
        self.api.apply('growth', True, '500'); self.g.flush()
        self.api.apply('ward', True, '33'); self.g.flush()
        self.lua.execute("core:trigger_event('BattleBeingFought', {is_autoresolved=function() return false end})")
        self.api.start()  # loading/reloading after a battle must remove transient bonuses
        self.assertEqual(self.api.read_model(self.feature('ward')), (False, None))
        self.assertEqual(self.api.read(self.feature('ward')), (True, 33))
        self.assertEqual(self.api.read_model(self.feature('growth')), (True, 500))

    def test_reopen_preserves_effects_but_saved_ui_cannot_fake_active_state(self):
        self.api.apply('order',True,'45'); self.g.flush()
        self.api.start()
        self.assertEqual(self.api.read(self.feature('order')),(True,45))
        self.g.bundles['wh3pt_order']=None
        self.api.start()
        self.assertEqual(self.api.read(self.feature('order')),(False,None))
        self.g.multiplayer=True
        with self.assertRaisesRegex(Exception,'单人'): self.api.apply('order',True,'45')

    def test_selected_commands_reject_missing_ineligible_and_stale_targets(self):
        def assert_rejected():
            for command,value in [('move_selected','1'),('xp_selected','10000')]:
                with self.subTest(command=command):
                    with self.assertRaisesRegex(Exception,'请先选中我方在役领主或英雄'):
                        self.api.command(command,value)
                    self.assertEqual(len(self.g.replenished_characters),0)
                    self.assertEqual(len(list(self.g.character_xp.keys())),0)

        assert_rejected()
        for index in (3,4):  # Wounded character and settlement garrison.
            self.lua.execute(f"core:trigger_event('CharacterSelected', {{character=function() return characters[{index}] end}})")
            assert_rejected()
        self.lua.execute("core:trigger_event('CharacterSelected', {character=function() return characters[2] end}); characters={characters[1],characters[3],characters[4]}")
        assert_rejected()

    def test_selected_commands_only_mutate_valid_target_and_clear_foreign_selection(self):
        self.lua.execute("core:trigger_event('CharacterSelected', {character=function() return characters[2] end})")
        self.api.command('move_selected','1')
        self.assertEqual(list(self.g.replenished_characters.values()),['character_cqi:102'])
        self.api.command('xp_selected','10000')
        self.assertIsNone(self.g.character_xp['character_cqi:101'])
        self.assertEqual(self.g.character_xp['character_cqi:102'],10000)
        self.lua.execute("core:trigger_event('CharacterSelected', {character=function() return {is_null_interface=function() return false end,faction=function() return {is_null_interface=function() return false end,name=function() return 'enemy' end} end} end})")
        for command,value in [('move_selected','1'),('xp_selected','10000')]:
            with self.assertRaisesRegex(Exception,'请先选中我方在役领主或英雄'):
                self.api.command(command,value)
        self.assertEqual(list(self.g.replenished_characters.values()),['character_cqi:102'])
        self.assertEqual(self.g.character_xp['character_cqi:102'],10000)

    def test_deselecting_or_picking_a_settlement_clears_the_selected_target(self):
        for event in ('CharacterDeselected','SettlementSelected'):
            with self.subTest(event=event):
                self.lua.execute("core:trigger_event('CharacterSelected', {character=function() return characters[1] end})")
                self.lua.execute(f"core:trigger_event('{event}', {{}})")
                for command,value in [('move_selected','1'),('xp_selected','10000')]:
                    with self.assertRaisesRegex(Exception,'请先选中我方在役领主或英雄'):
                        self.api.command(command,value)
                with self.assertRaisesRegex(Exception,'请先选中我方军队'):
                    self.api.command('unit_rank','9')
        self.assertEqual(len(self.g.replenished_characters),0)
        self.assertEqual(len(list(self.g.character_xp.keys())),0)

    # ---- Scale-robust layout -------------------------------------------------
    def populate(self):
        for key,value in [('vigour','1'),('unbreakable','1'),('healing','3'),('ward','45'),('attack','35'),
                          ('defence','20'),('order','50'),('growth','500'),('movement','100'),('upkeep','50'),
                          ('vanguard','1'),('attrition','1'),('armour','100'),('speed','20')]:
            self.api.apply(key,True,value); self.g.flush()
        self.api.click('fav_ward'); self.api.click('fav_order')
        self.lua.execute("core:trigger_event('CharacterSelected', {character=function() return characters[2] end})")

    def show(self,view):
        api=self.api
        if view=='collapsed':
            api.click('tab_常用'); api.click('collapse')
        elif view=='sheet':
            api.click('tab_常用'); api.click('rail_count')
        elif view=='preset':
            api.click('tab_全部'); api.click('preset_menu')
        else:
            api.click('tab_'+view)

    def live(self):
        return [w for w in self.g.widgets.values() if w.id!='root' and not w.dead]

    def list_rows(self):
        return {w.id[len('wh3pt_row_'):] for w in self.live() if w.id.startswith('wh3pt_row_')}

    def in_list(self,w):
        """List content scrolls and is clipped, so it may lie beyond the panel."""
        parent=w.parent
        while parent:
            if parent.id=='wh3pt_list': return True
            parent=parent.parent
        return False

    def descendants(self,w):
        for c in w.children.values():
            if not c.dead:
                yield c
                yield from self.descendants(c)

    def assert_layout(self,width,height,ts,ctx):
        g=self.g
        layout=self.api.layout()
        for w in self.live():
            self.assertIsInstance(w.text,str,(ctx,w.id))
            if self.in_list(w): continue
            self.assertGreaterEqual(w.x,0,(ctx,w.id)); self.assertGreaterEqual(w.y,0,(ctx,w.id))
            self.assertLessEqual(w.x+w.w,width,(ctx,w.id)); self.assertLessEqual(w.y+w.h,height,(ctx,w.id))
        panel=g.widgets['wh3pt_panel']
        self.assertTrue(panel.visible,ctx)
        clip=next((w for w in self.live() if w.id=='list_clip'),None)
        for w in self.descendants(panel):
            if self.in_list(w):
                if w.parent is not None and w.id.startswith('wh3pt_') and w.id!='wh3pt_list_content':
                    self.assertGreaterEqual(w.x,clip.x,(ctx,w.id))
                    self.assertLessEqual(w.x+w.w,clip.x+clip.w,(ctx,w.id))
                continue
            self.assertGreaterEqual(w.x,panel.x,(ctx,w.id)); self.assertGreaterEqual(w.y,panel.y,(ctx,w.id))
            self.assertLessEqual(w.x+w.w,panel.x+panel.w,(ctx,w.id))
            self.assertLessEqual(w.y+w.h,panel.y+panel.h,(ctx,w.id))
        for w in self.live():
            if w.template in ('ui/wh3pt/label','ui/wh3pt/button') and w.text:
                font=g.font_px_of(w)
                self.assertLessEqual(g.measure_text(w.text,font),w.w+0.01,(ctx,w.id,w.text,w.state))
                if not layout.clamped:
                    # Unclamped: geometry follows the calibrated scale, so the
                    # rendered line height fits too.
                    self.assertLessEqual(font*1.25*ts,w.h+1,(ctx,w.id,w.text))
                if w.template=='ui/wh3pt/button':
                    self.assertGreaterEqual(w.h,24*min(layout.scale,1.0)-0.5,(ctx,w.id))

    def test_layout_bounds_and_text_fit_at_resolutions_and_text_scales(self):
        self.populate()
        self.api.click('open')
        for width,height in RESOLUTIONS:
            for ts in TEXT_SCALES:
                self.g.screen_width=width; self.g.screen_height=height; self.g.text_scale=ts
                self.api.start()
                layout=self.api.layout()
                expected=min(ts,(width-32)/460,(height-28)/678)
                self.assertAlmostEqual(layout.scale,expected,3)
                self.assertEqual(layout.clamped,expected<ts-0.001)
                self.assertEqual(self.g.widgets['wh3pt_panel'].w,round(460*layout.scale))
                for view in VIEWS:
                    with self.subTest(res=(width,height),text_scale=ts,view=view):
                        self.show(view)
                        self.assert_layout(width,height,ts,(width,height,ts,view))
                        if view=='collapsed': self.api.click('collapse_rail')
        self.assertEqual(len(self.g.logs),0)

    def test_calibration_and_fitting_keep_states_and_utf8(self):
        self.g.text_scale=1.5
        self.api.start(); self.api.click('open')
        self.assertEqual(self.api.layout().scale,1.5)
        self.assertEqual(self.api.layout().probe_w,210)
        self.assertEqual(self.g.widgets['wh3pt_tab_常用'].state,'selected')
        self.assertEqual(self.g.widgets['wh3pt_panel'].w,690)
        # Clamped extreme: fonts step down, long text is truncated on UTF-8
        # boundaries with an ellipsis and never exceeds its component.
        self.g.screen_width,self.g.screen_height,self.g.text_scale=1280,720,2.0
        self.api.start(); self.api.click('tab_战役')
        self.assertTrue(self.api.layout().clamped)
        cut=[w for w in self.live() if w.text.endswith('…')]
        self.assertTrue(cut)
        self.assertTrue(any(w.state.endswith('_sm') for w in self.live() if w.template=='ui/wh3pt/label'))
        for w in cut:
            self.assertIsInstance(w.text,str)
            self.assertNotIn('\ufffd',w.text)
        self.assertEqual(self.g.widgets['wh3pt_tab_战役'].state.replace('_sm',''),'selected')

    def test_missing_text_dimensions_and_resolution_apis_fall_back(self):
        self.lua.execute('text_dimensions_enabled=false; core.get_screen_resolution=nil')
        self.populate()
        self.api.start(); self.api.click('open')
        self.assertIsNone(self.g.widgets['wh3pt_panel'].TextDimensions)
        layout=self.api.layout()
        self.assertEqual(layout.scale,1)
        self.assertIsNone(layout.probe_w)
        for view in VIEWS:
            with self.subTest(view=view):
                self.show(view)
                self.assert_layout(1920,1080,1.0,('no-measure',view))
                if view=='collapsed': self.api.click('collapse_rail')
        # Estimated fitting still truncates the long status line.
        self.g.ignore_apply=True
        self.api.apply('research',True,'50'); self.g.flush()
        status=self.g.widgets['wh3pt_status']
        self.assertTrue(status.text.startswith('失败：'))
        self.assertLessEqual(self.g.measure_text(status.text,self.g.font_px_of(status)),status.w)
        # Manual scale override (header 'A' button) is saved and applied.
        self.api.click('ui_scale')
        self.assertEqual(self.g.saved['wh3pt_ui_scale'],1.0)
        self.api.click('ui_scale')
        self.assertEqual(self.g.saved['wh3pt_ui_scale'],1.25)
        self.assertEqual(self.api.layout().scale,1.25)
        self.assertEqual(self.g.widgets['wh3pt_panel'].w,575)
        self.assertIn('125%',self.g.widgets['wh3pt_ui_scale'].text)
        self.api.start()
        self.assertEqual(self.api.layout().scale,1.25)
        for _ in range(4): self.api.click('ui_scale')  # 1.5, 1.75, 2.0, auto
        self.assertEqual(self.g.saved['wh3pt_ui_scale'],0)
        self.assertIn('自动',self.g.widgets['wh3pt_ui_scale'].text)
        self.assertEqual(len(self.g.logs),1)  # only the injected read-back failure

    # ---- Engine behaviour measured in game, see ui_engine.py ---------------------
    def states(self,kind):
        root=ET.fromstring(self.files['ui\\wh3pt\\'+kind+'.twui.xml'])
        return list(next(c for c in root.find('components') if c.tag==kind).find('states'))

    def test_templates_use_only_verified_fonts_and_never_wrap(self):
        for kind in ('button','label','card'):
            for state in self.states(kind):
                with self.subTest(kind=kind,state=state.get('name')):
                    text=state.find('component_text')
                    self.assertIn(int(text.get('font_m_size')),FONT_SIZES)
                    self.assertEqual(text.get('texthbehaviour'),'Never split')

    def test_nine_slice_margins_fit_inside_every_component(self):
        # Too-small 9-slices flipped into the thick bars and octagonal dots seen
        # in game. Margins follow ui_scale (text scale / 1.25 in zh-CN).
        margins={}
        for kind in ('button','card'):
            for state in self.states(kind):
                sizes=[float(i.get('margin','0').split(',')[0]) for i in state.find('imagemetrics')]
                margins[(kind,state.get('name'))]=max(sizes)
        self.assertEqual(margins[('card','divider')],0)
        self.assertEqual(margins[('card','ok_dot')],0)
        self.populate()
        self.g.screen_width,self.g.screen_height,self.g.text_scale=3840,2160,2.0
        self.api.start(); self.api.click('open')
        for view in VIEWS:
            self.show(view)
            for w in self.live():
                kind=(w.template or '').rsplit('/',1)[-1]
                if (kind,w.state) in margins:
                    need=2*margins[(kind,w.state)]*1.6
                    self.assertGreaterEqual(min(w.w,w.h),need,(view,w.id,w.state))
            if view=='collapsed': self.api.click('collapse_rail')

    def test_wrapping_text_is_truncated_instead_of_trusting_its_narrow_block(self):
        self.g.text_wraps=True
        self.g.screen_width,self.g.screen_height,self.g.text_scale=1280,720,2.0
        self.api.start(); self.api.click('open'); self.api.click('tab_战役')
        self.assertTrue([w for w in self.live() if w.text.endswith('…')])
        for w in self.live():
            if w.template=='ui/wh3pt/label' and w.text:
                self.assertLessEqual(self.g.measure_text(w.text,self.g.font_px_of(w)),w.w+0.01,(w.id,w.text))

    def test_status_line_keeps_the_longest_failure_message_whole(self):
        status=self.g.widgets['wh3pt_status']
        for text in ('失败：请先选中我方在役领主或英雄，再恢复行动力','全部修改已关闭；既有建筑、科技和伤亡不会回滚'):
            self.assertLessEqual(self.g.measure_text(text,self.g.font_px_of(status)),status.w,text)

    def test_launcher_stays_left_of_the_native_tab_strip(self):
        launcher=self.g.widgets['wh3pt_open']
        self.assertLess(launcher.x+launcher.w,self.g.screen_width*0.84)
        self.lua.execute('''function find_uicomponent(_,first,second)
            assert(first=='hud_campaign' and second=='bar_small_top')
            return {Position=function() return 1640,0 end}
        end''')
        self.api.start()
        launcher=self.g.widgets['wh3pt_open']
        self.assertEqual(launcher.x+launcher.w,1640-56)
        self.lua.execute('function find_uicomponent() error("hud not ready") end')
        self.api.start()
        launcher=self.g.widgets['wh3pt_open']
        self.assertLess(launcher.x+launcher.w,self.g.screen_width*0.84)

    def test_palette_lands_on_its_design_colours_after_the_engine_lift(self):
        for colour in [(17,22,26),(24,32,38),(70,83,91),(244,235,219),(147,143,134),(233,174,94)]:
            for shown,want in zip(engine_rgb(compensate(colour),is_image=False),colour):
                self.assertAlmostEqual(shown,want,delta=4,msg=colour)

    def test_home_active_list_overflow_and_sheet_scrolls(self):
        for defn in self.api.catalog.values():
            if defn.id!='revive':
                self.api.apply(defn.id,True,str(defn.default)); self.drain_income()
        self.api.click('open')
        self.assertIn('查看全部',self.g.widgets['wh3pt_sheet_open'].text)
        self.api.click('sheet_open')
        self.assertIn('34 项',self.g.widgets['wh3pt_sheet_title'].text)
        self.assertEqual(len(self.list_rows()),34)
        self.api.click('sheet_close')
        self.assertTrue(self.g.widgets['wh3pt_sheet_title'].dead)
        self.assertEqual(self.list_rows(),set())
        self.api.click('lock_all'); self.g.flush()
        self.assertIn('0 / 7',self.g.widgets['wh3pt_secr_locks'].text)

    # ---- Scrolling and dragging --------------------------------------------------
    def test_scroll_viewport_is_fixed_and_uses_native_clipping(self):
        root = ET.fromstring(self.files['ui\\wh3pt\\list.twui.xml'])
        clip = root.find('components/list_clip')
        self.assertEqual(clip.get('clipchildren'), 'true')
        # A layout engine on the viewport can expand it to the tall child;
        # only list_box owns the size-to-content layout.
        self.assertIsNone(clip.find('LayoutEngine'))
        state = clip.find('states/newstate')
        self.assertEqual(state.get('shader_name'), 'border_alpha_blend')
        self.assertNotEqual(state.get('interactive'), 'true')
        self.assertEqual(root.find('components/list_box/LayoutEngine').get('sizetocontent'), 'true')
        self.assertIsNotNone(root.find("components/list/callbackwithcontextlist/callback_with_context[@callback_id='Listview']"))

    def test_no_page_controls_remain(self):
        self.api.click('open'); self.api.click('tab_全部')
        for name in ('prev','next','page','sheet_prev','sheet_next','load_more'):
            self.assertIsNone(self.g.widgets['wh3pt_'+name],name)
        self.assertEqual(self.g.widgets['wh3pt_list'].template,'ui/wh3pt/list')
        content=self.g.widgets['wh3pt_list_content']
        self.assertGreater(content.h,self.g.widgets['list_clip'].h)
        self.assertEqual(self.g.widgets['list_box'].h,content.h)

    def test_list_keeps_position_and_rebuilds_only_the_changed_row(self):
        self.api.click('open'); self.api.click('tab_全部')
        view=self.g.widgets['wh3pt_list'].seq
        other=self.g.widgets['wh3pt_card_attack'].seq
        self.api.click('toggle_ward'); self.g.flush()
        self.assertEqual(self.g.widgets['wh3pt_list'].seq,view)
        self.assertEqual(self.g.widgets['wh3pt_card_attack'].seq,other)
        self.assertEqual(self.g.widgets['wh3pt_card_ward'].state,'enabled')
        self.api.click('plus_defence')
        self.assertEqual(self.g.widgets['wh3pt_list'].seq,view)
        self.assertEqual(self.g.widgets['wh3pt_card_attack'].seq,other)
        self.assertEqual(self.g.widgets['wh3pt_input_defence'].text,'25')
        self.api.click('tab_部队')
        self.assertNotEqual(self.g.widgets['wh3pt_list'].seq,view)

    def test_rebuilt_row_lands_where_the_list_has_scrolled_to(self):
        self.api.click('open'); self.api.click('tab_部队')
        content=self.g.widgets['wh3pt_list_content']
        first=self.g.widgets['wh3pt_card_ward']
        self.assertEqual((first.x,first.y),(content.x,content.y))
        content.MoveTo(content,content.x,content.y-150)
        self.api.click('toggle_ward'); self.g.flush()
        self.assertEqual(self.g.widgets['wh3pt_card_ward'].y,content.y)

    def test_launcher_drag_is_remembered_and_is_not_a_click(self):
        launcher=self.g.widgets['wh3pt_open']
        self.assertTrue(launcher.moveable)
        launcher.MoveTo(launcher,300,200)
        self.lua.execute("core:trigger_event('ComponentMoved', {string='wh3pt_open'})")
        self.lua.execute("core:trigger_event('ComponentLClickUp', {string='wh3pt_open'})")
        self.assertFalse(self.g.widgets['wh3pt_panel'].visible)
        self.assertEqual((self.g.saved['wh3pt_launcher_x'],self.g.saved['wh3pt_launcher_y']),(300,200))
        self.lua.execute("core:trigger_event('ComponentLClickUp', {string='wh3pt_open'})")
        self.assertTrue(self.g.widgets['wh3pt_panel'].visible)
        launcher=self.g.widgets['wh3pt_open']
        self.assertEqual((launcher.x,launcher.y),(300,200))
        self.api.start()
        launcher=self.g.widgets['wh3pt_open']
        self.assertEqual((launcher.x,launcher.y),(300,200))

    def test_collapsed_rail_is_short_draggable_and_stays_on_screen(self):
        self.api.click('open'); self.api.click('collapse')
        panel=self.g.widgets['wh3pt_panel']
        self.assertTrue(panel.moveable)
        self.assertLess(panel.h,480)
        self.assertLessEqual(self.g.widgets['wh3pt_collapse_rail'].y+self.g.widgets['wh3pt_collapse_rail'].h,panel.y+panel.h)
        panel.MoveTo(panel,500,300)
        self.lua.execute("core:trigger_event('ComponentMoved', {string='wh3pt_panel'})")
        self.api.click('collapse_rail')
        panel=self.g.widgets['wh3pt_panel']
        self.assertEqual((panel.x,panel.y),(500,300))
        self.assertEqual(panel.w,460)
        self.g.widgets['wh3pt_panel'].MoveTo(self.g.widgets['wh3pt_panel'],1800,900)
        self.lua.execute("core:trigger_event('ComponentMoved', {string='wh3pt_panel'})")
        self.api.start()
        panel=self.g.widgets['wh3pt_panel']
        self.assertLessEqual(panel.x+panel.w,self.g.screen_width)
        self.assertLessEqual(panel.y+panel.h,self.g.screen_height)

    def test_content_follows_a_dragged_panel(self):
        self.api.click('open'); self.api.click('tab_战役')
        panel=self.g.widgets['wh3pt_panel']
        panel.MoveTo(panel,100,120)
        self.api.click('tab_部队')
        self.assertEqual(self.g.widgets['wh3pt_list'].x,100+80)
        self.assertEqual(self.g.widgets['wh3pt_search'].y,120+60)
        self.assertEqual(self.g.widgets['wh3pt_card_ward'].y,120+102)

    def test_pack_has_no_always_on_handicaps_or_global_cap_and_unique_abilities(self):
        self.assertFalse(any('campaign_difficulty_handicap' in n or '_kv_' in n for n in self.files))
        self.assertEqual(len(self.report['features']),35)
        self.assertIn('chainsword',[f['id'] for f in self.report['features']])
        self.assertEqual(self.feature('health').scope,'faction_to_character_own')
        self.assertIn('领主与英雄',self.feature('health').label)
        self.assertEqual(self.feature('recruitment').scope,'faction_to_province_own')
        self.assertEqual(self.feature('recruitment').effect,'wh_main_effect_unit_recruitment_points')
        for n,b in self.files.items():
            if n.endswith('.xml'):
                root=ET.fromstring(b)
                for img in root.findall('.//image'):
                    self.assertEqual(img.attrib.get('canresizewidth'),'true',n)
                    self.assertEqual(img.attrib.get('canresizeheight'),'true',n)
            if n.startswith('db\\unit_special_abilities_tables'):
                rows=decode('unit_special_abilities_tables',b)[1]
                self.assertEqual(len(rows),12)
                self.assertEqual(len({r['unique_id'] for r in rows}),12)
            if n.startswith('db\\special_ability_phases_tables'):
                rows=decode('special_ability_phases_tables',b)[1]
                for row in rows:
                    if 'ammo' in row['id']:
                        self.assertEqual(row['heal_amount'],0); self.assertFalse(row['resurrect'])
                    else: self.assertEqual(row['replenish_ammo'],0)

    def test_optional_addons_bundle_cleanly(self):
        payload,report=build_ingame(addons={
            'building':True,'ai':True,'cap':True,'cap_value':888,
            'shop':True,'ballistic':True,'agent_ai':True,'agent_ai_value':50,
        })
        self.assertEqual(set(report['addons']),{'building','ai','cap','shop','ballistic','agent_ai'})
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'addons.pack'; p.write_bytes(payload)
            files=read_pack(p)
            self.assertTrue(any('_kv_unit_ability_scaling_rules_tables' in k for k in files))
            self.assertTrue(any('campaign_difficulty_handicap_effects_tables' in k for k in files))
            self.assertTrue(any('projectiles_tables' in k for k in files))

    def test_restoration_variants_have_only_their_selected_mechanics_and_matching_ui(self):
        tables={}
        for name,data in self.files.items():
            if name.startswith('db\\'):
                table=name.split('\\')[1]
                if table in {'special_ability_phases_tables','unit_special_abilities_tables',
                             'special_ability_to_special_ability_phase_junctions_tables',
                             'unit_abilities_to_additional_ui_effects_juncs_tables'}:
                    tables.setdefault(table,[]).extend(decode(table,data)[1])
        phases=tables['special_ability_phases_tables']
        self.assertEqual(len(phases),12)
        expected={'healing':[.01,.05,.125,.25], 'revive':[.01,.05,.125,.25], 'ammo':[.05,.20,.50,1]}
        for row in phases:
            with self.subTest(phase=row['id']):
                _,feature,level=row['id'].split('_')
                rate=expected[feature][int(level)-1]
                self.assertAlmostEqual(row['heal_amount'],rate if feature!='ammo' else 0)
                self.assertAlmostEqual(row['replenish_ammo'],rate if feature=='ammo' else 0)
                self.assertEqual(row['resurrect'],feature=='revive')
                self.assertEqual(row['hp_change_frequency'],1)
                self.assertEqual(row['duration'],1 if feature=='ammo' else -1)
                for field in ('ability_recharge_change','mana_regen_mod','mana_max_depletion_mod',
                              'damage_amount','barrier_heal_amount','fatigue_change_ratio',
                              'inspiration_aura_range_mod','execute_ratio'):
                    self.assertEqual(row[field],0,(row['id'],field))
                self.assertFalse(row['freeze_fatigue'])
                self.assertFalse(row['freeze_recharge'])
                self.assertFalse(row['affects_enemies'])
        for row in tables['unit_special_abilities_tables']:
            self.assertEqual(row['additional_melee_cp'],0,row['key'])
            self.assertEqual(row['additional_missile_cp'],0,row['key'])
            # Every faction unit owns one self effect, with no friendly aura.
            self.assertEqual(row['effect_range'],0,row['key'])
            self.assertEqual(row['num_effected_friendly_units'],0,row['key'])
            self.assertFalse(row['update_targets_every_frame'],row['key'])
            self.assertTrue(row['passive'])
            self.assertEqual(row['num_uses'],-1,row['key'])
            ammo=row['key'].startswith('wh3pt_ammo_')
            self.assertEqual(row['active_time'],1 if ammo else -1,row['key'])
            self.assertEqual(row['recharge_time'],0 if ammo else -1,row['key'])
            self.assertEqual(row['initial_recharge'],0,row['key'])
        for row in tables['special_ability_to_special_ability_phase_junctions_tables']:
            self.assertTrue(row['target_self'])
            self.assertFalse(row['target_friends'])
            self.assertFalse(row['target_enemies'])
        for row in tables['unit_abilities_to_additional_ui_effects_juncs_tables']:
            expected_ui='wh2_dlc11_all_replenish_ammo' if row['ability'].startswith('wh3pt_ammo_') else 'wh_main_all_replenish'
            self.assertEqual(row['effect'],expected_ui,row['ability'])
        self.assertEqual(self.feature('armour').unit,'点')
        self.assertIn('不含爆炸',self.feature('missile_damage').hint)
        self.assertIn('被动',self.feature('chainsword').hint)
        for feature in ('healing','revive','ammo'):
            self.assertIn('自身',self.feature(feature).hint)
            self.assertEqual(self.feature(feature).unit,'% / 秒')


if __name__ == '__main__': unittest.main()
