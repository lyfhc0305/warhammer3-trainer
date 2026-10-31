import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import asdict
from trainer_core import *
from database import decode

from tests import ROOT, SRC, REFERENCE


class TrainerTests(unittest.TestCase):
    def test_custom_attributes_roundtrip(self):
        from attributes import ATTRIBUTES, NEGATIVE
        values = {key: 47.5 for key in ATTRIBUTES}
        values['recruitment'] = 17
        files = self.unpack(build(GAME, Options(attributes=values), local_dir=REFERENCE)[0])
        data = next(b for n, b in files.items() if 'campaign_difficulty_handicap' in n)
        _, rows = decode('campaign_difficulty_handicap_effects_tables', data)
        self.assertEqual(len(rows), 100)
        for key, spec in ATTRIBUTES.items():
            matching = [r for r in rows if r['effect'] == spec[1] and r['human'] == spec[2]]
            self.assertEqual(len(matching), 5)
            self.assertTrue(all(r['effect_value'] == (-values[key] if key in NEGATIVE else values[key]) for r in matching))
            if key in ('health', 'recruitment'):
                scope = 'faction_to_character_own' if key == 'health' else 'faction_to_province_own'
                self.assertEqual({r['effect_scope'] for r in matching}, {scope})

    def test_custom_attribute_rejects_invalid_values(self):
        for values in [{'ward': 101}, {'health': float('nan')}, {'speed': -1}, {'unknown': 5}, {'recruitment': 2.5}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                build(GAME, Options(attributes=values), local_dir=REFERENCE)

    def test_attribute_and_one_turn_combination(self):
        files = self.unpack(build(GAME, Options(attributes={'ward': 48}, local_mods=['@玩家战役1回合建筑.pack']), local_dir=REFERENCE)[0])
        _, rows = decode('campaign_difficulty_handicap_effects_tables', next(b for n, b in files.items() if 'campaign_difficulty_handicap' in n))
        self.assertEqual(sorted({r['effect_value'] for r in rows}), [-500, 48])

    def test_custom_basic_values(self):
        files = self.unpack(build(GAME, Options(ammunition=True, ammo_percent=23, reload=True, reload_value=37, foreign_replenishment=True, foreign_value=82))[0])
        for n, b in files.items():
            if n.startswith('db\\special_ability_phases_tables\\'):
                rows=decode('special_ability_phases_tables', b)[1]
                self.assertEqual(next(r for r in rows if r['id']=='blood')['replenish_ammo'],0)
                self.assertAlmostEqual(next(r for r in rows if r['id']=='wh3pt_static_ammo')['replenish_ammo'], .23)
            if n.startswith('db\\special_ability_phase_stat_effects_tables\\'):
                self.assertEqual(decode('special_ability_phase_stat_effects_tables', b)[1][0]['value'], 37)

    def unpack(self, payload):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'test.pack'; path.write_bytes(payload)
            return read_pack(path)

    def test_all_local_mods_migrate_and_roundtrip(self):
        report = []
        for path in REFERENCE.glob('*.pack'):
            if path.name == PACK_NAME: continue
            with self.subTest(mod=path.name):
                payload, result = build(GAME, Options(local_mods=[path.name]), local_dir=REFERENCE)
                files = self.unpack(payload)
                for name, data in files.items():
                    if name.startswith('db\\'):
                        table = name.split('\\')[1]
                        version, rows = decode(table, data)
                        self.assertEqual(decode(table, encode(table, version, rows)), (version, rows))
                report.append({'mod': path.name, 'status': 'offline-pass', 'migrations': result['migrations']})
        self.assertGreaterEqual(len(report), 34)
        (ROOT / 'output').mkdir(exist_ok=True)
        (ROOT / 'output/local-validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    def test_all_basic(self):
        options = Options(**{k: True for k, v in asdict(Options()).items() if type(v) is bool})
        files = self.unpack(build(GAME, options)[0])
        self.assertTrue(any(n.endswith('aigeneral_iii_main.lua') for n in files))
        self.assertFalse(any(n.endswith('.rpfm_reserved') for n in files))
        phase = next(b for n, b in files.items() if n.startswith('db\\special_ability_phases_tables\\'))
        version, rows = decode('special_ability_phases_tables', phase)
        self.assertEqual(version, 44)
        self.assertAlmostEqual(rows[0]['heal_amount'], .125)
        self.assertFalse(rows[0]['freeze_fatigue'])

    def test_vigour_has_no_hidden_battle_effects(self):
        files = self.unpack(build(GAME, Options(vigour=True))[0])
        dbs = [n for n in files if n.startswith('db\\')]
        self.assertEqual(len(dbs), 1)
        _, rows = decode('campaign_difficulty_handicap_effects_tables', files[dbs[0]])
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(r['human'] and r['effect'] == EFFECTS['vigour'] for r in rows))

    def test_preset_excludes_vanilla_abilities(self):
        name = next(p.name for p in REFERENCE.glob('*每3秒60*.pack'))
        files = self.unpack(build(GAME, Options(local_mods=[name]), local_dir=REFERENCE)[0])
        data = next(b for n, b in files.items() if n.startswith('db\\unit_special_abilities_tables\\'))
        version, rows = decode('unit_special_abilities_tables', data)
        self.assertEqual(version, 74)
        self.assertEqual([r['key'] for r in rows], ['SSS','wh3pt_legacy_ammo'])

    def test_conflicting_strengths_rejected(self):
        with self.assertRaisesRegex(ValueError, '冲突'):
            build(GAME, Options(local_mods=['@玩家部队33特保.pack', '@玩家部队99特保.pack']), local_dir=REFERENCE)
        with self.assertRaises(ValueError):
            build(GAME, Options(local_mods=[p.name for p in REFERENCE.glob('*每3秒*.pack')]), local_dir=REFERENCE)

    def test_campaign_combination_preserves_ai_nerf(self):
        names = [p.name for p in REFERENCE.glob('@玩家战役*.pack') if '减半' not in p.name]
        files = self.unpack(build(GAME, Options(local_mods=names), local_dir=REFERENCE)[0])
        data = next(b for n, b in files.items() if 'campaign_difficulty_handicap' in n)
        _, rows = decode('campaign_difficulty_handicap_effects_tables', data)
        self.assertEqual(sum(not r['human'] for r in rows), 5)
        self.assertEqual(len(rows), 50)

    def test_invalid_and_empty_settings(self):
        for options in [Options(), Options(cap=True, cap_value=float('nan')), Options(regeneration=True, heal_percent=-1), Options(local_mods=['../x.pack'])]:
            with self.subTest(options=options), self.assertRaises(ValueError): build(GAME, options)

    def test_pack_truncation_and_duplicate_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'bad.pack'
            path.write_bytes(write_pack({'a.txt': b'abc'})[:-1])
            with self.assertRaises(ValueError): read_pack(path)
            path.write_bytes(write_pack({'../escape': b'x'}))
            with self.assertRaises(ValueError): read_pack(path)

    @patch('trainer_core.running', return_value=False)
    def test_install_update_uninstall_only_owned_file(self, _):
        with tempfile.TemporaryDirectory() as temp:
            game = Path(temp) / 'game'; (game / 'data').mkdir(parents=True)
            (game / 'Warhammer3.exe').write_bytes(b'fixture')
            (game / 'data/db.pack').write_bytes(b'fixture')
            original = game / 'data/original.pack'; original.write_bytes(b'never change')
            history = Path(temp) / 'history'
            payload = write_pack({'wh3_personal_trainer_marker.txt': b'WH3-PERSONAL-TRAINER-v1'})
            target = install(game, payload, history)
            self.assertEqual(target.read_bytes(), payload)
            payload2 = write_pack({'wh3_personal_trainer_marker.txt': b'WH3-PERSONAL-TRAINER-v1', 'x': b'2'})
            install(game, payload2, history)
            self.assertEqual((history / (sha(payload) + '.pack')).read_bytes(), payload)
            self.assertTrue(uninstall(game, history)); self.assertFalse(uninstall(game, history))
            self.assertEqual(original.read_bytes(), b'never change')
            target.write_bytes(write_pack({'foreign': b'untouched'}))
            before = target.read_bytes()
            with self.assertRaises(ValueError): install(game, payload, history)
            with self.assertRaises(ValueError): uninstall(game, history)
            self.assertEqual(target.read_bytes(), before)

    @patch('trainer_core.running', return_value=True)
    def test_running_game_blocks_mutation(self, _):
        with self.assertRaisesRegex(ValueError, '退出游戏'):
            install(GAME, b'', ROOT / 'history')


if __name__ == '__main__':
    unittest.main(verbosity=2)
