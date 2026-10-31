from pathlib import Path
import unittest
from lupa.lua51 import LuaRuntime

from tests import ROOT, SRC, REFERENCE


class MortarchTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute('''
            owner='wh3_dlc29_nag_host_of_nagash'; player=owner
            saved={}; locks={}; queue={}; messages={}; listeners={}; effects={}
            nag_mortarchs_config={locked_buildings_ritual={
                wh3_dlc29_nag_mortarchs_neferata='wh3_dlc29_nag_necropolis_military_melee_5',
                wh3_dlc29_nag_mortarchs_walach='wh3_dlc29_nag_necropolis_military_cavalry_5'}}
            building_locks={unrelated_building=true};building_calls={}
            for _,b in pairs(nag_mortarchs_config.locked_buildings_ritual) do building_locks[b]=true end
            f={name=function() return player end}
            function f:rituals() return {ritual_status=function(_,key)
                return {is_null_interface=function() return false end,
                    script_locked=function() return locks[key]~=false end}
            end} end
            cm={}
            function cm:remove_event_restricted_building_record_for_faction(b,faction)
                assert(faction==owner)
                if reject_buildings then error('building API failed') end
                building_calls[#building_calls+1]=b; building_locks[b]=nil
            end
            function cm:get_saved_value(k) return saved[k] end
            function cm:set_saved_value(k,v) saved[k]=v end
            function cm:unlock_ritual(f,k,d) if not reject_unlock then locks[k]=false end end
            function cm:lock_ritual(f,k) locks[k]=true end
            function cm:create_new_custom_effect_bundle(k)
                assert(k=='wh3pt_mortarchs')
                local b={items={}}
                function b:is_null_interface() return false end
                function b:add_effect(key,scope,v)
                    assert(scope=='faction_to_faction_own');self.items[key]=v;return true
                end
                function b:set_duration(v) assert(v==0) end
                return b
            end
            function cm:apply_custom_effect_bundle_to_faction(b,f) effects=b.items end
            function cm:remove_effect_bundle(k,f) effects={} end
            core={}
            function core:remove_listener(k) listeners[k]=nil end
            function core:add_listener(k,event,cond,fn,persistent) listeners[k]=fn end
            env={faction=function() return f end,message=function(s) messages[#messages+1]=s end,
                callback=function(fn) queue[#queue+1]=fn end,guard=function(fn) fn() end}
            function flush() local q=queue;queue={};for _,fn in ipairs(q) do fn() end end
        ''')
        self.factory = self.lua.execute((SRC/'ingame/mortarchs.lua').read_text(encoding='utf-8')+'\nreturn create_mortarch_actions')
        self.g = self.lua.globals()
        self.api = self.factory(self.g.env)

    def test_nine_native_rituals_unlock_with_cost_effect(self):
        self.api.toggle(True)
        self.assertEqual(len(list(self.g.locks.keys())),9)
        self.assertTrue(all(v is False for v in self.g.locks.values()))
        self.assertEqual(len(list(self.g.effects.keys())),10)
        self.assertEqual(self.g.effects['wh3_dlc29_effect_cost_mortarchs_subjugation'],-1000)

    def test_disable_restores_original_locks_without_relocking_natural_unlock(self):
        arkhan='wh3_dlc29_nag_mortarchs_arkhan'
        self.g.locks[arkhan]=False
        self.api.toggle(True)
        self.api.toggle(False)
        self.assertFalse(self.g.locks[arkhan])
        self.assertTrue(self.g.locks['wh3_dlc29_nag_mortarchs_nameless'])
        self.assertEqual(len(list(self.g.effects.keys())),0)

    def test_native_mission_progress_is_preserved_and_relock_is_overridden(self):
        self.api.toggle(True)
        self.lua.execute('''
            ctx={faction=function() return f end,
                mission=function() return {mission_record_key=function() return 'wh3_dlc29_nag_mortarchs_nameless' end} end,
                total_primary_objectives=function() return 3 end,
                completed_primary_objectives=function() return 2 end}
            listeners.wh3pt_mortarch_mission(ctx)
            locks.wh3_dlc29_nag_mortarchs_nameless=true
        ''')
        self.g.flush()
        self.assertFalse(self.g.locks['wh3_dlc29_nag_mortarchs_nameless'])
        self.api.toggle(False)
        self.assertFalse(self.g.locks['wh3_dlc29_nag_mortarchs_nameless'])

    def test_other_faction_cannot_activate_and_reload_restores_enabled(self):
        self.g.player='other'
        with self.assertRaises(Exception):self.api.toggle(True)
        self.assertEqual(len(list(self.g.locks.keys())),0)
        self.g.player=self.g.owner
        self.api.toggle(True)
        restored=self.factory(self.g.env);self.g.flush()
        self.assertTrue(restored.enabled())

    def test_failed_native_unlock_not_reported_as_success(self):
        self.g.reject_unlock=True
        with self.assertRaises(Exception):self.api.toggle(True)
        self.assertEqual(len(list(self.g.messages.values())),0)

    def test_buildings_unlock_only_native_associated_records_without_subjugating(self):
        self.api.unlock_buildings()
        self.assertEqual(set(self.g.building_calls.values()),{
            'wh3_dlc29_nag_necropolis_military_melee_5',
            'wh3_dlc29_nag_necropolis_military_cavalry_5'})
        self.assertTrue(self.g.building_locks.unrelated_building)
        self.assertEqual(len(list(self.g.locks.keys())),0)
        self.assertFalse(self.api.enabled())
        self.assertTrue(self.g.saved['wh3pt_'+self.g.owner+'_mortarchs_buildings_unlocked'])

    def test_buildings_persist_on_reload_and_native_first_turn_relock(self):
        self.api.unlock_buildings()
        self.api.toggle(True);self.api.toggle(False)
        self.g.building_locks['wh3_dlc29_nag_necropolis_military_melee_5']=True
        restored=self.factory(self.g.env);self.g.flush()
        self.assertIsNone(self.g.building_locks['wh3_dlc29_nag_necropolis_military_melee_5'])
        self.assertFalse(restored.enabled())
        self.g.building_locks['wh3_dlc29_nag_necropolis_military_cavalry_5']=True
        self.g.listeners.wh3pt_mortarch_FactionTurnStart();self.g.flush()
        self.assertIsNone(self.g.building_locks['wh3_dlc29_nag_necropolis_military_cavalry_5'])

    def test_buildings_other_faction_and_failed_api_never_report_success(self):
        self.g.player='other'
        self.assertFalse(self.api.buildings_supported())
        with self.assertRaises(Exception):self.api.unlock_buildings()
        self.assertEqual(len(list(self.g.building_calls.values())),0)
        self.g.player=self.g.owner;self.g.reject_buildings=True
        with self.assertRaises(Exception):self.api.unlock_buildings()
        self.assertIsNone(self.g.saved['wh3pt_'+self.g.owner+'_mortarchs_buildings_unlocked'])
        self.assertEqual(len(list(self.g.messages.values())),0)

    def test_buildings_queued_refresh_does_not_touch_another_player(self):
        self.api.unlock_buildings();self.api.schedule()
        self.g.player='other'
        self.g.flush()
        self.assertEqual(len(list(self.g.building_calls.values())),2)

    def test_buildings_empty_map_is_not_success(self):
        self.g.nag_mortarchs_config.locked_buildings_ritual=self.lua.table()
        self.assertFalse(self.api.buildings_supported())
        with self.assertRaises(Exception):self.api.unlock_buildings()
        self.assertEqual(len(list(self.g.messages.values())),0)


if __name__=='__main__': unittest.main()
