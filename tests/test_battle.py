from pathlib import Path
import unittest
from lupa.lua51 import LuaRuntime
from pack_io import read_pack
from trainer_core import GAME

from tests import ROOT, SRC, REFERENCE


class BattleCase(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute((ROOT / 'tests/runtime_mock.lua').read_text(encoding='utf-8'))
        self.lua.execute('''
            ENEMY_SUMMONS={passives={summon_on_death=true},units={skeletons_summoned=true,feral_cold_ones=true}}
            GENERAL_DATA={flags={},uses={},units={},abilities={}}
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
            function bm:is_any_cutscene_running() return false end
            function bm:is_siege_battle() return siege==true end
            function bm:battle_type() return battle_type or 'land_normal' end
            function bm:time_elapsed_ms() return battle_clock or 0 end
            commands={}
            function bm:register_command_handler_callback(name,fn,key) assert(key);commands[name]=fn end
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
        module = self.lua.execute((SRC / 'ingame/general.lua').read_text(encoding='utf-8') + '\n'
                                  + (SRC / 'ingame/battle.lua').read_text(encoding='utf-8'))
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


class BattleTests(BattleCase):
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
        self.assertEqual(len(list(self.g.timers.keys())),5)  # ui, revival, magic, general, dock

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
            self.assertEqual((panel.w,panel.h),(int(336*scale),int(262*scale)))
            self.assertLess(panel.h/height,0.25)
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


class GeneralPanelTests(BattleCase):
    """The commander as the player meets it. It has its own tests in test_general.py."""

    def battlefield(self):
        self.lua.execute('''
            function vec(x,z) return {get_x=function() return x end,get_y=function() return 0 end,get_z=function() return z end} end
            function dress(u,x,z,side)
                u.flags=0
                function u:position() return vec(x,z) end
                function u:bearing() return 0 end
                function u:is_routing() return false end
                function u:is_in_melee() return false end
                function u:is_currently_flying() return false end
                function u:is_under_missile_attack() return false end
                function u:is_wavering() return false end
                function u:is_shattered() return false end
                function u:is_moving_fast() return false end
                function u:is_leaving_battle() return false end
                function u:is_valid_target() return true end
                function u:is_visible_to_alliance() return true end
                function u:is_controllable() return true end
                function u:is_rampaging() return false end
                function u:is_idle() return true end
                function u:is_commanding_unit() return false end
                function u:is_artillery() return false end
                function u:is_fixed_artillery() return false end
                function u:is_cavalry() return false end
                function u:is_chariot() return false end
                function u:is_war_machine() return false end
                function u:is_war_beasts() return false end
                function u:is_anti_cavalry_infantry() return false end
                function u:is_pikemen() return false end
                function u:can_use_magic() return false end
                function u:unit_class() return 'inf_mel' end
                function u:strategic_value() return 500 end
                function u:fast_speed() return 5 end
                function u:slow_speed() return 3 end
                function u:missile_range() return 0 end
                function u:has_attribute() return false end
                function u:can_use_behaviour() return false end
                function u:can_fly() return false end
                function u:ordered_width() return 40 end
                function u:starting_ammo() return 0 end
            end
            dress(own,0,0); dress(ordinary,40,0); dress(ally,80,0); dress(enemy,0,50)
            function bm:get_player_alliance()
                return {armies=function() return collection(battle_armies) end,
                    is_attacker=function() return false end}
            end
            function v(x,y,z) return vec(x,z) end
            function v_to_ground(p) return p end
            attacks={}
            local create=player_army.create_unit_controller
            function player_army:create_unit_controller()
                local uc=create(self)
                function uc:attack_unit(target,primary,run) attacks[#attacks+1]={unit=self.unit,target=target} end
                function uc:melee() end
                return uc
            end
            common={selected={}}
            function common.get_context_value(kind,id,expression)
                if expression=='IsSelected' then return common.selected[tonumber(id)]==true end
                local key=string.match(expression,'^ShortcutKey%("(.*)"%)$')
                if key then return bound_keys and bound_keys[key] or '' end
            end
            function common.get_localised_string(key) return shortcut_names and shortcut_names[key] or '' end
            camera={moves={}}
            function camera:position() return 'camera_pos' end
            function camera:target() return 'camera_target' end
            function camera:move_to(position,target,time,linear,fov,queue)
                self.moves[#self.moves+1]={position=position,target=target,time=time,fov=fov,queue=queue}
            end
            function bm:camera() return camera end
        ''')

    def open_panel(self):
        self.api.start()
        self.g.timers['wh3pt_battle_ui']()

    def click(self, name):
        self.g.core.trigger_event(self.g.core, 'ComponentLClickUp', self.lua.table(string='wh3pt_battle_' + name))

    def test_the_mode_button_cycles_and_says_so(self):
        self.open_panel()
        button = self.g.widgets.wh3pt_battle_general
        self.assertEqual(button.text, 'AI 将军：关')
        self.click('general')
        self.assertEqual(button.text, 'AI 将军：全军')
        self.assertEqual(button.state, 'enabled')
        self.click('general')
        self.assertEqual(button.text, 'AI 将军：除主将外')
        self.click('general')
        self.assertEqual(button.text, 'AI 将军：仅援军')
        self.click('general')
        self.assertEqual(button.text, 'AI 将军：关')
        self.assertEqual(button.state, 'active')

    def test_the_doctrine_button_cycles_and_says_so(self):
        self.open_panel()
        button = self.g.widgets.wh3pt_battle_doctrine
        self.assertEqual(button.text, '战术：自动')
        for text in ('战术：进攻', '战术：固守', '战术：原生 AI', '战术：自动'):
            self.click('doctrine')
            self.assertEqual(button.text, text)

    def test_all_new_rows_fit_inside_the_panel(self):
        self.open_panel()
        panel = self.g.widgets.wh3pt_battle_panel
        for name in ('silence', 'general_fire', 'general', 'doctrine', 'general_pick', 'general_key',
                     'general_line', 'status'):
            widget = self.g.widgets['wh3pt_battle_' + name]
            self.assertLessEqual(widget.y + widget.h, panel.y + panel.h, name)
            self.assertLessEqual(widget.x + widget.w, panel.x + panel.w, name)

    def test_the_commander_runs_on_its_own_timer_and_commands(self):
        self.battlefield()
        self.open_panel()
        self.click('general')
        self.g.timers.wh3pt_battle_general()
        attacks = self.g.attacks
        self.assertEqual(len(attacks), 2)
        self.assertEqual(attacks[1].target.id, self.g.enemy.id)
        self.assertIn('托管 2 队', self.g.widgets.wh3pt_battle_general_line.text)

    def test_a_campaign_switch_starts_the_commander_for_the_owner(self):
        self.publish_campaign_magic()
        self.g.svr.wh3pt_general_enabled = True
        self.api.start()
        self.assertEqual(self.api.general.mode(), 'all')

    def test_no_campaign_switch_no_commander(self):
        self.publish_campaign_magic()
        self.api.start()
        self.assertEqual(self.api.general.mode(), 'off')

    def test_multiplayer_and_replays_get_no_commander(self):
        self.battlefield()
        self.g.multiplayer = True
        self.api.general.set_mode('all')
        self.api.general_tick()
        self.assertEqual(len(self.g.attacks), 0)

    def test_a_players_order_reaches_the_commander(self):
        self.battlefield()
        self.open_panel()
        self.click('general')
        self.g.battle_clock = 1000
        self.g.timers.wh3pt_battle_general()
        self.g.common.selected[self.g.own.id] = True
        self.g.battle_clock = 1500
        self.g.commands.Move()
        self.assertTrue(self.api.general.paused(self.g.own.id))
        self.assertFalse(self.api.general.paused(self.g.ordinary.id))

    def test_the_free_fire_button_switches_says_so_and_is_kept(self):
        self.open_panel()
        button = self.g.widgets.wh3pt_battle_general_fire
        self.assertEqual((button.text, button.state), ('远程无视友军：关', 'active'))
        self.click('general_fire')
        self.assertEqual((button.text, button.state), ('远程无视友军：开', 'enabled'))
        self.assertTrue(self.api.free_fire())
        self.assertEqual(self.g.registry.wh3pt_general_free_fire, 'on')
        self.assertIn('不再避开友军', self.api.status()[0])
        self.click('general_fire')
        self.assertEqual((button.text, button.state), ('远程无视友军：关', 'active'))
        self.assertEqual(self.g.registry.wh3pt_general_free_fire, 'off')

    def next_battle(self):
        runtime = self.module.new(self.g.bm)
        runtime.start()
        return runtime

    def test_a_pack_without_friendly_fire_off_starts_with_shooters_careful(self):
        self.assertFalse(self.next_battle().free_fire())

    def test_a_pack_with_friendly_fire_off_starts_with_shooters_free(self):
        self.g.GENERAL_DATA.friendly_fire_off = True
        self.assertTrue(self.next_battle().free_fire())

    def test_the_players_choice_beats_what_the_pack_says(self):
        self.g.GENERAL_DATA.friendly_fire_off = True
        self.g.registry.wh3pt_general_free_fire = 'off'
        self.assertFalse(self.next_battle().free_fire())
        self.g.GENERAL_DATA.friendly_fire_off = False
        self.g.registry.wh3pt_general_free_fire = 'on'
        self.assertTrue(self.next_battle().free_fire())

    def test_a_registry_that_fails_costs_the_setting_and_nothing_else(self):
        self.lua.execute('''
            function core:svr_load_registry_string() error('no registry') end
            function core:svr_save_registry_string() error('no registry') end
        ''')
        self.open_panel()
        self.click('general_fire')
        self.assertTrue(self.api.free_fire())
        self.assertEqual(self.g.widgets.wh3pt_battle_general_fire.text, '远程无视友军：开')

    def test_the_commander_hears_the_free_fire_button(self):
        self.battlefield()
        self.lua.execute('''
            dress(own,0,45)
            function ordinary:is_artillery() return true end
            function ordinary:missile_range() return 200 end
            function ordinary:ammo_left() return 10 end
            function ordinary:unit_in_range() return true end
        ''')
        self.open_panel()
        self.click('general')
        self.click('doctrine')
        self.click('doctrine')      # 固守: nobody moves, so only the gun's decision shows
        shots = lambda: [a for a in self.g.attacks.values() if a.unit.id == self.g.ordinary.id]
        self.g.battle_clock = 1000
        self.g.timers.wh3pt_battle_general()
        self.assertEqual(shots(), [])
        self.click('general_fire')
        self.g.battle_clock = 2000
        self.g.timers.wh3pt_battle_general()
        self.assertEqual(len(shots()), 1)
        self.assertEqual(shots()[0].target.id, self.g.enemy.id)

    def test_picking_nothing_asks_for_a_selection(self):
        self.battlefield()
        self.open_panel()
        self.click('general_pick')
        self.assertEqual(self.api.status()[0], '先选中部队')

    def press(self, key):
        self.g.core.trigger_event(self.g.core, 'ShortcutTriggered', self.lua.table_from({'string': key}))

    def hotkey_panel(self):
        self.battlefield()
        self.open_panel()
        self.g.common.selected[self.g.own.id] = True

    def test_shift_f9_hands_selected_units_over_and_takes_them_back(self):
        self.hotkey_panel()
        self.press('camera_bookmark_save0')
        self.assertTrue(self.api.general.managed(self.g.own.id))
        self.assertFalse(self.api.general.managed(self.g.ordinary.id))
        self.g.timers.wh3pt_battle_general()
        self.assertEqual(len(self.g.attacks), 1)
        self.press('camera_bookmark_save0')
        self.assertFalse(self.api.general.managed(self.g.own.id))
        self.g.timers.wh3pt_battle_general()
        self.assertFalse(self.api.general.managed(self.g.own.id))

    def test_a_second_press_undoes_the_first_before_any_tick_has_run(self):
        self.hotkey_panel()
        self.press('camera_bookmark_save0')
        self.press('camera_bookmark_save0')
        self.g.timers.wh3pt_battle_general()
        self.assertEqual(len(self.g.attacks), 0)

    def test_a_mixed_selection_goes_back_to_the_player(self):
        self.hotkey_panel()
        self.press('camera_bookmark_save0')
        self.g.common.selected[self.g.ordinary.id] = True
        self.press('camera_bookmark_save0')
        self.assertFalse(self.api.general.managed(self.g.own.id))
        self.assertFalse(self.api.general.managed(self.g.ordinary.id))

    def test_the_launcher_reports_a_hotkey_press_with_the_panel_shut(self):
        self.hotkey_panel()
        launcher = self.g.widgets.wh3pt_battle_open
        self.press('camera_bookmark_save0')
        self.assertEqual(launcher.text, 'AI 接管 1 队')
        self.press('camera_bookmark_save0')
        self.assertEqual(launcher.text, '已收回 1 队')
        self.g.timers.wh3pt_battle_flash()
        self.assertEqual(launcher.text, '◆ 修改器 [F9]')

    def test_only_the_latest_flash_clears_the_launcher(self):
        self.hotkey_panel()
        launcher = self.g.widgets.wh3pt_battle_open
        self.press('camera_bookmark_save0')
        first = self.g.timers.wh3pt_battle_flash
        self.press('camera_bookmark_save0')
        first()
        self.assertEqual(launcher.text, '已收回 1 队')

    def test_a_press_with_nothing_selected_says_so(self):
        self.battlefield()
        self.open_panel()
        self.press('camera_bookmark_save0')
        self.assertEqual(self.g.widgets.wh3pt_battle_open.text, '先选中部队')

    def test_a_press_marks_the_card_at_once_even_while_paused(self):
        self.battlefield()
        self.lua.execute('''
            cards=root:CreateComponent('review_DY','ui/wh3pt/label')
            own_card=cards:CreateComponent(tostring(own.id),'ui/wh3pt/label')
            function find_uicomponent(parent,...) return cards end
        ''')
        self.open_panel()
        self.g.common.selected[self.g.own.id] = True
        self.press('camera_bookmark_save0')
        mark = self.g.own_card.Find(self.g.own_card, 'wh3pt_general_mark')
        self.assertEqual(mark.state, 'ok_dot')
        self.press('camera_bookmark_save0')
        self.assertFalse(mark.visible)

    def test_multiplayer_ignores_the_hotkey(self):
        self.hotkey_panel()
        self.g.multiplayer = True
        self.press('camera_bookmark_save0')
        self.assertFalse(self.api.general.managed(self.g.own.id))

    def test_the_button_shows_the_key_the_game_really_has_on_the_shortcut(self):
        self.lua.execute("bound_keys={camera_bookmark_save0='Shift + F7'}")
        self.hotkey_panel()
        self.assertEqual(self.g.widgets.wh3pt_battle_general_key.text, '快捷键：Shift + F7')

    def test_the_button_falls_back_to_the_default_name(self):
        self.hotkey_panel()
        self.assertEqual(self.g.widgets.wh3pt_battle_general_key.text, '快捷键：Shift+F9')

    def test_a_new_hotkey_is_learned_from_the_next_press_and_replaces_the_old_one(self):
        self.lua.execute('''
            bound_keys={toggle_labels='Ctrl + T'}
            shortcut_names={shortcut_localisation_onscreen_toggle_labels='切换单位标签'}
        ''')
        self.hotkey_panel()
        button = self.g.widgets.wh3pt_battle_general_key
        self.click('general_key')
        self.assertEqual(button.text, '按下新快捷键…')
        self.press('toggle_labels')
        self.assertEqual(button.text, '快捷键：Ctrl + T')
        self.assertEqual(self.api.hotkey(), ('toggle_labels', False))
        self.assertEqual(self.g.registry.wh3pt_general_hotkey, 'toggle_labels')
        self.assertIn('切换单位标签', self.api.status()[0])
        self.assertFalse(self.api.general.managed(self.g.own.id))      # learning is not using
        self.press('camera_bookmark_save0')
        self.assertFalse(self.api.general.managed(self.g.own.id))
        self.press('toggle_labels')
        self.assertTrue(self.api.general.managed(self.g.own.id))

    def test_the_chosen_hotkey_survives_into_the_next_battle(self):
        self.g.registry.wh3pt_general_hotkey = 'camera_bookmark_save5'
        self.lua.execute("bound_keys={camera_bookmark_save5='Alt + Z'}")
        self.hotkey_panel()
        self.assertEqual(self.api.hotkey(), ('camera_bookmark_save5', False))
        self.assertEqual(self.g.widgets.wh3pt_battle_general_key.text, '快捷键：Alt + Z')
        self.press('camera_bookmark_save5')
        self.assertTrue(self.api.general.managed(self.g.own.id))

    def test_a_registry_that_cannot_be_read_or_written_leaves_a_working_key(self):
        self.lua.execute('''
            function core:svr_load_registry_string() error('no registry') end
            function core:svr_save_registry_string() error('no registry') end
        ''')
        self.hotkey_panel()
        self.assertEqual(self.api.hotkey()[0], 'camera_bookmark_save0')
        self.click('general_key')
        self.press('camera_bookmark_save1')
        self.assertEqual(self.api.hotkey(), ('camera_bookmark_save1', False))

    def test_f9_cannot_be_taken_and_does_not_close_the_panel_while_learning(self):
        self.hotkey_panel()
        self.click('open')
        panel = self.g.widgets.wh3pt_battle_panel
        self.assertTrue(panel.visible)
        self.click('general_key')
        self.press('camera_bookmark_view0')
        self.assertTrue(panel.visible)
        self.assertEqual(self.api.hotkey(), ('camera_bookmark_save0', True))
        self.assertIn('F9', self.api.status()[0])
        self.press('camera_bookmark_save1')
        self.assertEqual(self.api.hotkey(), ('camera_bookmark_save1', False))

    def test_camera_keys_cannot_be_taken(self):
        self.hotkey_panel()
        self.click('general_key')
        for key in ('step_fwd', 'rot_l', 'cam_up'):
            self.press(key)
            self.assertEqual(self.api.hotkey(), ('camera_bookmark_save0', True))

    def test_learning_can_be_cancelled_three_ways(self):
        self.hotkey_panel()
        button = self.g.widgets.wh3pt_battle_general_key
        self.click('general_key')
        self.press('escape_menu')
        self.assertEqual(self.api.hotkey(), ('camera_bookmark_save0', False))
        self.click('general_key')
        self.click('general_key')
        self.assertEqual(self.api.hotkey(), ('camera_bookmark_save0', False))
        self.click('general_key')
        self.g.timers.wh3pt_battle_capture()
        self.assertEqual(self.api.hotkey(), ('camera_bookmark_save0', False))
        self.assertEqual(button.text, '快捷键：Shift+F9')
        self.assertIsNone(self.g.registry.wh3pt_general_hotkey)

    def test_an_old_learning_timeout_does_not_end_a_new_one(self):
        self.hotkey_panel()
        self.click('general_key')
        old = self.g.timers.wh3pt_battle_capture
        self.click('general_key')
        self.click('general_key')
        old()
        self.assertEqual(self.api.hotkey(), ('camera_bookmark_save0', True))

    def test_f9_puts_the_camera_back_while_shift_f9_is_the_hotkey(self):
        self.hotkey_panel()
        self.press('camera_bookmark_view0')
        self.assertTrue(self.g.widgets.wh3pt_battle_panel.visible)
        self.g.timers.wh3pt_battle_keep_camera()
        move = self.g.camera.moves[1]
        self.assertEqual((move.position, move.target, move.time, move.fov), ('camera_pos', 'camera_target', 0, -1))
        self.assertFalse(move.queue)        # works while paused too

    def test_f9_leaves_the_camera_alone_with_any_other_hotkey(self):
        self.hotkey_panel()
        self.g.registry.wh3pt_general_hotkey = 'camera_bookmark_save1'
        other = self.module.new(self.g.bm)
        other.start()
        other.shortcut('camera_bookmark_view0')
        self.assertIsNone(self.g.timers.wh3pt_battle_keep_camera)

    def test_a_camera_that_cannot_be_read_does_not_stop_f9(self):
        self.hotkey_panel()
        self.lua.execute("function bm:camera() error('no camera') end")
        self.press('camera_bookmark_view0')
        self.assertTrue(self.g.widgets.wh3pt_battle_panel.visible)

    def test_picking_selected_units_hands_them_over(self):
        self.battlefield()
        self.open_panel()
        self.g.common.selected[self.g.own.id] = True
        self.click('general_pick')
        self.assertIn('1 队', self.api.status()[0])
        self.g.timers.wh3pt_battle_general()
        self.assertTrue(self.api.general.managed(self.g.own.id))
        self.assertFalse(self.api.general.managed(self.g.ordinary.id))

    def test_unit_cards_show_who_answers_to_the_commander(self):
        self.battlefield()
        self.lua.execute('''
            cards=root:CreateComponent('review_DY','ui/wh3pt/label')
            own_card=cards:CreateComponent(tostring(own.id),'ui/wh3pt/label')
            other_card=cards:CreateComponent('9999','ui/wh3pt/label')
            function find_uicomponent(parent,...) return cards end
        ''')
        self.open_panel()
        self.click('general')
        self.g.battle_clock = 1000
        self.g.timers.wh3pt_battle_general()
        mark = self.g.own_card.Find(self.g.own_card, 'wh3pt_general_mark')
        self.assertEqual(mark.state, 'ok_dot')
        self.assertTrue(mark.visible)
        self.assertIsNone(self.g.other_card.Find(self.g.other_card, 'wh3pt_general_mark'))
        self.g.common.selected[self.g.own.id] = True
        self.g.battle_clock = 1500
        self.g.commands.Move()
        self.g.timers.wh3pt_battle_general()
        self.assertEqual(mark.state, 'busy_dot')
        self.click('general'); self.click('general'); self.click('general')
        self.g.timers.wh3pt_battle_general()
        self.assertFalse(mark.visible)

    def test_cards_that_cannot_be_found_are_not_a_problem(self):
        self.battlefield()
        self.open_panel()
        self.click('general')
        self.g.timers.wh3pt_battle_general()    # find_uicomponent does not even exist here

    def test_the_battle_end_stops_the_commander(self):
        self.battlefield()
        self.open_panel()
        self.click('general')
        self.g.timers.wh3pt_battle_general()
        self.g.phases.Complete()
        self.assertIsNone(self.g.timers.wh3pt_battle_general)
        self.assertFalse(self.api.general.managed(self.g.own.id))

    def test_victory_turns_the_commander_to_pursuit(self):
        self.battlefield()
        self.open_panel()
        self.click('general')
        self.g.conflict = False
        self.g.phases.VictoryCountdown()
        self.g.timers.wh3pt_battle_general()
        self.assertEqual(self.api.general.phase(), 'pursuit')

    def test_a_failure_in_the_commander_never_reaches_the_other_timers(self):
        self.battlefield()
        self.open_panel()
        self.click('general')
        self.lua.execute("function bm:get_non_player_alliance() error('gone') end")
        for _ in range(3):
            self.g.timers.wh3pt_battle_general()
        self.assertEqual(self.api.general.mode(), 'off')
        self.g.timers.wh3pt_battle_revival()

    def test_units_under_the_native_ai_keep_every_other_feature(self):
        self.battlefield()
        self.open_panel()
        self.click('general')
        self.g.timers.wh3pt_battle_general()
        self.g.own.player = False
        self.g.own.men = 10
        self.api.action('revive')
        self.assertGreater(self.g.own.men, 10)


if __name__ == '__main__':
    unittest.main()
