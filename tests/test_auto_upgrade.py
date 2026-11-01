from pathlib import Path
import re
import unittest
from lupa.lua51 import LuaRuntime

from pack_io import read_pack
from trainer_core import GAME
from tests import SRC

# The context system is modelled expression by expression: anything the script asks
# that this table does not know is an error, so a misspelt path cannot pass as "false".
HARNESS = r'''
player='player'; players_turn=true; saved={}; queue={}; messages={}; notices={}; logs={}
listeners={}; commands={}; pending={}; settlements={}; order={}; caps={}
treasury=1000; resources=100; renders=0; last_error=false; reject_commands=false; broken_read=false
function out(s) logs[#logs+1]=s end
ModLog=out
local function list(items)
    return {num_items=function() return #items end, item_at=function(_,i) return items[i+1] end}
end
function slot(target,extra)
    local s={CanUpgrade=true,IsActive=true,IsPlayerFaction=true,IsRegionSlot=true,IsEmpty=false,
        IsBuildingNew=false,IsUpgrading=false,IsConverting=false,IsRepairing=false,IsDismantling=false,
        CanRepair=false,CanConvert=false,lifecycle=false,target=target,built=tostring(target)..'_old',
        cost=100,resource_cost=0,browser=true,buildable=true,points=true,
        episodic=false,by_level=false,by_tech=false}
    s.upgrades={{key=target,ever=true}}
    for k,v in pairs(extra or {}) do s[k]=v end
    return s
end
function place(cqi,slots,extra)
    local p={owned=true,siege=false,abandoning=false,slots=slots}
    for k,v in pairs(extra or {}) do p[k]=v end
    settlements[cqi]=p; order[#order+1]=cqi
end
local function read_level(s,option,fn,path)
    if fn=='Key' then return option.key end
    assert(option.key==s.target,'asked about a branch: '..option.key)
    local a='('..path..')'
    local values={IsLockedByAnyTech=s.by_tech,
        ['IsActiveForBuildingBrowser'..a]=s.browser,['CanBuildInCurrentSlot'..a]=s.buildable,
        ['HasEnoughDevelopmentPoints(this)']=s.points,['CanAfford(this)']=treasury>=s.cost,
        ['CanAffordResourceCostForSlot'..a]=resources>=s.resource_cost,
        ['IsEpisodicRestricted'..a]=s.episodic,['IsLockedByLevel'..a]=s.by_level,
        ['DoesBuildingExceedCap'..a]=s.cap~=nil and caps[s.cap]==true}
    local value=values[fn]
    assert(value~=nil,'unknown level function: '..fn)
    return value
end
common={}
function common.get_context_value(kind,cqi,e)
    assert(kind=='CcoCampaignSettlement')
    if broken_read then error('context unavailable') end
    local p=assert(settlements[cqi],'unknown settlement')
    if e=='IsPlayerOwned' then return p.owned end
    if e=='IsUnderSiege' then return p.siege end
    if e=='IsAbandoning' then return p.abandoning end
    if e=='BuildingSlotList.Size' then return #p.slots end
    local ever_slot,ever_k,ever_arg=e:match('^BuildingCanEverBeBuilt%(BuildingSlotList%.At%((%d+)%)%.BuildingContext%.BuildingLevelRecordContext%.UpgradesList%.At%((%d+)%), BuildingSlotList%.At%((%d+)%)%)$')
    if ever_slot then
        assert(ever_slot==ever_arg,'level and slot disagree: '..e)
        return assert(p.slots[tonumber(ever_slot)+1].upgrades[tonumber(ever_k)+1],'upgrade out of range: '..e).ever
    end
    local wrapped=e:match('^IsContextValid%(BuildingSlotList%.At%((%d+)%)%.LifecycleStateContext%)$')
    if wrapped then return p.slots[tonumber(wrapped)+1].lifecycle end
    local index,rest=e:match('^BuildingSlotList%.At%((%d+)%)%.(.+)$')
    assert(index,'unknown expression: '..e)
    local s=assert(p.slots[tonumber(index)+1],'slot out of range: '..e)
    if rest=='BuildingContext.BuildingLevelRecordContext.UpgradesList.Size' then return #s.upgrades end
    local k,fn=rest:match('^BuildingContext%.BuildingLevelRecordContext%.UpgradesList%.At%((%d+)%)%.(.+)$')
    if k then return read_level(s,assert(s.upgrades[tonumber(k)+1]),fn,'BuildingSlotList.At('..index..')') end
    if rest=='PossibleUpgradeList(true).FirstContext.Key' then return s.first or s.target end
    if rest=='BuildingContext.BuildingLevelRecordContext.Key' then return s.built end
    local value=s[rest]
    assert(type(value)=='boolean','unknown slot function: '..rest)
    return value
end
function common.call_context_command(kind,cqi,e)
    assert(kind=='CcoCampaignSettlement')
    local index=assert(e:match('^BuildingSlotList%.At%((%d+)%)%.UpgradeToFirstAvailableOption$'),'unknown command: '..e)
    local s=settlements[cqi].slots[tonumber(index)+1]
    commands[#commands+1]=s.target
    if not reject_commands then pending[#pending+1]=s end
end
f={name=function() return player end,treasury=function() return treasury end}
function f:region_list()
    local items={}
    for _,cqi in ipairs(order) do items[#items+1]={cqi=function() return cqi end} end
    return list(items)
end
cm={}
function cm:get_saved_value(k) return saved[k] end
function cm:set_saved_value(k,v) saved[k]=v end
function cm:is_local_players_turn(force) assert(force==true); return players_turn end
core={}
function core:remove_listener(k) listeners[k]=nil end
function core:add_listener(k,event,cond,fn,persistent)
    assert(event=='FactionTurnStart' and cond==true and persistent==true); listeners[k]=fn
end
function turn_start(name)
    listeners.wh3pt_auto_upgrade_turn({faction=function() return {name=function() return name end} end})
end
env={faction=function() return f end,
    setting=function(id,field) return 'wh3pt_'..player..'_'..id..'_'..field end,
    message=function(s,e) messages[#messages+1]=s; last_error=e==true end,
    callback=function(fn) queue[#queue+1]=fn end,
    guard=function(fn) local ok,err=pcall(fn); if not ok then notices[#notices+1]=tostring(err) end; return ok end,
    render=function() renders=renders+1 end}
-- The game applies a command between ticks: it is paid for and starts only then.
function flush()
    local due=pending; pending={}
    for _,s in ipairs(due) do
        treasury=treasury-s.cost; resources=resources-s.resource_cost
        s.IsUpgrading=true
        if s.cap then caps[s.cap]=true end
    end
    local q=queue; queue={}
    for _,fn in ipairs(q) do fn() end
end
'''


class AutoUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(HARNESS)
        factory = self.lua.execute((SRC / 'ingame/auto_upgrade.lua').read_text(encoding='utf-8') + '\nreturn create_auto_upgrade')
        self.g = self.lua.globals()
        self.api = factory(self.g.env)

    def settle(self):
        for _ in range(100):
            if not self.g.queue[1] and not self.g.pending[1]:
                return
            self.g.flush()
        self.fail('auto upgrade run did not finish')

    def place(self, code):
        self.lua.execute(code)

    def commands(self):
        return list(self.g.commands.values())

    def messages(self):
        return list(self.g.messages.values())

    def enable(self):
        self.api.toggle(True)
        self.g.messages = self.lua.table()

    def run_check(self):
        self.enable()
        self.api.check()
        self.settle()

    def test_single_option_upgrades_when_affordable(self):
        self.place("place(1,{slot('farm_2',{cost=300})})")
        self.run_check()
        self.assertEqual(self.commands(), ['farm_2'])
        self.assertEqual(self.g.treasury, 700)
        self.assertEqual(self.messages()[-1], '建筑自动升级：已开始 1 项，花费 300 金')
        self.assertEqual(self.g.renders, 1)
        self.assertEqual(list(self.g.notices.values()), [])

    def test_every_slot_of_every_settlement_is_visited(self):
        self.place("place(1,{slot('a_2',{cost=10}),slot('b_2',{cost=10})}); place(2,{slot('c_2',{cost=10})})")
        self.run_check()
        self.assertEqual(self.commands(), ['a_2', 'b_2', 'c_2'])

    def test_slots_that_are_not_a_finished_building_with_one_upgrade_are_left_alone(self):
        for case in ["{CanUpgrade=false}", "{IsActive=false}", "{IsPlayerFaction=false}", "{IsRegionSlot=false}",
                     "{IsEmpty=true}", "{IsBuildingNew=true}", "{IsUpgrading=true}", "{IsConverting=true}",
                     "{IsRepairing=true}", "{IsDismantling=true}", "{CanRepair=true}", "{CanConvert=true}",
                     "{lifecycle=true}", "{upgrades={}}", "{first='a_conversion'}",
                     "{upgrades={{key='farm_2',ever=true},{key='mill_2',ever=true}}}",
                     "{upgrades={{key='farm_2',ever=false}}}",
                     "{browser=false}", "{buildable=false}", "{points=false}", "{episodic=true}",
                     "{by_level=true}", "{by_tech=true}", "{cost=1001}", "{resource_cost=101}"]:
            with self.subTest(case):
                self.place("settlements={}; order={}; place(1,{slot('farm_2'," + case + ")})")
                self.run_check()
                self.assertEqual(self.commands(), [])
                self.assertEqual(self.g.treasury, 1000)

    def test_branch_this_settlement_can_never_build_does_not_count(self):
        self.place("place(1,{slot('farm_2',{upgrades={{key='farm_2_a',ever=false},{key='farm_2',ever=true}}})})")
        self.run_check()
        self.assertEqual(self.commands(), ['farm_2'])

    def test_unavailable_settlements_are_skipped(self):
        for case in ["{owned=false}", "{siege=true}", "{abandoning=true}"]:
            with self.subTest(case):
                self.place("settlements={}; order={}; place(1,{slot('farm_2')}," + case + ")")
                self.run_check()
                self.assertEqual(self.commands(), [])

    def test_each_upgrade_is_judged_against_what_the_last_one_spent(self):
        self.place("place(1,{slot('a_2',{cost=700}),slot('b_2',{cost=700}),slot('c_2',{cost=200})})")
        self.run_check()
        self.assertEqual(self.commands(), ['a_2', 'c_2'])
        self.assertEqual(self.g.treasury, 100)
        self.assertEqual(self.messages()[-1], '建筑自动升级：已开始 2 项，花费 900 金；1 项暂不满足条件')

    def test_pooled_resource_cost_is_spent_between_upgrades(self):
        self.place("place(1,{slot('a_2',{resource_cost=80}),slot('b_2',{resource_cost=80})})")
        self.run_check()
        self.assertEqual(self.commands(), ['a_2'])
        self.assertEqual(self.g.resources, 20)

    def test_building_cap_filled_by_the_previous_upgrade_stops_the_next(self):
        self.place("place(1,{slot('a_2',{cap='temple'}),slot('b_2',{cap='temple'})})")
        self.run_check()
        self.assertEqual(self.commands(), ['a_2'])

    def test_finished_instantly_counts_as_started(self):
        self.place("place(1,{slot('a_2',{built='a_2_x'})})")
        self.g.reject_commands = True
        self.enable()
        self.api.check()
        self.g.settlements[1].slots[1].built = 'a_2'
        self.settle()
        self.assertEqual(self.messages()[-1], '建筑自动升级：已开始 1 项')
        self.assertFalse(self.g.last_error)

    def test_command_without_effect_is_reported_and_the_next_still_runs(self):
        self.place("place(1,{slot('a_2'),slot('b_2')})")
        self.g.reject_commands = True
        self.run_check()
        self.assertEqual(self.commands(), ['a_2', 'b_2'])
        self.assertEqual(self.messages()[-1], '建筑自动升级：已开始 0 项；2 项未生效，详见 mod 日志')
        self.assertTrue(self.g.last_error)
        self.assertIn('a_2', list(self.g.logs.values())[0])

    def test_slow_command_is_given_time_before_it_counts_as_failed(self):
        self.place("place(1,{slot('a_2'),slot('b_2')})")
        self.g.reject_commands = True
        self.enable()
        self.api.check()
        for _ in range(3):
            self.g.flush()
        self.assertEqual(self.commands(), ['a_2'])
        self.g.settlements[1].slots[1].IsUpgrading = True
        self.g.reject_commands = False
        self.settle()
        self.assertEqual(self.commands(), ['a_2', 'b_2'])
        self.assertEqual(self.messages()[-1], '建筑自动升级：已开始 2 项，花费 100 金')

    def test_turn_start_only_acts_for_the_player_while_enabled(self):
        self.place("place(1,{slot('a_2')})")
        self.g.turn_start('player')
        self.settle()
        self.assertEqual(self.commands(), [])
        self.enable()
        self.g.turn_start('someone_else')
        self.settle()
        self.assertEqual(self.commands(), [])
        self.g.turn_start('player')
        self.assertEqual(self.commands(), [])
        self.settle()
        self.assertEqual(self.commands(), ['a_2'])

    def test_quiet_turn_says_nothing_and_a_click_always_answers(self):
        self.enable()
        self.g.turn_start('player')
        self.settle()
        self.assertEqual(self.messages(), [])
        self.api.toggle(False)
        self.api.toggle()
        self.assertEqual(self.messages()[-1], '建筑自动升级已开：当前没有符合条件的建筑')
        self.place("place(1,{slot('a_2',{cost=5000})})")
        self.api.toggle(False)
        self.api.toggle()
        self.settle()
        self.assertEqual(self.messages()[-1], '建筑自动升级已开：1 项暂不满足条件')

    def test_click_runs_now_but_an_undo_only_restores_the_switch(self):
        self.place("place(1,{slot('a_2')})")
        self.api.toggle(True)
        self.assertTrue(self.api.enabled())
        self.settle()
        self.assertEqual(self.commands(), [])
        self.api.toggle(False)
        self.api.toggle()
        self.settle()
        self.assertEqual(self.commands(), ['a_2'])

    def test_nothing_is_issued_outside_the_players_turn(self):
        self.place("place(1,{slot('a_2')})")
        self.g.players_turn = False
        self.api.toggle()
        self.settle()
        self.assertEqual(self.commands(), [])
        self.assertTrue(self.api.enabled())

    def test_switching_off_stops_the_remaining_slots(self):
        self.place("place(1,{slot('a_2'),slot('b_2')})")
        self.enable()
        self.api.check()
        self.api.toggle(False)
        self.settle()
        self.assertEqual(self.commands(), ['a_2'])

    def test_second_check_waits_for_the_run_in_progress(self):
        self.place("place(1,{slot('a_2'),slot('b_2')})")
        self.enable()
        self.api.check()
        self.api.check()
        self.settle()
        self.assertEqual(self.commands(), ['a_2', 'b_2'])

    def test_switch_is_saved_per_player_and_reset_drops_a_run(self):
        self.place("place(1,{slot('a_2'),slot('b_2')})")
        self.enable()
        self.assertTrue(self.g.saved['wh3pt_player_auto_upgrade_enabled'])
        self.api.check()
        self.g.player = 'other'
        self.api.reset()
        self.assertFalse(self.api.enabled())
        self.settle()
        self.assertEqual(self.commands(), ['a_2'])
        self.g.player = 'player'
        self.api.reset()
        self.assertTrue(self.api.enabled())
        self.api.check()
        self.settle()
        self.assertEqual(self.commands(), ['a_2', 'b_2'])

    def test_failed_read_is_reported_and_does_not_block_the_next_turn(self):
        self.place("place(1,{slot('a_2'),slot('b_2')})")
        self.enable()
        self.api.check()
        self.g.broken_read = True
        self.settle()
        self.assertIn('context unavailable', list(self.g.notices.values())[0])
        self.g.broken_read = False
        self.api.check()
        self.settle()
        self.assertEqual(self.commands(), ['a_2', 'b_2'])

    def test_unreadable_slot_count_is_an_error_not_an_empty_settlement(self):
        self.place("place(1,{slot('a_2')})")
        self.enable()
        self.lua.execute("local read=common.get_context_value; common.get_context_value=function(k,c,e) if e=='BuildingSlotList.Size' then return nil end; return read(k,c,e) end")
        with self.assertRaisesRegex(Exception, '建筑格子读取失败'):
            self.api.check()

    def test_every_context_function_exists_in_the_installed_game(self):
        source = (SRC / 'ingame/auto_upgrade.lua').read_text(encoding='utf-8')
        code = '\n'.join(line for line in source.splitlines() if not line.strip().startswith('--'))
        used = set(re.findall(r'\b(?:Is|Can|Has|Does|Possible|Building|UpgradeTo|Upgrades)[A-Z]\w+', code)) - {'IsContextValid'}
        self.assertGreater(len(used), 20)
        metadata = read_pack(Path(GAME) / 'data/ui.pack', lambda n: n == 'ui\\metadata.json')['ui\\metadata.json']
        known = set(re.findall(r'"name"\s*:\s*"(\w+)"', metadata.decode('utf-8')))
        self.assertEqual(sorted(used - known), [])


if __name__ == '__main__':
    unittest.main()
