from pathlib import Path
import unittest
from lupa.lua51 import LuaRuntime
from pack_io import read_pack
from trainer_core import GAME

from tests import ROOT, SRC, REFERENCE


class BattleTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute((ROOT / 'tests/runtime_mock.lua').read_text(encoding='utf-8'))
        self.lua.execute('''
            ENEMY_SUMMONS={passives={summon_on_death=true},units={skeletons_summoned=true,feral_cold_ones=true}}
            function collection(items)
                return {count=function() return #items end, item=function(_,i) return items[i] end}
            end
            function make_unit(id, player, abilities)
                local u={id=id, player=player, abilities=abilities or {}, men=50, hp=.5,
                    ammo=0, initial=100, deployed=true, disabled={}, calls=0,
                    fatigue='threshold_fresh', attributes={}}
                function u:fatigue_state() return self.fatigue end
                function u:set_stat_attribute(key,on) self.attributes[key]=on end
                function u:unique_ui_id() return self.id end
                function u:type() return self.key or 'ordinary_unit' end
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
                function a:is_reinforcement_army() return self.reinforcing==true end
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
                    local uc={resets={},refills={},releases=0,fatigue_calls=0}
                    function uc:add_units(u) self.unit=u end
                    function uc:kill() assert(not self.unit.player,'killed a player unit');self.unit.men=0 end
                    function uc:clear_all() self.unit=nil end
                    function uc:change_fatigue_amount(amount)
                        assert(amount>0 and amount<1)  -- 0 is a divide by zero in the engine
                        assert(self.unit.player)
                        self.unit.script_control=true
                        self.fatigue_calls=self.fatigue_calls+1
                        if self.unit.reject_fatigue then error('fatigue API failure') end
                        -- frozen: perfect vigour blocks the call. stuck: nothing helps.
                        if self.unit.stuck then return end
                        if self.unit.frozen and self.unit.attributes.fatigue_immune~=false then return end
                        self.unit.fatigue='threshold_fresh'
                    end
                    function uc:reset_ability_cooldown(key)
                        assert(self.unit.player)
                        self.unit.script_control=true
                        if self.unit.reject_reset then error('cooldown API failure') end
                        self.resets[#self.resets+1]=key
                        self.unit.cooldowns=self.unit.cooldowns or {}
                        self.unit.cooldowns[key]=0
                    end
                    function uc:reset_ability_number_of_uses(key)
                        self.refills[#self.refills+1]=key
                        self.unit.uses=self.unit.uses or {}
                        self.unit.uses[key]=true
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
            enemy_armies={enemy_army}
            phases, timers, callbacks = {}, {}, {}
            bm={}
            function bm:is_multiplayer() return multiplayer end
            function bm:is_replay() return replay==true end
            function bm:is_conflict_phase() return conflict~=false end
            function bm:get_player_alliance()
                return {armies=function() return collection(battle_armies) end}
            end
            function bm:get_non_player_alliance()
                return {armies=function() return collection(enemy_armies) end}
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
        self.api = module.new(self.g.bm)

    def publish_campaign_magic(self, owner='player'):
        self.g.svr.wh3pt_magic_pending = True
        self.g.svr.wh3pt_magic_owner = owner
        self.g.svr.wh3pt_magic_enabled = True
        self.g.svr.wh3pt_cooldown_enabled = True

    def publish_silence(self):
        self.publish_campaign_magic()
        self.g.svr.wh3pt_magic_enabled = False
        self.g.svr.wh3pt_cooldown_enabled = False
        self.g.svr.wh3pt_silence_enabled = True

    def tick(self):
        self.g.timers.wh3pt_battle_revival()

    def enemy_with(self, code):
        return self.lua.execute('''
            local u=make_unit(50+#enemy_army.items,false)
            %s
            u.owner_army=enemy_army
            enemy_army.items[#enemy_army.items+1]=u
            return u
        ''' % code)

    def test_silence_comes_from_the_campaign_and_only_for_the_owner(self):
        self.publish_silence()
        self.api.start()
        self.assertTrue(self.api.silence_state()[0])
        other = self.module.new(self.g.bm)
        other.start()
        self.assertFalse(other.silence_state()[0])

    def test_silence_blocks_enemy_abilities_and_never_the_players_side(self):
        self.lua.execute('''
            enemy.spells={'fireball','raise_dead'}
            own.spells={'fireball'}; ally.spells={'fireball'}; ordinary.spells={'buff'}
        ''')
        self.publish_silence()
        self.api.start()
        self.tick()
        self.assertTrue(self.g.enemy.disabled.fireball)
        self.assertTrue(self.g.enemy.disabled.raise_dead)
        for unit in (self.g.own, self.g.ally, self.g.ordinary):
            self.assertIsNone(unit.disabled.fireball)
            self.assertIsNone(unit.disabled.buff)
        self.assertEqual(self.api.silence_state(), (True, 2, 0))

    def test_silence_keeps_ordinary_passives_but_blocks_summoning_ones(self):
        self.lua.execute("enemy.abilities={'aura_of_dread','summon_on_death'}")
        self.publish_silence()
        self.api.start()
        self.tick()
        self.assertTrue(self.g.enemy.disabled.summon_on_death)
        self.assertIsNone(self.g.enemy.disabled.aura_of_dread)

    def test_silence_does_not_ask_again_for_a_unit_it_already_covered(self):
        self.lua.execute('''
            enemy.spells={'fireball'}; calls=0
            local native=enemy.disable_special_ability
            function enemy:disable_special_ability(k,on) calls=calls+1; native(self,k,on) end
        ''')
        self.publish_silence()
        self.api.start()
        self.tick(); self.tick(); self.tick()
        self.assertEqual(self.g.calls, 1)

    def test_units_arriving_later_are_silenced_too(self):
        self.publish_silence()
        self.api.start()
        self.tick()
        late = self.enemy_with("u.spells={'late_spell'}")
        self.tick()
        self.assertTrue(late.disabled.late_spell)
        self.assertGreater(late.men, 0)

    def test_summon_is_removed_on_arrival_but_a_unit_from_the_start_is_not(self):
        start = self.enemy_with("u.key='feral_cold_ones'")
        self.publish_silence()
        self.api.start()
        self.tick()
        summon = self.enemy_with("u.key='skeletons_summoned'")
        normal = self.enemy_with("u.key='ordinary_unit'")
        self.tick()
        self.assertEqual(summon.men, 0)
        self.assertEqual(normal.men, 50)
        self.assertEqual(start.men, 50)
        self.assertEqual(self.api.silence_state()[2], 1)
        self.assertFalse(summon.script_control)

    def test_reinforcement_army_is_not_mistaken_for_a_summon(self):
        self.publish_silence()
        self.api.start()
        self.tick()
        unit = self.lua.execute('''
            local u=make_unit(70,false); u.key='feral_cold_ones'; u.spells={'late_spell'}
            local army=make_army(9,false,{u}); army.reinforcing=true
            enemy_armies[#enemy_armies+1]=army
            return u
        ''')
        self.tick()
        self.assertEqual(unit.men, 50)
        self.assertTrue(unit.disabled.late_spell)

    def test_switching_off_gives_back_only_what_it_took(self):
        self.lua.execute("enemy.spells={'fireball'}; enemy.disabled.scripted=true")
        self.publish_silence()
        self.api.start()
        self.tick()
        self.api.action('silence')
        self.assertEqual(self.api.silence_state(), (False, 0, 0))
        self.assertFalse(self.g.enemy.disabled.fireball)
        self.assertTrue(self.g.enemy.disabled.scripted)
        self.tick()
        self.assertFalse(self.g.enemy.disabled.fireball)

    def test_switching_on_mid_battle_acts_at_once_and_leaves_what_is_there(self):
        self.lua.execute("enemy.spells={'fireball'}; enemy.key='skeletons_summoned'")
        self.api.start()
        self.assertFalse(self.api.silence_state()[0])
        self.api.action('silence')
        self.assertTrue(self.g.enemy.disabled.fireball)
        self.assertEqual(self.g.enemy.men, 50)

    def test_silence_stops_itself_when_the_engine_refuses(self):
        self.lua.execute("enemy.spells={'fireball'}; function enemy:disable_special_ability() error('ability API failure') end")
        self.publish_silence()
        self.api.start()
        self.tick()
        self.assertFalse(self.api.silence_state()[0])
        self.assertIn('禁止敌方技能已暂停', self.api.status()[0])

    def test_silence_is_off_in_replays_and_multiplayer_and_after_the_battle(self):
        self.lua.execute("enemy.spells={'fireball'}")
        self.publish_silence()
        self.api.start()
        self.g.replay = True
        self.tick()
        self.assertIsNone(self.g.enemy.disabled.fireball)
        self.g.replay = False
        self.g.phases.Complete()
        self.assertFalse(self.api.silence_state()[0])

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
        another = self.module.new(self.g.bm)
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
            other = self.module.new(self.g.bm)
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
        self.assertEqual(len(list(self.g.timers.keys())),4)  # ui, revival, magic, dock

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
        other = self.module.new(self.g.bm)
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

    def ability_ticks(self):
        for _ in range(5): self.api.magic_tick()

    def test_every_active_ability_gets_cooldown_and_uses_reset(self):
        self.lua.execute("""
            own.spells={'wh_main_spell_fire_fireball','wh2_dlc10_unit_abilities_frost_breath'}
            ally.spells=own.spells;enemy.spells=own.spells
        """)
        self.api.action('cooldown')
        keys=['wh_main_spell_fire_fireball','wh2_dlc10_unit_abilities_frost_breath']
        uc=self.g.player_army.controllers[1]
        self.assertEqual(list(uc.resets.values()),keys)
        self.assertEqual(list(uc.refills.values()),keys)
        self.assertTrue(self.g.own.uses['wh2_dlc10_unit_abilities_frost_breath'])
        self.assertFalse(self.g.own.script_control)
        self.assertIsNone(self.g.ally.cooldowns)
        self.assertIsNone(self.g.enemy.cooldowns)
        self.assertIsNone(self.g.ally.uses)
        self.assertIsNone(self.g.enemy.uses)

    def test_abilities_reset_twice_a_second_and_stop_when_switched_off(self):
        self.lua.execute("own.spells={'wh2_dlc10_unit_abilities_frost_breath'}")
        self.api.action('cooldown')
        uc=self.g.player_army.controllers[1]
        for _ in range(4): self.api.magic_tick()
        self.assertEqual(len(uc.resets),1)
        self.api.magic_tick()
        self.assertEqual(len(uc.resets),2)
        self.api.action('cooldown')
        self.ability_ticks()
        self.assertEqual(len(uc.resets),2)
        self.api.action('cooldown')
        self.assertEqual(len(uc.resets),3)  # Switching on acts at once.

    def test_spell_error_releases_control_and_pauses(self):
        self.lua.execute("own.spells={'wh_main_spell_fire_fireball'};own.reject_reset=true")
        self.api.action('cooldown')
        self.assertFalse(self.g.own.script_control)
        self.assertEqual(self.api.magic_state(),(False,False))
        self.assertIn('已暂停',self.api.status()[0])

    def publish_vigour(self):
        self.publish_campaign_magic()
        self.g.svr.wh3pt_vigour_enabled = True

    def second(self, count=1):
        for _ in range(count): self.g.timers.wh3pt_battle_revival()

    def test_perfect_vigour_clears_the_fatigue_a_battle_starts_with(self):
        self.publish_vigour()
        self.lua.execute("""
            own.fatigue='threshold_winded';ordinary.fatigue='threshold_exhausted'
            ally.fatigue='threshold_winded';enemy.fatigue='threshold_winded'
        """)
        self.api.start()
        self.assertEqual(self.api.fatigue_state(),(True,'开战后清除疲劳'))
        self.second()
        self.assertEqual(self.g.own.fatigue,'threshold_fresh')
        self.assertEqual(self.g.ordinary.fatigue,'threshold_fresh')
        self.assertEqual(self.g.ally.fatigue,'threshold_winded')
        self.assertEqual(self.g.enemy.fatigue,'threshold_winded')
        self.assertFalse(self.g.own.script_control)
        self.assertIsNone(self.g.own.attributes.fatigue_immune)  # one call was enough
        self.second(3)
        self.assertEqual(self.g.player_army.controllers[1].fatigue_calls,2)
        self.assertEqual(self.api.fatigue_state()[1],'疲劳已清 2队')

    def test_any_penalised_level_is_cleared_and_unknown_states_are_left_alone(self):
        self.publish_vigour()
        self.api.start()
        for state in ('threshold_active','threshold_winded','threshold_tired','threshold_very_tired','threshold_exhausted'):
            self.g.own.fatigue=state
            self.second()
            self.assertEqual(self.g.own.fatigue,'threshold_fresh',state)
            self.second()
        calls=self.g.player_army.controllers[1].fatigue_calls
        self.assertEqual(calls,5)
        self.g.own.fatigue='threshold_new'
        self.second(2)
        self.assertEqual(self.g.player_army.controllers[1].fatigue_calls,calls)

    def test_fatigue_is_left_alone_without_perfect_vigour(self):
        self.publish_campaign_magic()
        self.g.own.fatigue='threshold_winded'
        self.api.start()
        self.second(2)
        self.assertEqual(self.g.own.fatigue,'threshold_winded')
        self.assertEqual(self.api.fatigue_state(),(False,''))
        self.assertEqual(len(self.g.player_army.controllers),0)

    def test_vigour_setting_of_another_faction_is_ignored(self):
        self.publish_vigour()
        self.g.svr.wh3pt_magic_owner='other_faction'
        self.g.own.fatigue='threshold_winded'
        self.api.start()
        self.second()
        self.assertEqual(self.g.own.fatigue,'threshold_winded')

    def test_fatigue_waits_for_the_battle_to_start(self):
        self.publish_vigour()
        self.g.own.fatigue='threshold_winded'
        self.g.conflict=False
        self.api.start()
        self.second(5)  # deployment must not use up the tries
        self.assertEqual(len(self.g.player_army.controllers),0)
        self.g.conflict=True
        self.second()
        self.assertEqual(self.g.own.fatigue,'threshold_fresh')

    def test_frozen_fatigue_is_cleared_with_the_attribute_lifted_for_one_pass(self):
        self.publish_vigour()
        self.lua.execute("own.fatigue='threshold_winded';own.frozen=true")
        self.api.start()
        self.second()
        self.assertEqual(self.g.own.fatigue,'threshold_winded')
        self.assertIsNone(self.g.own.attributes.fatigue_immune)
        self.second()
        self.assertEqual(self.g.own.fatigue,'threshold_fresh')
        self.assertFalse(self.g.own.attributes.fatigue_immune)
        self.second()
        self.assertTrue(self.g.own.attributes.fatigue_immune)
        self.assertEqual(self.api.fatigue_state()[1],'疲劳已清 1队')

    def test_fatigue_the_game_will_not_clear_is_reported_and_left_alone(self):
        self.publish_vigour()
        self.lua.execute("own.fatigue='threshold_winded';own.stuck=true")
        self.api.start()
        self.second(6)
        self.assertEqual(self.g.player_army.controllers[1].fatigue_calls,2)
        self.assertTrue(self.g.own.attributes.fatigue_immune)
        self.assertFalse(self.g.own.script_control)
        self.assertEqual(self.api.fatigue_state()[1],'疲劳已清 0队 · 未清 1队')

    def test_fatigue_error_releases_control_and_pauses(self):
        self.publish_vigour()
        self.lua.execute("own.fatigue='threshold_winded';own.reject_fatigue=true")
        self.api.start()
        self.second()
        self.assertFalse(self.g.own.script_control)
        self.assertEqual(self.api.fatigue_state(),(False,'清除疲劳已暂停'))
        self.assertIn('fatigue API failure',self.api.status()[0])

    def test_fatigue_reinforcements_dead_units_and_lifecycle(self):
        self.publish_vigour()
        self.api.start()
        self.lua.execute("""
            late=make_unit(6,true)
            late.fatigue='threshold_tired'
            late.deployed=false
            late_army=make_army(6,true,{late})
            battle_armies[#battle_armies+1]=late_army
        """)
        self.second()
        self.assertEqual(len(self.g.late_army.controllers),0)
        self.g.late.deployed=True
        self.second()
        self.assertEqual(self.g.late.fatigue,'threshold_fresh')
        self.second()
        self.g.late.men=0
        self.g.late.fatigue='threshold_tired'
        self.second()
        self.assertEqual(self.g.late_army.controllers[1].fatigue_calls,1)
        self.g.phases.Complete()
        self.assertFalse(self.api.fatigue_state()[0])
        self.assertIsNone(self.api.fatigue_tick())

    def test_fatigue_result_shows_in_the_panel_and_fits_the_title_row(self):
        self.publish_vigour()
        self.g.own.fatigue='threshold_winded'
        self.api.start()
        self.g.timers.wh3pt_battle_ui()
        label=self.g.widgets.wh3pt_battle_fatigue
        self.assertEqual(label.text,'开战后清除疲劳')
        self.second(2)
        self.assertEqual(label.text,'疲劳已清 1队')
        for text in (1.0,1.25,1.5,2.0):
            self.g.text_scale=text;self.g.screen_width=3840;self.g.screen_height=2160
            runtime=self.module.new(self.g.bm)
            runtime.ui()
            title=self.g.widgets.wh3pt_battle_title
            label=self.g.widgets.wh3pt_battle_fatigue
            close=self.g.widgets.wh3pt_battle_close
            self.assertLess(title.TextDimensions(title)[0],title.w,text)
            self.assertLess(self.g.measure_text('疲劳已清 20队 · 未清 20队',12),label.w,text)
            self.assertLessEqual(title.x+title.w,label.x)
            self.assertLessEqual(label.x+label.w,close.x)

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
        self.ability_ticks()
        self.assertEqual(self.g.reinforcement.cooldowns['wh_main_spell_fire_fireball'],0)
        self.g.reinforcement.men=0
        count=len(self.g.reinforcement_army.controllers[1].resets)
        self.ability_ticks()
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

    BAR = (388, 54)  # hud_battle.twui.xml: bar_small_top, docked top right at 1600x900.

    def with_bar(self,width):
        self.g.bar_w=width
        self.lua.execute("""
            function find_uicomponent(_,first,second,third)
                assert(first=='hud_battle' and second=='radar_holder' and third=='bar_small_top')
                return {Dimensions=function() return bar_w,54 end,
                    Position=function() return screen_width+500,0 end}  -- still sliding in
            end
        """)

    def test_battle_panel_fits_and_stays_clear_of_the_speed_buttons(self):
        for width,height in [(1280,720),(1920,1080),(2560,1440),(3840,2160)]:
            self.g.screen_width=width;self.g.screen_height=height
            bar=int(self.BAR[0]*width/1600)
            self.with_bar(bar)
            runtime=self.module.new(self.g.bm)
            runtime.ui()
            launcher=self.g.widgets.wh3pt_battle_open
            panel=self.g.widgets.wh3pt_battle_panel
            for widget in (launcher,panel):
                self.assertLessEqual(widget.x+widget.w,width-bar,(width,widget.id))
                self.assertGreaterEqual(widget.x,0)
            for name in ['close','fatigue','mode','revive','heal','ammo','magic','cooldown','silence','status']:
                widget=self.g.widgets['wh3pt_battle_'+name]
                self.assertGreaterEqual(widget.x,panel.x,name)
                self.assertGreaterEqual(widget.y,panel.y,name)
                self.assertLessEqual(widget.x+widget.w,panel.x+panel.w,name)
                self.assertLessEqual(widget.y+widget.h,panel.y+panel.h,name)
                self.assertLessEqual(widget.y+widget.h,height,name)

    def test_battle_panel_scales_with_the_text_not_the_resolution(self):
        for width,height,text,scale in [(2560,1440,1.25,1.25),(3840,2160,2.0,2.0),(1920,1080,1.0,1.0)]:
            self.g.screen_width=width;self.g.screen_height=height;self.g.text_scale=text
            runtime=self.module.new(self.g.bm)
            runtime.ui()
            panel=self.g.widgets.wh3pt_battle_panel
            self.assertEqual(self.g.widgets.wh3pt_battle_open.w,int(112*scale))
            self.assertEqual((panel.w,panel.h),(int(336*scale),int(174*scale)))
            self.assertLess(panel.h/height,0.17)
            self.assertTrue(self.g.widgets.wh3pt_battle_probe.dead)

    def test_battle_panel_scale_falls_back_without_text_measurement(self):
        self.lua.execute("text_dimensions_enabled=false")
        self.g.screen_width=2560;self.g.screen_height=1440
        self.api.start();self.g.timers['wh3pt_battle_ui']()
        self.assertEqual(self.g.widgets.wh3pt_battle_open.w,224)
        self.assertTrue(self.g.widgets.wh3pt_battle_probe.dead)

    def test_every_battle_label_fits_its_button(self):
        for text in (1.0,1.25,1.5,2.0):
            self.g.text_scale=text;self.g.screen_width=3840;self.g.screen_height=2160
            runtime=self.module.new(self.g.bm)
            runtime.ui()
            for name in ('open','mode','revive','heal','ammo','magic','cooldown','silence'):
                widget=self.g.widgets['wh3pt_battle_'+name]
                self.assertLess(self.g.measure_text(widget.text,12),widget.w,(text,name,widget.text))

    def test_battle_panel_falls_back_when_the_bar_cannot_be_found(self):
        self.g.screen_width=1920;self.g.screen_height=1080
        self.api.start();self.g.timers['wh3pt_battle_ui']()
        launcher=self.g.widgets.wh3pt_battle_open
        self.assertLess(launcher.x+launcher.w,1920-388)

    def test_launcher_follows_the_hud_while_it_settles_then_stops_looking(self):
        self.g.screen_width=2560;self.g.screen_height=1440
        self.with_bar(388)  # not scaled yet
        self.api.start();self.g.timers['wh3pt_battle_ui']()
        launcher=self.g.widgets.wh3pt_battle_open
        first=launcher.x
        self.g.bar_w=620
        self.g.timers.wh3pt_battle_dock()
        self.assertEqual(launcher.x,first-232)
        self.assertLessEqual(launcher.x+launcher.w,2560-620)
        for _ in range(20):
            timer=self.g.timers.wh3pt_battle_dock
            self.g.timers.wh3pt_battle_dock=None
            if timer is None: break
            timer()
        self.assertIsNone(self.g.timers.wh3pt_battle_dock)

    def mouse(self,event):
        self.g.core.trigger_event(self.g.core,event,self.lua.table_from({'string':'wh3pt_battle_open'}))

    def watch(self):
        timer=self.g.timers.wh3pt_battle_watch
        self.g.timers.wh3pt_battle_watch=None
        if timer: timer()
        return timer is not None

    def test_launcher_stays_where_it_is_dropped_when_the_engine_puts_it_back(self):
        self.api.start();self.g.timers['wh3pt_battle_ui']()
        launcher=self.g.widgets.wh3pt_battle_open
        panel=self.g.widgets.wh3pt_battle_panel
        start=(launcher.x,launcher.y)
        self.mouse('ComponentMouseOn')
        self.watch()
        launcher.dragged=True
        launcher.MoveTo(launcher,300,420)
        self.watch()
        # Release: the engine restores the start of the drag, then sends the click.
        launcher.dragged=False
        launcher.MoveTo(launcher,*start)
        self.click('open')
        self.assertFalse(panel.visible)
        self.watch()
        self.assertEqual((launcher.x,launcher.y),(300,420))
        self.assertEqual(self.g.svr.wh3pt_battle_launcher,'300,420')
        self.assertEqual(panel.y,420+launcher.h+4)
        self.click('open')
        self.assertTrue(panel.visible)

    def test_launcher_drop_without_a_click_still_counts(self):
        self.api.start();self.g.timers['wh3pt_battle_ui']()
        launcher=self.g.widgets.wh3pt_battle_open
        start=(launcher.x,launcher.y)
        self.mouse('ComponentMouseOn')
        self.watch()
        launcher.dragged=True
        launcher.MoveTo(launcher,500,300)
        self.watch()
        launcher.dragged=False
        launcher.MoveTo(launcher,*start)
        self.watch()
        self.assertEqual((launcher.x,launcher.y),(500,300))

    def test_small_wobble_is_a_click_and_stays_home(self):
        self.api.start();self.g.timers['wh3pt_battle_ui']()
        launcher=self.g.widgets.wh3pt_battle_open
        start=(launcher.x,launcher.y)
        self.mouse('ComponentMouseOn')
        self.watch()
        launcher.dragged=True
        launcher.MoveTo(launcher,start[0]+2,start[1]+1)
        self.watch()
        launcher.dragged=False
        self.click('open')
        self.assertTrue(self.g.widgets.wh3pt_battle_panel.visible)
        self.watch()
        self.assertEqual((launcher.x,launcher.y),start)

    def test_placed_launcher_is_no_longer_docked(self):
        self.g.screen_width=2560;self.g.screen_height=1440
        self.with_bar(388)
        self.api.start();self.g.timers['wh3pt_battle_ui']()
        launcher=self.g.widgets.wh3pt_battle_open
        launcher.MoveTo(launcher,300,420)
        self.click('open')
        self.g.bar_w=620
        self.g.timers.wh3pt_battle_dock()
        self.g.phases.Deployed()
        self.assertEqual((launcher.x,launcher.y),(300,420))

    def test_launcher_watch_stops_with_the_mouse_and_the_battle(self):
        self.api.start();self.g.timers['wh3pt_battle_ui']()
        self.assertIsNone(self.g.timers.wh3pt_battle_watch)
        self.mouse('ComponentMouseOn')
        self.assertTrue(self.watch())
        self.assertTrue(self.watch())
        self.mouse('ComponentMouseOff')
        self.watch()
        self.assertFalse(self.watch())
        self.mouse('ComponentMouseOn')
        self.g.phases.Complete()
        self.watch()
        self.assertFalse(self.watch())

    def click(self,key):
        self.g.core.trigger_event(self.g.core,'ComponentLClickUp',self.lua.table_from({'string':'wh3pt_battle_'+key}))

    def test_dragged_launcher_is_remembered_and_is_not_a_click(self):
        self.api.start();self.g.timers['wh3pt_battle_ui']()
        launcher=self.g.widgets.wh3pt_battle_open
        panel=self.g.widgets.wh3pt_battle_panel
        self.assertTrue(launcher.moveable)
        launcher.MoveTo(launcher,800,200)
        self.click('open')
        self.assertFalse(panel.visible)
        self.assertEqual(self.g.svr.wh3pt_battle_launcher,'800,200')
        self.click('open')
        self.assertTrue(panel.visible)
        self.assertEqual(panel.y,200+launcher.h+4)
        self.assertEqual(panel.x+panel.w,800+launcher.w)
        other=self.module.new(self.g.bm)
        other.ui()
        again=self.g.widgets.wh3pt_battle_open
        self.assertEqual((again.x,again.y),(800,200))

    def test_panel_opens_above_a_launcher_dragged_to_the_bottom(self):
        self.api.start();self.g.timers['wh3pt_battle_ui']()
        launcher=self.g.widgets.wh3pt_battle_open
        panel=self.g.widgets.wh3pt_battle_panel
        launcher.MoveTo(launcher,900,self.g.screen_height-launcher.h)
        self.click('open');self.click('open')
        self.assertEqual(panel.y+panel.h+4,launcher.y)

    def test_panel_content_follows_it_when_it_opens_elsewhere(self):
        self.api.start();self.g.timers['wh3pt_battle_ui']()
        launcher=self.g.widgets.wh3pt_battle_open
        panel=self.g.widgets.wh3pt_battle_panel
        status=self.g.widgets.wh3pt_battle_status
        offset=(status.x-panel.x,status.y-panel.y)
        launcher.MoveTo(launcher,700,300)
        self.click('open');self.click('open')
        self.assertEqual((status.x-panel.x,status.y-panel.y),offset)


if __name__ == '__main__':
    unittest.main()
