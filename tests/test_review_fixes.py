import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import app
from trainer_core import build, GAME, Options
from pack_io import read_pack, write_pack
from database import decode


class ReviewFixTests(unittest.TestCase):
    def test_disabled_invalid_basic_fields_and_draft_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'STATE', Path(tmp)/'settings.json'):
            window = app.App(); window.withdraw()
            try:
                window.clear()
                for _, control, _ in window.basic_numbers(): control.set('')
                window.attribute_options['ward'][0].set(True)
                window.attribute_options['ward'][1].set('45')
                opts = window.collect_options(); opts.validate()
                build(GAME,opts,local_dir=app.REFERENCE)
                window.options['regeneration'].set(True)
                with self.assertRaisesRegex(ValueError,'有效数值'): window.collect_options()
                window.attribute_options['ward'][1].set('incomplete')
                window.save()
                data = json.loads(app.STATE.read_text(encoding='utf-8'))
                self.assertEqual(data['basic_controls']['heal_percent'],'')
                self.assertEqual(data['attribute_controls']['ward']['value'],'incomplete')
                window.destroy()
                window = app.App(); window.withdraw()
                self.assertEqual(window.heal.get(),'')
                self.assertTrue(window.options['regeneration'].get())
                self.assertEqual(window.attribute_options['ward'][1].get(),'incomplete')
            finally: window.destroy()

    def test_failed_save_does_not_silently_close(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app,'STATE',Path(tmp)/'state.json'):
            window=app.App(); window.withdraw()
            try:
                with patch.object(window,'save',side_effect=OSError('disk full')), patch.object(app.messagebox,'showerror') as error:
                    window.close()
                    self.assertTrue(window.winfo_exists()); error.assert_called_once()
            finally: window.destroy()

    def test_unselected_corrupt_pack_isolated_but_selected_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            shutil.copy2(app.REFERENCE/'@玩家部队33特保.pack',root)
            (root/'bad.pack').write_bytes(write_pack({'db\\campaign_difficulty_handicap_effects_tables\\bad':b'\x01'}))
            _, report=build(GAME,Options(attributes={'ward':45}),local_dir=root)
            self.assertTrue(any('bad.pack' in w for w in report['warnings']))
            with self.assertRaises(Exception): build(GAME,Options(local_mods=['bad.pack']),local_dir=root)

    def test_desktop_restoration_build_drops_reference_cooldown_and_combat_potential(self):
        cases=[
            ('healing',Options(regeneration=True),.125,0,False),
            ('revive',Options(regeneration=True,resurrection=True),.125,0,True),
            ('ammo',Options(ammunition=True),0,.5,False),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for feature,opts,heal,ammo,resurrect in cases:
                with self.subTest(feature=feature):
                    payload,_=build(GAME,opts,local_dir=app.REFERENCE)
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
