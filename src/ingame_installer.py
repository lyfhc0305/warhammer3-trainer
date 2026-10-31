"""Standalone installer for the bundled native campaign panel (dark-gold wizard)."""
import json
import os
import sys
import tempfile
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
from trainer_core import BUNDLE, GAME, HOME, PACK_NAME, REFERENCE, VERSION, install, uninstall, game_path, owned
from game_compat import check as check_compatibility
from pack_io import read_pack, db_header
import ui_kit as ui
from ui_kit import THEME, px, launch_game_or_dir

ADDON_SPECS = [
    ('building', '建筑快捷升级菜单', '复用 cpecific 快捷建造菜单脚本'),
    ('ai', 'AI 将军与部队托管', '复用 Pancake 战斗内 AI 托管界面'),
    ('cap', '全局战斗回血上限', '默认回血池为最大生命的 50 倍，也影响敌方'),
    ('agent_ai', '敌方间谍成功率减益', '降低 AI 事务官行动成功率（-100）'),
    ('shop', '基斯里夫祈祷神器奖励', '支持军队／行省四神祈祷，启动时获神器；保留原生消耗与效果'),
    ('ballistic', '旧版全局弹道 / 爆炸覆盖', '兼容移植旧数值，含加强/削弱且影响敌方；旧独有键跳过'),
]


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
    """The bundled Pack while it matches the game, otherwise one built from the game as installed."""
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


def detect_installed_mode(game_dir):
    try:
        g = game_path(game_dir)
    except Exception as exc:
        return '游戏目录无效', str(exc), THEME['red'], 'error'
    target = g / 'data' / PACK_NAME
    if not target.is_file():
        return '尚未安装', f'已定位游戏目录：{g}。点击「一键安装」即可完成部署。', THEME['gold'], 'info'
    try:
        owned(target)
        status = check_compatibility(g, target)
        if not status.usable:
            title = '修改器包需要更新' if status.state == 'unknown' else '游戏已更新，需要重新适配'
            return title, status.describe() + '请重新适配并安装；暂时无法适配时先卸载，保证游戏能启动。', THEME['gold'], 'stale'
        entries = read_pack(target, lambda n: n == 'wh3pt_mode.txt')
        if entries.get('wh3pt_mode.txt') == b'ingame-v2':
            installed = status.manifest.get('trainer_version')
            if installed != VERSION:
                return f'可更新到 {VERSION}', f'当前安装的是 {installed}。点击「一键安装」更新。', THEME['gold'], 'info'
            return '已安装游戏内面板', '一切就绪：进入单人战役后按 F9，或点击顶栏「◆ 修改器」呼出面板。', THEME['green'], 'ok'
        return '已安装静态配置包', '当前安装的是战前静态配置包；重新安装即可切换为游戏内实时面板。', THEME['gold'], 'info'
    except Exception as exc:
        return '检测到同名冲突文件', str(exc), THEME['red'], 'error'


class Installer(tk.Tk):
    def __init__(self):
        super().__init__()
        ui.setup(self)
        self.title('全面战争：战锤 III 修改器 · 安装器')
        self.minsize(px(720), px(560))
        ui.center_window(self, px(820), px(720))
        self.logs = []
        self._installed_kind = 'info'
        self.path = tk.StringVar(value=GAME)
        self.addons = {k: tk.BooleanVar(value=False) for k, _, _ in ADDON_SPECS}

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
        ui.AddonGrid(self._addon_body, ADDON_SPECS, self.addons).pack(fill='x')
        for var in self.addons.values():
            var.trace_add('write', lambda *_: self._update_addon_summary())

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

        self.refresh_status()

    # -------------------------------------------------------------- toggles
    def _update_addon_summary(self):
        n = sum(v.get() for v in self.addons.values())
        self.addon_summary.set(f'已选 {n} / {len(ADDON_SPECS)}' if n else '纯净面板', 'gold' if n else 'neutral')

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

    # ---------------------------------------------------------------- state
    def _draw_badge(self, kind, title=None, desc=None):
        self._installed_kind = kind
        if title is not None:
            self.hero.set_state(kind, title, desc or '')
        if kind == 'ok':
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
        title, desc, _colour, kind = detect_installed_mode(self.path.get())
        self._draw_badge(kind, title, desc)

    # -------------------------------------------------------------- actions
    def verify_only(self):
        try:
            verify_game(self.path.get())
            title, desc, _colour, kind = detect_installed_mode(self.path.get())
            self._draw_badge(kind, title if kind == 'stale' else '安装包与游戏表校验通过', desc)
            self.log('验证通过：' + self.path.get())
        except Exception as exc:
            self._draw_badge('error', '验证未通过', str(exc))
            messagebox.showerror('验证失败', str(exc))

    def deploy(self):
        try:
            chosen = {k: v.get() for k, v in self.addons.items() if v.get()}
            target = install(self.path.get(), prepare_payload(self.path.get(), chosen), HOME / 'history')
            note = f'（含 {len(chosen)} 项附加模块）' if chosen else '（纯净面板）'
            self._draw_badge('ok', '已安装游戏内面板',
                             f'已写入并通过回读校验{note}。点击「启动游戏」，进入单人战役后按 F9 打开面板。')
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
            self._draw_badge('info', '已卸载修改器' if removed else '未找到已安装文件',
                             '已安全移除并备份配置包。' if removed else '当前游戏目录未找到本修改器的包。')
            self.log('卸载完成' if removed else '无需卸载')
        except Exception as exc:
            messagebox.showerror('卸载失败', str(exc))

    def open_game(self):
        self.refresh_status()
        if self._installed_kind == 'stale':
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
