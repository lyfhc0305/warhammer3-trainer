"""Checks of trainer_core.build that came out of a review."""
from pathlib import Path
import shutil
import tempfile
import unittest
from trainer_core import build, GAME, Options
from pack_io import read_pack, write_pack
from database import decode
from tests import REFERENCE


class ReviewFixTests(unittest.TestCase):
    def test_unselected_corrupt_pack_isolated_but_selected_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            shutil.copy2(REFERENCE/'@玩家部队33特保.pack',root)
            (root/'bad.pack').write_bytes(write_pack({'db\\campaign_difficulty_handicap_effects_tables\\bad':b'\x01'}))
            _, report=build(GAME,Options(attributes={'ward':45}),local_dir=root)
            self.assertTrue(any('bad.pack' in w for w in report['warnings']))
            with self.assertRaises(Exception): build(GAME,Options(local_mods=['bad.pack']),local_dir=root)

    def test_restoration_build_drops_reference_cooldown_and_combat_potential(self):
        cases=[
            ('healing',Options(regeneration=True),.125,0,False),
            ('revive',Options(regeneration=True,resurrection=True),.125,0,True),
            ('ammo',Options(ammunition=True),0,.5,False),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for feature,opts,heal,ammo,resurrect in cases:
                with self.subTest(feature=feature):
                    payload,_=build(GAME,opts,local_dir=REFERENCE)
                    p=Path(tmp)/(feature+'.pack'); p.write_bytes(payload)
                    files=read_pack(p)
                    phase_rows=[]; ability_rows=[]; junction_rows=[]
                    for name,data in files.items():
                        if name.startswith('db\\special_ability_phases_tables\\'):
                            phase_rows.extend(decode('special_ability_phases_tables',data)[1])
                        if name.startswith('db\\unit_special_abilities_tables\\'):
                            ability_rows.extend(decode('unit_special_abilities_tables',data)[1])
                        if name.startswith('db\\special_ability_to_special_ability_phase_junctions_tables\\'):
                            junction_rows.extend(decode('special_ability_to_special_ability_phase_junctions_tables',data)[1])
                    self.assertEqual(len(phase_rows),1)
                    self.assertEqual(len(ability_rows),1)
                    phase=phase_rows[0]
                    self.assertEqual(phase['ability_recharge_change'],0)
                    self.assertAlmostEqual(phase['heal_amount'],heal)
                    self.assertAlmostEqual(phase['replenish_ammo'],ammo)
                    self.assertEqual(phase['resurrect'],resurrect)
                    self.assertEqual(ability_rows[0]['additional_melee_cp'],0)
                    self.assertEqual(ability_rows[0]['effect_range'],0)
                    self.assertEqual(ability_rows[0]['num_effected_friendly_units'],0)
                    self.assertFalse(ability_rows[0]['update_targets_every_frame'])
                    self.assertTrue(junction_rows[0]['target_self'])
                    self.assertFalse(junction_rows[0]['target_friends'])


if __name__ == '__main__': unittest.main()
