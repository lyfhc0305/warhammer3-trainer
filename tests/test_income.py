from pathlib import Path
import unittest
from lupa.lua51 import LuaRuntime

from tests import ROOT, SRC, REFERENCE


class SpecialIncomeTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute((ROOT / 'tests/runtime_mock.lua').read_text(encoding='utf-8'))
        self.lua.execute((SRC / 'ingame/income.lua').read_text(encoding='utf-8') + '\n_income_factory=create_income_actions')
        self.lua.execute('''
            active_name='player';messages={};transactions={};queries={};reject_resource=false
            known_bundle_keys={wh3pt_income=true}
            definitions={
                {id='energy',label='亡灵术能量',factor='other',region_group='land_of_the_dead',faction='player'},
                {id='devotion',label='虔信',factor='other',faction='kislev'},
                {id='supplies',label='补给',factor='other',faction='kislev'}
            }
            for _,d in ipairs(definitions) do d.income_effect='income_'..d.id;d.income_scope=d.region_group and 'faction_to_region_own_regions' or 'faction_to_faction_own' end
            pools={}
            for _,d in ipairs(definitions) do
                local p={id=d.id,amount=11937,max=2000000000,pending={131,-20},factor_value=0}
                function p:value() return self.amount end
                function p:maximum_value() return self.max end
                function p:factor_by_key(key)
                    assert(key=='other')
                    return {is_null_interface=function() return false end,
                        value=function() return p.factor_value end,
                        maximum_value=function() return p.factor_max or 2000000000 end}
                end
                pools[d.id]=p
            end
            cco=nil
            common={}
            function common.get_context_value(kind, cqi, expression)
                assert(kind=='CcoCampaignFaction' and cqi==47)

                    queries[#queries+1]=expression
                    if bad_read then error('native CCO unavailable') end
                    if expression=='SettlementList().Size()' then return no_settlement and 0 or 2 end
                    if expression:find('BelongsToRegionGroup') then
                        error('Dynamic manager ids cannot be used as static region group keys')
                    end
                    if expression:match('^IsContextValid%(') then
                        return not missing_regional_resource and expression:find('At(1)') ~= nil
                    end
                    local key=expression:match('PooledResourceContext%("([%w_]+)"%)')
                        or expression:match('FindPooledResourceFromSettlementRegionGroups%("([%w_]+)"%)')
                    local p=assert(pools[key],expression)
                    if expression:match('%.Key%(%)$') then return p.id end
                    if expression:match('%.PendingFactorList%(%)%.Size%(%)$') then return #p.pending end
                    local i=expression:match('PendingFactorList%(%)%.At%((%d+)%)%.IntValue%(%)$')
                    assert(i, 'Only pending recurring factors may be read: '..expression)
                    local value=p.pending[tonumber(i)+1]
                    if value>0 and bundles.wh3pt_income then
                        for _,effect in ipairs(bundles.wh3pt_income.entries) do
                            if effect:key()=='income_'..p.id then value=math.floor(value*(1+effect:value()/100)+.5) end
                        end
                    end
                    return value
            end
            local player=cm:get_faction('player')
            function player:name() return active_name end
            function player:command_queue_index() return 47 end
            function cm:treasury_mod() error('Native income must not grant treasury') end
            function cm:remove_effect_bundle(key,name) bundles[key]=nil end
            function cm:pooled_resource_factor_transaction(resource, factor, amount)
                transactions[#transactions+1]={id=resource.id,amount=amount}
                if not reject_resource then
                    resource.amount=resource.amount+amount
                    resource.factor_value=resource.factor_value+amount
                end
            end
            income=_income_factory({faction=function() return player end,
                resources=definitions,resource=function(def)
                    if def.faction==active_name then return pools[def.id] end
                end,
                message=function(text) messages[#messages+1]=text end,
                guard=function(fn) local ok,err=pcall(fn);if not ok then logs[#logs+1]=err end end})
            income.start()
            function settle()
                core:trigger_event('FactionTurnEnd',{faction=function() return player end})
            end
            function start_turn()
                core:trigger_event('FactionTurnStart',{faction=function() return player end})
                drain_income()
            end
        ''')
        self.lua.execute("""
            function drain_income()
                for i=1,40 do flush() end
            end
            local raw_set,raw_refresh=income.set,income.refresh
            function begin_income(v)
                local wrapped=income.refresh
                income.refresh=raw_refresh
                raw_set(v)
                income.refresh=wrapped
            end
            function income.set(v)
                local count=#logs
                raw_set(v);drain_income()
                if income.failure() then error(income.failure()) end
            end
            function income.refresh(force)
                raw_refresh(force);drain_income()
                if income.failure() then error(income.failure()) end
            end
        """)
        self.g = self.lua.globals()

    def native_resource_prediction(self, key):
        p=self.g.pools[key]
        value=sum(max(0,v) for v in p.pending.values())
        bundle=self.g.bundles.wh3pt_income
        if bundle:
            for effect in bundle.entries.values():
                if effect.key(effect)=='income_'+key:
                    value*=1+effect.value(effect)/100
        return round(value)

    def test_direct_native_percent_no_prediction_queries_or_payments(self):
        self.g.bad_read=True
        self.g.income.set(200)
        b=self.g.bundles.wh3pt_income
        self.assertTrue(all(e.value(e)==200 for e in b.entries.values()))
        self.assertEqual(len(self.g.queries),0)
        self.assertEqual(len(self.g.transactions),0)
        self.assertEqual(self.g.treasury_balance,5000)
        self.assertEqual(self.g.pools.energy.amount,11937)

    def test_every_recurring_gold_source_gets_the_same_percent(self):
        self.g.income.set(150)
        found={(e.key(e),e.scope(e)):e.value(e) for e in self.g.bundles.wh3pt_income.entries.values()}
        for effect,scope in [('wh_main_effect_economy_gdp_mod_all','faction_to_region_own_regions'),
                             ('wh_main_effect_economy_trade_tariff_mod','faction_to_faction_own'),
                             ('wh_main_effect_force_all_campaign_raid_income','faction_to_force_own'),
                             ('wh_main_effect_modify_vassal_income','faction_to_faction_own')]:
            self.assertEqual(found[(effect,scope)],150)

    def reject_effect(self,key):
        self.lua.globals().rejected_effect=key
        self.lua.execute("""
            local create=cm.create_new_custom_effect_bundle
            function cm:create_new_custom_effect_bundle(key)
                local b=create(self,key)
                local add=b.add_effect
                function b:add_effect(effect,scope,value)
                    if effect==rejected_effect then return false end
                    return add(self,effect,scope,value)
                end
                return b
            end
        """)

    def test_rejected_unverified_source_keeps_the_verified_bonus(self):
        self.reject_effect('wh_main_effect_modify_vassal_income')
        self.g.income.set(100)
        keys={e.key(e) for e in self.g.bundles.wh3pt_income.entries.values()}
        self.assertIn('wh_main_effect_economy_gdp_mod_all',keys)
        self.assertNotIn('wh_main_effect_modify_vassal_income',keys)
        self.assertEqual(self.g.income.read(),(True,100))
        self.assertTrue(any('wh_main_effect_modify_vassal_income' in s for s in self.g.logs.values()))

    def test_rejected_verified_source_stops_the_bonus(self):
        self.reject_effect('wh_main_effect_economy_gdp_mod_all')
        with self.assertRaisesRegex(Exception,'游戏拒绝收入效果'): self.g.income.set(100)
        self.assertFalse(self.g.income.read()[0])
        self.assertIsNone(self.g.bundles.wh3pt_income)

    def test_switching_replaces_and_off_removes_bonus(self):
        self.g.income.set(200)
        self.g.income.set(100)
        self.assertEqual(self.native_resource_prediction('energy'),262)
        self.g.income.set(None)
        self.assertIsNone(self.g.bundles.wh3pt_income)
        self.assertEqual(self.g.income.read(),(False,None))

    def test_turn_events_and_reload_never_compound_or_remove_income(self):
        self.g.income.set(100)
        self.g.settle();self.g.start_turn();self.g.income.start();self.g.drain_income()
        self.assertEqual(self.native_resource_prediction('energy'),262)
        self.assertIsNone(self.g.campaign_timers.wh3pt_income_prediction)
        self.g.pools.energy.pending=self.lua.table_from([200,-20])
        self.assertEqual(self.native_resource_prediction('energy'),400)
        self.assertEqual(len(self.g.transactions),0)

    def test_rejected_bundle_is_failure(self):
        self.g.ignore_apply=True
        with self.assertRaisesRegex(Exception,'收入效果包未生效'): self.g.income.set(200)
        self.assertFalse(self.g.income.read()[0])

    def test_reload_failure_is_reported_instead_of_silently_switching_off(self):
        self.g.income.set(100)
        self.g.messages=self.lua.table_from([])
        self.g.ignore_apply=True
        self.g.bundles.wh3pt_income=None
        self.g.income.start();self.g.drain_income()
        self.assertFalse(self.g.income.read()[0])
        self.assertTrue(any('收入增幅已暂停' in s for s in self.g.messages.values()))
        self.assertTrue(any('收入效果包未生效' in s for s in self.g.logs.values()))

    def test_disabled_before_readback_stays_disabled(self):
        self.g.begin_income(200);self.g.begin_income(None);self.g.drain_income()
        self.assertFalse(self.g.income.read()[0])
        self.assertIsNone(self.g.bundles.wh3pt_income)

    def test_faction_switch_matches_resources_and_saved_settings(self):
        self.g.income.set(100)
        self.g.active_name='kislev'
        self.assertEqual(self.g.income.read(),(False,None))
        self.assertIsNone(self.g.bundles.wh3pt_income)
        self.g.income.set(200)
        self.assertEqual(self.native_resource_prediction('energy'),131)
        self.assertEqual(self.native_resource_prediction('devotion'),393)

    def test_invalid_percent_rejected(self):
        for value in (0,0.5,10001,float('nan'),float('inf')):
            with self.assertRaises(Exception): self.g.income.set(value)

    def test_sigils_are_not_recurring_income(self):
        self.g.definitions[1].kind='sigils'
        self.g.income.set(200)
        self.assertEqual(self.native_resource_prediction('energy'),131)

    def test_saved_multiplier_migrates_once_to_percent(self):
        saved=self.g.saved
        saved.wh3pt_player_income_enabled=True
        saved.wh3pt_player_income_value=3
        self.g.income.migrate()
        self.assertEqual(self.g.income.read(),(True,200))
        self.g.income.migrate()
        self.assertEqual(self.g.income.read(),(True,200))

    def test_saved_one_times_had_no_bonus_and_migrates_to_off(self):
        saved=self.g.saved
        saved.wh3pt_player_income_enabled=True
        saved.wh3pt_player_income_value=1
        self.g.income.migrate()
        self.assertEqual(self.g.income.read(),(False,None))

    def test_forecast_change_is_reported_from_the_engine(self):
        self.g.income.set(100)
        self.assertTrue(any('1000 → 2000' in s for s in self.g.messages.values()))

    def test_unchanged_forecast_reports_nothing(self):
        self.g.income.set(100)
        self.g.messages=self.lua.table_from([])
        self.g.income.set(100)
        self.assertEqual(len(self.g.messages),0)


class NativeIncomeDataTests(unittest.TestCase):
    def test_income_links_only_positive_recurring_factors_and_preserve_vanilla_rows(self):
        from trainer_core import GAME
        from strategic_data import strategic_catalog
        from income_data import native_income_files
        from pack_io import read_pack
        from database import decode
        catalog=strategic_catalog(GAME)
        files=native_income_files(GAME,catalog)
        factors={r['unique_id']:r for n,b in read_pack(Path(GAME)/'data/db.pack',lambda n:n.startswith('db\\pooled_resource_factor_junctions_tables\\')).items() for r in decode(n.split('\\')[1],b)[1]}
        rows=[r for n,b in files.items() if n.startswith('db\\effect_bonus_value_pooled_resource_factor_junctions_tables\\') for r in decode(n.split('\\')[1],b)[1]]
        self.assertGreater(len(rows),10)
        for row in rows:
            self.assertTrue(row['effect'].startswith('wh3pt_income_'))
            self.assertEqual(row['bonus_value_id'],'percentage_multiplier_post_mod')
            self.assertGreaterEqual(factors[row['resource_factor']]['minimum'],0)
        nag=next(r for r in catalog['resources'] if r['id']=='wh3_dlc29_nag_necromantic_energy')
        links={r['resource_factor'] for r in rows if r['effect']==nag['income_effect']}
        self.assertEqual(links,{'wh3_dlc29_nag_necromantic_energy_buildings','wh3_dlc29_nag_necromantic_energy_characters'})
        self.assertEqual(nag['income_scope'],'faction_to_region_own_regions')


if __name__ == '__main__':
    unittest.main()
