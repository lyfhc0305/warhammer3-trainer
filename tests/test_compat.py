"""What happens to an installed Pack when the game underneath it changes."""
import json
from pathlib import Path
import re
import struct
import tempfile
import unittest
from unittest.mock import patch

from tests import REFERENCE, SRC
import database
from database import decode, table_version
from game_compat import MANIFEST, addon_record, check, game_version, make_manifest, manifest_addons, read_manifest, steam_build
from ingame_installer import detect_installed_mode, prepare_payload
from pack_io import read_pack, write_pack
import trainer_core
from trainer_core import GAME, PACK_NAME, Options, additive_change, build

MARKER = {'wh3_personal_trainer_marker.txt': b'WH3-PERSONAL-TRAINER-v1', 'wh3pt_mode.txt': b'ingame-v2'}


def table(version, body=b''):
    return b'\xfc\xfd\xfe\xff' + struct.pack('<I', version) + b'\1' + struct.pack('<I', 0) + body


class FakeGame:
    """A Steam-shaped install whose db.pack holds only the tables a test names."""

    def __init__(self, root, tables):
        self.root = Path(root)
        self.path = self.root / 'steamapps/common/Total War WARHAMMER III'
        (self.path / 'data').mkdir(parents=True)
        (self.path / 'Warhammer3.exe').write_bytes(b'fixture')
        (self.root / 'steamapps/appmanifest_1142710.acf').write_text('"AppState"\n{\n\t"buildid"\t\t"123"\n}\n')
        self.write(tables)

    def write(self, tables):
        (self.path / 'data/db.pack').write_bytes(
            write_pack({f'db\\{name}\\data__': data for name, data in tables.items()}, pack_type=1))

    def pack(self, tracked, extra=None, version=trainer_core.VERSION, addons=None):
        files = dict(MARKER, **{MANIFEST: make_manifest(self.path, tracked, version, addons)}, **(extra or {}))
        target = self.path / 'data' / PACK_NAME
        target.write_bytes(write_pack(files))
        return target


class CompatibilityTests(unittest.TestCase):
    TABLES = {'main_units_tables': table(7, b'a'), 'effects_tables': table(2, b'b'), 'other_tables': table(1, b'c')}
    TRACKED = {'main_units_tables', 'effects_tables'}

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.game = FakeGame(temp.name, self.TABLES)
        self.pack = self.game.pack(self.TRACKED)

    def changed(self, **updates):
        self.game.write(dict(self.TABLES, **updates))
        return check(self.game.path, self.pack)

    def test_unchanged_game_is_current(self):
        status = check(self.game.path, self.pack)
        self.assertEqual(status.state, 'current')
        self.assertTrue(status.usable)
        self.assertEqual(status.manifest['steam_build'], '123')
        self.assertEqual(status.manifest['trainer_version'], trainer_core.VERSION)

    def test_patch_that_leaves_our_tables_alone_stays_usable(self):
        status = self.changed(other_tables=table(1, b'patched'))
        self.assertEqual(status.state, 'compatible')
        self.assertTrue(status.usable)

    def test_changed_content_of_a_tracked_table_is_stale(self):
        status = self.changed(main_units_tables=table(7, b'patched'))
        self.assertEqual(status.state, 'stale')
        self.assertFalse(status.usable)
        self.assertEqual(status.changes, ['main_units_tables 内容变化'])

    def test_table_version_bump_is_named(self):
        status = self.changed(main_units_tables=table(8, b'a'))
        self.assertEqual(status.changes, ['main_units_tables v7 → v8'])
        self.assertIn('v7 → v8', status.describe())

    def test_removed_table_is_stale(self):
        tables = dict(self.TABLES)
        del tables['effects_tables']
        self.game.write(tables)
        self.assertEqual(check(self.game.path, self.pack).changes, ['effects_tables 已不存在'])

    def test_pack_without_a_manifest_is_unknown_not_assumed_good(self):
        self.pack.write_bytes(write_pack(MARKER))
        status = check(self.game.path, self.pack)
        self.assertEqual(status.state, 'unknown')
        self.assertFalse(status.usable)

    def test_unreadable_manifest_is_unknown(self):
        self.pack.write_bytes(write_pack(dict(MARKER, **{MANIFEST: b'{broken'})))
        self.assertIsNone(read_manifest(self.pack))
        self.assertEqual(check(self.game.path, self.pack).state, 'unknown')

    def test_installer_reports_a_stale_pack_and_a_healthy_one(self):
        self.assertEqual(detect_installed_mode(self.game.path).kind, 'ok')
        self.game.write(dict(self.TABLES, main_units_tables=table(8, b'a')))
        status = detect_installed_mode(self.game.path)
        self.assertEqual(status.kind, 'stale')
        self.assertIn('重新适配', status.title)
        self.assertIn('v7 → v8', status.desc)

    def test_installer_offers_an_update_when_the_installed_pack_is_older(self):
        self.game.pack(self.TRACKED, version='1.0.0', addons=addon_record(['building']))
        status = detect_installed_mode(self.game.path)
        self.assertEqual(status.kind, 'info')
        self.assertIn(trainer_core.VERSION, status.title)
        self.assertIn('1.0.0', status.desc)
        self.assertEqual(status.addons['enabled'], ['building'])  # an update keeps the modules

    def test_installer_flags_packs_from_before_manifests(self):
        self.pack.write_bytes(write_pack(MARKER))
        status = detect_installed_mode(self.game.path)
        self.assertEqual(status.kind, 'stale')
        self.assertIn('需要更新', status.title)
        self.assertIsNone(status.addons)

    def test_manifest_records_optional_modules_and_their_parameters(self):
        record = addon_record(['shop', 'cap', 'bogus'], {'cap_value': 75, 'agent_ai_value': 40})
        self.assertEqual(record, {'enabled': ['cap', 'shop'], 'values': {'cap_value': 75.0}})
        self.game.pack(self.TRACKED, addons=record)
        self.assertEqual(manifest_addons(read_manifest(self.pack)), record)
        self.assertEqual(manifest_addons({'addons': {'enabled': ['building'], 'values': {'cap_value': 'x'}}}),
                         {'enabled': ['building'], 'values': {}})
        # The module that became part of every build falls out of old records quietly.
        self.assertEqual(manifest_addons({'addons': {'enabled': ['ai', 'cap'], 'values': {}}}),
                         {'enabled': ['cap'], 'values': {}})
        self.game.pack(self.TRACKED)
        self.assertIsNone(manifest_addons(read_manifest(self.pack)))
        self.assertIsNone(manifest_addons({'addons': 'cap'}))

    def test_installer_names_the_installed_modules(self):
        self.game.pack(self.TRACKED, addons=addon_record(['shop', 'cap'], {'cap_value': 50}))
        status = detect_installed_mode(self.game.path)
        self.assertEqual((status.kind, status.title), ('ok', '已安装 · 含 2 个附加模块'))
        self.assertIn('全局战斗回血上限（50 倍）', status.desc)
        self.assertIn('基斯里夫祈祷神器奖励', status.desc)
        self.assertEqual(status.addons, {'enabled': ['cap', 'shop'], 'values': {'cap_value': 50.0}})
        self.game.pack(self.TRACKED, addons=addon_record([]))
        status = detect_installed_mode(self.game.path)
        self.assertEqual((status.kind, status.title), ('ok', '已安装 · 纯净面板'))
        self.assertEqual(status.addons, {'enabled': [], 'values': {}})

    def test_installer_keeps_working_with_packs_that_predate_module_records(self):
        status = detect_installed_mode(self.game.path)
        self.assertEqual((status.kind, status.title), ('ok', '已安装游戏内面板'))
        self.assertIsNone(status.addons)
        self.assertIn('没有记录附加模块', status.desc)

    def test_stale_pack_still_reports_its_modules(self):
        self.game.pack(self.TRACKED, addons=addon_record(['agent_ai'], {'agent_ai_value': 80}))
        self.game.write(dict(self.TABLES, main_units_tables=table(8, b'a')))
        status = detect_installed_mode(self.game.path)
        self.assertEqual(status.kind, 'stale')
        self.assertEqual(status.addons, {'enabled': ['agent_ai'], 'values': {'agent_ai_value': 80.0}})

    def test_steam_build_is_optional(self):
        self.assertEqual(steam_build(self.game.path), '123')
        (self.game.root / 'steamapps/appmanifest_1142710.acf').unlink()
        self.assertIsNone(steam_build(self.game.path))

    def test_game_version_is_optional(self):
        self.assertIsNone(game_version(self.game.path))
        self.assertRegex(game_version(GAME), r'^\d+\.\d+\.\d+$')


class PreparePayloadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.game = FakeGame(temp.name, CompatibilityTests.TABLES)
        self.pack = self.game.pack(CompatibilityTests.TRACKED)

    def run_prepare(self, addons=None):
        with patch('ingame_installer.verify_game', side_effect=lambda game, package: package) as verify, \
                patch('build_ingame.build_ingame', return_value=(b'rebuilt', {})) as rebuild:
            result = prepare_payload(self.game.path, addons, self.pack)
        return result, rebuild, verify

    def test_matching_bundle_is_used_without_a_rebuild(self):
        result, rebuild, _ = self.run_prepare()
        self.assertEqual(result, self.pack)
        rebuild.assert_not_called()

    def test_stale_bundle_is_rebuilt_from_the_installed_game(self):
        self.game.write(dict(CompatibilityTests.TABLES, main_units_tables=table(8, b'a')))
        result, rebuild, verify = self.run_prepare()
        self.assertEqual(result, b'rebuilt')
        self.assertEqual(rebuild.call_args.args[0], self.game.path)
        verify.assert_called_once_with(self.game.path, b'rebuilt')

    def test_addons_always_rebuild(self):
        self.assertEqual(self.run_prepare({'cap': True})[0], b'rebuilt')


class TableMigrationTests(unittest.TestCase):
    HANDICAP = 'campaign_difficulty_handicap_effects_tables'

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        original = database.SCHEMA[self.HANDICAP]
        self.base = original['0']
        fields = [dict(f) for f in self.base] + [{'name': 'added_later', 'type': 'I32', 'default': '7'}]
        retyped = [dict(f, type='StringU8', default='') if f['name'] == 'effect_value' else dict(f) for f in self.base]
        schema = dict(original, **{'1': fields, '2': retyped})
        patcher = patch.dict(database.SCHEMA, {self.HANDICAP: schema})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.temp = temp.name

    def game_with(self, version):
        return FakeGame(self.temp, {self.HANDICAP: table(version)}).path

    def test_additive_change_detects_only_new_columns(self):
        self.assertTrue(additive_change(self.HANDICAP, 0, 1))
        self.assertFalse(additive_change(self.HANDICAP, 0, 2))

    def test_new_columns_are_filled_and_reported(self):
        payload, report = build(self.game_with(1), Options(attributes={'ward': 45}), local_dir=REFERENCE)
        path = Path(self.temp) / 'out.pack'
        path.write_bytes(payload)
        name = f'db\\{self.HANDICAP}\\zz_wh3_personal_trainer'
        version, rows = decode(self.HANDICAP, read_pack(path)[name])
        self.assertEqual(version, 1)
        self.assertTrue(all(r['added_later'] == 7 for r in rows))
        self.assertIn(f'{self.HANDICAP}: 0 → 1', report['migrations'])
        self.assertTrue(any('自动补齐新增字段' in w for w in report['warnings']))

    def test_retyped_columns_still_need_a_person(self):
        with self.assertRaisesRegex(ValueError, '需要人工迁移'):
            build(self.game_with(2), Options(attributes={'ward': 45}), local_dir=REFERENCE)


class GameDataTests(unittest.TestCase):
    def test_unknown_table_version_says_what_to_do(self):
        with self.assertRaisesRegex(ValueError, 'extract_schema'):
            decode('effects_tables', table(9999))

    def test_generated_tables_follow_the_installed_game(self):
        self.assertIsInstance(table_version(GAME, 'effect_bundles_tables'), int)
        with self.assertRaisesRegex(ValueError, '无法确定'):
            table_version(GAME, 'no_such_tables')

    def test_real_build_records_what_it_depends_on(self):
        from build_ingame import build_ingame
        from income_data import SOURCE_TABLES as income
        from strategic_data import SOURCE_TABLES as strategic
        from skill_data import SOURCE_TABLES as skills
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / PACK_NAME
            path.write_bytes(build_ingame()[0])
            status = check(GAME, path)
            script = read_pack(path, lambda n: n.endswith('wh3pt_panel.lua'))
        self.assertEqual(status.state, 'current')
        self.assertEqual(status.manifest['trainer_version'], trainer_core.VERSION)
        self.assertEqual(manifest_addons(status.manifest), {'enabled': [], 'values': {}})
        self.assertTrue((income | strategic | skills) <= set(status.manifest['tables']))
        head = next(iter(script.values())).decode('utf-8').split('\n', 1)[0]
        self.assertIn('["trainer"]="' + trainer_core.VERSION + '"', head)

    def test_every_effect_named_in_lua_exists_in_the_game(self):
        source = ''.join(path.read_text(encoding='utf-8') for path in (SRC / 'ingame').glob('*.lua'))
        names = set(re.findall(r"'(wh[0-9a-z_]*_effect[a-z0-9_]*)'", source))
        names |= {'wh3_dlc29_nag_mortarch_ritual_unlock_' + n for n in re.search(
            r"names = \{([^}]*)\}", source)[1].replace("'", '').split(',')}
        rows = []
        for name, data in read_pack(Path(GAME) / 'data/db.pack', lambda n: n.startswith('db\\effects_tables\\')).items():
            rows += decode('effects_tables', data)[1]
        missing = names - {r['effect'] for r in rows}
        self.assertGreaterEqual(len(names), 15)
        self.assertEqual(missing, set())


if __name__ == '__main__':
    unittest.main()
