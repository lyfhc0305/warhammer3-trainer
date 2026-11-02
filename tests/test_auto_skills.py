import unittest
from lupa.lua51 import LuaRuntime

from tests import SRC

# The character context is modelled expression by expression: anything the script asks
# that this table does not know is an error, so a misspelt path cannot pass as "false".
HARNESS = r'''
player='player'; players_turn=true; saved={}; queue={}; messages={}; notices={}; logs={}
listeners={}; chars={}; order={}; pending={}; added={}; refuse={}; journal={}
renders=0; last_error=false; no_context=false
function out(s) logs[#logs+1]=s end
ModLog=out
local function list(items)
    return {num_items=function() return #items end, item_at=function(_,i) return items[i+1] end}
end
function skill(key,x,y,extra)
    local s={key=key,x=x,y=y,level=0,levels=1,status='available',background=false}
    for k,v in pairs(extra or {}) do s[k]=v end
    return s
end
function hero(cqi,points,skills,extra)
    local c={cqi=cqi,points=points,skills=skills,subtype='wh_main_emp_lord',faction=player,uncommitted=false,valid=true}
    for k,v in pairs(extra or {}) do c[k]=v end
    chars[cqi]=c; order[#order+1]=cqi
    return c
end
function interface(c)
    return {cqi=function() return c.cqi end, character_subtype_key=function() return c.subtype end,
        is_null_interface=function() return not c.valid end,
        faction=function() return {name=function() return c.faction end} end}
end
common={}
function common.get_context_value(kind,cqi,e)
    assert(kind=='CcoCampaignCharacter')
    if no_context then error('context unavailable') end
    local c=assert(chars[cqi],'unknown character')
    if e=='SkillPointsAvailable' then return c.points end
    if e=='HasUncommitedSkills' then return c.uncommitted end
    if e=='SkillList.Size' then return #c.skills end
    local index,fn=e:match('^SkillList%.At%((%d+)%)%.(%a+)$')
    assert(index,'unknown expression: '..e)
    local s=assert(c.skills[tonumber(index)+1],'skill out of range: '..e)
    local values={Status=s.status,Key=s.key,Level=s.level,TotalLevels=s.levels,
        IsBackgroundSkill=s.background,Indent=s.x,Tier=s.y}
    local value=values[fn]
    assert(value~=nil,'unknown skill function: '..fn)
    return value
end
f={name=function() return player end}
function f:character_list()
    local items={}
    for _,cqi in ipairs(order) do items[#items+1]=interface(chars[cqi]) end
    return list(items)
end
cm={}
function cm:get_saved_value(k) return saved[k] end
function cm:set_saved_value(k,v) saved[k]=v end
function cm:is_local_players_turn(force) assert(force==true); return players_turn end
function cm:get_character_by_cqi(cqi) local c=chars[cqi]; return c and interface(c) or nil end
function cm:add_skill(ch,key,ignore_requirements,ignore_points)
    assert(ignore_requirements==false and ignore_points==false,'auto allocation must pay and respect requirements')
    added[#added+1]=key
    pending[#pending+1]={cqi=ch:cqi(),key=key}
end
core={}
function core:remove_listener(k) listeners[k]=nil end
function core:add_listener(k,event,cond,fn,persistent)
    assert(cond==true and persistent==true); listeners[k]={event=event,fn=fn}
end
function fire(event,ctx) for _,l in pairs(listeners) do if l.event==event then l.fn(ctx) end end end
function rank_up(cqi) fire('CharacterRankUp',{character=function() return interface(chars[cqi]) end}) end
function turn_start(name) fire('FactionTurnStart',{faction=function() return {name=function() return name end} end}) end
env={faction=function() return f end,
    setting=function(id,field) return 'wh3pt_'..player..'_'..id..'_'..field end,
    message=function(s,e) messages[#messages+1]=s; last_error=e==true end,
    callback=function(fn) queue[#queue+1]=fn end,
    guard=function(fn) local ok,err=pcall(fn); if not ok then notices[#notices+1]=tostring(err) end; return ok end,
    render=function() renders=renders+1 end,
    journal=function(name,limit) journal_name=name; return function(text) journal[#journal+1]=text end end,
    data={exclusive={wh_main_emp_lord={mount_griffon=true}}, useless={hinder=true}}}
-- The game applies add_skill between ticks, and only what the skill tree would allow.
function flush()
    local due=pending; pending={}
    for _,p in ipairs(due) do
        local c=chars[p.cqi]
        for _,s in ipairs(c.skills) do
            if s.key==p.key and not refuse[p.key] and c.points>0 and s.status=='available' then
                s.level=s.level+1; c.points=c.points-1
                if s.level>=s.levels then s.status='complete' end
                for _,child in ipairs(s.unlocks or {}) do
                    for _,t in ipairs(c.skills) do if t.key==child then t.status='available' end end
                end
            end
        end
    end
    local q=queue; queue={}
    for _,fn in ipairs(q) do fn() end
end
'''


class AutoSkillTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(HARNESS)
        factory = self.lua.execute((SRC / 'ingame/auto_skills.lua').read_text(encoding='utf-8') + '\nreturn create_auto_skills')
        self.g = self.lua.globals()
        self.api = factory(self.g.env)

    def tearDown(self):
        self.assertEqual(list(self.g.notices.values()), [])

    def settle(self):
        for _ in range(200):
            if not self.g.queue[1] and not self.g.pending[1]:
                return
            self.g.flush()
        self.fail('auto skill run did not finish')

    def added(self):
        return list(self.g.added.values())

    def messages(self):
        return list(self.g.messages.values())

    def enable(self):
        self.api.toggle(True)
        self.g.messages = self.lua.table()

    def run_check(self):
        self.enable()
        self.api.check()
        self.settle()

    def test_spends_every_point_left_to_right_then_top_to_bottom(self):
        self.lua.execute("hero(1,3,{skill('b',1,0),skill('a',0,1),skill('c',0,0)})")
        self.run_check()
        self.assertEqual(self.added(), ['c', 'a', 'b'])
        self.assertEqual(self.g.chars[1].points, 0)
        self.assertEqual(self.messages(), ['自动加点：1 名角色学习了 3 个技能'])
        self.assertEqual(self.g.renders, 1)

    def test_every_character_with_points_is_visited(self):
        self.lua.execute("hero(1,1,{skill('a',0,0)}); hero(2,0,{skill('b',0,0)}); hero(3,1,{skill('c',0,0)})")
        self.run_check()
        self.assertEqual(self.added(), ['a', 'c'])
        self.assertEqual(self.messages(), ['自动加点：2 名角色学习了 2 个技能'])

    def test_choices_between_options_stay_with_the_player(self):
        self.lua.execute("hero(1,2,{skill('mount_griffon',0,0),skill('blade',1,0)})")
        self.run_check()
        self.assertEqual(self.added(), ['blade'])
        self.assertEqual(self.g.chars[1].points, 1)
        self.assertEqual(self.messages(), ['自动加点：1 名角色学习了 1 个技能；1 名角色的剩余点数只能用在多选一或未解锁的技能上，留给你'])
        self.assertTrue(any('1 choices left to the player' in line for line in self.g.logs.values()))

    def test_a_choice_in_one_tree_is_an_ordinary_skill_in_another(self):
        self.lua.execute("hero(1,1,{skill('mount_griffon',0,0)},{subtype='wh_main_emp_wizard'})")
        self.run_check()
        self.assertEqual(self.added(), ['mount_griffon'])

    def test_useless_skills_come_last(self):
        self.lua.execute("hero(1,1,{skill('hinder',0,0),skill('useful',3,3)})")
        self.run_check()
        self.assertEqual(self.added(), ['useful'])

    def test_skills_unlocked_by_a_point_are_considered_next(self):
        self.lua.execute("hero(1,2,{skill('parent',0,0,{unlocks={'child'}}),skill('child',1,0,{status='locked'})})")
        self.run_check()
        self.assertEqual(self.added(), ['parent', 'child'])

    def test_a_skill_with_levels_takes_points_until_full(self):
        self.lua.execute("hero(1,4,{skill('deep',0,0,{levels=3}),skill('next',1,0)})")
        self.run_check()
        self.assertEqual(self.added(), ['deep', 'deep', 'deep', 'next'])

    def test_locked_complete_and_background_skills_are_left_alone(self):
        self.lua.execute("""hero(1,2,{skill('locked',0,0,{status='locked'}),skill('done',0,1,{status='complete'}),
            skill('born',0,2,{background=true}),skill('full',0,3,{level=1})})""")
        self.api.toggle()
        self.settle()
        self.assertEqual(self.added(), [])
        self.assertEqual(self.messages()[-1], '自动加点已开：1 名角色只剩多选一或未解锁的技能，留给你')

    def test_a_refused_skill_is_skipped_and_reported(self):
        self.lua.execute("refuse.a=true; hero(1,1,{skill('a',0,0),skill('b',1,0)})")
        self.run_check()
        self.assertEqual(self.added(), ['a', 'b'])
        self.assertEqual(self.messages(), ['自动加点：1 名角色学习了 1 个技能；1 次未生效，详见 wh3pt_skills.log'])
        self.assertTrue(self.g.last_error)
        self.assertTrue(any('not learned: a' in line for line in self.g.logs.values()))

    def test_a_character_whose_picks_keep_failing_is_given_up(self):
        self.lua.execute("refuse.a=true; refuse.b=true; refuse.c=true; refuse.d=true;"
                         "hero(1,1,{skill('a',0,0),skill('b',1,0),skill('c',2,0),skill('d',3,0)})")
        self.run_check()
        self.assertEqual(self.added(), ['a', 'b', 'c'])

    def test_picks_the_player_has_not_confirmed_win(self):
        self.lua.execute("hero(1,1,{skill('a',0,0)},{uncommitted=true})")
        self.run_check()
        self.assertEqual(self.added(), [])

    def test_points_are_only_spent_on_the_players_turn(self):
        self.lua.execute("players_turn=false; hero(1,1,{skill('a',0,0)})")
        self.run_check()
        self.assertEqual(self.added(), [])

    def test_turn_start_and_rank_up_start_a_run(self):
        self.lua.execute("hero(1,1,{skill('a',0,0)}); hero(2,1,{skill('b',0,0)})")
        self.enable()
        self.g.turn_start('someone_else')
        self.settle()
        self.assertEqual(self.added(), [])
        self.g.turn_start('player')
        self.settle()
        self.assertEqual(self.added(), ['a', 'b'])
        self.lua.execute("chars[2].points=1; chars[2].skills[1+1]=skill('c',1,0)")
        self.g.rank_up(2)
        self.g.rank_up(2)
        self.settle()
        self.assertEqual(self.added(), ['a', 'b', 'c'])

    def test_a_character_ranking_up_after_its_visit_gets_a_second_run(self):
        self.lua.execute("hero(1,1,{skill('a',0,0)}); hero(2,1,{skill('b',0,0)})")
        self.enable()
        self.api.check()
        self.g.flush()  # character 1 is done, character 2 is waiting for its point
        self.lua.execute("chars[1].points=1; chars[1].skills[2]=skill('c',1,0)")
        self.api.check()
        self.settle()
        self.assertEqual(self.added(), ['a', 'b', 'c'])

    def test_switching_off_stops_a_run_in_flight(self):
        self.lua.execute("hero(1,3,{skill('a',0,0),skill('b',1,0),skill('c',2,0)})")
        self.enable()
        self.api.check()
        self.api.toggle(False)
        self.settle()
        self.assertEqual(self.added(), ['a'])
        self.assertEqual(self.messages()[-1], '自动加点已关闭；已学的技能保留')

    def test_unreadable_skill_trees_are_reported(self):
        self.lua.execute("no_context=true; hero(1,1,{skill('a',0,0)})")
        self.api.toggle()
        self.settle()
        self.assertEqual(self.messages()[-1], '自动加点已开，但读不到角色的技能树，详见 wh3pt_skills.log')
        self.assertTrue(self.g.last_error)

    def test_every_run_is_written_down(self):
        self.lua.execute("hero(1,1,{skill('a',0,0)}); hero(2,1,{skill('mount_griffon',0,0)})")
        self.run_check()
        lines = list(self.g.journal.values())
        self.assertEqual(self.g.journal_name, 'wh3pt_skills.log')
        self.assertIn('add_skill a for character 1', lines)
        self.assertEqual(lines[-1], 'run: 2 characters; learned 1 for 1, left to the player 1, failed 0, unreadable 0')

    def test_nothing_to_spend_is_said_on_the_click_only(self):
        self.lua.execute("hero(1,0,{skill('a',0,0)})")
        self.api.toggle()
        self.settle()
        self.assertEqual(self.messages()[-1], '自动加点已开：现在没有待分配的技能点')
        self.g.messages = self.lua.table()
        self.api.check()
        self.settle()
        self.assertEqual(self.messages(), [])

    def test_switch_is_saved_and_read_back_on_reset(self):
        self.api.toggle()
        self.assertTrue(self.g.saved['wh3pt_player_auto_skills_enabled'])
        self.api.reset()
        self.assertTrue(self.api.enabled())
        self.api.toggle()
        self.assertFalse(self.g.saved['wh3pt_player_auto_skills_enabled'])
        self.api.reset()
        self.assertFalse(self.api.enabled())


if __name__ == '__main__':
    unittest.main()
