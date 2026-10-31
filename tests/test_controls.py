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

    def test_switch_button_slider_and_field(self):
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
            seen = []
            slider = ui.Slider(root, 0, 100, seen.append)
            slider.set_value(250); self.assertEqual(slider.value, 100)
            slider.set_value('abc'); self.assertEqual(slider.value, 100)
            value = tk.StringVar(root, '10')
            field = ui.NumberField(root, value, unit='%', step=5, minimum=0, maximum=20)
            field._nudge(1); self.assertEqual(value.get(), '15')
            field._nudge(1); self.assertEqual(value.get(), '20')
            field.set_invalid(True)
            self.assertEqual(field.cget('highlightbackground'), ui.THEME['red'])
        finally:
            root.destroy()


class ControlTests(unittest.TestCase):
    def test_feature_row_bidirectional_values_and_invalid_text(self):
        import app
        root = tk.Tk(); root.withdraw()
        try:
            ui.setup(root)
            enabled, value, changes = tk.BooleanVar(root, False), tk.StringVar(root, '33'), []
            row = app.FeatureRow(root, '特保', '', enabled, lambda value_only=False: changes.append(value_only),
                                 value=value, maximum=100, unit='效果值')
            value.set('47.5')
            self.assertEqual(row.slider.value, 47.5)
            row._slider_user(72.4)
            self.assertEqual(value.get(), '72')
            self.assertTrue(enabled.get())
            value.set('')
            self.assertEqual(row.slider.value, 72)
            value.set('101')
            self.assertEqual(value.get(), '101')
            self.assertIn('请输入', row.desc_lbl.full_text)
            self.assertTrue(row.field.invalid)
            value.set('66')
            self.assertFalse(row.field.invalid)
            self.assertEqual(row.slider.value, 66)
            self.assertTrue(row.matches('特保')); self.assertFalse(row.matches('护甲'))
            row.destroy()
        finally:
            root.destroy()

    def test_migration_rescan_persistence(self):
        import app
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / 'settings.json'
            state.write_text(json.dumps({'game': app.GAME, 'local': str(app.REFERENCE), 'options': {'local_mods': ['@玩家部队66特保.pack', '@玩家战役部队维护费全免.pack', '@玩家战役1回合建筑.pack']}}), encoding='utf-8')
            with patch.object(app, 'STATE', state):
                window = app.App(); window.withdraw()
                try:
                    self.assertEqual(window.collect_options().attributes['ward'], 66)
                    self.assertEqual(window.collect_options().attributes['upkeep'], 100)
                    self.assertNotIn('@玩家部队66特保.pack', window.local_options)
                    self.assertTrue(window.local_options['@玩家战役1回合建筑.pack'].get())
                    window.attribute_options['ward'][1].set('47.5')
                    window.scan()
                    self.assertEqual(window.collect_options().attributes['ward'], 47.5)
                    window.save()
                    record = json.loads(state.read_text(encoding='utf-8'))
                    self.assertEqual(record['options']['attributes']['ward'], 47.5)
                    self.assertNotIn('@玩家部队66特保.pack', record['options']['local_mods'])
                    window.ingame_addons['building'].set(True)
                    window.ingame_addons['agent_ai'].set(True)
                    self.assertIn('2', window.addon_badge.text_value)
                    with patch.object(app, 'HOME', Path(temp)):
                        payload = window.generate_ingame()
                        self.assertIsNotNone(payload)
                        self.assertTrue((Path(temp) / 'output/ingame' / app.PACK_NAME).is_file())
                    # Navigation, counts, empty states, global search and the active list.
                    total = window.update_count()
                    self.assertGreaterEqual(total, 3)
                    self.assertEqual(window.nav['active'].badge.text_value, str(total))
                    window.show_group('units')
                    self.assertEqual(window.current_page, 'catalog')
                    self.assertTrue(window.nav['units'].selected)
                    self.assertGreater(window.filter(), 10)
                    window.show_group('favs')
                    self.assertIsNotNone(window._empty_state)
                    fav_row = next(r for r in window.all_rows() if r.item_id == 'attr_ward')
                    fav_row._toggle_fav()
                    self.assertEqual(window.filter(), 1)
                    self.assertIsNone(window._empty_state)
                    window.show_group('campaign')
                    window.search.set('不存在的功能关键词')
                    self.assertIsNotNone(window._empty_state)
                    window.search.set('伤害')
                    self.assertEqual(window.filter(), 2)  # global search across categories
                    window.search.set('')
                    window.show_page('active')
                    self.assertTrue(window.build_bar.winfo_manager())
                    self.assertEqual(len(window._collect_active_items()), total)
                    window.show_page('settings')
                    self.assertFalse(window.build_bar.winfo_manager())
                    window.clear()
                    self.assertEqual(window.update_count(), 0)
                finally:
                    window.destroy()

    def test_installer_window_builds_and_toggles(self):
        import ingame_installer
        win = ingame_installer.Installer(); win.withdraw()
        try:
            self.assertFalse(win._addons_open)
            win.toggle_addons(); self.assertTrue(win._addons_open)
            win.addons['shop'].set(True)
            self.assertIn('1', win.addon_summary.text_value)
            win._draw_badge('ok', '已安装游戏内面板', '')
            self.assertEqual(win.primary_cta_btn.text_value, '启动游戏')
            win._draw_badge('error', '游戏目录无效', 'x')
            self.assertEqual(win.primary_cta_btn.text_value, '一键安装')
            self.assertTrue(win._path_open)
        finally:
            win.destroy()


if __name__ == '__main__':
    unittest.main(verbosity=2)
