"""Shared Tkinter UI kit for the WH3 trainer desktop apps.

Design language: dark obsidian surfaces, imperial-gold accent, flat cards with
anti-aliased rounded controls. All controls are DPI aware: pixel sizes go
through ``px()`` and fonts use point sizes that Tk scales automatically once
the process is DPI aware (see ``enable_dpi_awareness``).

Rounded shapes are rendered to small RGBA PNGs in pure Python (no Pillow), so
the frozen executables stay small and everything stays crisp at 125%–200%.
"""
import base64
import math
import os
import struct
import zlib
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont

FONT = 'Microsoft YaHei UI'
MONO = 'Consolas'

# Design tokens. The legacy keys (bg/card/raise/gold/red/...) are kept because
# ingame_installer.detect_installed_mode and older callers rely on them.
THEME = {
    'bg': '#15110e',
    'side': '#0f0c0a',
    'card': '#1d1814',
    'raise': '#28211a',
    'hover': '#30281f',
    'divider': '#2a231c',
    'border': '#362c22',
    'frame': '#56432f',
    'ink': '#f3ebe0',
    'muted': '#c0af98',
    'quiet': '#8a7a69',
    'faint': '#5f5347',
    'gold': '#e2b04f',
    'blue': '#e2b04f',
    'gold_hover': '#efc46c',
    'gold_ink': '#f3c86f',
    'gold_dim': '#3a2b16',
    'primary_ink': '#1b1207',
    'sel_fill': '#2a1f13',
    'sel_line': '#a67c32',
    'green': '#7ec89a',
    'ok_bg': '#16261b',
    'info_bg': '#33261a',
    'red': '#e8766e',
    'danger_bg': '#321918',
    'danger_hover': '#43201e',
    'danger_line': '#7d3530',
    'input_bg': '#110e0b',
    'track': '#3a3027',
}

_SCALE = [1.0]


def enable_dpi_awareness():
    """Make text crisp on high-DPI screens; must run before the first Tk()."""
    if os.name != 'nt':
        return
    try:
        import ctypes
    except Exception:
        return
    for attempt in (
        lambda: ctypes.windll.shcore.SetProcessDpiAwareness(1),
        lambda: ctypes.windll.user32.SetProcessDPIAware(),
    ):
        try:
            attempt()
            return
        except Exception:
            continue


def px(value):
    """Scale a 96-DPI design pixel value to the current screen."""
    return int(round(value * _SCALE[0]))


def scale():
    return _SCALE[0]


def setup(root):
    """Initialise scale, fonts and image cache for a Tk root. Call once per root."""
    try:
        dpi = float(root.winfo_fpixels('1i'))
    except Exception:
        dpi = 96.0
    _SCALE[0] = max(1.0, min(3.0, dpi / 96.0))
    root.configure(bg=THEME['bg'])
    root.option_add('*Font', (FONT, 10))
    root._kit_images = {}
    root._kit_fonts = {
        'xs': tkfont.Font(root, family=FONT, size=8),
        'sm': tkfont.Font(root, family=FONT, size=9),
        'sm_b': tkfont.Font(root, family=FONT, size=9, weight='bold'),
        'body': tkfont.Font(root, family=FONT, size=10),
        'body_b': tkfont.Font(root, family=FONT, size=10, weight='bold'),
        'em': tkfont.Font(root, family=FONT, size=11, weight='bold'),
        'h2': tkfont.Font(root, family=FONT, size=13, weight='bold'),
        'h1': tkfont.Font(root, family=FONT, size=16, weight='bold'),
        'mono': tkfont.Font(root, family=MONO, size=10),
    }
    families = set(tkfont.families(root))
    icon_family = next((f for f in ('Segoe Fluent Icons', 'Segoe MDL2 Assets') if f in families), None)
    root._kit_icon_family = icon_family
    if icon_family:
        root._kit_fonts['icon'] = tkfont.Font(root, family=icon_family, size=11)
        root._kit_fonts['icon_sm'] = tkfont.Font(root, family=icon_family, size=9)
        root._kit_fonts['icon_lg'] = tkfont.Font(root, family=icon_family, size=18)
    return root


def font(widget, name):
    return widget.winfo_toplevel()._kit_fonts[name]


# Segoe Fluent / MDL2 code points with plain-text fallbacks.
ICONS = {
    'game': ('\ue7fc', '◆'),
    'globe': ('\ue909', '◎'),
    'shield': ('\uea18', '◈'),
    'health': ('\ue95e', '✚'),
    'tune': ('\ue9e9', '≡'),
    'star': ('\ue734', '☆'),
    'star_on': ('\ue735', '★'),
    'check': ('\ue73e', '✓'),
    'list': ('\ue8fd', '≡'),
    'doc': ('\ue7c3', '▤'),
    'settings': ('\ue713', '⚙'),
    'search': ('\ue721', '⌕'),
    'close': ('\ue711', '×'),
    'folder': ('\ue8b7', '▣'),
    'play': ('\ue768', '▶'),
    'download': ('\ue896', '↓'),
    'delete': ('\ue74d', '×'),
    'refresh': ('\ue72c', '↻'),
    'info': ('\ue946', 'i'),
    'chevron_down': ('\ue70d', '▾'),
    'chevron_up': ('\ue70e', '▴'),
    'warning': ('\ue7ba', '!'),
    'error': ('\uea39', '!'),
    'accept': ('\ue8fb', '✓'),
    'package': ('\ue7b8', '▦'),
    'flash': ('\ue945', '⚡'),
}


def icon_text(widget, name):
    """Return (text, font) for an icon, falling back to a Unicode glyph."""
    root = widget.winfo_toplevel()
    glyph, fallback = ICONS[name]
    if getattr(root, '_kit_icon_family', None):
        return glyph, root._kit_fonts['icon']
    return fallback, root._kit_fonts['body_b']


# ---------------------------------------------------------------------------
# Anti-aliased shape rendering (pure Python PNG)
# ---------------------------------------------------------------------------

def _rgb(colour):
    colour = colour.lstrip('#')
    return tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))


def _rrect(x0, y0, x1, y1, r):
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    hw, hh = (x1 - x0) / 2.0, (y1 - y0) / 2.0
    r = max(0.0, min(r, hw, hh))
    inner = ((x0 + r + 1, y0 + 1, x1 - r - 1, y1 - 1), (x0 + 1, y0 + r + 1, x1 - 1, y1 - r - 1))
    outer = (x0 - 1, y0 - 1, x1 + 1, y1 + 1)

    def sdf(px_, py_):
        qx = abs(px_ - cx) - hw + r
        qy = abs(py_ - cy) - hh + r
        return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r
    return sdf, inner, outer


def _circle(cx, cy, rad):
    k = rad * 0.7071 - 1
    inner = ((cx - k, cy - k, cx + k, cy + k),)
    outer = (cx - rad - 1, cy - rad - 1, cx + rad + 1, cy + rad + 1)
    return (lambda px_, py_: math.hypot(px_ - cx, py_ - cy) - rad), inner, outer


def _render(w, h, layers):
    """Composite (shape, colour, opacity) layers into RGBA PNG bytes."""
    prepared = [(shape, _rgb(colour), opacity) for shape, colour, opacity in layers]
    rows = []
    for y in range(h):
        py_ = y + 0.5
        row = bytearray(1 + w * 4)
        for x in range(w):
            px_ = x + 0.5
            r = g = b = a = 0.0
            for (sdf, inner, outer), (cr, cg, cb), opacity in prepared:
                if not (outer[0] <= px_ <= outer[2] and outer[1] <= py_ <= outer[3]):
                    continue
                if any(bx0 <= px_ <= bx1 and by0 <= py_ <= by1 for bx0, by0, bx1, by1 in inner):
                    cov = 1.0
                else:
                    cov = 0.5 - sdf(px_, py_)
                    if cov <= 0.0:
                        continue
                    if cov > 1.0:
                        cov = 1.0
                cov *= opacity
                r = cr * cov + r * (1 - cov)
                g = cg * cov + g * (1 - cov)
                b = cb * cov + b * (1 - cov)
                a = cov + a * (1 - cov)
            if a > 0.0:
                i = 1 + x * 4
                row[i] = min(255, int(r / a + 0.5))
                row[i + 1] = min(255, int(g / a + 0.5))
                row[i + 2] = min(255, int(b / a + 0.5))
                row[i + 3] = min(255, int(a * 255 + 0.5))
        rows.append(bytes(row))

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

    header = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
            + chunk(b'IDAT', zlib.compress(b''.join(rows), 6)) + chunk(b'IEND', b''))


def _image(widget, key, w, h, layers_fn):
    root = widget.winfo_toplevel()
    cache = root._kit_images
    full_key = (key, w, h)
    if full_key not in cache:
        data = base64.b64encode(_render(w, h, layers_fn(w, h))).decode('ascii')
        cache[full_key] = tk.PhotoImage(master=root, data=data, format='png')
    return cache[full_key]


def rounded_box(widget, w, h, radius, fill=None, border=None, border_w=1.0):
    """Rounded rectangle image with optional 1px border; transparent outside."""
    bw = border_w * scale()

    def layers(w_, h_):
        out = []
        if border:
            out.append((_rrect(0, 0, w_, h_, radius), border, 1.0))
            if fill:
                out.append((_rrect(bw, bw, w_ - bw, h_ - bw, max(0.0, radius - bw)), fill, 1.0))
        elif fill:
            out.append((_rrect(0, 0, w_, h_, radius), fill, 1.0))
        return out
    return _image(widget, ('box', radius, fill, border, bw), w, h, layers)


def disc(widget, d, fill, ring=None, ring_w=2.0):
    rw = ring_w * scale()

    def layers(w_, h_):
        c = d / 2.0
        out = []
        if ring:
            out.append((_circle(c, c, c), ring, 1.0))
            out.append((_circle(c, c, c - rw), fill, 1.0))
        else:
            out.append((_circle(c, c, c), fill, 1.0))
        return out
    return _image(widget, ('disc', fill, ring, rw), d, d, layers)


def switch_image(widget, on, hover=False, disabled=False):
    w, h = px(38), px(22)
    if disabled:
        track, knob = THEME['divider'], THEME['faint']
    elif on:
        track, knob = (THEME['gold_hover'] if hover else THEME['gold']), THEME['primary_ink']
    else:
        track, knob = ('#463a2f' if hover else THEME['track']), THEME['muted']

    def layers(w_, h_):
        r = h_ / 2.0
        pad = max(3.0, h_ * 0.16)
        kr = r - pad
        kx = (w_ - r) if on else r
        return [(_rrect(0, 0, w_, h_, r), track, 1.0), (_circle(kx, r, kr), knob, 1.0)]
    return _image(widget, ('switch', on, hover, disabled), w, h, layers)


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

def bg_of(widget):
    try:
        return widget.cget('bg')
    except Exception:
        return THEME['bg']


class Button(tk.Label):
    """Rounded, hover-aware push button rendered as image + centred text."""
    VARIANTS = {
        'primary': ('gold', 'gold_hover', 'primary_ink', None),
        'secondary': ('raise', 'hover', 'ink', 'frame'),
        'ghost': (None, 'raise', 'muted', None),
        'danger': ('danger_bg', 'danger_hover', 'red', 'danger_line'),
        'chip': ('raise', 'hover', 'muted', 'border'),
        'chip_on': ('gold_dim', 'sel_fill', 'gold_ink', 'sel_line'),
    }
    SIZES = {'sm': (28, 10, 'sm'), 'md': (34, 14, 'body_b'), 'lg': (42, 20, 'em')}

    def __init__(self, parent, text, command=None, variant='secondary', size='md', icon=None,
                 min_width=0, tooltip=None):
        super().__init__(parent, bd=0, highlightthickness=0, padx=0, pady=0, compound='center',
                         cursor='hand2', bg=bg_of(parent))
        self.command = command
        self.variant = variant
        self.size = size
        self.icon = icon
        self.min_width = min_width
        self.text_value = text
        self.enabled = True
        self.hover = False
        self.pressed = False
        self.bind('<Enter>', self._enter)
        self.bind('<Leave>', self._leave)
        self.bind('<ButtonPress-1>', self._press)
        self.bind('<ButtonRelease-1>', self._release)
        if tooltip:
            Tooltip(self, tooltip)
        self._render()

    def _render(self):
        h, pad, font_name = self.SIZES[self.size]
        f = font(self, font_name)
        # A Tk label renders with one font, so symbol-font icons are not mixed into text buttons.
        label = self.text_value
        text_w = f.measure(label or '')
        w = max(px(self.min_width), text_w + px(pad) * 2, px(h))
        w = int(math.ceil(w / px(4)) * px(4))
        hh = px(h)
        fill, hover_fill, fg, border = self.VARIANTS[self.variant]
        if not self.enabled:
            fill_c = THEME['card'] if fill else None
            border_c = THEME['border'] if border or fill else None
            fg_c = THEME['faint']
        else:
            fill_key = hover_fill if (self.hover and not self.pressed) else fill
            if self.pressed:
                fill_key = fill or hover_fill
            fill_c = THEME[fill_key] if fill_key else None
            border_c = THEME[border] if border else None
            fg_c = THEME[fg] if not (self.variant == 'ghost' and self.hover) else THEME['ink']
        img = rounded_box(self, w, hh, px(7), fill=fill_c, border=border_c)
        self.configure(image=img, text=label, fg=fg_c, font=f,
                       cursor='hand2' if self.enabled else 'arrow')

    def _enter(self, _e=None):
        self.hover = True
        self._render()

    def _leave(self, _e=None):
        self.hover = False
        self.pressed = False
        self._render()

    def _press(self, _e=None):
        if self.enabled:
            self.pressed = True
            self._render()

    def _release(self, event=None):
        was = self.pressed
        self.pressed = False
        self._render()
        if not (was and self.enabled and self.command):
            return
        if event is not None:
            x, y = event.x, event.y
            if not (0 <= x <= self.winfo_width() and 0 <= y <= self.winfo_height()):
                return
        self.command()

    def invoke(self):
        if self.enabled and self.command:
            self.command()

    def set_text(self, text):
        self.text_value = text
        self._render()

    def set_variant(self, variant):
        self.variant = variant
        self._render()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        self._render()

    def set_command(self, command):
        self.command = command


class IconButton(tk.Label):
    """Borderless glyph button (star, close, clear...)."""

    def __init__(self, parent, icon, command=None, fg=None, hover_fg=None, tooltip=None, size='icon'):
        glyph, f = icon_text(parent, icon)
        if size == 'icon_sm' and getattr(parent.winfo_toplevel(), '_kit_icon_family', None):
            f = font(parent, 'icon_sm')
        super().__init__(parent, text=glyph, font=f, bg=bg_of(parent), fg=fg or THEME['quiet'],
                         cursor='hand2', bd=0, padx=px(4), pady=px(2))
        self.icon = icon
        self.command = command
        self.base_fg = fg or THEME['quiet']
        self.hover_fg = hover_fg or THEME['ink']
        self.bind('<Enter>', lambda _e: self.configure(fg=self.hover_fg))
        self.bind('<Leave>', lambda _e: self.configure(fg=self.base_fg))
        self.bind('<ButtonRelease-1>', self._click)
        if tooltip:
            Tooltip(self, tooltip)

    def _click(self, event):
        if 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height() and self.command:
            self.command()

    def set_icon(self, icon, fg=None):
        glyph, _f = icon_text(self, icon)
        self.icon = icon
        if fg:
            self.base_fg = fg
        self.configure(text=glyph, fg=self.base_fg)


class Switch(tk.Label):
    """iOS-style toggle bound to a BooleanVar."""

    def __init__(self, parent, variable, command=None):
        super().__init__(parent, bd=0, highlightthickness=0, padx=0, pady=0, bg=bg_of(parent), cursor='hand2')
        self.variable = variable
        self.command = command
        self.hover = False
        self.enabled = True
        self.bind('<Enter>', lambda _e: self._set_hover(True))
        self.bind('<Leave>', lambda _e: self._set_hover(False))
        self.bind('<ButtonRelease-1>', self._click)
        self._trace = variable.trace_add('write', lambda *_: self._render())
        self._render()

    def _set_hover(self, value):
        self.hover = value
        self._render()

    def _render(self):
        try:
            on = bool(self.variable.get())
        except Exception:
            on = False
        self.configure(image=switch_image(self, on, self.hover, not self.enabled))

    def _click(self, event):
        if not self.enabled:
            return
        if 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
            self.toggle()

    def toggle(self):
        self.variable.set(not self.variable.get())
        if self.command:
            self.command()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        self.configure(cursor='hand2' if enabled else 'arrow')
        self._render()

    def destroy(self):
        try:
            self.variable.trace_remove('write', self._trace)
        except Exception:
            pass
        super().destroy()


class Slider(tk.Canvas):
    """Flat slider: thin track, gold fill and a round knob. Emits on user drag only."""

    def __init__(self, parent, minimum, maximum, on_user_change, width=170):
        self.h = px(24)
        super().__init__(parent, width=px(width), height=self.h, bg=bg_of(parent),
                         highlightthickness=0, bd=0, cursor='hand2')
        self.minimum = float(minimum)
        self.maximum = float(maximum)
        self.on_user_change = on_user_change
        self.value = self.minimum
        self.active = False
        self.knob_d = px(16)
        self.pad = self.knob_d // 2 + 1
        self.bind('<Configure>', lambda _e: self.redraw())
        self.bind('<ButtonPress-1>', self._drag)
        self.bind('<B1-Motion>', self._drag)

    def set_value(self, value):
        try:
            self.value = max(self.minimum, min(self.maximum, float(value)))
        except (TypeError, ValueError):
            pass
        self.redraw()

    def set_active(self, active):
        self.active = bool(active)
        self.redraw()

    def _fraction(self):
        span = self.maximum - self.minimum
        return 0.0 if span <= 0 else (self.value - self.minimum) / span

    def redraw(self):
        self.delete('all')
        w = max(self.winfo_width(), int(self.cget('width')))
        if w <= 2 * self.pad:
            return
        cy = self.h // 2
        x0, x1 = self.pad, w - self.pad
        th = max(2, px(4))
        self.create_rectangle(x0, cy - th // 2, x1, cy - th // 2 + th, fill=THEME['track'], width=0)
        kx = x0 + (x1 - x0) * self._fraction()
        fill = THEME['gold'] if self.active else THEME['frame']
        self.create_rectangle(x0, cy - th // 2, kx, cy - th // 2 + th, fill=fill, width=0)
        knob = disc(self, self.knob_d, THEME['ink'] if self.active else THEME['muted'],
                    ring=THEME['gold'] if self.active else THEME['frame'], ring_w=2.5)
        self.create_image(kx, cy, image=knob)

    def _drag(self, event):
        w = self.winfo_width()
        x0, x1 = self.pad, w - self.pad
        if x1 <= x0:
            return
        frac = max(0.0, min(1.0, (event.x - x0) / float(x1 - x0)))
        value = self.minimum + frac * (self.maximum - self.minimum)
        self.on_user_change(value)


class NumberField(tk.Frame):
    """Bordered numeric entry with unit suffix and invalid/focus states."""

    def __init__(self, parent, variable, unit='', width=7, on_user_edit=None, step=1, minimum=0, maximum=100):
        super().__init__(parent, bg=THEME['input_bg'], highlightthickness=1,
                         highlightbackground=THEME['border'], highlightcolor=THEME['sel_line'])
        self.variable = variable
        self.on_user_edit = on_user_edit
        self.step = step
        self.minimum = minimum
        self.maximum = maximum
        self.invalid = False
        self.entry = tk.Entry(self, textvariable=variable, width=width, justify='right', bd=0,
                              bg=THEME['input_bg'], fg=THEME['ink'], insertbackground=THEME['gold'],
                              disabledbackground=THEME['input_bg'], font=font(parent, 'body_b'),
                              relief='flat', highlightthickness=0)
        self.entry.pack(side='left', padx=(px(8), px(2)), pady=px(5))
        self.unit_lbl = tk.Label(self, text=unit, bg=THEME['input_bg'], fg=THEME['quiet'], font=font(parent, 'sm'),
                                 width=6, anchor='w')
        if unit:
            self.unit_lbl.pack(side='left', padx=(0, px(8)))
        self.entry.bind('<FocusIn>', lambda _e: self._border())
        self.entry.bind('<FocusOut>', lambda _e: self._border())
        self.entry.bind('<KeyRelease>', self._key)
        self.entry.bind('<Up>', lambda _e: self._nudge(1))
        self.entry.bind('<Down>', lambda _e: self._nudge(-1))
        for w in (self, self.unit_lbl):
            w.bind('<Button-1>', lambda _e: self.entry.focus_set())

    def _border(self):
        if self.invalid:
            colour = THEME['red']
        elif self.focus_get() is self.entry:
            colour = THEME['sel_line']
        else:
            colour = THEME['border']
        self.configure(highlightbackground=colour, highlightcolor=colour)

    def set_invalid(self, invalid):
        self.invalid = bool(invalid)
        self._border()

    def _key(self, event):
        if event.keysym in ('Up', 'Down', 'Tab', 'Shift_L', 'Shift_R', 'Control_L', 'Control_R', 'Left', 'Right'):
            return
        if self.on_user_edit:
            self.on_user_edit()

    def _nudge(self, direction):
        try:
            cur = float(self.variable.get())
        except ValueError:
            cur = float(self.minimum)
        nxt = max(self.minimum, min(self.maximum, cur + direction * self.step))
        self.variable.set(fmt_number(nxt))
        if self.on_user_edit:
            self.on_user_edit()
        return 'break'


def fmt_number(value):
    value = float(value)
    if value == int(value):
        return str(int(value))
    return f'{value:g}'


class Tooltip:
    """Small dark tooltip shown after a short hover delay."""

    def __init__(self, widget, text, delay=450):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tip = None
        self.after_id = None
        widget.bind('<Enter>', self._schedule, add='+')
        widget.bind('<Leave>', self.hide, add='+')
        widget.bind('<ButtonPress>', self.hide, add='+')

    def _schedule(self, _e=None):
        self._cancel()
        if self.text:
            self.after_id = self.widget.after(self.delay, self.show)

    def _cancel(self):
        if self.after_id:
            try:
                self.widget.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None

    def show(self):
        if self.tip or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + px(8)
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + px(6)
            self.tip = tw = tk.Toplevel(self.widget)
            tw.wm_overrideredirect(True)
            tw.configure(bg=THEME['frame'])
            tk.Label(tw, text=self.text, justify='left', bg=THEME['side'], fg=THEME['ink'],
                     padx=px(10), pady=px(7), wraplength=px(380), font=font(self.widget, 'sm')).pack(padx=1, pady=1)
            tw.wm_geometry(f'+{x}+{y}')
        except Exception:
            self.tip = None

    def hide(self, _e=None):
        self._cancel()
        if self.tip is not None:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


class EllipsisLabel(tk.Label):
    """Single-line label that truncates with '…' to the available width."""

    def __init__(self, parent, text='', **kw):
        kw.setdefault('anchor', 'w')
        kw.setdefault('bg', bg_of(parent))
        super().__init__(parent, width=1, **kw)
        self.full_text = text
        self._last_w = None
        self.bind('<Configure>', self._fit)
        self.configure(text=text)

    def set_text(self, text, fg=None):
        self.full_text = text
        if fg:
            self.configure(fg=fg)
        self._last_w = None
        self._fit()

    def _fit(self, _e=None):
        w = self.winfo_width()
        if w <= 1 or w == self._last_w:
            if w <= 1:
                self.configure(text=self.full_text)
            return
        self._last_w = w
        f = tkfont.Font(font=self.cget('font'))
        text = self.full_text
        if f.measure(text) <= w - 2:
            self.configure(text=text)
            return
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if f.measure(text[:mid] + '…') <= w - 2:
                lo = mid
            else:
                hi = mid - 1
        self.configure(text=text[:lo].rstrip() + '…')


class ScrollArea(tk.Frame):
    """Vertical scroll container with a slim auto-hiding scrollbar."""

    def __init__(self, parent, bg=None, padx=0):
        bg = bg or THEME['bg']
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.bar = tk.Canvas(self, width=px(10), bg=bg, highlightthickness=0, bd=0)
        self.inner = tk.Frame(self.canvas, bg=bg, padx=padx)
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.bar.pack(side='right', fill='y')
        self.inner.bind('<Configure>', self._inner_changed)
        self.canvas.bind('<Configure>', self._canvas_changed)
        self.bar.bind('<Configure>', lambda _e: self._draw_bar())
        self.bar.bind('<ButtonPress-1>', self._bar_press)
        self.bar.bind('<B1-Motion>', self._bar_drag)
        self.bind('<Enter>', self._bind_wheel)
        self.bind('<Leave>', self._unbind_wheel)
        self._drag_offset = 0
        self._bar_hover = False
        self.bar.bind('<Enter>', lambda _e: self._set_bar_hover(True))
        self.bar.bind('<Leave>', lambda _e: self._set_bar_hover(False))

    def _set_bar_hover(self, value):
        self._bar_hover = value
        self._draw_bar()

    def _inner_changed(self, _e=None):
        self.canvas.configure(scrollregion=(0, 0, self.inner.winfo_reqwidth(), self.inner.winfo_reqheight()))
        self._draw_bar()

    def _canvas_changed(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)
        self._draw_bar()

    def _bind_wheel(self, _e=None):
        self.bind_all('<MouseWheel>', self._wheel)

    def _unbind_wheel(self, event=None):
        if event is not None:
            w = self.winfo_containing(event.x_root, event.y_root)
            while w is not None:
                if w is self:
                    return
                w = getattr(w, 'master', None)
        self.unbind_all('<MouseWheel>')

    def _wheel(self, event):
        if self.inner.winfo_reqheight() <= self.canvas.winfo_height():
            return
        self.canvas.yview_scroll(int(-event.delta / 120) * 3, 'units')
        self._draw_bar()

    def _geometry(self):
        total = max(1, self.inner.winfo_reqheight())
        view = max(1, self.canvas.winfo_height())
        top, _bottom = self.canvas.yview()
        return total, view, top

    def _draw_bar(self):
        self.bar.delete('all')
        total, view, top = self._geometry()
        if total <= view + 1:
            return
        h = self.bar.winfo_height()
        thumb = max(px(36), int(h * view / total))
        y0 = int((h - thumb) * (top / max(1e-6, 1 - view / total)))
        y0 = max(0, min(h - thumb, y0))
        colour = THEME['sel_line'] if self._bar_hover else THEME['frame']
        x0, x1 = px(3), px(7)
        self.bar.create_rectangle(x0, y0 + px(2), x1, y0 + thumb - px(2), fill=colour, width=0)
        self._thumb = (y0, thumb)

    def _bar_press(self, event):
        y0, thumb = getattr(self, '_thumb', (0, 0))
        if y0 <= event.y <= y0 + thumb:
            self._drag_offset = event.y - y0
        else:
            self._drag_offset = thumb // 2
            self._bar_drag(event)

    def _bar_drag(self, event):
        total, view, _top = self._geometry()
        if total <= view:
            return
        h = self.bar.winfo_height()
        _y0, thumb = getattr(self, '_thumb', (0, px(36)))
        frac = (event.y - self._drag_offset) / max(1, h - thumb)
        frac = max(0.0, min(1.0, frac))
        self.canvas.yview_moveto(frac * (1 - view / total))
        self._draw_bar()

    def scroll_top(self):
        self.canvas.yview_moveto(0)
        self._draw_bar()


class SearchBox(tk.Frame):
    """Search entry with magnifier glyph, placeholder and clear button."""

    def __init__(self, parent, variable, placeholder='搜索', width=26):
        super().__init__(parent, bg=THEME['input_bg'], highlightthickness=1,
                         highlightbackground=THEME['border'], highlightcolor=THEME['sel_line'])
        self.variable = variable
        self.placeholder = placeholder
        glyph, f = icon_text(parent, 'search')
        if getattr(parent.winfo_toplevel(), '_kit_icon_family', None):
            f = font(parent, 'icon_sm')
        tk.Label(self, text=glyph, font=f, bg=THEME['input_bg'], fg=THEME['quiet']).pack(side='left', padx=(px(10), px(4)))
        self.entry = tk.Entry(self, textvariable=variable, width=width, bd=0, relief='flat',
                              bg=THEME['input_bg'], fg=THEME['ink'], insertbackground=THEME['gold'],
                              font=font(parent, 'body'), highlightthickness=0)
        self.entry.pack(side='left', fill='x', expand=True, pady=px(7))
        self.hint = tk.Label(self, text=placeholder, bg=THEME['input_bg'], fg=THEME['faint'], font=font(parent, 'body'))
        self.clear_btn = IconButton(self, 'close', command=lambda: (variable.set(''), self.entry.focus_set()),
                                    size='icon_sm')
        self.clear_btn.configure(bg=THEME['input_bg'])
        self.hint.bind('<Button-1>', lambda _e: self.entry.focus_set())
        self.entry.bind('<FocusIn>', lambda _e: self._sync())
        self.entry.bind('<FocusOut>', lambda _e: self._sync())
        self.entry.bind('<Escape>', lambda _e: variable.set(''))
        self._trace = variable.trace_add('write', lambda *_: self._sync())
        self._sync()

    def _sync(self):
        has_text = bool(self.variable.get())
        if has_text:
            self.hint.place_forget()
            self.clear_btn.pack(side='right', padx=(0, px(6)))
        else:
            self.clear_btn.pack_forget()
            self.hint.place(in_=self.entry, x=0, rely=0.5, anchor='w')

    def focus(self):
        self.entry.focus_set()
        self.entry.select_range(0, 'end')

    def destroy(self):
        try:
            self.variable.trace_remove('write', self._trace)
        except Exception:
            pass
        super().destroy()


# ---------------------------------------------------------------------------
# Composite building blocks
# ---------------------------------------------------------------------------

def card(parent, padx=16, pady=14, bg=None, border=None):
    return tk.Frame(parent, bg=bg or THEME['card'], highlightthickness=1,
                    highlightbackground=border or THEME['border'], padx=px(padx), pady=px(pady))


def label(parent, text='', style='body', fg=None, bg=None, **kw):
    colours = {'h1': 'ink', 'h2': 'ink', 'em': 'ink', 'body': 'ink', 'body_b': 'ink',
               'sm': 'quiet', 'sm_b': 'quiet', 'xs': 'quiet', 'mono': 'ink'}
    return tk.Label(parent, text=text, font=font(parent, style), bg=bg or bg_of(parent),
                    fg=fg or THEME[colours.get(style, 'ink')], **kw)


def section_title(parent, text, right=None):
    row = tk.Frame(parent, bg=bg_of(parent))
    tk.Label(row, text=text, font=font(parent, 'sm_b'), bg=bg_of(parent), fg=THEME['quiet']).pack(side='left')
    if right is not None:
        right(row)
    return row


def divider(parent, bg=None, pady=0):
    line = tk.Frame(parent, bg=bg or THEME['divider'], height=1)
    return line


def set_bg(widget, colour, skip=()):
    """Recursively recolour a widget subtree (skipping inputs and given widgets)."""
    if widget in skip or getattr(widget, '_keep_bg', False):
        return
    try:
        widget.configure(bg=colour)
    except Exception:
        pass
    for child in widget.winfo_children():
        set_bg(child, colour, skip)


def is_descendant(widget, ancestor):
    while widget is not None:
        if widget is ancestor:
            return True
        widget = getattr(widget, 'master', None)
    return False


class Badge(tk.Label):
    """Small pill label (counts, states)."""
    KINDS = {
        'neutral': ('raise', 'muted'),
        'gold': ('gold_dim', 'gold_ink'),
        'ok': ('ok_bg', 'green'),
        'danger': ('danger_bg', 'red'),
        'info': ('info_bg', 'gold'),
    }

    def __init__(self, parent, text='', kind='neutral', style='sm_b'):
        super().__init__(parent, bd=0, padx=0, pady=0, compound='center', bg=bg_of(parent))
        self.style = style
        self.kind = kind
        self.text_value = text
        self._render()

    def _render(self):
        fill, fg = self.KINDS[self.kind]
        f = font(self, self.style)
        if not self.text_value:
            self.configure(image='', text='')
            return
        h = px(22)
        w = max(h, f.measure(self.text_value) + px(18))
        w = int(math.ceil(w / px(2)) * px(2))
        img = rounded_box(self, w, h, h / 2.0, fill=THEME[fill])
        self.configure(image=img, text=self.text_value, fg=THEME[fg], font=f)

    def set(self, text, kind=None):
        self.text_value = text
        if kind:
            self.kind = kind
        self._render()


STATUS_STYLE = {
    'ok': ('green', 'ok_bg', 'accept'),
    'info': ('gold', 'info_bg', 'info'),
    'stale': ('gold', 'info_bg', 'warning'),
    'error': ('red', 'danger_bg', 'warning'),
}


class StatusIcon(tk.Label):
    """Round status medallion (ok / info / error)."""

    def __init__(self, parent, d=52):
        super().__init__(parent, bd=0, padx=0, pady=0, compound='center', bg=bg_of(parent))
        self.d = d
        self.set('info')

    def set(self, kind):
        colour, tint, icon = STATUS_STYLE.get(kind, STATUS_STYLE['info'])
        img = disc(self, px(self.d), THEME[tint], ring=THEME[colour], ring_w=2)
        glyph, f = icon_text(self, icon)
        if getattr(self.winfo_toplevel(), '_kit_icon_family', None):
            f = font(self, 'icon_lg')
        self.configure(image=img, text=glyph, font=f, fg=THEME[colour])


class StatusHero(tk.Frame):
    """Install status card: medallion, title, description and action buttons."""

    def __init__(self, parent):
        super().__init__(parent, bg=THEME['card'], highlightthickness=1,
                         highlightbackground=THEME['border'], padx=px(22), pady=px(20))
        top = tk.Frame(self, bg=THEME['card'])
        top.pack(fill='x')
        self.icon = StatusIcon(top)
        self.icon.pack(side='left', anchor='n', padx=(0, px(16)))
        text = tk.Frame(top, bg=THEME['card'])
        text.pack(side='left', fill='x', expand=True)
        self.eyebrow = tk.Label(text, text='游戏内实时面板', font=font(self, 'sm_b'), bg=THEME['card'], fg=THEME['quiet'])
        self.eyebrow.pack(anchor='w')
        self.title = tk.Label(text, text='正在检查…', font=font(self, 'h1'), bg=THEME['card'], fg=THEME['ink'], anchor='w')
        self.title.pack(anchor='w', pady=(px(2), 0))
        self.desc = tk.Label(text, text='', font=font(self, 'body'), bg=THEME['card'], fg=THEME['muted'],
                             anchor='w', justify='left', wraplength=px(560))
        self.desc.pack(anchor='w', fill='x', pady=(px(4), 0))
        text.bind('<Configure>', lambda e: self.desc.configure(wraplength=max(px(200), e.width - px(4))))
        self.actions = tk.Frame(self, bg=THEME['card'])
        self.actions.pack(fill='x', pady=(px(18), 0), padx=(px(68), 0))

    def set_state(self, kind, title, desc):
        colour, _tint, _icon = STATUS_STYLE.get(kind, STATUS_STYLE['info'])
        self.icon.set(kind)
        self.title.configure(text=title, fg=THEME['green'] if kind == 'ok' else THEME['red'] if kind == 'error' else THEME['ink'])
        self.desc.configure(text=desc)
        self.configure(highlightbackground=THEME['green'] if kind == 'ok' else THEME['danger_line'] if kind == 'error' else THEME['sel_line'])


STEPS = [
    ('1', '安装面板', '点击「一键安装」写入游戏 data 目录'),
    ('2', '进入单人战役', '多人模式下面板自动停用'),
    ('3', '按 F9 呼出', '或点击屏幕顶栏「◆ 修改器」'),
]


def steps_row(parent):
    """Three numbered usage steps in a single row."""
    row = tk.Frame(parent, bg=bg_of(parent))
    for idx, (num, title, desc) in enumerate(STEPS):
        cell = tk.Frame(row, bg=THEME['card'], highlightthickness=1, highlightbackground=THEME['border'],
                        padx=px(14), pady=px(12))
        cell.grid(row=0, column=idx, sticky='nsew', padx=(0 if idx == 0 else px(10), 0))
        row.columnconfigure(idx, weight=1, uniform='steps')
        head = tk.Frame(cell, bg=THEME['card'])
        head.pack(fill='x')
        badge = tk.Label(head, image=disc(cell, px(24), THEME['gold_dim'], ring=THEME['sel_line'], ring_w=1),
                         text=num, compound='center', font=font(cell, 'sm_b'), fg=THEME['gold_ink'],
                         bg=THEME['card'], bd=0)
        badge.pack(side='left')
        tk.Label(head, text=title, font=font(cell, 'body_b'), bg=THEME['card'], fg=THEME['ink']).pack(side='left', padx=(px(8), 0))
        desc_lbl = tk.Label(cell, text=desc, font=font(cell, 'sm'), bg=THEME['card'], fg=THEME['quiet'],
                            anchor='w', justify='left', wraplength=px(220))
        desc_lbl.pack(fill='x', anchor='w', pady=(px(6), 0))
        cell.bind('<Configure>', lambda e, l=desc_lbl: l.configure(wraplength=max(px(120), e.width - px(30))))
    return row


class AddonGrid(tk.Frame):
    """Two-column grid of optional module switches."""

    def __init__(self, parent, specs, variables, columns=2):
        super().__init__(parent, bg=bg_of(parent))
        for idx, (key, title, desc) in enumerate(specs):
            r, c = divmod(idx, columns)
            cell = tk.Frame(self, bg=bg_of(parent), padx=px(4), pady=px(8))
            cell.grid(row=r, column=c, sticky='nsew', padx=(0 if c == 0 else px(18), 0))
            Switch(cell, variables[key]).pack(side='left', anchor='n', pady=(px(1), 0))
            text = tk.Frame(cell, bg=bg_of(parent))
            text.pack(side='left', fill='x', expand=True, padx=(px(12), 0))
            title_lbl = tk.Label(text, text=title, font=font(self, 'body_b'), bg=bg_of(parent), fg=THEME['ink'], anchor='w', cursor='hand2')
            title_lbl.pack(anchor='w')
            title_lbl.bind('<Button-1>', lambda _e, v=variables[key]: v.set(not v.get()))
            desc_lbl = tk.Label(text, text=desc, font=font(self, 'sm'), bg=bg_of(parent), fg=THEME['quiet'],
                                anchor='w', justify='left', wraplength=px(260))
            desc_lbl.pack(anchor='w', fill='x', pady=(px(2), 0))
            text.bind('<Configure>', lambda e, l=desc_lbl: l.configure(wraplength=max(px(120), e.width - px(4))))
        for c in range(columns):
            self.columnconfigure(c, weight=1, uniform='addons')


def empty_state(parent, title, subtitle, action_text=None, action=None, icon='info'):
    box = tk.Frame(parent, bg=THEME['card'], highlightthickness=1, highlightbackground=THEME['border'],
                   padx=px(24), pady=px(34))
    glyph, f = icon_text(parent, icon)
    if getattr(parent.winfo_toplevel(), '_kit_icon_family', None):
        f = font(parent, 'icon_lg')
    tk.Label(box, text=glyph, font=f, bg=THEME['card'], fg=THEME['faint']).pack()
    tk.Label(box, text=title, font=font(parent, 'em'), bg=THEME['card'], fg=THEME['ink']).pack(pady=(px(10), px(4)))
    tk.Label(box, text=subtitle, font=font(parent, 'sm'), bg=THEME['card'], fg=THEME['quiet']).pack()
    if action_text and action:
        Button(box, action_text, action, variant='secondary', size='sm').pack(pady=(px(16), 0))
    return box


def launch_game_or_dir(game_dir):
    """Launch Warhammer3.exe if available, or open the game directory."""
    p = Path(game_dir)
    exe = p / 'Warhammer3.exe'
    if exe.is_file() and os.name == 'nt':
        os.startfile(exe)
        return True
    if p.is_dir() and os.name == 'nt':
        os.startfile(p)
        return True
    return False


def center_window(win, width, height):
    win.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    width, height = min(width, sw - px(40)), min(height, sh - px(80))
    x, y = max(0, (sw - width) // 2), max(0, (sh - height) // 3)
    win.geometry(f'{width}x{height}+{x}+{y}')
