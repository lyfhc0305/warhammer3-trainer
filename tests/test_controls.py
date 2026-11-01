"""In-process widget tests, no game launch, installation, or desktop input."""
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
import ui_kit as ui


class KitTests(unittest.TestCase):
    def test_png_shapes_render_and_cache(self):
        root = tk.Tk(); root.withdraw()
        try:
            ui.setup(root)
            a = ui.rounded_box(root, 40, 20, 6, fill=ui.THEME['gold'], border=ui.THEME['frame'])
            b = ui.rounded_box(root, 40, 20, 6, fill=ui.THEME['gold'], border=ui.THEME['frame'])
            self.assertIs(a, b)
            self.assertEqual((a.width(), a.height()), (40, 20))
            self.assertEqual(ui.switch_image(root, True).width(), ui.px(38))
        finally:
            root.destroy()

    def test_switch_button_and_field(self):
        root = tk.Tk(); root.withdraw()
        try:
            ui.setup(root)
            var, clicks = tk.BooleanVar(root, False), []
            sw = ui.Switch(root, var, command=lambda: clicks.append(1))
            sw.toggle()
            self.assertTrue(var.get()); self.assertEqual(clicks, [1])
            hits = []
            btn = ui.Button(root, '安装', lambda: hits.append(1), variant='primary')
            btn.invoke(); btn.set_enabled(False); btn.invoke()
            self.assertEqual(hits, [1])
            btn.set_text('启动游戏')
            self.assertEqual(btn.cget('text'), '启动游戏')
            value = tk.StringVar(root, '10')
            field = ui.NumberField(root, value, unit='%', step=5, minimum=0, maximum=20)
            field._nudge(1); self.assertEqual(value.get(), '15')
            field._nudge(1); self.assertEqual(value.get(), '20')
            field.set_invalid(True)
            self.assertEqual(field.cget('highlightbackground'), ui.THEME['red'])
        finally:
            root.destroy()


class InstallerTests(unittest.TestCase):
    """The installer window against a fake install state; nothing touches the game."""

    def window(self, status, settings=None):
        import ingame_installer as inst
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'settings.json'
        if settings is not None:
            path.write_text(json.dumps(settings), encoding='utf-8')
        for patcher in (patch.object(inst, 'SETTINGS', path), patch.object(inst, 'detect_installed_mode', return_value=status)):
            patcher.start()
            self.addCleanup(patcher.stop)
        win = inst.Installer(); win.withdraw()
        self.addCleanup(win.destroy)
        return inst, win, path

    def test_switches_follow_the_installed_pack_and_the_button_tracks_changes(self):
        import ingame_installer as inst
        installed = inst.InstallStatus('ok', '已安装 · 含 1 个附加模块', '', {'enabled': ['cap'], 'values': {'cap_value': 50.0}})
        inst, win, settings = self.window(installed, settings={'game': inst.GAME, 'addons': {'shop': True, 'cap': False}})
        # The pack in the game directory wins over the remembered selection.
        self.assertTrue(win.addons['cap'].get()); self.assertFalse(win.addons['shop'].get())
        self.assertEqual(win.values['cap'].get(), '50')
        self.assertEqual(win.addon_summary.text_value, '已选 1 / 5')
        self.assertEqual(win.primary_cta_btn.text_value, '启动游戏')
        self.assertEqual(win.reinstall_cta_btn.text_value, '重新安装 / 更新')
        self.assertFalse(win._addons_open)
        win.toggle_addons(); self.assertTrue(win._addons_open)
        win.addons['shop'].set(True)
        self.assertEqual(win.primary_cta_btn.text_value, '重新安装以应用更改')
        self.assertEqual(win.reinstall_cta_btn.text_value, '启动游戏')
        self.assertEqual(win.addon_summary.text_value, '已选 2 / 5 · 未应用')
        win.addons['shop'].set(False)
        self.assertEqual(win.primary_cta_btn.text_value, '启动游戏')
        win.values['cap'].set('100')
        self.assertEqual(win.primary_cta_btn.text_value, '重新安装以应用更改')
        win.values['cap'].set('abc')
        self.assertTrue(win.addon_grid.fields['cap'].invalid)
        with patch.object(inst.messagebox, 'showerror') as error, patch.object(inst, 'install') as install:
            win.deploy()
            error.assert_called_once(); install.assert_not_called()
        self.assertIn('0～100000000', error.call_args.args[1])
        win.values['cap'].set('100')
        self.assertFalse(win.addon_grid.fields['cap'].invalid)
        with patch.object(inst, 'prepare_payload', return_value=b'payload') as prepare, \
                patch.object(inst, 'install', return_value=Path(settings.parent) / 'x.pack'):
            win.deploy()
        self.assertEqual(prepare.call_args.args[1], {'cap': True, 'cap_value': 100.0})
        saved = json.loads(settings.read_text(encoding='utf-8'))
        self.assertEqual(set(saved), {'game', 'addons', 'addon_values'})
        self.assertEqual(saved['addons'], {'building': False, 'cap': True, 'agent_ai': False, 'shop': False, 'ballistic': False})
        self.assertEqual(saved['addon_values'], {'cap_value': '100', 'agent_ai_value': '100'})
        self.assertEqual(win.logs[-1][:4], '已安装：')
        win._draw_badge(inst.InstallStatus('error', '游戏目录无效', 'x'))
        self.assertEqual(win.primary_cta_btn.text_value, '一键安装')
        self.assertTrue(win._path_open)

    def test_remembered_selection_applies_when_nothing_is_installed(self):
        import ingame_installer as inst
        desktop = {'game': inst.GAME, 'ingame_addons': {'building': True, 'agent_ai': True, 'bogus': True},
                   'options': {'cap_value': 60}, 'attribute_controls': {'agent_ai': {'enabled': True, 'value': '80'}}}
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(inst.load_settings(Path(temp) / 'missing.json'),
                             {'game': None, 'addons': {k: False for k in inst.ADDON_KEYS}, 'addon_values': {}})
            (Path(temp) / 'old.json').write_text(json.dumps(desktop), encoding='utf-8')
            self.assertEqual(inst.load_settings(Path(temp) / 'old.json')['addon_values'], {'cap_value': '60', 'agent_ai_value': '80'})
            (Path(temp) / 'bad.json').write_text('[1', encoding='utf-8')
            self.assertEqual(inst.load_settings(Path(temp) / 'bad.json')['game'], None)
        inst, win, settings = self.window(inst.InstallStatus('info', '尚未安装', ''), settings=desktop)
        self.assertTrue(win.addons['building'].get()); self.assertTrue(win.addons['agent_ai'].get())
        self.assertFalse(win.addons['cap'].get())
        self.assertEqual((win.values['cap'].get(), win.values['agent_ai'].get()), ('60', '80'))
        self.assertEqual(win.addon_summary.text_value, '已选 2 / 5')
        self.assertEqual(win.primary_cta_btn.text_value, '一键安装')
        self.assertEqual(win.selected_addons(), {'building': True, 'agent_ai': True, 'agent_ai_value': 80.0})
        with patch.object(win, 'destroy') as destroy:
            win.close()
            destroy.assert_called_once()
        self.assertEqual(json.loads(settings.read_text(encoding='utf-8'))['addons']['building'], True)

    def test_addon_number_fields_follow_their_switches(self):
        import ingame_installer as inst
        inst, win, _ = self.window(inst.InstallStatus('info', '尚未安装', ''))
        field = win.addon_grid.fields['agent_ai']
        self.assertEqual(field.entry.cget('state'), 'disabled')
        win.addons['agent_ai'].set(True)
        self.assertEqual(field.entry.cget('state'), 'normal')
        field._nudge(1)
        self.assertEqual(win.values['agent_ai'].get(), '100')  # capped at the attribute maximum
        field._nudge(-1)
        self.assertEqual(win.values['agent_ai'].get(), '95')
        with self.assertRaisesRegex(ValueError, '0～100'):
            inst.parse_value('agent_ai', '101')
        self.assertEqual(inst.parse_value('cap', ' 12.5 '), 12.5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
