"""Offline preview of the in-game panel, drawn the way the engine draws it.

Runs the real panel Lua against tests/runtime_mock.lua, then paints the widgets
with the XML templates and PNGs from the pack and the behaviour in ui_engine.py.
It reads everything from the pack, so old packs can be replayed for comparison.
Needs Pillow, NumPy and lupa; none of them ship in the executables.
"""
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
from pack_io import read_pack
from database import decode
import ui_engine as engine

ROOT = Path(__file__).parent
LUA_NAME = 'script\\campaign\\mod\\wh3pt_panel.lua'
FONT_PATHS = {
    False: ['C:/Windows/Fonts/msyh.ttc', '/System/Library/Fonts/STHeiti Medium.ttc'],
    True: ['C:/Windows/Fonts/msyhbd.ttc', '/System/Library/Fonts/STHeiti Medium.ttc'],
}
# 3840x2160 ui_scale=1.6 (the shipped target). phys is the ratio between script
# coordinates and screenshot pixels on that machine.
SCREEN_4K = dict(screen=(3840, 2160), text_ratio=2.0, ui_scale=1.6, fallback_px=13.0, phys=1.036)
SCREEN_1440 = dict(screen=(2560, 1440), text_ratio=1.5, ui_scale=1.25, fallback_px=12.0, phys=1.0)
SETUP_EFFECTS = [
    ('vigour', '1'), ('unbreakable', '1'), ('healing', '3'), ('ward', '45'),
    ('attack', '35'), ('order', '50'), ('growth', '500'),
]
VIEWS = [
    ('总览', '总览'), ('内政', '内政与经济'), ('军队', '军队与将领'),
    ('战斗', '手动战斗加成'), ('精英', '精英部队招募'), ('search', '跨页搜索'),
    ('preset', '预设预览'), ('sheet', '正在生效与全关'), ('collapsed', '收起为侧栏'),
]
NATIVE_INPUT = dict(fill=(14, 40, 51), line=(120, 76, 45), keyline=(6, 18, 24), text=(255, 248, 215))


def unpack(payload):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'p.pack'
        path.write_bytes(payload)
        return read_pack(path)


def parse_templates(files):
    """kind -> state -> {images: [(path, colour, margin)], text, size, align}."""
    templates = {}
    for kind in ('button', 'label', 'card'):
        root = ET.fromstring(files['ui\\wh3pt\\' + kind + '.twui.xml'])
        component = next(c for c in root.find('components') if c.tag == kind)
        images = {}
        block = component.find('componentimages')
        for image in (block if block is not None else []):
            images[image.get('this')] = image.get('imagepath')
        states = {}
        for state in component.find('states'):
            metrics = state.find('imagemetrics')
            layers = []
            for image in (metrics if metrics is not None else []):
                margin = float((image.get('margin') or '0').split(',')[0])
                layers.append((images[image.get('componentimage')], image.get('colour') or '#FFFFFFFF', margin))
            text = state.find('component_text')
            states[state.get('name')] = dict(
                images=layers, text=text.get('font_m_colour'), size=int(text.get('font_m_size')),
                align=text.get('texthalign'),
            )
        templates[kind] = states
    return templates


def lua_font_table(templates):
    def lua_map(mapping):
        return '{' + ','.join('["%s"]=%d' % (k, v['size']) for k, v in mapping.items()) + '}'
    return 'UI_FONT_TABLE = {' + ','.join('["%s"]=%s' % (k, lua_map(v)) for k, v in templates.items()) + '}'


def snapshot(lua_code, templates, view, env, effects=True, bundle_keys=()):
    """Run the panel Lua and return (widgets, panel_rect, layout) for one view."""
    from lupa.lua51 import LuaRuntime
    rt = LuaRuntime(unpack_returned_tuples=True)
    rt.execute((ROOT.parent / 'tests/runtime_mock.lua').read_text(encoding='utf-8'))
    rt.globals().known_bundle_keys = rt.table_from({key: True for key in bundle_keys})
    rt.execute(lua_font_table(templates))
    ratio, fallback = env['text_ratio'], env['fallback_px']
    rt.execute(f'''
        screen_width, screen_height = {env['screen'][0]}, {env['screen'][1]}
        text_scale = {ratio}
        local measure = measure_text
        function measure_text(text, font_px)
            if font_px % 2 == 1 then return measure(text, {fallback} / {ratio}) end
            return measure(text, font_px)
        end
    ''')
    api = rt.execute(lua_code)
    g = rt.globals()
    g.first_tick()
    if effects:
        for key, value in SETUP_EFFECTS:
            api.apply(key, True, value)
            g.flush()
        api.click('fav_ward')
        api.click('fav_order')
        rt.execute("core:trigger_event('CharacterSelected', {character=function() return characters[2] end})")
    api.click('open')
    if view == 'collapsed':
        api.click('collapse')
    elif view == 'sheet':
        api.click('rail_count')
        api.click('clear')          # armed: the countdown button is part of the design
    elif view == 'preset':
        api.click('cmd_preset_campaign')
    elif view == 'search':
        api.click('tab_内政')
        api.click('search_open')
        g.widgets['wh3pt_search'].text = '加速'
        api.click('search_go')
    elif view != 'closed':
        api.click('tab_' + view)
    panel = g.widgets['wh3pt_panel']
    items = []

    def clip_rect(w):
        parent = w.parent
        while parent:
            if parent.id == 'list_clip':
                return parent.x, parent.y, parent.w, parent.h
            parent = parent.parent
        return None

    for w in g.widgets.values():
        if w.id == 'root' or w.dead or not w.visible:
            continue
        items.append(dict(
            id=w.id, seq=int(w.seq or 0), template=w.template or '', text=w.text or '',
            state=w.state or 'active', x=w.x, y=w.y, w=w.w, h=w.h, clip=clip_rect(w),
        ))
    items.sort(key=lambda it: it['seq'])
    return items, (panel.x, panel.y, panel.w, panel.h), dict(api.layout())


class Painter:
    def __init__(self, files, templates, env):
        import numpy
        self.np = numpy
        from PIL import Image, ImageDraw, ImageFont
        self.Image, self.ImageDraw, self.ImageFont = Image, ImageDraw, ImageFont
        self.files, self.templates, self.env = files, templates, env
        self.fonts, self.tinted = {}, {}

    def font(self, px, bold=False):
        key = (round(px, 1), bold)
        if key not in self.fonts:
            for path in FONT_PATHS[bold]:
                try:
                    self.fonts[key] = self.ImageFont.truetype(path, max(6.0, px))
                    break
                except (OSError, ValueError):
                    continue
            else:
                self.fonts[key] = self.ImageFont.load_default()
        return self.fonts[key]

    def lifted(self, rgb, is_image=True):
        return tuple(int(round(c)) for c in engine.engine_rgb(rgb, is_image))

    def tint(self, path, colour):
        key = (path, colour)
        if key in self.tinted:
            return self.tinted[key]
        import io
        np = self.np
        source = self.Image.open(io.BytesIO(self.files[path.replace('/', '\\')])).convert('RGBA')
        data = np.asarray(source).astype('float64')
        rgb = engine.hex_rgb(colour)
        alpha = int(colour[7:9] or 'FF', 16) / 255
        data[..., :3] = data[..., :3] * np.array(rgb) / 255
        data[..., 3] = data[..., 3] * alpha
        peak = data[..., :3].max(axis=-1)
        out_peak = np.interp(peak, engine._LIFT_IN, engine._LIFT_OUT)
        gain = out_peak / np.maximum(peak, 1)
        pull = np.interp(out_peak, [110, 200], [engine._DARK_DRIFT, 0.0])
        for channel in range(3):
            value = data[..., channel] * gain
            value = value + pull * (out_peak - value)
            k = 1 + 0.3 * np.clip((peak - 150) / 40, 0, 1)
            data[..., channel] = np.clip(out_peak - (out_peak - value) * k, 0, 255)
        image = self.Image.fromarray(data.round().astype('uint8'))
        self.tinted[key] = image
        return image

    def paste(self, canvas, piece, x0, y0, x1, y1):
        w, h = int(round(x1)) - int(round(x0)), int(round(y1)) - int(round(y0))
        if w <= 0 or h <= 0 or piece.width <= 0 or piece.height <= 0:
            return
        resized = piece.resize((w, h), self.Image.BILINEAR)
        canvas.alpha_composite(resized, (int(round(x0)), int(round(y0))))

    def nine_slice(self, canvas, path, colour, margin, x, y, w, h):
        """Margins keep their ui_scale size; too-short boxes draw them flipped."""
        image = self.tint(path, colour)
        iw, ih = image.size
        if not margin:
            self.paste(canvas, image, x, y, x + w, y + h)
            return
        m = margin * self.env['ui_scale'] * self.env['phys']
        columns = [(0, margin, x, x + m), (iw - margin, iw, x + w - m, x + w)]
        rows = [(0, margin, y, y + m), (ih - margin, ih, y + h - m, y + h)]
        if w > 2 * m:
            columns.insert(1, (margin, iw - margin, x + m, x + w - m))
        if h > 2 * m:
            rows.insert(1, (margin, ih - margin, y + m, y + h - m))
        for sy0, sy1, dy0, dy1 in rows:
            for sx0, sx1, dx0, dx1 in columns:
                self.paste(canvas, image.crop((int(sx0), int(sy0), int(sx1), int(sy1))), dx0, dy0, dx1, dy1)

    def text(self, canvas, value, size, colour, align, box, shadow=True):
        if not value:
            return
        env = self.env
        odd = size % 2 == 1
        px = env['fallback_px'] if odd else size * env['text_ratio']
        draw = self.ImageDraw.Draw(canvas)
        font = self.font(px, bold=odd)
        x0, y0, x1, y1 = box
        cy = (y0 + y1) / 2
        if align == 'Center':
            xy, anchor = ((x0 + x1) / 2, cy), 'mm'
        elif align == 'Right':
            xy, anchor = (x1, cy), 'rm'
        else:
            xy, anchor = (x0, cy), 'lm'
        if shadow:
            draw.text((xy[0] + 1, xy[1] + 1), value, font=font, fill=(0, 0, 0, 150), anchor=anchor)
        draw.text(xy, value, font=font, fill=tuple(colour) + (255,), anchor=anchor)

    def widget(self, canvas, it, ox, oy):
        env = self.env
        phys = env['phys']
        x, y, w, h = ox + it['x'] * phys, oy + it['y'] * phys, it['w'] * phys, it['h'] * phys
        template = it['template']
        if template == 'ui/common ui/text_box':
            draw = self.ImageDraw.Draw(canvas)
            box = [x, y, x + w - 1, y + h - 1]
            draw.rectangle(box, fill=NATIVE_INPUT['fill'] + (255,), outline=NATIVE_INPUT['line'] + (255,), width=3)
            self.text(canvas, it['text'], 12, NATIVE_INPUT['text'], 'Center', (x, y, x + w, y + h))
            return
        kind = template.rsplit('/', 1)[-1]
        states = self.templates.get(kind)
        if not states:
            return
        spec = states.get(it['state']) or states.get('active')
        for path, colour, margin in spec['images']:
            self.nine_slice(canvas, path, colour, margin, x, y, w, h)
        colour = self.lifted(engine.hex_rgb(spec['text']), is_image=False)
        self.text(canvas, it['text'], spec['size'], colour, spec['align'], (x, y, x + w, y + h))

    def clipped(self, canvas, items, offset):
        """Paint list content through its clip rectangle; the scroll offset is always 0 here."""
        layer = self.Image.new('RGBA', canvas.size, (0, 0, 0, 0))
        for it in items:
            self.widget(layer, it, *offset)
        phys = self.env['phys']
        x, y, w, h = items[0]['clip']
        box = tuple(int(round(v)) for v in (
            offset[0] + x * phys, offset[1] + y * phys, offset[0] + (x + w) * phys, offset[1] + (y + h) * phys,
        ))
        canvas.alpha_composite(layer.crop(box), box[:2])

    def scrollbar(self, canvas, it, content_h, offset):
        """Stand-in for the native parchment_slider_vertical art."""
        phys, ui = self.env['phys'], self.env['ui_scale']
        draw = self.ImageDraw.Draw(canvas)
        width = 12 * ui
        x1 = offset[0] + (it['x'] + it['w']) * phys - 4
        x0 = x1 - width
        y0, y1 = offset[1] + it['y'] * phys, offset[1] + (it['y'] + it['h']) * phys
        draw.rectangle([x0, y0, x1, y1], fill=(10, 14, 17, 255), outline=(107, 74, 44, 255), width=2)
        length = max(39 * ui, (y1 - y0) * min(1.0, it['h'] / max(1, content_h)))
        draw.rectangle([x0 + 2, y0 + 2, x1 - 2, y0 + length], fill=(25, 92, 121, 255), outline=(70, 180, 200, 255), width=1)

    def view(self, items, background, offset=(0, 0)):
        canvas = background.copy().convert('RGBA')
        index = 0
        while index < len(items):
            it = items[index]
            if it['clip']:
                end = index
                while end < len(items) and items[end]['clip']:
                    end += 1
                self.clipped(canvas, items[index:end], offset)
                index = end
                continue
            self.widget(canvas, it, *offset)
            if it['id'] == 'wh3pt_list':
                content = next((i for i in items if i['id'] == 'wh3pt_list_content'), None)
                if content:
                    self.scrollbar(canvas, it, content['h'], offset)
            index += 1
        return canvas


def render_view(payload, view, env=SCREEN_4K, background=None, offset=None, margin=24, effects=True):
    """Render one view; returns (RGBA image, layout, panel rect in pixels)."""
    from PIL import Image
    files = unpack(payload)
    templates = parse_templates(files)
    bundle_keys = {r['key'] for n, data in files.items() if n.startswith('db\\effect_bundles_tables\\')
                   for r in decode('effect_bundles_tables', data)[1]}
    items, rect, layout = snapshot(files[LUA_NAME].decode('utf-8'), templates, view, env, effects, bundle_keys)
    painter = Painter(files, templates, env)
    phys = env['phys']
    if background is None:
        px, py, pw, ph = rect
        # List content scrolls past the panel but only its clipped part is drawn.
        shown = [it for it in items if not it['clip']]
        lx0 = min(it['x'] for it in shown)
        ly0 = min(it['y'] for it in shown)
        lx1 = max(it['x'] + it['w'] for it in shown)
        ly1 = max(it['y'] + it['h'] for it in shown)
        size = (int((lx1 - lx0) * phys) + 2 * margin, int((ly1 - ly0) * phys) + 2 * margin)
        background = Image.new('RGBA', size, (30, 33, 38, 255))
        offset = (margin - lx0 * phys, margin - ly0 * phys)
    image = painter.view(items, background, offset or (0, 0))
    return image, layout, rect


def render_sheet(payload, out_path, env=SCREEN_4K, views=None, columns=3, shrink=0.5):
    """Contact sheet of panel views."""
    from PIL import Image, ImageDraw
    views = views or VIEWS
    shots = [(caption, render_view(payload, view, env)) for view, caption in views]
    scaled = []
    for caption, (image, layout, _) in shots:
        size = (int(image.width * shrink), int(image.height * shrink))
        scaled.append((caption, image.convert('RGB').resize(size, Image.LANCZOS), layout))
    cell_w = max(s[1].width for s in scaled) + 24
    cell_h = max(s[1].height for s in scaled) + 40
    rows = (len(scaled) + columns - 1) // columns
    sheet = Image.new('RGB', (columns * cell_w + 24, rows * cell_h + 24), (20, 22, 26))
    draw = ImageDraw.Draw(sheet)
    painter = Painter({}, {}, env)
    for index, (caption, image, layout) in enumerate(scaled):
        x = 24 + (index % columns) * cell_w
        y = 12 + (index // columns) * cell_h
        note = '%s · 面板缩放 %d%%' % (caption, round(layout['scale'] * 100))
        draw.text((x, y), note, font=painter.font(18), fill=(236, 190, 110))
        sheet.paste(image, (x, y + 30))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, 'PNG')
    return out_path
