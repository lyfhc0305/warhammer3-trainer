from pathlib import Path
import unittest
from lupa.lua51 import LuaRuntime
from build_ingame import battle_spell_keys
from pack_io import read_pack
from trainer_core import GAME

from tests import ROOT, SRC, REFERENCE


class BattleTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute((ROOT / 'tests/runtime_mock.lua').read_text(encoding='utf-8'))
        self.lua.execute('''
            function collection(items)
                return {count=function() return #items end, item=function(_,i) return items[i] end}
            end
            function make_unit(id, player, abilities)
                local u={id=id, player=player, abilities=abilities or {}, men=50, hp=.5,
                    ammo=0, initial=100, deployed=true, disabled={}, calls=0}
                function u:unique_ui_id() return self.id end
                function u:is_player_controlled() return self.player end
                function u:owned_non_passive_special_abilities() return self.spells or {} end
                function u:army() return self.owner_army end
                function u:owned_passive_special_abilities() return self.abilities end
                function u:disable_special_ability(key,on) self.disabled[key]=on end
                function u:number_of_men_alive() return self.men end
                function u:initial_number_of_men() return self.initial end
                function u:unary_hitpoints() return self.hp end
                function u:is_deployed() return self.deployed end
                function u:heal_hitpoints_unary(target,resurrect)
                    self.calls=self.calls+1
                    if self.reject then return end
                    self.hp=target
                    if resurrect and not self.unsupported then self.men=math.floor(self.initial*target+.00001) end
                end
                function u:starting_ammo() return 20 end
                function u:ammo_left() return self.ammo end
                function u:set_current_ammo_unary(v) if not self.reject then self.ammo=20*v end end
                return u
            end
            own=make_unit(1,true,{'wh3pt_revive_2','native_other_heal'})
            ordinary=make_unit(2,true)
            ally=make_unit(3,false,{'wh3pt_revive_2'})
            enemy=make_unit(4,false,{'wh3pt_revive_2'})
            all_units={own,ordinary,ally}
            function make_army(id,player,items)
                local a={id=id,player=player,current=5,reserve=10,items=items,calls=0,controllers={}}
                function a:faction_key() return self.faction or 'player' end
                function a:unique_id() return self.id end
                function a:is_player_controlled() return self.player end
                function a:units() return collection(self.items) end
                function a:winds_of_magic_current() return self.current end
                function a:winds_of_magic_reserve() return self.reserve end
                function a:modify_winds_of_magic_current(n,direct)
                    assert(direct==true and n>0);self.calls=self.calls+1
                    if not self.reject then self.current=self.current+n end
                end
                function a:modify_winds_of_magic_reserve(n)
                    assert(n>0);self.calls=self.calls+1
                    if not self.reject then self.reserve=self.reserve+n end
                end
                function a:create_unit_controller()
                    local uc={resets={},releases=0}
                    function uc:add_units(u) assert(u.player);self.unit=u end
                    function uc:clear_all() self.unit=nil end
                    function uc:reset_ability_cooldown(key)
                        self.unit.script_control=true
                        if self.unit.reject_reset then error('cooldown API failure') end
                        self.resets[#self.resets+1]=key
                        self.unit.cooldowns=self.unit.cooldowns or {}
                        self.unit.cooldowns[key]=0
                    end
                    function uc:release_control()
                        self.releases=self.releases+1
                        if self.unit then self.unit.script_control=false end
                    end
                    self.controllers[#self.controllers+1]=uc
                    return uc
                end
                for _,u in ipairs(items) do u.owner_army=a end
                return a
            end
            player_army=make_army(1,true,all_units)
            allied_army=make_army(2,false,{})
            enemy_army=make_army(3,false,{enemy})
            battle_armies={player_army,allied_army}
            phases, timers, callbacks = {}, {}, {}
            bm={}
            function bm:is_multiplayer() return multiplayer end
            function bm:is_replay() return replay==true end
            function bm:get_player_alliance()
                return {armies=function() return collection(battle_armies) end}
            end
            function bm:callback(fn,delay,name) callbacks[#callbacks+1]=fn end
            function bm:real_callback(fn,delay,name) timers[name]=fn end
            function bm:repeat_callback(fn,delay,name) timers[name]=fn end
            function bm:remove_process(name) timers[name]=nil end
            function bm:register_phase_change_callback(name,fn) phases[name]=fn end
            function flush_battle() local q=callbacks; callbacks={}; for _,fn in ipairs(q) do fn() end end
        ''')
        module = self.lua.execute((SRC / 'ingame/battle.lua').read_text(encoding='utf-8'))
        self.g = self.lua.globals()
        self.module = module
        self.spells = battle_spell_keys()
        self.g.SPELL_KEYS = self.lua.table_from(self.spells)  # same catalog injected by pack builder
        self.api = module.new(self.g.bm,self.lua.table_from(self.spells))

    def publish_campaign_magic(self, owner='player'):
        self.g.svr.wh3pt_magic_pending = True
        self.g.svr.wh3pt_magic_owner = owner
        self.g.svr.wh3pt_magic_enabled = True
        self.g.svr.wh3pt_cooldown_enabled = True

    def test_campaign_magic_inheritance_consumed_once_and_local_override(self):
        self.publish_campaign_magic()
        self.assertTrue(self.api.start())
        self.assertEqual(self.api.magic_state(), (True, True))
        self.assertFalse(self.g.svr.wh3pt_magic_pending)
        self.g.timers.wh3pt_battle_magic()
        self.assertEqual((self.g.player_army.current,self.g.player_army.reserve),(30,100))
        self.api.action('magic')
        self.assertEqual(self.api.magic_state(), (False, True))
        self.assertTrue(self.g.svr.wh3pt_magic_enabled)  # local switch never overwrites preferences
        another = self.module.new(self.g.bm, self.lua.table_from(self.spells))
        another.start()
        self.assertEqual(another.magic_state(), (False, False))

    def test_campaign_magic_rejects_other_faction_replay_and_multiplayer(self):
        self.publish_campaign_magic('other_faction')
        self.api.start()
        self.assertEqual(self.api.magic_state(), (False, False))
        self.assertFalse(self.g.svr.wh3pt_magic_pending)
        for field in ('replay', 'multiplayer'):
            self.publish_campaign_magic()
            self.g[field] = True
            other = self.module.new(self.g.bm, self.lua.table_from(self.spells))
            self.assertFalse(other.start())
            self.assertFalse(self.g.svr.wh3pt_magic_pending)
            self.assertEqual(other.magic_state(), (False, False))
            self.g[field] = False

    def test_native_battle_loader_starts_runtime_without_execute_mods(self):
        # Execute the actual local native loader branch: it only loads chunks and
        # emits ScriptEventAllModsLoaded; it does not call the mod's named function.
        name = 'script\\_lib\\lib_mod_loader.lua'
        loader = read_pack(Path(GAME) / 'data/data_script.pack', lambda n: n == name)[name].decode('utf-8')
        loader = loader[loader.index('-- load mods here'):]
        self.lua.execute("""
            function core:is_campaign() return false end
            function core:is_battle() return true end
            function core:load_mods(...) return true end
            function core:execute_mods() error('Battle loader must not call execute_mods') end
        """)
        self.lua.execute(loader)
        runtime = self.g.wh3pt_battle()
        self.assertIsNotNone(self.g.timers.wh3pt_battle_revival)
        self.assertIsNotNone(self.g.timers.wh3pt_battle_magic)
        self.g.timers.wh3pt_battle_ui()
        self.assertIsNotNone(self.g.widgets.wh3pt_battle_open)
        self.g.core.trigger_event(self.g.core, 'ShortcutTriggered', self.lua.table_from({'string':'script_F9'}))
        self.assertTrue(self.g.widgets.wh3pt_battle_panel.visible)
        self.g.timers.wh3pt_battle_revival()
        self.assertEqual(self.g.own.men, 55)
        self.lua.execute("core:trigger_event('LoadingScreenDismissed', {}); core:trigger_event('ScriptEventAllModsLoaded', true)")
        self.assertEqual(sum('Started: revival timer' in line for line in self.g.logs.values()),1)
        self.assertEqual(len(list(self.g.timers.keys())),3)

    def test_all_battle_panel_actions_after_native_boot_event(self):
        self.publish_campaign_magic()
        self.lua.execute("own.spells={'wh_main_spell_fire_fireball'};core:trigger_event('ScriptEventAllModsLoaded', true)")
        self.g.timers.wh3pt_battle_ui()
        self.g.timers.wh3pt_battle_magic()
        self.assertEqual(self.g.player_army.current, 30)
        self.assertEqual(self.g.own.cooldowns['wh_main_spell_fire_fireball'], 0)
        def click(key):
            self.g.core.trigger_event(self.g.core, 'ComponentLClickUp', self.lua.table_from({'string':'wh3pt_battle_' + key}))
            self.g.flush_battle()
        click('heal')
        self.assertEqual(self.g.ordinary.hp, 1)
        self.assertEqual(self.g.ordinary.men, 50)
        click('ammo')
        self.assertEqual(self.g.ordinary.ammo, 20)
        click('revive')
        self.assertEqual(self.g.ordinary.men, 100)
        click('mode')
        self.g.ordinary.hp = .4
        self.g.ordinary.men = 40
        self.g.timers.wh3pt_battle_revival()
        self.assertEqual(self.g.ordinary.men, 45)
        click('magic'); click('cooldown')
        self.assertEqual(self.g.wh3pt_battle().magic_state(), (False, False))
        self.assertEqual(self.g.ally.calls,0)
        self.assertEqual(self.g.enemy.calls,0)

    def test_loading_screen_fallback_starts_and_multiplayer_stays_disabled(self):
        self.g.multiplayer = True
        self.lua.execute("core:trigger_event('ScriptEventAllModsLoaded', true)")
        self.assertEqual(len(list(self.g.timers.keys())),0)
        self.g.multiplayer = False
        self.lua.execute("core:trigger_event('LoadingScreenDismissed', {})")
        self.assertIsNotNone(self.g.timers.wh3pt_battle_revival)

    def test_campaign_revival_inherited_without_native_passive_and_explicit_off(self):
        self.publish_campaign_magic()
        self.g.svr.wh3pt_revive_level = '3'
        self.api.start()
        self.api.tick(False)
        self.assertEqual(self.g.ordinary.men, 62)  # no trainer passive on this unit
        self.assertEqual(self.g.ally.calls, 0)
        self.assertEqual(self.g.enemy.calls, 0)
        self.publish_campaign_magic()
        self.g.svr.wh3pt_revive_level = '0'
        other = self.module.new(self.g.bm, self.lua.table_from(self.spells))
        other.start()
        count = self.g.own.calls
        other.tick(True)
        self.assertEqual(self.g.own.calls, count)  # stale native passive cannot re-enable it
        other.action('mode')  # explicit battle override still works
        other.tick(True)
        self.assertEqual(self.g.ordinary.men, 100)

    def test_magic_refill_only_player_armies_and_switch_off(self):
        self.api.action('magic')
        self.assertEqual((self.g.player_army.current,self.g.player_army.reserve),(30,100))
        self.assertEqual(self.g.allied_army.calls,0)
        self.assertEqual(self.g.enemy_army.calls,0)
        self.g.player_army.current=2;self.g.player_army.reserve=0
        self.api.magic_tick()
        self.assertEqual((self.g.player_army.current,self.g.player_army.reserve),(30,100))
        self.g.player_army.current=80;self.g.player_army.reserve=250
        self.api.magic_tick()
        self.assertEqual((self.g.player_army.current,self.g.player_army.reserve),(80,250))
        self.api.action('magic')
        self.g.player_army.current=4
        self.api.magic_tick()
        self.assertEqual(self.g.player_army.current,4)

    def test_rejected_magic_mutation_pauses_and_reports_failure(self):
        self.g.player_army.reject=True
        self.api.action('magic')
        self.assertEqual(self.api.magic_state(),(False,False))
        self.assertIn('回读未达到目标',self.api.status()[0])

    def test_spell_cooldown_keys_and_controller_release(self):
        self.lua.execute("""
            own.spells={'wh_main_spell_fire_fireball','wh_main_spell_fire_fireball_upgraded',
                'wh2_dlc09_spell_death_spirit_leech_bound','native_non_spell'}
            ally.spells=own.spells;enemy.spells=own.spells
            own.uses=1
        """)
        self.api.action('cooldown')
        resets=list(self.g.player_army.controllers[1].resets.values())
        self.assertEqual(len(resets),3)
        self.assertNotIn('native_non_spell',resets)
        self.assertFalse(self.g.own.script_control)
        self.assertEqual(self.g.own.uses,1)
        self.assertIsNone(self.g.ally.cooldowns)
        self.assertIsNone(self.g.enemy.cooldowns)
        self.api.action('cooldown')
        self.api.magic_tick()
        self.assertEqual(len(self.g.player_army.controllers[1].resets),3)

    def test_spell_error_releases_control_and_pauses(self):
        self.lua.execute("own.spells={'wh_main_spell_fire_fireball'};own.reject_reset=true")
        self.api.action('cooldown')
        self.assertFalse(self.g.own.script_control)
        self.assertEqual(self.api.magic_state(),(False,False))
        self.assertIn('已暂停',self.api.status()[0])

    def test_magic_reinforcements_dead_units_and_lifecycle(self):
        self.api.start();self.api.action('magic');self.api.action('cooldown')
        self.lua.execute("""
            reinforcement=make_unit(5,true)
            reinforcement.spells={'wh_main_spell_fire_fireball'}
            reinforcement.deployed=false
            reinforcement_army=make_army(5,true,{reinforcement})
            battle_armies[#battle_armies+1]=reinforcement_army
        """)
        self.api.magic_tick()
        self.assertEqual(self.g.reinforcement_army.current,30)
        self.assertEqual(len(self.g.reinforcement_army.controllers),0)
        self.g.reinforcement.deployed=True
        self.api.magic_tick()
        self.assertEqual(self.g.reinforcement.cooldowns['wh_main_spell_fire_fireball'],0)
        self.g.reinforcement.men=0
        count=len(self.g.reinforcement_army.controllers[1].resets)
        self.api.magic_tick()
        self.assertEqual(len(self.g.reinforcement_army.controllers[1].resets),count)
        self.g.phases.Complete()
        self.assertIsNone(self.g.timers['wh3pt_battle_magic'])
        self.assertEqual(self.api.magic_state(),(False,False))
        self.assertEqual(self.module.new(self.g.bm).magic_state(),(False,False))

    def test_magic_multiplayer_and_replay_prevent_mutations(self):
        for key in ('multiplayer','replay'):
            self.g[key]=True
            for action in ('magic','cooldown'):
                with self.assertRaises(Exception):self.api.action(action)
            self.api.magic_tick()
            self.g[key]=False
        self.assertEqual(self.g.player_army.calls,0)

    def test_inherited_revival_only_affects_owned_enabled_units(self):
        restored, missing, failures = self.api.tick(False)
        self.assertEqual((restored, missing, failures), (5, 45, 0))
        self.assertEqual(self.g.ordinary.calls, 0)
        self.assertEqual(self.g.ally.calls, 0)
        self.assertEqual(self.g.enemy.calls, 0)
        self.assertTrue(self.g.own.disabled['wh3pt_revive_2'])
        self.assertIsNone(self.g.own.disabled['native_other_heal'])

    def test_mode_on_off_and_final_phase(self):
        self.api.start()
        self.api.action('mode')
        self.api.tick(False)
        self.assertEqual(self.g.ordinary.men, 55)
        self.g.phases.VictoryCountdown()
        self.assertEqual(self.g.ordinary.men, 100)
        self.api.action('mode')  # off
        self.g.own.men = 40
        self.api.tick(True)
        self.assertEqual(self.g.own.men, 40)
        self.g.phases.Complete()
        self.assertIsNone(self.g.timers['wh3pt_battle_revival'])
        with self.assertRaises(Exception):
            self.api.action('revive')

    def test_partial_recovery_and_errors_are_not_reported_as_success(self):
        self.g.own.unsupported = True
        self.assertEqual(self.api.tick(True), (0, 50, 0))
        self.assertIn('缺 50', self.api.status()[0])
        self.g.own.reject = True
        self.api.action('revive')
        self.g.flush_battle()
        self.assertIn('缺50', self.api.status()[0])
        self.lua.execute('function own:heal_hitpoints_unary() error("native failure") end')
        self.assertEqual(self.api.tick(True)[2], 1)

    def test_manual_heal_does_not_resurrect_and_ammo_reads_back(self):
        self.api.action('heal')
        self.g.flush_battle()
        self.assertEqual(self.g.own.men, 50)
        self.assertEqual(self.g.own.hp, 1)
        self.api.action('ammo')
        self.g.flush_battle()
        self.assertEqual(self.g.own.ammo, 20)
        self.assertEqual(self.g.ally.ammo, 0)
        self.assertIn('变化2队', self.api.status()[0])

    def test_undeployed_and_inactive_not_revived(self):
        self.g.own.deployed = False
        self.api.tick(True)
        self.assertEqual(self.g.own.calls, 0)
        self.assertEqual(self.g.ordinary.calls, 0)

    def test_multiplayer_replay_and_complete_prevent_actions(self):
        for key in ('multiplayer', 'replay'):
            self.g[key] = True
            self.assertFalse(self.api.start())
            self.api.tick(True)
            with self.assertRaises(Exception): self.api.action('revive')
            self.g[key] = False
        self.assertEqual(self.g.own.calls, 0)

    def test_battle_ui_entry_and_native_shortcut(self):
        self.api.start()
        self.g.timers['wh3pt_battle_ui']()
        panel = self.g.widgets['wh3pt_battle_panel']
        self.assertFalse(panel.visible)
        self.g.core.trigger_event(self.g.core, 'ComponentLClickUp', self.lua.table_from({'string':'wh3pt_battle_magic'}))
        self.assertEqual(self.api.magic_state(),(True,False))
        self.assertIn('开',self.g.widgets['wh3pt_battle_magic'].text)
        self.g.core.trigger_event(self.g.core, 'ComponentLClickUp', self.lua.table_from({'string':'wh3pt_battle_cooldown'}))
        self.assertEqual(self.api.magic_state(),(True,True))
        self.g.core.trigger_event(self.g.core, 'ShortcutTriggered', self.lua.table_from({'string':'camera_bookmark_view0'}))
        self.assertTrue(panel.visible)
        self.g.core.trigger_event(self.g.core, 'ComponentLClickUp', self.lua.table_from({'string':'wh3pt_battle_close'}))
        self.assertFalse(panel.visible)
        for item in self.g.widgets.values():
            if item.id.startswith('wh3pt_battle_'):
                self.assertLessEqual(item.x + item.w, self.g.screen_width)
                self.assertLessEqual(item.y + item.h, self.g.screen_height)

    def test_battle_magic_panel_fits_small_and_large_screens(self):
        for width,height in [(1280,720),(1920,1080),(3840,2160)]:
            self.g.screen_width=width;self.g.screen_height=height
            runtime=self.module.new(self.g.bm,self.lua.table_from(self.spells))
            runtime.ui()
            for name in ['panel','magic','cooldown','status']:
                widget=self.g.widgets['wh3pt_battle_'+name]
                self.assertLessEqual(widget.x+widget.w,width)
                self.assertLessEqual(widget.y+widget.h,height)


if __name__ == '__main__':
    unittest.main()
