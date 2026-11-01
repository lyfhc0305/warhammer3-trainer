"""Standalone installer for the bundled native campaign panel (dark-gold wizard)."""
from dataclasses import dataclass, replace
import json
import math
import os
import sys
import tempfile
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
from attributes import ATTRIBUTES
from trainer_core import (BUNDLE, CAP_VALUE_RANGE, GAME, HOME, PACK_NAME, REFERENCE, VERSION, Options,
                          atomic_write, install, uninstall, game_path, owned)
from game_compat import ADDON_KEYS, ADDON_VALUES, addon_record, check as check_compatibility, manifest_addons
from pack_io import read_pack, db_header
import ui_kit as ui
from ui_kit import THEME, px, launch_game_or_dir

SETTINGS = HOME / 'settings.json'

ADDON_SPECS = [
    ('building', '建筑快捷升级菜单', '复用 cpecific 快捷建造菜单脚本'),
    ('cap', '全局战斗回血上限', '累计回血池为单位最大生命的若干倍，并非无限，也影响敌方'),
    ('agent_ai', '敌方间谍成功率减益', '按百分点降低 AI 事务官的行动成功率'),
    ('shop', '基斯里夫祈祷神器奖励', '支持军队／行省四神祈祷，启动时获神器；保留原生消耗与效果'),
    ('ballistic', '旧版全局弹道 / 爆炸覆盖', '兼容移植旧数值，含加强/削弱且影响敌方；旧独有键跳过'),
    ('friendly_fire', '关闭友军伤害', '弹丸、爆炸和漩涡不再伤自己人，也影响敌方；治疗不受影响，数值不变'),
]
ADDON_NAMES = {key: title for key, title, _ in ADDON_SPECS}
assert tuple(ADDON_NAMES) == ADDON_KEYS


@dataclass(frozen=True)
class ValueSpec:
    """A module parameter the installer asks for; the range is the one the build checks."""
    key: str      # addons dict key given to build_ingame
    label: str
    unit: str
    minimum: float
    maximum: float
    default: float
    step: float
    width: int = 6


ADDON_VALUE_SPECS = {
    'cap': ValueSpec(ADDON_VALUES['cap'], '倍数', '倍', *CAP_VALUE_RANGE, Options.cap_value, 5, width=9),
    'agent_ai': ValueSpec(ADDON_VALUES['agent_ai'], '减益', ATTRIBUTES['agent_ai'][6],
                          0, ATTRIBUTES['agent_ai'][4], ATTRIBUTES['agent_ai'][5], 5),
}


def parse_value(key, text):
    """The number typed for a module parameter; ValueError names the allowed range."""
    spec = ADDON_VALUE_SPECS[key]
    try:
        value = float(str(text).strip())
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or not spec.minimum <= value <= spec.maximum:
        raise ValueError(f'{ADDON_NAMES[key]}的{spec.label}必须在 '
                         f'{ui.fmt_number(spec.minimum)}～{ui.fmt_number(spec.maximum)} 之间')
    return value


def verify_game(game, package=BUNDLE):
    game = game_path(game)
    if isinstance(package, (bytes, bytearray)):
        with tempfile.TemporaryDirectory() as tmp:
            temp_pack = Path(tmp) / PACK_NAME
            temp_pack.write_bytes(package)
            return verify_game(game, temp_pack)
    files = read_pack(package)
    if files.get('wh3pt_mode.txt') != b'ingame-v2':
        raise ValueError('缺少游戏内面板安装包，请重新构建')
    versions = {n.split('\\')[1]: db_header(b)[0] for n, b in files.items() if n.startswith('db\\')}
    base = read_pack(game / 'data/db.pack', lambda n: n.startswith('db\\') and n.split('\\')[1] in versions)
    found = set()
    for name, data in base.items():
        table = name.split('\\')[1]
        if versions[table] != db_header(data)[0]:
            raise ValueError('游戏表版本发生变化，请重新适配：' + table)
        found.add(table)
    if found != set(versions):
        raise ValueError('游戏缺少面板所需数据表')
    return Path(package).read_bytes()


def prepare_payload(game, addons=None, package=BUNDLE):
    """The bundled Pack while it matches the game, otherwise one built from the game as installed.

    addons: {key: True, ...} for the chosen modules, plus 'cap_value' / 'agent_ai_value'.
    """
    if not addons and check_compatibility(game_path(game), package).usable:
        return verify_game(game, package)
    from build_ingame import build_ingame
    return verify_game(game, build_ingame(game, addons=addons or None, local_dir=REFERENCE)[0])


def smoke_test(path):
    """Build-time check of a frozen exe: bundled data and a full rebuild, nothing is installed."""
    try:
        from build_ingame import build_ingame
        game = game_path(GAME)
        payload, report = build_ingame(game, local_dir=REFERENCE)
        result = dict(version=VERSION, bundle=check_compatibility(game, BUNDLE).state,
                      bundle_bytes=len(verify_game(game, BUNDLE)), rebuilt_bytes=len(verify_game(game, payload)),
                      features=len(report['features']), warnings=report['warnings'])
    except Exception as exc:
        result = dict(error=f'{type(exc).__name__}: {exc}')
    Path(path).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')


@dataclass(frozen=True)
class InstallStatus:
    kind: str    # ok, info, stale or error
    title: str
    desc: str
    # Modules the installed pack records (addon_record shape). None when nothing
    # usable is installed, or the pack predates the record.
    addons: dict = None


def describe_addons(record):
    """'全局战斗回血上限（50 倍）、基斯里夫祈祷神器奖励' for a manifest record."""
    parts = []
    for key in record['enabled']:
        text = ADDON_NAMES.get(key, key)
        spec = ADDON_VALUE_SPECS.get(key)
        if spec and spec.key in record['values']:
            text += f'（{ui.fmt_number(record["values"][spec.key])} {spec.unit}）'
        parts.append(text)
    return '、'.join(parts)


def detect_installed_mode(game_dir):
    try:
        g = game_path(game_dir)
    except Exception as exc:
        return InstallStatus('error', '游戏目录无效', str(exc))
    target = g / 'data' / PACK_NAME
    if not target.is_file():
        return InstallStatus('info', '尚未安装', f'已定位游戏目录：{g}。点击「一键安装」即可完成部署。')
    try:
        owned(target)
        status = check_compatibility(g, target)
        addons = manifest_addons(status.manifest)
        if not status.usable:
            title = '修改器包需要更新' if status.state == 'unknown' else '游戏已更新，需要重新适配'
            return InstallStatus('stale', title, status.describe() + '请重新适配并安装；暂时无法适配时先卸载，保证游戏能启动。', addons)
        entries = read_pack(target, lambda n: n == 'wh3pt_mode.txt')
        if entries.get('wh3pt_mode.txt') != b'ingame-v2':
            return InstallStatus('info', '已安装旧版静态配置包', '当前安装的是旧版战前静态配置包；重新安装即可切换为游戏内实时面板。')
        installed = status.manifest.get('trainer_version')
        if installed != VERSION:
            return InstallStatus('info', f'可更新到 {VERSION}', f'当前安装的是 {installed}。点击「一键安装」更新。', addons)
        ready = '进入单人战役后按 F9，或点击顶栏「◆ 修改器」呼出面板。'
        if addons is None:
            return InstallStatus('ok', '已安装游戏内面板', '这个安装包没有记录附加模块，重新安装后会记录。' + ready)
        if addons['enabled']:
            return InstallStatus('ok', f'已安装 · 含 {len(addons["enabled"])} 个附加模块',
                                 f'附加模块：{describe_addons(addons)}。' + ready, addons)
        return InstallStatus('ok', '已安装 · 纯净面板', '一切就绪：' + ready, addons)
    except Exception as exc:
        return InstallStatus('error', '检测到同名冲突文件', str(exc))


def load_settings(path=None):
    """What the installer remembers: game path, module switches and parameters.

    The retired desktop console kept its own settings.json; its in-game module
    selection and the two parameters are read from that layout too.
    """
    try:
        data = json.loads(Path(path or SETTINGS).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    switches = data.get('addons')
    if not isinstance(switches, dict):
        switches = data.get('ingame_addons') if isinstance(data.get('ingame_addons'), dict) else {}
    values = data.get('addon_values')
    if not isinstance(values, dict):
        values = {}
        options = data.get('options') if isinstance(data.get('options'), dict) else {}
        if 'cap_value' in options:
            values['cap_value'] = options['cap_value']
        controls = data.get('attribute_controls') if isinstance(data.get('attribute_controls'), dict) else {}
        agent = controls.get('agent_ai')
        if isinstance(agent, dict) and 'value' in agent:
            values['agent_ai_value'] = agent['value']
    return dict(
        game=data['game'] if isinstance(data.get('game'), str) and data['game'] else None,
        addons={key: bool(switches.get(key, False)) for key in ADDON_KEYS},
        addon_values={spec.key: str(values[spec.key]) for spec in ADDON_VALUE_SPECS.values() if spec.key in values},
    )


def save_settings(game, addons, addon_values, path=None):
    data = {'game': game, 'addons': {key: bool(addons.get(key, False)) for key in ADDON_KEYS},
            'addon_values': dict(addon_values)}
    atomic_write(path or SETTINGS, json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))


class Installer(tk.Tk):
    def __init__(self):
        super().__init__()
        ui.setup(self)
        self.title('全面战争：战锤 III 修改器 · 安装器')
        self.minsize(px(720), px(560))
        ui.center_window(self, px(820), px(720))
        self.logs = []
        self.status = InstallStatus('info', '正在检查…', '')
        self._synced_path = None
        settings = load_settings()
        self.path = tk.StringVar(value=settings['game'] or GAME)
        self.addons = {k: tk.BooleanVar(value=settings['addons'][k]) for k, _, _ in ADDON_SPECS}
        self.values = {k: tk.StringVar(value=settings['addon_values'].get(spec.key, ui.fmt_number(spec.default)))
                       for k, spec in ADDON_VALUE_SPECS.items()}

        # Footer (fixed)
        footer = tk.Frame(self, bg=THEME['side'], padx=px(28), pady=px(10))
        footer.pack(side='bottom', fill='x')
        tk.Frame(self, bg=THEME['border'], height=1).pack(side='bottom', fill='x')
        tk.Label(footer, text='v' + VERSION, font=ui.font(self, 'xs'), bg=THEME['side'], fg=THEME['faint']).pack(side='right')
        for text, cmd in [('查看日志', self.show_log), ('打开游戏目录', self.open_game_dir)]:
            link = tk.Label(footer, text=text, font=ui.font(self, 'sm'), bg=THEME['side'], fg=THEME['gold'], cursor='hand2')
            link.pack(side='right', padx=(0, px(18)))
            link.bind('<Button-1>', lambda _e, fn=cmd: fn())
        tk.Label(footer, text='安装、更新或卸载前请先退出游戏', font=ui.font(self, 'sm'), bg=THEME['side'],
                 fg=THEME['quiet']).pack(side='left')

        area = ui.ScrollArea(self, padx=px(28))
        area.pack(fill='both', expand=True)
        body = area.inner

        head = tk.Frame(body, bg=THEME['bg'], pady=px(22))
        head.pack(fill='x')
        ui.Button(head, '使用说明', self.show_guide, variant='ghost', size='sm').pack(side='right', anchor='n')
        tk.Label(head, image=ui.disc(head, px(42), THEME['gold_dim'], ring=THEME['sel_line'], ring_w=1.5),
                 text='◆', compound='center', fg=THEME['gold_ink'], bg=THEME['bg'], font=ui.font(self, 'em'),
                 bd=0).pack(side='left')
        names = tk.Frame(head, bg=THEME['bg'])
        names.pack(side='left', padx=(px(14), 0))
        tk.Label(names, text='TOTAL WAR: WARHAMMER III', font=ui.font(self, 'xs'), bg=THEME['bg'], fg=THEME['gold']).pack(anchor='w')
        tk.Label(names, text='游戏内修改面板 · 安装器', font=ui.font(self, 'h1'), bg=THEME['bg'], fg=THEME['ink']).pack(anchor='w')

        self.hero = ui.StatusHero(body)
        self.hero.pack(fill='x')
        self.primary_cta_btn = ui.Button(self.hero.actions, '一键安装', self.deploy, variant='primary', size='lg', min_width=150)
        self.primary_cta_btn.pack(side='left')
        self.reinstall_cta_btn = ui.Button(self.hero.actions, '验证安装', self.verify_only, variant='secondary', size='lg')
        self.reinstall_cta_btn.pack(side='left', padx=(px(10), 0))
        ui.Button(self.hero.actions, '卸载', self.remove, variant='ghost', size='lg').pack(side='left', padx=(px(6), 0))

        ui.section_title(body, '使用步骤').pack(fill='x', pady=(px(22), px(8)))
        ui.steps_row(body).pack(fill='x')

        # Optional modules (collapsible)
        self._addons_open = False
        addon_card = ui.card(body, padx=18, pady=12)
        addon_card.pack(fill='x', pady=(px(22), 0))
        addon_head = tk.Frame(addon_card, bg=THEME['card'], cursor='hand2')
        addon_head.pack(fill='x')
        self._addon_toggle_btn = ui.Button(addon_head, '展开', self.toggle_addons, variant='ghost', size='sm')
        self._addon_toggle_btn.pack(side='right')
        tk.Label(addon_head, text='附加模块（可选）', font=ui.font(self, 'body_b'), bg=THEME['card'], fg=THEME['ink']).pack(side='left')
        self.addon_summary = ui.Badge(addon_head, '纯净面板', 'neutral', style='xs')
        self.addon_summary.pack(side='left', padx=(px(10), 0))
        self._addon_body = tk.Frame(addon_card, bg=THEME['card'])
        tk.Frame(self._addon_body, bg=THEME['divider'], height=1).pack(fill='x', pady=(px(10), px(4)))
        self.addon_grid = ui.AddonGrid(self._addon_body, ADDON_SPECS, self.addons,
                                       values={k: (self.values[k], spec) for k, spec in ADDON_VALUE_SPECS.items()})
        self.addon_grid.pack(fill='x')
        ui.label(self._addon_body, '勾选或数值与已安装的面板不同时，主按钮会变为「重新安装以应用更改」。', 'sm',
                 anchor='w').pack(fill='x', pady=(px(6), 0))
        for var in list(self.addons.values()) + list(self.values.values()):
            var.trace_add('write', lambda *_: self._selection_changed())

        # Game directory
        self._path_open = False
        path_card = ui.card(body, padx=18, pady=12)
        path_card.pack(fill='x', pady=(px(12), px(24)))
        path_head = tk.Frame(path_card, bg=THEME['card'])
        path_head.pack(fill='x')
        self._path_toggle_btn = ui.Button(path_head, '修改', self.toggle_path, variant='ghost', size='sm')
        self._path_toggle_btn.pack(side='right')
        tk.Label(path_head, text='游戏目录', font=ui.font(self, 'body_b'), bg=THEME['card'], fg=THEME['ink']).pack(side='left')
        self.path_preview_lbl = ui.EllipsisLabel(path_head, text=self.path.get(), font=ui.font(self, 'sm'),
                                                 fg=THEME['quiet'], bg=THEME['card'])
        self.path_preview_lbl.pack(side='left', fill='x', expand=True, padx=(px(12), px(8)))
        self.path.trace_add('write', lambda *_: self.path_preview_lbl.set_text(self.path.get()))
        self._path_body = tk.Frame(path_card, bg=THEME['card'])
        row = tk.Frame(self._path_body, bg=THEME['card'])
        row.pack(fill='x', pady=(px(10), px(4)))
        ui.Button(row, '自动查找', self.rediscover, variant='ghost').pack(side='right', padx=(px(6), 0))
        ui.Button(row, '浏览…', self.browse, variant='secondary').pack(side='right', padx=(px(10), 0))
        field = tk.Frame(row, bg=THEME['input_bg'], highlightthickness=1, highlightbackground=THEME['border'],
                         highlightcolor=THEME['sel_line'])
        field.pack(side='left', fill='x', expand=True)
        entry = tk.Entry(field, textvariable=self.path, bd=0, relief='flat', bg=THEME['input_bg'], fg=THEME['ink'],
                         insertbackground=THEME['gold'], font=ui.font(self, 'body'), highlightthickness=0)
        entry.pack(fill='x', padx=px(10), pady=px(8))
        entry.bind('<FocusOut>', lambda *_: self.refresh_status())
        entry.bind('<Return>', lambda *_: self.refresh_status())
        ui.label(self._path_body, '需包含 Warhammer3.exe 与 data/db.pack', 'sm', anchor='w').pack(fill='x')

        self.protocol('WM_DELETE_WINDOW', self.close)
        self.refresh_status()

    # -------------------------------------------------------------- toggles
    def toggle_addons(self):
        self._addons_open = not self._addons_open
        if self._addons_open:
            self._addon_body.pack(fill='x')
            self._addon_toggle_btn.set_text('收起')
        else:
            self._addon_body.pack_forget()
            self._addon_toggle_btn.set_text('展开')

    def toggle_path(self):
        self._path_open = not self._path_open
        if self._path_open:
            self._path_body.pack(fill='x')
            self._path_toggle_btn.set_text('收起')
        else:
            self._path_body.pack_forget()
            self._path_toggle_btn.set_text('修改')

    # ------------------------------------------------------------ selection
    def selected_addons(self):
        """The switches' request in prepare_payload's shape; ValueError names a bad parameter."""
        chosen = {k: True for k, v in self.addons.items() if v.get()}
        for key, spec in ADDON_VALUE_SPECS.items():
            if key in chosen:
                chosen[spec.key] = parse_value(key, self.values[key].get())
        return chosen

    def pending_change(self):
        """True when the switches no longer match the installed pack, or cannot be read."""
        try:
            chosen = self.selected_addons()
        except ValueError:
            return True
        wanted = addon_record(chosen, chosen)
        installed = self.status.addons
        if installed is None:
            return bool(wanted['enabled'])
        return wanted != installed

    def _select(self, record):
        """Point the switches at what a pack records."""
        for key, var in self.addons.items():
            var.set(key in record['enabled'])
        for key, spec in ADDON_VALUE_SPECS.items():
            if spec.key in record['values']:
                self.values[key].set(ui.fmt_number(record['values'][spec.key]))

    def _selection_changed(self):
        for key in ADDON_VALUE_SPECS:
            try:
                parse_value(key, self.values[key].get())
                self.addon_grid.fields[key].set_invalid(False)
            except ValueError:
                # Only a module that is switched on needs a usable number.
                self.addon_grid.fields[key].set_invalid(self.addons[key].get())
        n = sum(v.get() for v in self.addons.values())
        text = f'已选 {n} / {len(ADDON_SPECS)}' if n else '纯净面板'
        if self.status.kind == 'ok' and self.pending_change():
            text += ' · 未应用'
        self.addon_summary.set(text, 'gold' if n else 'neutral')
        self._refresh_actions()

    # ---------------------------------------------------------------- state
    def _draw_badge(self, status):
        self.status = status
        self.hero.set_state(status.kind, status.title, status.desc)
        self._selection_changed()

    def _refresh_actions(self):
        kind = self.status.kind
        if kind == 'ok' and self.pending_change():
            self.primary_cta_btn.set_text('重新安装以应用更改')
            self.primary_cta_btn.set_command(self.deploy)
            self.reinstall_cta_btn.set_text('启动游戏')
            self.reinstall_cta_btn.set_command(self.open_game)
        elif kind == 'ok':
            self.primary_cta_btn.set_text('启动游戏')
            self.primary_cta_btn.set_command(self.open_game)
            self.reinstall_cta_btn.set_text('重新安装 / 更新')
            self.reinstall_cta_btn.set_command(self.deploy)
        else:
            self.primary_cta_btn.set_text('重新适配并安装' if kind == 'stale' else '一键安装')
            self.primary_cta_btn.set_command(self.deploy)
            self.reinstall_cta_btn.set_text('验证游戏目录')
            self.reinstall_cta_btn.set_command(self.verify_only)
            if kind == 'error' and not self._path_open:
                self.toggle_path()

    def log(self, msg):
        self.logs.append(msg)

    def browse(self):
        path = filedialog.askdirectory(initialdir=self.path.get())
        if path:
            self.path.set(path)
            self.refresh_status()

    def rediscover(self):
        self.path.set(GAME)
        self.refresh_status()

    def refresh_status(self):
        path = self.path.get()
        status = detect_installed_mode(path)
        if path != self._synced_path:
            # A directory looked at for the first time: its pack says which
            # modules are on, so the switches follow it rather than the last session.
            self._synced_path = path
            if status.addons is not None:
                self._select(status.addons)
        self._draw_badge(status)

    def save(self):
        save_settings(self.path.get(), {k: v.get() for k, v in self.addons.items()},
                      {spec.key: self.values[key].get() for key, spec in ADDON_VALUE_SPECS.items()})

    def close(self):
        try:
            self.save()
        except OSError as exc:
            messagebox.showerror('保存设置', f'设置未能保存：{exc}')
        self.destroy()

    # -------------------------------------------------------------- actions
    def verify_only(self):
        try:
            verify_game(self.path.get())
            status = detect_installed_mode(self.path.get())
            if status.kind != 'stale':
                status = replace(status, title='安装包与游戏表校验通过')
            self._draw_badge(status)
            self.log('验证通过：' + self.path.get())
        except Exception as exc:
            self._draw_badge(InstallStatus('error', '验证未通过', str(exc)))
            messagebox.showerror('验证失败', str(exc))

    def deploy(self):
        try:
            chosen = self.selected_addons()
        except ValueError as exc:
            messagebox.showerror('附加模块参数', str(exc))
            return
        try:
            target = install(self.path.get(), prepare_payload(self.path.get(), chosen), HOME / 'history')
            self.save()
            # Read the installed pack back: the status card now describes what
            # is really in the game directory.
            self._synced_path = self.path.get()
            status = detect_installed_mode(self.path.get())
            self._draw_badge(replace(status, desc='已写入并通过回读校验。' + status.desc))
            self.log('已安装：' + str(target))
        except Exception as exc:
            self.log('安装失败：' + str(exc))
            messagebox.showerror('安装失败', str(exc) + '\n\n如果游戏刚更新，可先点「卸载」移除修改器包，保证游戏能启动。')

    def remove(self):
        if not messagebox.askyesno(
            '卸载修改器',
            '建议先在游戏面板内点击「全关」并保存战役，再退出游戏执行卸载。\n'
            '直接卸载文件不会清除已有存档中的效果包。是否现在卸载？',
        ):
            return
        try:
            removed = uninstall(self.path.get(), HOME / 'history')
            self._draw_badge(InstallStatus('info', '已卸载修改器' if removed else '未找到已安装文件',
                                           '已安全移除并备份配置包。' if removed else '当前游戏目录未找到本修改器的包。'))
            self.log('卸载完成' if removed else '无需卸载')
        except Exception as exc:
            messagebox.showerror('卸载失败', str(exc))

    def open_game(self):
        self.refresh_status()
        if self.status.kind == 'stale':
            messagebox.showwarning('启动游戏', '修改器包与当前游戏数据不匹配，直接启动可能闪退。请先重新适配，或卸载修改器。')
            return
        if not launch_game_or_dir(self.path.get()):
            messagebox.showinfo('启动游戏', '请先确认游戏安装目录有效。')

    def open_game_dir(self):
        p = Path(self.path.get())
        if p.is_dir():
            os.startfile(p)

    def show_guide(self):
        guide = HOME / 'docs/内置修改器说明.txt'
        if guide.is_file():
            os.startfile(guide)
        else:
            messagebox.showinfo('使用说明', '进入单人战役后按 F9 或点击顶栏「◆ 修改器」即可打开面板。')

    def show_log(self):
        messagebox.showinfo('操作日志', '\n'.join(self.logs) if self.logs else '暂无操作记录。')


if __name__ == '__main__':
    if sys.argv[1:2] == ['--smoke']:
        smoke_test(sys.argv[2])
    else:
        ui.enable_dpi_awareness()
        Installer().mainloop()
