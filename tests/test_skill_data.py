import unittest

from skill_data import skill_data
from trainer_core import GAME


class SkillDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = skill_data(GAME)

    def test_every_skill_table_is_read(self):
        self.assertTrue(self.data['complete'])
        self.assertGreater(len(self.data['exclusive']), 100)
        self.assertGreater(len(self.data['useless']), 50)

    def test_options_that_lock_each_other_are_choices(self):
        necrotect = self.data['exclusive']['wh2_dlc09_tmb_necrotect']
        self.assertEqual(set(necrotect), {
            'wh2_dlc09_skill_tmb_necrotect_master_heirotitan',
            'wh2_dlc09_skill_tmb_necrotect_master_necrosphinx',
            'wh2_dlc09_skill_tmb_necrotect_master_warsphinx',
        })

    def test_a_choice_belongs_to_the_trees_that_lock_it(self):
        # The Skaven campaign path is a choice in Skaven trees only.
        holders = {s for s, skills in self.data['exclusive'].items() if 'wh2_main_skill_skv_campaign_corruptive' in skills}
        self.assertIn('wh2_main_skv_warlord', holders)
        self.assertTrue(all('_skv_' in s for s in holders))

    def test_skills_the_ai_never_wants_are_known(self):
        self.assertIn('wh2_main_skill_all_hero_hinder_agent_assassinate', self.data['useless'])


if __name__ == '__main__':
    unittest.main()
