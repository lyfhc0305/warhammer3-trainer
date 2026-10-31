from pathlib import Path
import unittest
from lupa.lua51 import LuaRuntime

from tests import ROOT, SRC, REFERENCE


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute('''
            current_player='player'; saved={}; queue={}; notices={}
            function UIComponent(c) return c end
            function wrap(data)
                function data:Call(key,...)
                    local v=self[key]
                    assert(v~=nil, 'undefined CCO field '..key)
                    if type(v)=='function' then return v(self,...) else return v end
                end
                return data
            end
            f={name=function() return current_player end}
            owner=wrap({Key='player'})
            char=wrap({CQI=101,IsPlayerCharacter=true,SkillPointsAvailable=0,HasUncommitedSkills=false})
            ch={is_null_interface=function() return false end,faction=function() return f end,
                is_wounded=function() return wounded==true end,has_military_force=function() return not no_force end}
            skill=wrap({Key='skill1',Level=0,TotalLevels=3,CharacterContext=char,IsBackgroundSkill=false})
            contexts={}
            native={}
            for i=1,4 do
                local id=i
                contexts[i]=wrap({UniqueUiId='unit_'..id,Name='Same type',FactionContext=owner,IsCharacter=false,ExperienceLevel=1,
                    DevLevelUp=function(self,delta) if not reject_rank then self.ExperienceLevel=self.ExperienceLevel+delta end end})
                native[i]={command_queue_index=function() return id end,
                    is_null_interface=function() return false end, faction=function() return f end,
                    has_unit_commander=function() return id==3 end,
                    associated_character=function() return {is_null_interface=function() return id~=4 end} end,
                    experience_level=function() return contexts[id].ExperienceLevel end}
            end
            units={num_items=function() return #native end,item_at=function(_,i) return native[i+1] end}
            function ch:military_force() return {unit_list=function() return units end,is_armed_citizenry=function() return garrison==true end} end
            function f:character_list() return {num_items=function() return 1 end,item_at=function() return ch end} end
            function cco(kind,id) assert(kind=='CcoCampaignUnit'); return contexts[id] end
            function component(kind, value) return {GetContextObject=function(_,requested) if requested==kind then return value end end} end
            cm={}
            function cm:get_saved_value(key) return saved[key] end
            function cm:set_saved_value(key,value) saved[key]=value end
            function cm:get_character_by_cqi(id) assert(id==101); return ch end
            function cm:add_experience_to_unit(u,delta)
                if not reject_rank and u:command_queue_index()~=reject_unit then local c=contexts[u:command_queue_index()]; c.ExperienceLevel=c.ExperienceLevel+delta end
            end
            function cm:add_skill(c,key,ignore_requirements,ignore_points)
                assert(c==ch and key=='skill1' and ignore_requirements==false and ignore_points==true)
                if not locked_skill then skill.Level=skill.Level+1 end
            end
            env={faction=function() return f end,
                character=function() if not no_selection then return ch end end,
                setting=function(id,field) return current_player..'_'..id..'_'..field end,
                message=function(text) notices[#notices+1]=text end,
                callback=function(fn) queue[#queue+1]=fn end,
                guard=function(fn) local ok,err=pcall(fn); if not ok then notices[#notices+1]=tostring(err) end end,
                render=function() render_count=(render_count or 0)+1 end}
            function flush() local q=queue; queue={}; for _,fn in ipairs(q) do fn() end end
        ''')
        factory = self.lua.execute((SRC / 'ingame/target_actions.lua').read_text(encoding='utf-8') + '\nreturn create_target_actions')
        self.g = self.lua.globals()
        self.api = factory(self.g.env)

    def card(self, index=1):
        return self.g.component('CcoCampaignUnit', self.g.contexts[index])

    def skill(self):
        return self.g.component('CcoCampaignCharacterSkill', self.g.skill)

    def test_all_regular_and_elite_units_change_but_lord_and_hero_do_not(self):
        self.api.rank(9);self.g.flush()
        self.assertEqual([self.g.contexts[i].ExperienceLevel for i in range(1,5)],[9,9,1,1])
        self.api.rank(3);self.g.flush()
        self.assertEqual([self.g.contexts[i].ExperienceLevel for i in range(1,5)],[3,3,1,1])
        self.assertIn('2',self.api.summary())

    def test_missing_wounded_garrison_and_detached_character_rejected(self):
        for flag in ['no_selection','wounded','garrison','no_force']:
            self.g[flag]=True
            with self.assertRaises(Exception):self.api.rank(9)
            self.g[flag]=False
        self.assertEqual(self.g.contexts[1].ExperienceLevel,1)

    def test_rank_rejection_bad_values_and_partial_failure(self):
        for rank in (-1,10,1.5):
            with self.assertRaises(Exception):self.api.rank(rank)
        self.g.reject_unit=2
        self.api.rank(9);self.g.flush()
        self.assertIn('1/2',self.g.notices[1])
        self.assertEqual(self.g.contexts[2].ExperienceLevel,1)

    def test_callback_checks_original_army_after_selection_changes(self):
        self.api.rank(8)
        self.g.no_selection=True
        self.g.flush()
        self.assertIn('2',self.g.notices[1])
        self.assertEqual(self.g.contexts[3].ExperienceLevel,1)

    def test_zero_points_native_skill_and_requirements(self):
        self.api.skill(self.skill())
        self.assertEqual(self.g.skill.Level, 0)
        self.api.toggle(True)
        self.api.skill(self.skill()); self.g.flush()
        self.assertEqual(self.g.skill.Level, 1)
        self.assertEqual(self.g.char.SkillPointsAvailable, 0)
        self.g.locked_skill = True
        self.api.skill(self.skill()); self.g.flush()
        self.assertIn('未学会', list(self.g.notices.values())[-1])

    def test_uncommitted_foreign_background_and_max_skills_rejected(self):
        self.api.toggle(True)
        for obj, key, value in [(self.g.char,'HasUncommitedSkills',True),
                                (self.g.char,'IsPlayerCharacter',False),
                                (self.g.skill,'IsBackgroundSkill',True),
                                (self.g.skill,'Level',3)]:
            before = obj[key]; obj[key] = value
            with self.assertRaises(Exception): self.api.skill(self.skill())
            obj[key] = before
        self.assertEqual(self.g.skill.Level, 0)

    def test_switch_state_is_per_player_and_selection_clears(self):
        self.api.toggle(True)
        self.g.current_player = 'other'
        self.api.reset()
        self.assertFalse(self.api.enabled())
        self.g.current_player = 'player'
        self.api.reset()
        self.assertTrue(self.api.enabled())


if __name__ == '__main__':
    unittest.main()
