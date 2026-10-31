"""Warhammer III trainer desktop console.

Two modes, one window:
- 游戏内实时面板 (recommended): install the native Lua drawer, toggle everything in-game.
- 静态配置包: pick difficulty-table overrides here, build and install a pack.
"""
import json
import os
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox
from dataclasses import asdict
from trainer_core import GAME, HOME, PACK_NAME, REFERENCE, Options, build, discover, atomic_write, install, uninstall
from pack_io import read_pack
from database import decode
from attributes import ATTRIBUTES, attribute_pack
import ui_kit as ui
from ui_kit import THEME, px, launch_game_or_dir
from ingame_installer import ADDON_SPECS, VERSION, verify_game, detect_installed_mode, smoke_test

STATE = HOME / 'settings.json'

# Display names only; selection and build provenance retain the source filenames.
RECOVERY_PRESETS = {
    '@10倍回血复活每3秒60回血5%弹药200魔风上限.pack': ('复合预设 I（回血 1% / 补弹 5%）', 1, 5),
    '@10倍回血复活每3秒90回血20%弹药500魔风上限.pack': ('复合预设 II（回血 1.5% / 补弹 20%）', 1.5, 20),
    '@10倍回血复活每3秒120回血50%弹药1000魔风上限.pack': ('复合预设 III（回血 2% / 补弹 50%）', 2, 50),
}


def local_mod_label(path):
    return RECOVERY_PRESETS[path.name][0] if path.name in RECOVERY_PRESETS else path.stem.lstrip('@')


LABELS = {
    'building': ('建筑快捷升级菜单', '复用 cpecific 脚本；是建造操作便利功能，不是瞬间建造。'),
    'ai': ('AI 将军 / 部队托管', '复用 Pancake 完整脚本与界面资源；战斗内使用原 Mod 控件。'),
    'regeneration': ('持续回血', '每队己方部队每秒仅恢复自身生命；可调整回复比例。'),
    'resurrection': ('允许回血复活模型', '需同时开启持续回血；是否能复活取决于单位与游戏规则。'),
    'ammunition': ('持续补充弹药', '每队己方部队每秒仅补充自身弹药；不向其它部队传播。'),
    'reload': ('装填属性加成', '保留旧能力的 stat_reloading 加成。'),
    'vigour': ('完美活力', '仅玩家战役势力；静态包不清除开局疲劳，内置面板可以。'),
    'unbreakable': ('不溃', '仅玩家战役势力。'),
    'vanguard': ('先锋部署', '仅玩家战役势力；仍受地图与单位规则限制。'),
    'attrition': ('免疫战役损耗', '仅玩家战役势力。'),
    'foreign_replenishment': ('异地补员加成', '沿用旧 Mod 的外国领土补员效果。'),
    'replenishment_cap': ('解除补员限制', '沿用旧 Mod 效果值 100。'),
    'cap': ('自定义战斗回血上限', '全局参数，也可能影响敌方；50 为原 no regen cap 的实际值，并非无限。'),
}

# key -> (value attribute, maximum, unit, minimum)
BASIC_NUMERIC = {
    'regeneration': ('heal', 100, '%/秒', 0.01),
    'ammunition': ('ammo_percent', 100, '%/秒', 0),
    'reload': ('reload_value', 500, '%', 0),
    'foreign_replenishment': ('foreign_value', 500, '%', 0),
    'cap': ('cap_value', 100000000, '倍', 0),
}

# Same structure as the in-game panel: features sit where they act.
BASIC_SECTIONS = [
    ('battle_basic', '恢复与特性', ['regeneration', 'resurrection', 'ammunition', 'reload', 'vigour', 'unbreakable', 'vanguard', 'cap']),
    ('army_basic', '行军与补员', ['attrition', 'foreign_replenishment', 'replenishment_cap']),
    ('mods_basic', '模组整合', ['building', 'ai']),
]
BASIC_GROUP = {key: section.split('_')[0] for section, _, keys in BASIC_SECTIONS for key in keys}

GROUPS = {
    'domestic': ('内政', '秩序、外交、维护费，以及发展、建筑、科技加速'),
    'army': ('军队', '移动距离、招募格子、补员、损耗与间谍'),
    'battle': ('战斗', '回血与特性、部队属性、复合预设'),
    'mods': ('Mod 整合', '建筑快捷菜单、AI 托管，以及神器和弹道效果包'),
    'favs': ('收藏', '点击每行末尾的 ☆ 即可收藏常用项'),
}
ATTRIBUTE_GROUP = {key: 'battle' if spec[3] == '部队属性' else
                   'domestic' if key in ('order', 'diplomacy', 'upkeep') else 'army'
                   for key, spec in ATTRIBUTES.items()}

SECTION_ORDER = [
    ('domestic_attr', 'domestic', '数值加成'),
    ('domestic_mod', 'domestic', '效果包'),
    ('army_attr', 'army', '数值加成'),
    ('army_basic', 'army', '行军与补员'),
    ('battle_basic', 'battle', '恢复与特性'),
    ('battle_attr', 'battle', '部队属性'),
    ('battle_mod', 'battle', '效果包与复合预设'),
    ('mods_basic', 'mods', '模组整合'),
    ('mods_mod', 'mods', '其他效果包'),
]

NAV = [
    ('游戏内面板', [('home', 'game', '安装与启动')]),
    ('静态配置包', [
        ('domestic', 'globe', '内政'),
        ('army', 'shield', '军队'),
        ('battle', 'flash', '战斗'),
        ('mods', 'package', 'Mod 整合'),
        ('favs', 'star', '收藏'),
        ('active', 'check', '已启用'),
    ]),
    ('其他', [('log', 'doc', '生成日志'), ('settings', 'settings', '设置')]),
]


def describe(path):
    entries = read_pack(path)
    notes = []
    for name, data in entries.items():
        if 'campaign_difficulty_handicap_effects_tables' in name:
            _, rows = decode('campaign_difficulty_handicap_effects_tables', data)
            human = sum(r['human'] for r in rows)
            effects = sorted({(r['effect'], r['effect_value'], r['human']) for r in rows})
            notes.append(f'{len(effects)} 类效果，{human} 条玩家难度记录' + ('，包含 AI 修改' if human != len(rows) else ''))
            if len(effects) == 1:
                notes.append('效果值：' + str(effects[0][1]))
    if '1回合' in path.name:
        notes.append('实际为高额加速，不保证所有项目固定一回合')
    if path.name in RECOVERY_PRESETS:
        _label, heal, ammo = RECOVERY_PRESETS[path.name]
        notes.append(f'每3秒回血参数为最大生命的 {heal:g}%，允许复活；补弹参数 {ammo:g}%；'
                     '按单位自身恢复，不叠加友军恢复光环；'
                     '全局回血池为最大生命的10倍，也影响敌方；含额外战斗属性')
    elif '每3秒' in path.name:
        notes.append('组合预设：回血/复活/弹药及多种属性；仅保留 SSS 自定义能力')
    if '神器' in path.name:
        notes.append('仅人类基斯里夫，支持现版军队／行省四神祈祷和旧版入口，启动时获对应神器；保留原生消耗、冷却与祈祷效果；旧无效魔风加成已移除')
    if '信鸽' in path.name:
        notes.append('向现版已有记录移植旧弹道数值，保留当前引用；旧独有键未合并；含加强与削弱，也影响敌方')
    return '；'.join(notes) or '保留原 Mod 功能，按当前基础表版本生成'


def first_clause(text):
    return (text or '').split('；')[0].split('。')[0].strip()


# ---------------------------------------------------------------------------
# Rows & sections
# ---------------------------------------------------------------------------

class FeatureRow(tk.Frame):
    """One list row: accent bar | switch | title + description | value controls | star."""

    def __init__(self, parent, title, desc, enabled, on_change, fav_var=None, on_fav=None,
                 value=None, minimum=0, maximum=100, unit='', item_id='', group=''):
        super().__init__(parent, bg=THEME['card'])
        self.title = title
        self.full_desc = desc or ''
        self.enabled = enabled
        self.on_change = on_change
        self.fav_var = fav_var
        self.on_fav = on_fav
        self.value = value
        self.minimum = minimum
        self.maximum = maximum
        self.unit = unit
        self.item_id = item_id
        self.group = group
        self.search_text = (title + ' ' + (desc or '') + ' ' + unit + ' ' + item_id).lower()
        self.slider = None
        self.field = None
        self.star = None
        self._value_trace = None
        self._fav_trace = None
        self._hovered = False

        self.sep = tk.Frame(self, bg=THEME['divider'], height=1)
        self.sep.pack(fill='x')
        self.body = body = tk.Frame(self, bg=THEME['card'])
        body.pack(fill='x')
        self.accent = tk.Frame(body, bg=THEME['card'], width=px(3))
        self.accent.grid(row=0, column=0, sticky='ns')
        self.switch = ui.Switch(body, enabled, command=self._toggled)
        self.switch.grid(row=0, column=1, padx=(px(13), px(14)), pady=px(13))

        text = tk.Frame(body, bg=THEME['card'])
        text.grid(row=0, column=2, sticky='ew', pady=px(8))
        self.title_lbl = tk.Label(text, text=title, font=ui.font(self, 'body_b'), bg=THEME['card'],
                                  fg=THEME['ink'], anchor='w', cursor='hand2')
        self.title_lbl.pack(fill='x')
        self.title_lbl.bind('<Button-1>', lambda _e: self.switch.toggle())
        self.default_desc = self._default_desc()
        self.desc_lbl = ui.EllipsisLabel(text, text=self.default_desc, font=ui.font(self, 'sm'), fg=THEME['quiet'])
        self.desc_lbl.pack(fill='x', pady=(px(1), 0))
        tip = self.full_desc
        if value is not None:
            tip = (tip + '\n' if tip else '') + f'范围 {ui.fmt_number(minimum)}～{ui.fmt_number(maximum)} {unit}'.strip()
        if tip:
            ui.Tooltip(self.desc_lbl, tip)
        body.columnconfigure(2, weight=1)

        if value is not None:
            controls = tk.Frame(body, bg=THEME['card'])
            controls.grid(row=0, column=3, padx=(px(16), 0))
            if maximum <= 100000:
                self.slider = ui.Slider(controls, minimum, maximum, self._slider_user, width=170)
                self.slider.pack(side='left', padx=(0, px(12)))
            step = 1 if maximum <= 20 else 5 if maximum <= 100 else 10
            self.field = ui.NumberField(controls, value, unit=unit, width=9 if maximum > 100000 else 6,
                                        on_user_edit=self._field_user, step=step,
                                        minimum=minimum, maximum=maximum)
            self.field._keep_bg = True
            self.field.pack(side='left')
            self._value_trace = value.trace_add('write', lambda *_: self._value_changed())
        if fav_var is not None:
            on = bool(fav_var.get())
            self.star = ui.IconButton(body, 'star_on' if on else 'star', command=self._toggle_fav,
                                      fg=THEME['gold'] if on else THEME['faint'],
                                      hover_fg=THEME['gold_ink'], tooltip='收藏')
            self.star.grid(row=0, column=4, padx=(px(10), px(12)))
            self._fav_trace = fav_var.trace_add('write', lambda *_: self._sync_fav())
        else:
            tk.Frame(body, bg=THEME['card'], width=px(12)).grid(row=0, column=4)

        self._enabled_trace = enabled.trace_add('write', lambda *_: self._sync_state())
        for w in (self, body):
            w.bind('<Enter>', self._hover_on, add='+')
            w.bind('<Leave>', self._hover_off, add='+')
        self._value_changed(notify=False)
        self._sync_state()

    def _default_desc(self):
        short = first_clause(self.full_desc)
        if self.value is not None:
            rng = f'{ui.fmt_number(self.minimum)}～{ui.fmt_number(self.maximum)} {self.unit}'.strip()
            return f'{short} · 范围 {rng}' if short else '范围 ' + rng
        return short

    # --- state -----------------------------------------------------------
    def number(self):
        try:
            n = float(self.value.get())
        except (ValueError, tk.TclError):
            return None
        if n != n or not self.minimum <= n <= self.maximum:
            return None
        return n

    def _value_changed(self, notify=True):
        if self.value is None:
            return
        n = self.number()
        if self.field is not None:
            self.field.set_invalid(n is None)
        if n is None:
            self.desc_lbl.set_text(f'请输入 {ui.fmt_number(self.minimum)}～{ui.fmt_number(self.maximum)} 的数值',
                                   fg=THEME['red'])
        else:
            self.desc_lbl.set_text(self.default_desc, fg=THEME['quiet'])
            if self.slider is not None:
                self.slider.set_value(n)
        if notify and self.enabled.get() and self.on_change:
            self.on_change(value_only=True)

    def _sync_state(self):
        on = bool(self.enabled.get())
        self.accent._keep_bg = on
        self.accent.configure(bg=THEME['gold'] if on else self.body.cget('bg'))
        self.title_lbl.configure(fg=THEME['gold_ink'] if on else THEME['ink'])
        if self.slider is not None:
            self.slider.set_active(on)

    def _sync_fav(self):
        if self.star is not None:
            on = bool(self.fav_var.get())
            self.star.set_icon('star_on' if on else 'star', fg=THEME['gold'] if on else THEME['faint'])

    def _toggled(self):
        if self.on_change:
            self.on_change()

    def _enable(self):
        if not self.enabled.get():
            self.enabled.set(True)
            if self.on_change:
                self.on_change()

    def _slider_user(self, raw):
        n = max(self.minimum, min(self.maximum, round(raw)))
        self.value.set(ui.fmt_number(n))
        self._enable()

    def _field_user(self):
        self._enable()

    def _toggle_fav(self):
        self.fav_var.set(not self.fav_var.get())
        if self.on_fav:
            self.on_fav()

    def _hover_on(self, _e=None):
        if not self._hovered:
            self._hovered = True
            ui.set_bg(self.body, THEME['raise'])

    def _hover_off(self, event=None):
        if event is not None:
            w = self.winfo_containing(event.x_root, event.y_root)
            if w is not None and ui.is_descendant(w, self):
                return
        if self._hovered:
            self._hovered = False
            ui.set_bg(self.body, THEME['card'])

    def set_first(self, first):
        if first:
            self.sep.pack_forget()
        elif not self.sep.winfo_manager():
            self.sep.pack(fill='x', before=self.body)

    def matches(self, query):
        return not query or query in self.search_text

    def destroy(self):
        for var, trace in ((self.value, self._value_trace), (self.enabled, getattr(self, '_enabled_trace', None)),
                           (self.fav_var, self._fav_trace)):
            if var is not None and trace is not None:
                try:
                    var.trace_remove('write', trace)
                except Exception:
                    pass
        super().destroy()


class Section(tk.Frame):
    """Section title + bordered list card holding FeatureRows."""

    def __init__(self, parent, title):
        super().__init__(parent, bg=THEME['bg'])
        self.base_title = title
        self.title_lbl = tk.Label(self, text=title, font=ui.font(self, 'sm_b'), bg=THEME['bg'], fg=THEME['quiet'])
        self.title_lbl.pack(anchor='w', pady=(px(18), px(8)))
        self.list = tk.Frame(self, bg=THEME['card'], highlightthickness=1, highlightbackground=THEME['border'])
        self.list.pack(fill='x')
        self.rows = []

    def show_rows(self, rows, title=None):
        for row in self.rows:
            row.pack_forget()
        for idx, row in enumerate(rows):
            row.set_first(idx == 0)
            row.pack(fill='x')
        self.title_lbl.configure(text=(title or self.base_title) + f'  ·  {len(rows)}')


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------

class NavItem(tk.Frame):
    def __init__(self, parent, key, icon, text, command):
        super().__init__(parent, bg=THEME['side'], cursor='hand2')
        self.key = key
        self.command = command
        self.selected = False
        self.bar = tk.Frame(self, bg=THEME['side'], width=px(3))
        self.bar.pack(side='left', fill='y')
        glyph, f = ui.icon_text(self, icon)
        self.icon = tk.Label(self, text=glyph, font=f, bg=THEME['side'], fg=THEME['quiet'], width=2)
        self.icon.pack(side='left', padx=(px(12), px(6)), pady=px(8))
        self.text = tk.Label(self, text=text, font=ui.font(self, 'body'), bg=THEME['side'], fg=THEME['muted'], anchor='w')
        self.text.pack(side='left', fill='x', expand=True)
        self.badge = ui.Badge(self, '', 'neutral', style='xs')
        self.badge.pack(side='right', padx=(0, px(12)))
        for w in (self, self.icon, self.text, self.badge, self.bar):
            w.bind('<ButtonRelease-1>', lambda _e: self.command(self.key))
            w.bind('<Enter>', lambda _e: self._paint(hover=True))
            w.bind('<Leave>', lambda _e: self._paint(hover=False))

    def _paint(self, hover=False):
        if self.selected:
            bg, fg, ic, bar = THEME['raise'], THEME['ink'], THEME['gold'], THEME['gold']
        elif hover:
            bg, fg, ic, bar = '#1a1511', THEME['ink'], THEME['muted'], '#1a1511'
        else:
            bg, fg, ic, bar = THEME['side'], THEME['muted'], THEME['quiet'], THEME['side']
        for w in (self, self.icon, self.text, self.badge):
            w.configure(bg=bg)
        self.text.configure(fg=fg, font=ui.font(self, 'body_b' if self.selected else 'body'))
        self.icon.configure(fg=ic)
        self.bar.configure(bg=bar)

    def set_selected(self, selected):
        self.selected = selected
        self._paint()

    def set_count(self, count, gold=False):
        self.badge.set(str(count) if count else '', 'gold' if gold else 'neutral')


def text_field(parent, variable):
    frame = tk.Frame(parent, bg=THEME['input_bg'], highlightthickness=1,
                     highlightbackground=THEME['border'], highlightcolor=THEME['sel_line'])
    entry = tk.Entry(frame, textvariable=variable, bd=0, relief='flat', bg=THEME['input_bg'], fg=THEME['ink'],
                     insertbackground=THEME['gold'], font=ui.font(parent, 'body'), highlightthickness=0)
    entry.pack(fill='x', padx=px(10), pady=px(8))
    frame.entry = entry
    return frame


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        ui.setup(self)
        self.title('全面战争：战锤 III 修改器')
        self.minsize(px(980), px(640))
        ui.center_window(self, px(1200), px(800))

        self.game = tk.StringVar(value=GAME)
        self.local = tk.StringVar(value=str(REFERENCE))
        self.search = tk.StringVar()
        self.options = {k: tk.BooleanVar(value=False) for k in LABELS}
        self.heal = tk.StringVar(value='12.5')
        self.cap_value = tk.StringVar(value='50')
        self.ammo_percent = tk.StringVar(value='50')
        self.reload_value = tk.StringVar(value='20')
        self.foreign_value = tk.StringVar(value='100')
        self.attribute_options = {
            key: (tk.BooleanVar(value=False), tk.StringVar(value=str(spec[5])))
            for key, spec in ATTRIBUTES.items()
        }
        self.ingame_addons = {k: tk.BooleanVar(value=False) for k, _, _ in ADDON_SPECS}
        self.favourites = {}
        self.saved_favourites = set()
        self.explicit_attributes = set()
        self.local_options = {}
        self.saved_local = []
        self.status = tk.StringVar(value='准备就绪')
        self.current_page = None
        self.current_group = 'domestic'
        self._empty_state = None
        self._install_kind = 'info'
        self._scanned_dirs = None

        self._build_shell()
        self._build_home()
        self._build_catalog()
        self._build_active()
        self._build_log()
        self._build_settings()

        self.load()
        self._build_basic_rows()
        for var in self.ingame_addons.values():
            var.trace_add('write', lambda *_: self._refresh_addon_badge())
        self.search.trace_add('write', lambda *_: self.filter())
        self.bind_all('<Control-f>', lambda _e: self._focus_search())
        self.scan()
        self.show_page('home')
        self.protocol('WM_DELETE_WINDOW', self.close)

    # ------------------------------------------------------------------ shell
    def _build_shell(self):
        side = tk.Frame(self, bg=THEME['side'], width=px(232))
        side.pack(side='left', fill='y')
        side.pack_propagate(False)
        tk.Frame(self, bg=THEME['border'], width=1).pack(side='left', fill='y')

        brand = tk.Frame(side, bg=THEME['side'], padx=px(18), pady=px(20))
        brand.pack(fill='x')
        tk.Label(brand, image=ui.disc(brand, px(38), THEME['gold_dim'], ring=THEME['sel_line'], ring_w=1.5),
                 text='◆', compound='center', fg=THEME['gold_ink'], bg=THEME['side'],
                 font=ui.font(brand, 'em'), bd=0).pack(side='left')
        names = tk.Frame(brand, bg=THEME['side'])
        names.pack(side='left', padx=(px(12), 0))
        tk.Label(names, text='WARHAMMER III', font=ui.font(brand, 'xs'), bg=THEME['side'], fg=THEME['gold']).pack(anchor='w')
        tk.Label(names, text='统帅修改器', font=ui.font(brand, 'h2'), bg=THEME['side'], fg=THEME['ink']).pack(anchor='w')

        self.nav = {}
        for group_title, items in NAV:
            tk.Label(side, text=group_title, font=ui.font(side, 'xs'), bg=THEME['side'], fg=THEME['faint'],
                     anchor='w').pack(fill='x', padx=px(20), pady=(px(14), px(4)))
            for key, icon, text in items:
                item = NavItem(side, key, icon, text, self._nav_click)
                item.pack(fill='x')
                self.nav[key] = item

        foot = tk.Frame(side, bg=THEME['side'], padx=px(18), pady=px(16))
        foot.pack(side='bottom', fill='x')
        self.side_state = tk.Label(foot, text='● 检测中…', font=ui.font(side, 'sm'), bg=THEME['side'],
                                   fg=THEME['quiet'], anchor='w')
        self.side_state.pack(fill='x')
        tk.Label(foot, text='v' + VERSION + ' · 仅限单人战役', font=ui.font(side, 'xs'), bg=THEME['side'],
                 fg=THEME['faint'], anchor='w').pack(fill='x', pady=(px(2), 0))

        main = tk.Frame(self, bg=THEME['bg'])
        main.pack(side='left', fill='both', expand=True)

        statusbar = tk.Frame(main, bg=THEME['side'], height=px(30))
        statusbar.pack(side='bottom', fill='x')
        statusbar.pack_propagate(False)
        self.status_dot = tk.Label(statusbar, text='●', font=ui.font(main, 'xs'), bg=THEME['side'], fg=THEME['green'])
        self.status_dot.pack(side='left', padx=(px(16), px(6)))
        self.status_right = tk.Label(statusbar, text='', font=ui.font(main, 'sm'), bg=THEME['side'], fg=THEME['quiet'])
        self.status_right.pack(side='right', padx=px(16))
        self.status_lbl = ui.EllipsisLabel(statusbar, text=self.status.get(), font=ui.font(main, 'sm'),
                                           fg=THEME['muted'], bg=THEME['side'])
        self.status_lbl.pack(side='left', fill='x', expand=True)
        self.status.trace_add('write', lambda *_: self.status_lbl.set_text(self.status.get()))
        tk.Frame(main, bg=THEME['border'], height=1).pack(side='bottom', fill='x')

        # Static pack action bar: only shown on static pages.
        self.build_bar = tk.Frame(main, bg=THEME['card'], padx=px(28), pady=px(12))
        self.build_bar_line = tk.Frame(main, bg=THEME['border'], height=1)
        ui.Button(self.build_bar, '生成并安装', self.deploy, variant='primary').pack(side='right')
        ui.Button(self.build_bar, '仅生成', self.generate, variant='secondary').pack(side='right', padx=(0, px(8)))
        ui.Button(self.build_bar, '全部关闭', self.clear, variant='ghost').pack(side='right', padx=(0, px(8)))
        left = tk.Frame(self.build_bar, bg=THEME['card'])
        left.pack(side='left', fill='x', expand=True)
        self.build_count = ui.Badge(left, '未选择', 'neutral')
        self.build_count.pack(side='left')
        self.build_note = ui.EllipsisLabel(left, text='静态包写入难度表，重启游戏生效；安装会替换已安装的游戏内面板',
                                           font=ui.font(main, 'sm'), fg=THEME['quiet'], bg=THEME['card'])
        self.build_note.pack(side='left', fill='x', expand=True, padx=px(12))

        self.stack = tk.Frame(main, bg=THEME['bg'])
        self.stack.pack(fill='both', expand=True)
        self.stack.rowconfigure(0, weight=1)
        self.stack.columnconfigure(0, weight=1)
        self.pages = {}

    def _page(self, key):
        frame = tk.Frame(self.stack, bg=THEME['bg'])
        frame.grid(row=0, column=0, sticky='nsew')
        self.pages[key] = frame
        return frame

    def _header(self, parent, title, subtitle):
        head = tk.Frame(parent, bg=THEME['bg'], padx=px(28), pady=px(20))
        head.pack(fill='x')
        right = tk.Frame(head, bg=THEME['bg'])
        right.pack(side='right')
        text = tk.Frame(head, bg=THEME['bg'])
        text.pack(side='left', fill='x', expand=True)
        t = tk.Label(text, text=title, font=ui.font(parent, 'h1'), bg=THEME['bg'], fg=THEME['ink'], anchor='w')
        t.pack(anchor='w')
        s = ui.EllipsisLabel(text, text=subtitle, font=ui.font(parent, 'sm'), fg=THEME['quiet'], bg=THEME['bg'])
        s.pack(fill='x', pady=(px(3), 0))
        return t, s, right

    # ------------------------------------------------------------------- home
    def _build_home(self):
        page = self._page('home')
        self._header(page, '游戏内实时面板', '推荐方式：进入战役后随时开关加成、执行指令，无需重启游戏')
        area = ui.ScrollArea(page, padx=px(28))
        area.pack(fill='both', expand=True)
        body = area.inner

        self.hero = ui.StatusHero(body)
        self.hero.pack(fill='x', pady=(0, px(4)))
        self.hero_primary = ui.Button(self.hero.actions, '一键安装', self.deploy_ingame, variant='primary',
                                      size='lg', min_width=150)
        self.hero_primary.pack(side='left')
        self.hero_secondary = ui.Button(self.hero.actions, '重新安装 / 更新', self.deploy_ingame,
                                        variant='secondary', size='lg')
        self.hero_secondary.pack(side='left', padx=(px(10), 0))
        ui.Button(self.hero.actions, '卸载', self.remove, variant='ghost', size='lg').pack(side='left', padx=(px(6), 0))

        ui.section_title(body, '使用步骤').pack(fill='x', pady=(px(22), px(8)))
        ui.steps_row(body).pack(fill='x')

        def addon_badge(row):
            self.addon_badge = ui.Badge(row, '0 / 6', 'neutral', style='xs')
            self.addon_badge.pack(side='left', padx=(px(8), 0))
        ui.section_title(body, '附加模块（可选）', right=addon_badge).pack(fill='x', pady=(px(22), px(8)))
        box = ui.card(body, padx=18, pady=10)
        box.pack(fill='x')
        ui.AddonGrid(box, ADDON_SPECS, self.ingame_addons).pack(fill='x')
        tk.Frame(box, bg=THEME['divider'], height=1).pack(fill='x', pady=(px(8), px(10)))
        foot = tk.Frame(box, bg=THEME['card'])
        foot.pack(fill='x')
        ui.Button(foot, '仅构建到 output', self.generate_ingame, variant='ghost', size='sm').pack(side='right')
        ui.EllipsisLabel(foot, text='默认安装纯净面板；开启的模块会随面板一起打包，部分模块也会影响敌方。',
                         font=ui.font(foot, 'sm'), fg=THEME['quiet'], bg=THEME['card']).pack(side='left', fill='x', expand=True)

        ui.section_title(body, '游戏目录').pack(fill='x', pady=(px(22), px(8)))
        dir_card = ui.card(body, padx=18, pady=12)
        dir_card.pack(fill='x', pady=(0, px(28)))
        ui.Button(dir_card, '更改', lambda: self.show_page('settings'), variant='secondary', size='sm').pack(side='right')
        self.home_dir_lbl = ui.EllipsisLabel(dir_card, text=self.game.get(), font=ui.font(body, 'body'),
                                             fg=THEME['muted'], bg=THEME['card'])
        self.home_dir_lbl.pack(side='left', fill='x', expand=True)
        self.game.trace_add('write', lambda *_: self.home_dir_lbl.set_text(self.game.get()))

    # ---------------------------------------------------------------- catalog
    def _build_catalog(self):
        page = self._page('catalog')
        self.cat_title, self.cat_sub, right = self._header(page, '战役加成', '')
        self.search_box = ui.SearchBox(right, self.search, placeholder='搜索全部功能  Ctrl+F', width=22)
        self.search_box.pack(side='left')
        self.preset_btn = ui.Button(right, '预设 ▾', self.show_preset_menu, variant='secondary')
        self.preset_btn.pack(side='left', padx=(px(10), 0))
        self.preset_menu = tk.Menu(self, tearoff=0, bg=THEME['card'], fg=THEME['ink'], bd=0,
                                   activebackground=THEME['gold_dim'], activeforeground=THEME['gold_ink'],
                                   font=ui.font(self, 'body'))
        self.preset_menu.add_command(label='  战役发育预设  ·  秩序 / 移动 / 外交 / 招募 / 维护费', command=self.apply_campaign_preset)
        self.preset_menu.add_command(label='  精锐战斗预设  ·  特保 / 远抗 / 攻防 / 伤害 / 不溃', command=self.apply_battle_preset)
        self.preset_menu.add_separator()
        self.preset_menu.add_command(label='  全部关闭', command=self.clear)

        self.catalog_area = ui.ScrollArea(page, padx=px(28))
        self.catalog_area.pack(fill='both', expand=True)
        self.catalog_body = self.catalog_area.inner
        self.sections = {}
        for key, _group, title in SECTION_ORDER:
            self.sections[key] = Section(self.catalog_body, title)
        self.section_group = {key: group for key, group, _ in SECTION_ORDER}
        self.catalog_pad = tk.Frame(self.catalog_body, bg=THEME['bg'], height=px(24))

    def _build_basic_rows(self):
        for section_key, _title, keys in BASIC_SECTIONS:
            section = self.sections[section_key]
            for key in keys:
                label_text, hint = LABELS[key]
                kw = {}
                if key in BASIC_NUMERIC:
                    attr, maximum, unit, minimum = BASIC_NUMERIC[key]
                    kw = dict(value=getattr(self, attr), maximum=maximum, unit=unit, minimum=minimum)
                row = FeatureRow(section.list, label_text, hint, self.options[key], self._row_changed,
                                 fav_var=self._fav_var('basic_' + key), on_fav=self._on_fav_changed,
                                 item_id='basic_' + key, group=BASIC_GROUP[key], **kw)
                section.rows.append(row)

    # ----------------------------------------------------------------- active
    def _build_active(self):
        page = self._page('active')
        _t, self.active_sub, right = self._header(page, '已启用', '')
        ui.Button(right, '全部关闭', self.clear, variant='danger').pack(side='left')
        self.active_area = ui.ScrollArea(page, padx=px(28))
        self.active_area.pack(fill='both', expand=True)

    # -------------------------------------------------------------------- log
    def _build_log(self):
        page = self._page('log')
        _t, _s, right = self._header(page, '生成日志', '最近一次生成 / 安装的结果与说明')
        ui.Button(right, '打开输出目录', self.open_output, variant='secondary').pack(side='left')
        wrap = tk.Frame(page, bg=THEME['border'], padx=1, pady=1)
        wrap.pack(fill='both', expand=True, padx=px(28), pady=(0, px(24)))
        self.log = tk.Text(wrap, bg=THEME['input_bg'], fg=THEME['ink'], insertbackground=THEME['gold'],
                           wrap='word', relief='flat', borderwidth=0, padx=px(18), pady=px(16),
                           font=ui.font(page, 'mono'), selectbackground=THEME['gold_dim'], spacing1=px(2))
        self.log.pack(fill='both', expand=True)
        self.log.insert('end', '暂无记录。生成或安装后，结果会显示在这里。\n')

    # --------------------------------------------------------------- settings
    def _build_settings(self):
        page = self._page('settings')
        self._header(page, '设置', '目录、扫描与维护')
        area = ui.ScrollArea(page, padx=px(28))
        area.pack(fill='both', expand=True)
        body = area.inner

        def path_card(title, hint, variable, extra=None):
            ui.section_title(body, title).pack(fill='x', pady=(px(4), px(8)))
            box = ui.card(body, padx=18, pady=14)
            box.pack(fill='x', pady=(0, px(18)))
            row = tk.Frame(box, bg=THEME['card'])
            row.pack(fill='x')
            if extra:
                extra(row)
            ui.Button(row, '浏览…', lambda: self.browse(variable), variant='secondary').pack(side='right', padx=(px(10), 0))
            field = text_field(row, variable)
            field.pack(side='left', fill='x', expand=True)
            field.entry.bind('<FocusOut>', lambda _e: self._maybe_rescan())
            field.entry.bind('<Return>', lambda _e: self._maybe_rescan())
            info = ui.label(box, hint, 'sm', anchor='w', justify='left')
            info.pack(fill='x', pady=(px(8), 0))
            return info

        def auto_find(row):
            ui.Button(row, '自动查找', lambda: (self.game.set(GAME), self.scan()), variant='ghost').pack(side='right', padx=(px(6), 0))
        self.game_info = path_card('游戏安装目录', '需包含 Warhammer3.exe 与 data/db.pack', self.game, auto_find)
        self.local_info = path_card('参考 Mod 目录', '存放 @*.pack 参考包的目录，默认为本程序所在目录', self.local)

        ui.section_title(body, '维护').pack(fill='x', pady=(px(4), px(8)))
        box = ui.card(body, padx=18, pady=8)
        box.pack(fill='x', pady=(0, px(24)))
        entries = [
            ('重新扫描', '重新读取本地参考包与创意工坊基础包', '扫描', self.scan, 'secondary'),
            ('输出目录', '生成的静态包与构建报告保存在 output/ 目录', '打开', self.open_output, 'secondary'),
            ('卸载修改器', '从游戏 data 目录移除本修改器的包（会先备份）；存档中已产生的变化不会回滚', '卸载', self.remove, 'danger'),
        ]
        for idx, (title, desc, text, cmd, variant) in enumerate(entries):
            if idx:
                tk.Frame(box, bg=THEME['divider'], height=1).pack(fill='x')
            row = tk.Frame(box, bg=THEME['card'], pady=px(10))
            row.pack(fill='x')
            ui.Button(row, text, cmd, variant=variant, size='sm', min_width=72).pack(side='right')
            text_col = tk.Frame(row, bg=THEME['card'])
            text_col.pack(side='left', fill='x', expand=True)
            ui.label(text_col, title, 'body_b', anchor='w').pack(fill='x')
            ui.label(text_col, desc, 'sm', anchor='w').pack(fill='x')

    # ------------------------------------------------------------- navigation
    def _nav_click(self, key):
        if key in GROUPS:
            self.show_group(key)
        else:
            self.show_page(key)

    def show_page(self, key):
        if key in GROUPS:
            return self.show_group(key)
        self.current_page = key
        self.pages[key].tkraise()
        for nav_key, item in self.nav.items():
            item.set_selected(nav_key == (self.current_group if key == 'catalog' else key))
        if key in ('catalog', 'active'):
            self.build_bar_line.pack(side='bottom', fill='x', before=self.stack)
            self.build_bar.pack(side='bottom', fill='x', before=self.build_bar_line)
        else:
            self.build_bar.pack_forget()
            self.build_bar_line.pack_forget()
        if key == 'active':
            self._render_active()
        elif key == 'catalog':
            self.filter()
        return None

    def show_group(self, group):
        self.current_group = group
        self.show_page('catalog')
        self.catalog_area.scroll_top()

    def _focus_search(self):
        if self.current_page != 'catalog':
            self.show_group(self.current_group)
        self.search_box.focus()
        return 'break'

    # ---------------------------------------------------------------- filters
    def _fav_var(self, item_id):
        if item_id not in self.favourites:
            self.favourites[item_id] = tk.BooleanVar(value=item_id in self.saved_favourites)
        return self.favourites[item_id]

    def _on_fav_changed(self):
        self.saved_favourites = {k for k, v in self.favourites.items() if v.get()}
        self._update_nav_counts()
        if self.current_page == 'catalog' and self.current_group == 'favs':
            self.filter()

    def all_rows(self):
        return [row for section in self.sections.values() for row in section.rows]

    def filter(self):
        raw = self.search.get().strip()
        query = raw.lower()
        group = self.current_group
        title, subtitle = GROUPS[group]
        if self._empty_state is not None:
            self._empty_state.destroy()
            self._empty_state = None
        self.catalog_pad.pack_forget()
        for section in self.sections.values():
            section.pack_forget()
        visible = 0
        for key, sec_group, _title in SECTION_ORDER:
            section = self.sections[key]
            if group == 'favs':
                rows = [r for r in section.rows if r.fav_var is not None and r.fav_var.get() and r.matches(query)]
            elif query:
                rows = [r for r in section.rows if r.matches(query)]
            elif sec_group == group:
                rows = list(section.rows)
            else:
                rows = []
            if not rows:
                continue
            label_text = section.base_title
            if group == 'favs' or query:
                label_text = GROUPS[sec_group][0] + ' · ' + section.base_title
            section.show_rows(rows, label_text)
            section.pack(fill='x')
            visible += len(rows)
        self.cat_title.configure(text=title)
        if query:
            scope = '收藏' if group == 'favs' else '全部分类'
            self.cat_sub.set_text(f'在{scope}中搜索 “{raw}” · {visible} 项结果')
        else:
            self.cat_sub.set_text(subtitle + f'  ·  共 {visible} 项')
        if visible == 0:
            if group == 'favs' and not query:
                self._empty_state = ui.empty_state(self.catalog_body, '收藏夹还是空的', '点击任意功能行末尾的 ☆ 即可加入收藏',
                                                   '浏览内政', lambda: self.show_group('domestic'), icon='star')
            else:
                self._empty_state = ui.empty_state(self.catalog_body, f'未找到 “{raw}”',
                                                   '换个关键词试试，或清除搜索查看全部功能',
                                                   '清除搜索', lambda: self.search.set(''), icon='search')
            self._empty_state.pack(fill='x', pady=(px(8), 0))
        self.catalog_pad.pack(fill='x')
        return visible

    # ---------------------------------------------------------------- presets
    def show_preset_menu(self):
        try:
            x = self.preset_btn.winfo_rootx()
            y = self.preset_btn.winfo_rooty() + self.preset_btn.winfo_height() + px(4)
            self.preset_menu.tk_popup(x, y)
        finally:
            self.preset_menu.grab_release()

    def _apply_preset(self, values, switches, message):
        for key, val in values.items():
            if key in self.attribute_options:
                enabled, value = self.attribute_options[key]
                value.set(val)
                enabled.set(True)
        for key in switches:
            self.options[key].set(True)
        self.update_count()
        self.status.set(message)

    def apply_campaign_preset(self):
        self._apply_preset({'order': '50', 'movement': '100', 'diplomacy': '50', 'recruitment': '10', 'upkeep': '50',
                            'agent_player': '100'}, ('attrition', 'foreign_replenishment', 'replenishment_cap'),
                           '已应用战役发育预设（9 项）')

    def apply_battle_preset(self):
        self._apply_preset({'ward': '33', 'missile_resistance': '33', 'attack': '50', 'defence': '50', 'weapon': '50',
                            'missile_damage': '50', 'ammunition': '100', 'armour': '100', 'reload': '100'},
                           ('vigour', 'unbreakable'), '已应用精锐战斗预设（11 项）')

    # ------------------------------------------------------------------ state
    def _row_changed(self, value_only=False):
        if value_only:
            if self.current_page == 'active':
                self._render_active()
            return
        self.update_count()

    def _refresh_addon_badge(self):
        n = sum(v.get() for v in self.ingame_addons.values())
        self.addon_badge.set(f'{n} / {len(ADDON_SPECS)}', 'gold' if n else 'neutral')

    def _refresh_install_state(self):
        title, desc, _colour, kind = detect_installed_mode(self.game.get())
        self._install_kind = kind
        self.hero.set_state(kind, title, desc)
        if kind == 'ok':
            self.hero_primary.set_text('启动游戏')
            self.hero_primary.set_command(self.open_game)
            self.hero_secondary.set_text('重新安装 / 更新')
            self.hero_secondary.set_command(self.deploy_ingame)
        else:
            self.hero_primary.set_text('重新适配并安装' if kind == 'stale' else '一键安装')
            self.hero_primary.set_command(self.deploy_ingame)
            self.hero_secondary.set_text('仅构建到 output')
            self.hero_secondary.set_command(self.generate_ingame)
        colour = THEME['green'] if kind == 'ok' else THEME['red'] if kind == 'error' else THEME['gold']
        self.side_state.configure(text='● ' + title, fg=colour)
        self.status_dot.configure(fg=colour)
        self.status_right.configure(text=title)

    @staticmethod
    def _local_group(name):
        return 'domestic' if '战役' in name else 'battle' if '部队' in name or '回血' in name else 'mods'

    def _collect_active_items(self):
        items = []
        for key, var in self.options.items():
            if var.get():
                if key in BASIC_NUMERIC:
                    attr, _m, unit, _min = BASIC_NUMERIC[key]
                    v_txt = f'{getattr(self, attr).get()} {unit}'
                else:
                    v_txt = '开启'
                items.append(dict(id='opt_' + key, label=LABELS[key][0], value_text=v_txt, group=BASIC_GROUP[key],
                                  disable=lambda v=var: (v.set(False), self.update_count())))
        for key, (enabled, value) in self.attribute_options.items():
            if enabled.get():
                label_text, _, _, _, _, _, unit = ATTRIBUTES[key]
                items.append(dict(id='attr_' + key, label=label_text, value_text=f'{value.get()} {unit}',
                                  group=ATTRIBUTE_GROUP[key],
                                  disable=lambda e=enabled: (e.set(False), self.update_count())))
        for name, var in self.local_options.items():
            if var.get():
                items.append(dict(id='local_' + name, label=local_mod_label(Path(name)), value_text='效果包',
                                  group=self._local_group(name),
                                  disable=lambda v=var: (v.set(False), self.update_count())))
        return items

    def _render_active(self):
        body = self.active_area.inner
        for child in body.winfo_children():
            child.destroy()
        items = self._collect_active_items()
        self.active_sub.set_text(f'{len(items)} 项将写入静态包' if items else '尚未启用任何静态包功能')
        if not items:
            ui.empty_state(body, '当前没有启用的功能', '在左侧分类中打开开关，启用的项目会汇总在这里',
                           '浏览内政', lambda: self.show_group('domestic'), icon='check').pack(fill='x')
            return
        box = tk.Frame(body, bg=THEME['card'], highlightthickness=1, highlightbackground=THEME['border'])
        box.pack(fill='x', pady=(0, px(24)))
        for idx, item in enumerate(items):
            if idx:
                tk.Frame(box, bg=THEME['divider'], height=1).pack(fill='x')
            row = tk.Frame(box, bg=THEME['card'], padx=px(18), pady=px(10))
            row.pack(fill='x')
            tk.Frame(row, bg=THEME['gold'], width=px(3), height=px(18)).pack(side='left', padx=(0, px(12)))
            ui.IconButton(row, 'close', command=item['disable'], hover_fg=THEME['red'], tooltip='关闭').pack(side='right')
            ui.Badge(row, item['value_text'], 'gold').pack(side='right', padx=(0, px(10)))
            tk.Label(row, text=item['label'], font=ui.font(row, 'body_b'), bg=THEME['card'], fg=THEME['ink']).pack(side='left')
            tk.Label(row, text=GROUPS[item['group']][0], font=ui.font(row, 'sm'), bg=THEME['card'],
                     fg=THEME['quiet']).pack(side='left', padx=(px(10), 0))

    def _update_nav_counts(self):
        counts = {g: 0 for g in GROUPS}
        for item in self._collect_active_items():
            counts[item['group']] += 1
        for group in ('domestic', 'army', 'battle', 'mods'):
            self.nav[group].set_count(counts[group], gold=True)
        self.nav['favs'].set_count(sum(1 for v in self.favourites.values() if v.get()))
        total = sum(counts.values())
        self.nav['active'].set_count(total, gold=True)
        return total

    def update_count(self):
        total = self._update_nav_counts()
        self.build_count.set(f'已选 {total} 项' if total else '未选择', 'gold' if total else 'neutral')
        if self.current_page == 'active':
            self._render_active()
        return total

    def clear(self):
        for var in list(self.options.values()) + list(self.local_options.values()) + [e for e, _v in self.attribute_options.values()]:
            var.set(False)
        self.update_count()
        self.status.set('已关闭全部静态包功能')

    # ------------------------------------------------------------------- scan
    def browse(self, var):
        path = filedialog.askdirectory(initialdir=var.get())
        if path:
            var.set(path)
            self.scan()

    def _maybe_rescan(self):
        if self._scanned_dirs != (self.game.get(), self.local.get()):
            self.scan()

    def scan(self):
        self._scanned_dirs = (self.game.get(), self.local.get())
        try:
            selected = {n for n, v in self.local_options.items() if v.get()} | set(self.saved_local)
            self.saved_local = []
            for key, section in self.sections.items():
                if key.endswith('_attr') or key.endswith('_mod'):
                    for row in section.rows:
                        row.destroy()
                    section.rows = []
            self.local_options = {}
            paths = [p for p in sorted(Path(self.local.get()).glob('*.pack')) if p.name != PACK_NAME]
            available = set()
            legacy_values = {}
            for path in paths:
                try:
                    groups = attribute_pack(path)
                except Exception:
                    groups = {}
                if groups:
                    available.update(groups)
                    if path.name in selected:
                        for key, records in groups.items():
                            legacy_values.setdefault(key, set()).add(abs(records[0]['effect_value']))
                    continue
                group = self._local_group(path.name)
                var = tk.BooleanVar(value=path.name in selected)
                self.local_options[path.name] = var
                try:
                    hint = describe(path)
                except Exception as exc:
                    hint = '无法解析：' + str(exc)
                section = self.sections[group + '_mod']
                section.rows.append(FeatureRow(
                    section.list, local_mod_label(path), hint, var, self._row_changed,
                    fav_var=self._fav_var('mod_' + path.name), on_fav=self._on_fav_changed,
                    item_id='mod_' + path.name, group=group))
            for key, values in legacy_values.items():
                if key not in self.explicit_attributes:
                    enabled, value = self.attribute_options[key]
                    enabled.set(True)
                    value.set(f'{next(iter(values)):g}' if len(values) == 1 else '请选择数值')
            for key, spec in ATTRIBUTES.items():
                label_text, _, _, _, maximum, _, unit = spec
                group = ATTRIBUTE_GROUP[key]
                enabled, value = self.attribute_options[key]
                section = self.sections[group + '_attr']
                section.rows.append(FeatureRow(
                    section.list, label_text, '' if key in available else '缺少参考包，仍可按数值生成',
                    enabled, self._row_changed, fav_var=self._fav_var('attr_' + key),
                    on_fav=self._on_fav_changed, value=value, maximum=maximum, unit=unit,
                    item_id='attr_' + key, group=group))
            found = discover(self.game.get(), local_dir=self.local.get())
            self._refresh_install_state()
            self._refresh_addon_badge()
            base = sum(p is not None for p in found.values())
            self.local_info.configure(text=f'已找到 {len(paths)} 个本地参考包 · 创意工坊基础包 {base}/4')
            exe_ok = (Path(self.game.get()) / 'Warhammer3.exe').is_file()
            self.game_info.configure(
                text='✓ 已找到 Warhammer3.exe' if exe_ok else '未找到 Warhammer3.exe，请选择正确的游戏安装目录',
                fg=THEME['green'] if exe_ok else THEME['red'])
            self.status.set(f'就绪 · 本地参考包 {len(paths)} 个 · 工坊基础包 {base}/4')
            if self.current_page == 'catalog':
                self.filter()
            self.update_count()
        except Exception as exc:
            self.status.set('扫描失败：' + str(exc))

    # ----------------------------------------------------------- persistence
    def collect_options(self, draft=False):
        values = {}
        for key, (enabled, value) in self.attribute_options.items():
            if enabled.get():
                try:
                    values[key] = float(value.get())
                except ValueError:
                    if not draft:
                        raise ValueError(ATTRIBUTES[key][0] + '：请输入有效数值') from None
        defaults = Options()
        numbers = {}
        for field, control, switch in self.basic_numbers():
            numbers[field] = getattr(defaults, field)
            if self.options[switch].get():
                try:
                    numbers[field] = float(control.get())
                except ValueError:
                    if not draft:
                        raise ValueError(LABELS[switch][0] + '：请输入有效数值') from None
        return Options(
            **{k: v.get() for k, v in self.options.items()},
            **numbers,
            attributes=values,
            local_mods=[n for n, v in self.local_options.items() if v.get()],
        )

    def basic_numbers(self):
        return [
            ('heal_percent', self.heal, 'regeneration'),
            ('cap_value', self.cap_value, 'cap'),
            ('ammo_percent', self.ammo_percent, 'ammunition'),
            ('reload_value', self.reload_value, 'reload'),
            ('foreign_value', self.foreign_value, 'foreign_replenishment'),
        ]

    def save(self):
        data = {'game': self.game.get(), 'local': self.local.get(), 'options': asdict(self.collect_options(draft=True))}
        data['basic_controls'] = {field: control.get() for field, control, _ in self.basic_numbers()}
        data['attribute_controls'] = {k: {'enabled': e.get(), 'value': v.get()} for k, (e, v) in self.attribute_options.items()}
        data['ingame_addons'] = {k: v.get() for k, v in self.ingame_addons.items()}
        data['favourites'] = sorted(k for k, v in self.favourites.items() if v.get())
        atomic_write(STATE, json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))

    def load(self):
        if not STATE.exists():
            return
        try:
            data = json.loads(STATE.read_text(encoding='utf-8'))
            self.game.set(data['game'])
            self.local.set(data.get('local', str(REFERENCE)))
            values = data['options']
            for k, v in self.options.items():
                v.set(values.get(k, False))
            self.heal.set(str(values.get('heal_percent', 12.5)))
            self.cap_value.set(str(values.get('cap_value', 50)))
            for key in ('ammo_percent', 'reload_value', 'foreign_value'):
                getattr(self, key).set(str(values.get(key, getattr(self, key).get())))
            for field, control, _ in self.basic_numbers():
                if field in data.get('basic_controls', {}):
                    control.set(data['basic_controls'][field])
            for key, number in values.get('attributes', {}).items():
                if key in self.attribute_options:
                    enabled, value = self.attribute_options[key]
                    enabled.set(True)
                    value.set(str(number))
                    self.explicit_attributes.add(key)
            for key, record in data.get('attribute_controls', {}).items():
                if key in self.attribute_options:
                    enabled, value = self.attribute_options[key]
                    enabled.set(record['enabled'])
                    value.set(record['value'])
                    self.explicit_attributes.add(key)
            for key, enabled in data.get('ingame_addons', {}).items():
                if key in self.ingame_addons:
                    self.ingame_addons[key].set(bool(enabled))
            self.saved_favourites = set(data.get('favourites', []))
            for fav_key, fav_var in self.favourites.items():
                fav_var.set(fav_key in self.saved_favourites)
            self.saved_local = values.get('local_mods', [])
        except Exception as exc:
            self.status.set('配置读取失败：' + str(exc))

    # --------------------------------------------------------------- actions
    def _write_log(self, text):
        self.log.delete('1.0', 'end')
        self.log.insert('end', text)

    def generate_ingame(self):
        try:
            from build_ingame import build_ingame
            addons = {k: v.get() for k, v in self.ingame_addons.items() if v.get()}
            if addons.get('cap'):
                try:
                    addons['cap_value'] = float(self.cap_value.get())
                except ValueError:
                    addons['cap_value'] = 50.0
            if addons.get('agent_ai'):
                try:
                    addons['agent_ai_value'] = float(self.attribute_options['agent_ai'][1].get())
                except ValueError:
                    addons['agent_ai_value'] = 100.0
            self.status.set('正在构建游戏内面板…')
            self.update_idletasks()
            payload, report = build_ingame(self.game.get(), addons=addons or None, local_dir=self.local.get())
            verify_game(self.game.get(), payload)
            output = HOME / 'output/ingame'
            atomic_write(output / PACK_NAME, payload)
            atomic_write(output / 'build-report.json', json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8'))
            self.save()
            self._write_log(
                f'游戏内实时面板构建成功\n{output / PACK_NAME}\n\n'
                f'包含 {len(report["features"])} 项功能\n'
                f'附加模块：{"、".join(report["addons"]) if report["addons"] else "无（纯净面板）"}\n\n'
                '说明：\n' + ('\n'.join(report['warnings']) or '无警告') + '\n\n'
                '进入单人战役后按 F9 或点击顶栏「◆ 修改器」即可呼出面板。\n',
            )
            self.status.set('已构建游戏内面板（尚未安装）')
            return payload
        except Exception as exc:
            self.status.set('构建失败：' + str(exc))
            messagebox.showerror('构建游戏内面板失败', str(exc))
            return None

    def deploy_ingame(self):
        payload = self.generate_ingame()
        if payload is None:
            return
        if not messagebox.askyesno(
            '安装游戏内面板',
            '请确认已退出游戏，并在启动器中停用此前整合的旧版同类 Mod。\n\n'
            '安装后进入单人战役，按 F9 或点击顶栏「◆ 修改器」即可调整加成与执行指令。\n继续安装？',
        ):
            return
        try:
            target = install(self.game.get(), payload, HOME / 'history')
            self.log.insert('end', '\n已安装至：' + str(target))
            self._refresh_install_state()
            self.status.set('安装完成，已通过回读校验 · 点击「启动游戏」进入单人战役')
            self.show_page('home')
        except Exception as exc:
            messagebox.showerror('安装失败', str(exc))

    def generate(self):
        try:
            payload, report = build(self.game.get(), self.collect_options(), local_dir=self.local.get())
            output = HOME / 'output'
            atomic_write(output / PACK_NAME, payload)
            atomic_write(output / 'build-report.json', json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8'))
            self.save()
            self._write_log(
                f'静态配置包生成成功\n{output / PACK_NAME}\n共 {len(report["files"])} 个内部文件\n\n表版本迁移：\n'
                + ('\n'.join(report['migrations']) or '无需迁移')
                + '\n\n说明：\n'
                + '\n'.join(report['warnings'])
                + '\n\n仅完成离线格式校验，尚未验证游戏内表现。\n'
                + '安装前请在启动器停用已整合的原 Mod，避免重复加载。\n'
                + '生成包为自动加载类型；安装后退出并重新启动游戏生效。\n\n'
                + json.dumps(report['options'], ensure_ascii=False, indent=2),
            )
            self.status.set('已生成静态配置包（尚未安装）· 详见「生成日志」')
            return payload
        except Exception as exc:
            messagebox.showerror('生成失败', str(exc))
            return None

    def deploy(self):
        payload = self.generate()
        if payload is None:
            return
        replace_note = '\n\n注意：当前已安装游戏内面板，静态包将替换它。' if self._install_kind == 'ok' else ''
        if not messagebox.askyesno(
            '安装静态配置包',
            '请确认已退出游戏，并在启动器中停用本次整合的原 Mod。\n\n'
            '将只写入本修改器的独立配置包，下次启动游戏生效。' + replace_note + '\n继续安装？',
        ):
            return
        try:
            target = install(self.game.get(), payload, HOME / 'history')
            self.log.insert('end', '\n\n已安装：' + str(target))
            self._refresh_install_state()
            self.status.set('静态包安装完成，已回读校验 · 请重新启动游戏')
        except Exception as exc:
            messagebox.showerror('安装失败', str(exc))

    def remove(self):
        if not messagebox.askyesno('卸载修改器', '将从游戏 data 目录移除本修改器的包（会先备份）。\n'
                                   '存档中已产生的变化不会自动回滚。是否继续？'):
            return
        try:
            removed = uninstall(self.game.get(), HOME / 'history')
            self.status.set('已卸载并备份 · 请重启游戏' if removed else '未找到本修改器的包，无需卸载')
            self._refresh_install_state()
        except Exception as exc:
            messagebox.showerror('卸载失败', str(exc))

    def open_game(self):
        self._refresh_install_state()
        if self._install_kind == 'stale':
            messagebox.showwarning('启动游戏', '修改器包与当前游戏数据不匹配，直接启动可能闪退。请先重新适配，或卸载修改器。')
            return
        if not launch_game_or_dir(self.game.get()):
            messagebox.showinfo('启动游戏', '请先在「设置」中确认游戏安装目录。')

    def open_output(self):
        path = HOME / 'output'
        path.mkdir(exist_ok=True)
        os.startfile(path)

    def close(self):
        try:
            self.save()
        except Exception as exc:
            messagebox.showerror('保存失败', '配置尚未保存：' + str(exc))
            return
        self.destroy()


if __name__ == '__main__':
    if sys.argv[1:2] == ['--smoke']:
        smoke_test(sys.argv[2])
    else:
        ui.enable_dpi_awareness()
        App().mainloop()
