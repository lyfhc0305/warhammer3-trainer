"""Build the self-contained campaign drawer; no writes to the game directory."""
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zlib
from attributes import ATTRIBUTES, NEGATIVE, SCOPE_CORRECTIONS, templates, validate_value
from trainer_core import GAME, Options, EFFECTS, OUTPUT, REFERENCE, VERSION, build, sha, atomic_write, PACK_NAME
from database import encode, decode, table_version
from game_compat import MANIFEST, game_version, make_manifest
from pack_io import read_pack, write_pack
from shop_addon import SHOP_FILENAME
from ballistic_addon import BALLISTIC_FILENAME
from ui_engine import FONT_SIZES, on_screen

ROOT = Path(__file__).parent

# Ink, copper and gold taken from the native HUD (input fill #0E2833, frame
# #9D6B45, selection #195C79). These are on-screen colours: on_screen() converts
# them to the template values that survive the engine's colour lift.
DESIGN = {
    'Background': '#11161A',
    'Side': '#0A0E11',
    'Surface': '#182026',
    'Raise': '#222D34',
    'Hover': '#2E3F48',
    'Divider': '#343F46',
    'Line': '#46535B',
    'Copper': '#C69259',
    'TextMain': '#F4EBDB',
    'Muted': '#C2BBAD',
    'Quiet': '#938F86',
    'Accent': '#E9AE5E',
    'AccentInk': '#FFD48F',
    'SelFill': '#143F4E',
    'SelFillBg': '#2B2317',
    'SelLine': '#D9A25A',
    'Teal': '#46B4C8',
    'Primary': '#46301D',
    'PrimaryLine': '#B98346',
    'PrimaryInk': '#FFE3B3',
    'Danger': '#FF9F93',
    'DangerFill': '#3A1714',
    'DangerLine': '#A5443A',
    'Ok': '#7EE0A4',
}
PALETTE = {name: on_screen(colour) for name, colour in DESIGN.items()}


def _small(size):
    """Fallback font for panel.lua's text fitting; None when nothing smaller is verified."""
    index = FONT_SIZES.index(size)
    return FONT_SIZES[index - 1] if index else None


# Button states: (fill_colour, text_colour, border_colour). Borderless buttons
# are filled with the surface under them rather than relying on transparency, so
# nothing about hit testing depends on how the engine treats image alpha.
_BUTTON_BASE = {
    'active': (PALETTE['Raise'], PALETTE['TextMain'], PALETTE['Line']),
    'hover': (PALETTE['Hover'], PALETTE['AccentInk'], PALETTE['PrimaryLine']),
    'down': (PALETTE['Surface'], PALETTE['Muted'], PALETTE['Line']),
    'selected': (PALETTE['SelFill'], PALETTE['AccentInk'], PALETTE['Teal']),
    'enabled': (PALETTE['SelFillBg'], PALETTE['AccentInk'], PALETTE['SelLine']),
    'accent': (PALETTE['Primary'], PALETTE['PrimaryInk'], PALETTE['PrimaryLine']),
    'danger': (PALETTE['DangerFill'], PALETTE['Danger'], PALETTE['DangerLine']),
    'danger_solid': (PALETTE['DangerLine'], PALETTE['TextMain'], PALETTE['Danger']),
    'ghost': (PALETTE['Side'], PALETTE['Muted'], None),
    'ghost_page': (PALETTE['Background'], PALETTE['Muted'], None),
    'ghost_card': (PALETTE['Surface'], PALETTE['Muted'], None),
    'ghost_lit': (PALETTE['SelFillBg'], PALETTE['Muted'], None),
    'star': (PALETTE['Surface'], PALETTE['Accent'], None),
    'star_lit': (PALETTE['SelFillBg'], PALETTE['Accent'], None),
    'inactive': (PALETTE['Surface'], PALETTE['Quiet'], PALETTE['Divider']),
}
BUTTON_FONT = 12
BUTTON_STATES = dict(_BUTTON_BASE)
BUTTON_FONTS = {k: BUTTON_FONT for k in BUTTON_STATES}

# Card states: (fill_colour, border_colour, text_colour). A border of None draws
# the fill alone; PLAIN_CARDS have no corners, so they can shrink to a hairline.
CARD_STATES = {
    'active': (PALETTE['Surface'], PALETTE['Divider'], PALETTE['TextMain']),
    'panel_bg': (PALETTE['Background'], None, PALETTE['TextMain']),
    'rail_bg': (PALETTE['Side'], None, PALETTE['Muted']),
    'raise': (PALETTE['Surface'], PALETTE['Line'], PALETTE['TextMain']),
    'enabled': (PALETTE['SelFillBg'], PALETTE['SelLine'], PALETTE['AccentInk']),
    'accent': (PALETTE['SelFill'], PALETTE['Teal'], PALETTE['AccentInk']),
    'logo': (PALETTE['Side'], None, PALETTE['Accent']),
    'divider': (PALETTE['Divider'], None, PALETTE['Quiet']),
    'ok_dot': (PALETTE['Ok'], None, PALETTE['Ok']),
    'danger_dot': (PALETTE['Danger'], None, PALETTE['Danger']),
}
PLAIN_CARDS = {'divider': 'solid', 'ok_dot': 'dot', 'danger_dot': 'dot'}
CARD_FONT = 12

# Label states: (text_colour, font_size, halign). States above the smallest
# verified font also get a *_sm variant one step down for text fitting.
_LABEL_BASE = {
    'active': (PALETTE['TextMain'], '14', 'Left'),
    'body': (PALETTE['TextMain'], '12', 'Left'),
    'title': (PALETTE['AccentInk'], '16', 'Left'),
    'title_center': (PALETTE['TextMain'], '14', 'Center'),
    'value': (PALETTE['AccentInk'], '18', 'Left'),
    'section': (PALETTE['Accent'], '12', 'Left'),
    'muted': (PALETTE['Muted'], '12', 'Left'),
    'quiet': (PALETTE['Quiet'], '12', 'Left'),
    'quiet_right': (PALETTE['Quiet'], '12', 'Right'),
    'quiet_center': (PALETTE['Quiet'], '12', 'Center'),
    'accent': (PALETTE['AccentInk'], '14', 'Left'),
    'accent_center': (PALETTE['AccentInk'], '12', 'Center'),
    'accent_right': (PALETTE['AccentInk'], '14', 'Right'),
    'center': (PALETTE['TextMain'], '14', 'Center'),
    'ok': (PALETTE['Ok'], '12', 'Left'),
    'danger': (PALETTE['Danger'], '12', 'Left'),
    'danger_right': (PALETTE['Danger'], '14', 'Right'),
}
LABEL_STATES = dict(_LABEL_BASE)
LABEL_STATES.update({
    k + '_sm': (c, str(_small(int(s))), a) for k, (c, s, a) in _LABEL_BASE.items() if _small(int(s))
})
# Calibration probe: panel.lua expects exactly 14px for its 100% reference.
LABEL_STATES['probe'] = (PALETTE['TextMain'], '14', 'Left')
LABEL_STATES['hint'] = (PALETTE['Quiet'], '12', 'Left')

HINTS = {
    'vigour': '不累积疲劳，开战后清除开局疲劳',
    'unbreakable': '全军战斗永不白旗溃散',
    'vanguard': '全军获得先锋部署能力',
    'attrition': '全军免疫战役非战斗损耗',
    'foreign_replenishment': '非己方领土也可补员',
    'replenishment_cap': '突破每回合补员速率上限',
    'chainsword': '击杀越多近战越强的被动',
}


def ui_font_table():
    """Font size (px at 100% UI scale) of every template state, for panel.lua."""
    return {
        'label': {k: int(v[1]) for k, v in LABEL_STATES.items()},
        'button': dict(BUTTON_FONTS),
        'card': {k: CARD_FONT for k in CARD_STATES},
    }


def mock_font_lua():
    """Lua snippet giving tests/runtime_mock.lua the same font table."""
    return 'UI_FONT_TABLE = ' + lua(ui_font_table())


def encode_png(width, height, pixel):
    """RGBA PNG from pixel(x, y) -> (r, g, b, a), stdlib only."""
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            row.extend(pixel(x, y))
        rows.append(bytes(row))
    raw = b''.join(rows)

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')


def _clamp(value):
    return max(0.0, min(1.0, value))


def _edge_depth(x, y, size, cut):
    """Distance of a pixel inside a square with 45-degree cut corners (negative outside)."""
    half = size / 2
    ax, ay = abs(x + 0.5 - half), abs(y + 0.5 - half)
    return -max(ax - half, ay - half, (ax + ay - (size - cut)) / math.sqrt(2))


PANEL_CUT = 6.0


def make_box_png(size=32, cut=3.0):
    """White 9-slice fill with cut corners; the template colour tints it."""
    def pixel(x, y):
        return 255, 255, 255, int(round(_clamp(_edge_depth(x, y, size, cut) + 0.5) * 255))
    return encode_png(size, size, pixel)


def make_edge_png(size=32, cut=3.0, thickness=1.0):
    """White 9-slice outline matching make_box_png."""
    def pixel(x, y):
        depth = _edge_depth(x, y, size, cut)
        return 255, 255, 255, int(round(_clamp(depth + 0.5) * _clamp(thickness - depth + 0.5) * 255))
    return encode_png(size, size, pixel)


def make_solid_png():
    """Plain white with no corners, for hairlines that must stay thin."""
    return encode_png(4, 4, lambda x, y: (255, 255, 255, 255))


def make_dot_png(size=16):
    def pixel(x, y):
        distance = math.hypot(x + 0.5 - size / 2, y + 0.5 - size / 2)
        return 255, 255, 255, int(round(_clamp(size / 2 - distance + 0.5) * 255))
    return encode_png(size, size, pixel)


def make_frame_png(size=64, cut=PANEL_CUT):
    """Panel frame as a gray mask for the Copper tint: black keyline, lit band, inner hairline."""
    shades = dict(top=255, left=216, right=176, bottom=142)

    def band(depth, near, far):
        return _clamp(depth - near + 0.5) * _clamp(far - depth + 0.5)

    def pixel(x, y):
        depth = _edge_depth(x, y, size, cut)
        side = min((y, 'top'), (x, 'left'), (size - 1 - x, 'right'), (size - 1 - y, 'bottom'))[1]
        lit, thin = band(depth, 1, 3), band(depth, 4, 5) * 0.4
        dark = _clamp(depth + 0.5) * (band(depth, -1, 1) + band(depth, 3, 4))
        cover = _clamp(lit + thin + dark)
        grey = int(round(shades[side] * (lit + thin) / cover)) if cover else 0
        return grey, grey, grey, int(round(cover * 255))
    return encode_png(size, size, pixel)


# Every image is a white or gray mask tinted by its template colour: that is the
# path the colour measurements cover, so the palette holds for the frame too.
UI_IMAGES = {
    'box': make_box_png, 'edge': make_edge_png, 'solid': make_solid_png, 'dot': make_dot_png,
    'panel': lambda: make_box_png(64, PANEL_CUT), 'frame': make_frame_png,
}
# Margins of the 9-slice images; the engine scales them with the UI scale, so a
# component smaller than twice the margin breaks up (see ui_engine.py).
UI_MARGINS = {'box': 8, 'edge': 8, 'panel': 20, 'frame': 20}


def _state_layers(kind, state):
    """(image, colour) stack and text settings of one template state."""
    if kind == 'button':
        fill, text, border = BUTTON_STATES[state]
        layers = [('box', fill)] + ([('edge', border)] if border else [])
        return layers, text, str(BUTTON_FONTS[state]), 'Center'
    fill, border, text = CARD_STATES[state]
    if state == 'panel_bg':
        layers = [('panel', fill), ('frame', PALETTE['Copper'])]
    elif state in PLAIN_CARDS:
        layers = [(PLAIN_CARDS[state], fill)]
    else:
        layers = [('box', fill)] + ([('edge', border)] if border else [])
    return layers, text, str(CARD_FONT), 'Center'


def ui_template(kind):
    """Layout with 9-slice surfaces and multi-state typography."""
    def guid(name):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, 'wh3pt/' + kind + '/' + name)).upper()

    layout = ET.Element('layout', version='142', comment='WH3 DawnTrainer UI', precache_condition='')
    hierarchy = ET.SubElement(layout, 'hierarchy')
    root = ET.SubElement(hierarchy, 'root', this=guid('root'))
    ET.SubElement(root, kind, this=guid(kind))
    components = ET.SubElement(layout, 'components')
    images = {'button': ('box', 'edge'), 'card': ('box', 'edge', 'panel', 'frame', 'solid', 'dot')}.get(kind, ())
    for name in ['root', kind]:
        node = ET.SubElement(
            components, name, this=guid(name), id=name, uniqueguid=guid(name),
            currentstate=guid(name + 'active'), defaultstate=guid(name + 'active'),
            priority='100', tooltipslocalised='true',
        )
        is_target = name == kind
        if is_target and kind == 'button':
            cb = ET.SubElement(node, 'callbackwithcontextlist')
            ET.SubElement(cb, 'callback_with_context', callback_id='Button')
        if is_target and images:
            block = ET.SubElement(node, 'componentimages')
            for image in images:
                ET.SubElement(
                    block, 'component_image', this=guid(image), uniqueguid=guid(image),
                    imagepath='ui/wh3pt/' + image + '.png',
                )
        states = ET.SubElement(node, 'states')
        if is_target and kind == 'button':
            state_names = list(BUTTON_STATES)
        elif is_target and kind == 'card':
            state_names = list(CARD_STATES)
        elif is_target and kind == 'label':
            state_names = list(LABEL_STATES)
        else:
            state_names = ['active']
        for state in state_names:
            attrs = dict(
                this=guid(name + state), uniqueguid=guid(name + state),
                name=state, width='200', height='32',
                # The panel takes the mouse too: it is dragged by its background.
                interactive=str(is_target and (kind == 'button' or state == 'panel_bg')).lower(),
            )
            if state == 'inactive':
                attrs['disabled'] = 'true'
            st = ET.SubElement(states, state, **attrs)
            if is_target and kind in ('button', 'card'):
                layers, text_colour, font_size, halign = _state_layers(kind, state)
                metrics = ET.SubElement(st, 'imagemetrics')
                for image, colour in layers:
                    extra = {}
                    if image in UI_MARGINS:
                        extra = dict(tile='true', margin='%.2f,%.2f,%.2f,%.2f' % ((UI_MARGINS[image],) * 4))
                    ET.SubElement(
                        metrics, 'image', this=guid('image_' + image + '_' + state),
                        uniqueguid=guid('image_' + image + '_' + state),
                        componentimage=guid(image), width='200', height='32',
                        dockpoint='Center', canresizewidth='true', canresizeheight='true',
                        colour=colour, **extra,
                    )
            elif is_target and kind == 'label':
                text_colour, font_size, halign = LABEL_STATES[state]
            else:
                text_colour, font_size, halign = PALETTE['TextMain'], '14', 'Left'
            # Never split: a long line must reach the truncation in panel.lua, not wrap.
            ET.SubElement(
                st, 'component_text', text='', textvalign='Center',
                texthalign=halign, texthbehaviour='Never split',
                font_m_size=font_size, font_m_colour=text_colour, font_m_leading='2', fontcat_name='body_14',
            )
    return ET.tostring(layout, encoding='utf-8', xml_declaration=True)


def lua(value):
    if isinstance(value, dict):
        return '{' + ','.join('[' + lua(k) + ']=' + lua(v) for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '{' + ','.join(map(lua, value)) + '}'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return json.dumps(value, ensure_ascii=False)


def _unpack(payload):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'p.pack'
        path.write_bytes(payload)
        return read_pack(path)


def icon_template():
    root = ET.fromstring(ui_template('button'))
    # A single untinted image, with native hover/tooltip hit testing.
    for state in root.findall('components/button/states/*'):
        metrics = state.find('imagemetrics')
        if metrics is not None:
            for image in list(metrics)[1:]:
                metrics.remove(image)
            for image in metrics:
                image.attrib.pop('margin', None)
                image.attrib.pop('tile', None)
                image.set('colour', '#FFFFFFFF')
        state.set('interactive', 'true')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def ui_files(catalog, strategic=None, game=GAME):
    """Everything in the pack that defines the look of the panel."""
    files = {'script\\campaign\\mod\\wh3pt_panel.lua': (
        'local CATALOG = ' + lua(catalog) + '; local STRATEGIC = '
        + lua(dict(strategic or {'resources': [], 'units': []}, build={'trainer': VERSION, 'game': game_version(game) or ''})) + '\n'
        + 'local UI_FONTS = ' + lua(ui_font_table()) + '\n'
        + (ROOT / 'ingame/manual_battle.lua').read_text(encoding='utf-8') + '\n'
        + (ROOT / 'ingame/target_actions.lua').read_text(encoding='utf-8') + '\n'
        + (ROOT / 'ingame/mortarchs.lua').read_text(encoding='utf-8') + '\n'
        + (ROOT / 'ingame/income.lua').read_text(encoding='utf-8') + '\n'
        + (ROOT / 'ingame/elite_tabs.lua').read_text(encoding='utf-8') + '\n'
        + (ROOT / 'ingame/panel.lua').read_text(encoding='utf-8')
    ).encode('utf-8')}
    files['script\\battle\\mod\\wh3pt_battle.lua'] = (ROOT / 'ingame/battle.lua').read_bytes()
    for name, make in UI_IMAGES.items():
        files['ui\\wh3pt\\' + name + '.png'] = make()
    for kind in ['button', 'label', 'card']:
        files['ui\\wh3pt\\' + kind + '.twui.xml'] = ui_template(kind)
    files['ui\\wh3pt\\icon.twui.xml'] = icon_template()
    files['ui\\wh3pt\\list.twui.xml'] = (ROOT / 'ingame/list.twui.xml').read_bytes()
    return files


def build_ingame(game=GAME, addons=None, local_dir=None, workshop=None):
    local_dir = Path(local_dir) if local_dir else REFERENCE
    files, catalog, warnings, enabled_addons = {}, [], [], []
    refs = templates(local_dir, warnings)
    for key, spec in ATTRIBUTES.items():
        label, effect, human, category, maximum, default, unit = spec
        # AI faction-wide changes require separate targeting; never apply them to the player.
        if not human:
            continue
        rows = refs[key][1]
        catalog.append(dict(
            id=key, label=label, effect=effect, scope=SCOPE_CORRECTIONS.get(key, rows[0]['effect_scope']),
            group='部队' if category == '部队属性' else '战役', max=maximum,
            default=default, step=1 if key == 'recruitment' else 5,
            integer=key == 'recruitment', sign=-1 if key in NEGATIVE else 1, unit=unit,
        ))
        if key == 'health':
            catalog[-1]['hint'] = '仅领主与英雄生效'
        elif key == 'missile_damage':
            catalog[-1]['hint'] = '直击与破甲伤害，不含爆炸'
        elif key == 'movement':
            catalog[-1]['hint'] = '下回合生效，事务官不变'
    # Resolve effect scopes from the inspected source instead of guessing them.
    basic = _unpack(build(game, Options(**{k: True for k in EFFECTS}), workshop=workshop, local_dir=local_dir)[0])
    basic_rows = [
        r for n, b in basic.items() if n.startswith('db\\campaign_difficulty_handicap')
        for r in decode('campaign_difficulty_handicap_effects_tables', b)[1]
    ]
    labels = dict(
        vigour='完美活力', unbreakable='不溃', vanguard='先锋部署', attrition='免疫损耗',
        foreign_replenishment='异地补员', replenishment_cap='解除补员限制',
    )
    for key, effect in EFFECTS.items():
        row = next(r for r in basic_rows if r['effect'] == effect)
        numeric = key == 'foreign_replenishment'
        catalog.append(dict(
            id=key, label=labels[key], effect=effect, scope=row['effect_scope'],
            group='特殊', max=500 if numeric else row['effect_value'],
            default=row['effect_value'], step=5, toggle=not numeric, sign=1,
            unit='%' if numeric else '', hint=HINTS.get(key, ''),
        ))
    chainsword_pack = local_dir / '@玩家部队buff链锯剑.pack'
    if not chainsword_pack.is_file():
        chainsword_pack = REFERENCE / '@玩家部队buff链锯剑.pack'
    cs_row = next(
        r for n, b in read_pack(chainsword_pack).items() if n.startswith('db\\')
        for r in decode(n.split('\\')[1], b)[1]
    )
    catalog.append(dict(
        id='chainsword', label='链锯剑神力', effect=cs_row['effect'],
        scope=cs_row['effect_scope'], group='特殊', max=cs_row['effect_value'],
        default=cs_row['effect_value'], step=1, toggle=True, sign=1, unit='',
        hint=HINTS['chainsword'],
    ))
    for key, name, title, unit in [
        ('growth', '@玩家战役1回合发展.pack', '发展', '点'),
        ('construction', '@玩家战役1回合建筑.pack', '建筑', '%'),
        ('research', '@玩家战役1回合科技.pack', '科技', '%'),
    ]:
        pack_path = (local_dir / name) if (local_dir / name).is_file() else (REFERENCE / name)
        row = next(
            r for n, b in read_pack(pack_path).items() if n.startswith('db\\')
            for r in decode(n.split('\\')[1], b)[1]
        )
        catalog.append(dict(
            id=key, label=title + '加速', effect=row['effect'],
            scope=row['effect_scope'], group='战役', max=abs(row['effect_value']),
            default=abs(row['effect_value']), step=10,
            sign=-1 if row['effect_value'] < 0 else 1, unit=unit,
        ))
    # Independent ability variants: enabling ammo must not implicitly heal or resurrect.
    ability_tables = {}
    variants = [('healing', [1, 5, 12.5, 25]), ('revive', [1, 5, 12.5, 25]), ('ammo', [5, 20, 50, 100])]
    for feature, rates in variants:
        effects = []
        for i, rate in enumerate(rates, 1):
            ident = f'wh3pt_{feature}_{i}'
            effects.append(ident + '_effect')
            opts = Options(
                regeneration=feature != 'ammo', resurrection=feature == 'revive',
                ammunition=feature == 'ammo', heal_percent=rate, ammo_percent=rate,
            )
            for name, data in _unpack(build(game, opts, workshop=workshop, local_dir=local_dir)[0]).items():
                if not name.startswith('db\\') or 'campaign_difficulty_handicap' in name:
                    continue
                table = name.split('\\')[1]
                version, rows = decode(table, data)
                for row in rows:
                    for field, value in list(row.items()):
                        if value == 'blood':
                            row[field] = ident
                        elif value == 'blood_returning':
                            row[field] = ident + '_effect'
                    if table == 'unit_special_abilities_tables':
                        offset = 0 if feature == 'healing' else 100 if feature == 'revive' else 200
                        row['unique_id'] = 1910000 + len(effects) + offset
                    if table == 'unit_abilities_tables':
                        row['requires_effect_enabling'] = True
                    if table == 'unit_abilities_to_additional_ui_effects_juncs_tables' and feature == 'ammo':
                        row['effect'] = 'wh2_dlc11_all_replenish_ammo'
                ability_tables.setdefault((table, version), []).extend(rows)
        catalog.append(dict(
            id=feature,
            label={'healing': '持续回血', 'revive': '回血并复活', 'ammo': '持续补充弹药'}[feature],
            effects=effects, choices=rates, scope='faction_to_force_own', group='特殊',
            max=len(rates), default=3, step=1, integer=True, sign=1, unit='%/秒',
            hint={
                'healing': '每秒恢复自身生命，不复活',
                'revive': '每秒恢复自身生命并复活',
                'ammo': '每秒补充自身弹药，不回血',
            }[feature],
        ))
    for (table, version), rows in ability_tables.items():
        files[f'db\\{table}\\wh3pt_runtime'] = encode(table, version, rows)
    for feature in catalog:
        feature['manual_battle'] = feature['group'] == '部队' or feature['id'] in {'vigour', 'unbreakable', 'vanguard', 'chainsword', 'healing', 'revive', 'ammo'}
    catalog.append(dict(id='income', label='每回合收入增幅', group='战役', min=1, max=10000,
        default=100, step=25, integer=True, unit='%', income=True, manual_battle=False,
        hint='与已有加成相加，非总收入倍率', presets=[100, 200, 500, 1000]))
    catalog.extend([
        dict(id='battle_magic', label='无限魔法之风', group='特殊', min=1, max=1,
             default=1, step=1, toggle=True, unit='', manual_battle=True, battle_setting='magic',
             hint='开战自动启用；持续补充玩家魔法池与储备'),
        dict(id='battle_cooldown', label='技能无冷却无限次数', group='特殊', min=1, max=1,
             default=1, step=1, toggle=True, unit='', manual_battle=True, battle_setting='cooldown',
             hint='开战自动启用；玩家全部主动技能与法术'),
    ])
    bundles = [
        dict(
            key='wh3pt_' + f['id'], localised_title=f['label'], localised_description='',
            bundle_target='faction', priority=0, ui_icon='', is_global_effect=False,
            show_in_3d_space=False, owner_only=True,
        )
        for f in catalog if not f.get('income') and not f.get('battle_setting')
    ]
    bundles.append(dict(key='wh3pt_native_ror', localised_title='精英无限招募',
        localised_description='', bundle_target='faction', priority=0, ui_icon='',
        is_global_effect=False, show_in_3d_space=False, owner_only=True))
    bundles.append(dict(key='wh3pt_mortarchs', localised_title='死亡统帅无条件收服',
        localised_description='', bundle_target='faction', priority=0, ui_icon='',
        is_global_effect=False, show_in_3d_space=False, owner_only=True))
    bundles.append(dict(key='wh3pt_sigils', localised_title='可强化符印数量',
        localised_description='', bundle_target='faction', priority=0, ui_icon='',
        is_global_effect=False, show_in_3d_space=False, owner_only=True))
    files['db\\effect_bundles_tables\\wh3pt_runtime'] = encode('effect_bundles_tables', table_version(game, 'effect_bundles_tables'), bundles)

    if addons:
        local_mods = []
        if addons.get('shop'):
            local_mods.append(SHOP_FILENAME)
            enabled_addons.append('shop')
        if addons.get('ballistic'):
            local_mods.append(BALLISTIC_FILENAME)
            enabled_addons.append('ballistic')
        has_builder_addons = any(addons.get(k) for k in ('building', 'ai', 'cap')) or bool(local_mods)
        if has_builder_addons:
            for k in ('building', 'ai', 'cap'):
                if addons.get(k):
                    enabled_addons.append(k)
            addon_opts = Options(
                building=bool(addons.get('building')),
                ai=bool(addons.get('ai')),
                cap=bool(addons.get('cap')),
                cap_value=float(addons.get('cap_value', 50.0)),
                local_mods=local_mods,
            )
            addon_payload, addon_report = build(game, addon_opts, workshop=workshop, local_dir=local_dir)
            warnings.extend(addon_report['warnings'])
            for name, data in _unpack(addon_payload).items():
                if name in ('wh3_personal_trainer_marker.txt', MANIFEST):
                    continue
                files[name] = data
        if addons.get('agent_ai'):
            val = float(addons.get('agent_ai_value', 100.0))
            validate_value('agent_ai', val)
            if 'agent_ai' not in refs:
                raise ValueError('缺少敌方间谍减益参考包')
            _, ai_rows = refs['agent_ai']
            edited = [dict(r, effect_value=-val) for r in ai_rows if not r['human']]
            files['db\\campaign_difficulty_handicap_effects_tables\\wh3pt_agent_ai'] = encode(
                'campaign_difficulty_handicap_effects_tables',
                table_version(game, 'campaign_difficulty_handicap_effects_tables'), edited
            )
            enabled_addons.append('agent_ai')

    from strategic_data import SOURCE_TABLES as STRATEGIC_TABLES, strategic_catalog, native_recruitment_files
    strategic = strategic_catalog(game)
    from income_data import SOURCE_TABLES as INCOME_TABLES, native_income_files
    files.update(native_income_files(game, strategic))
    files.update(native_recruitment_files(game, strategic))
    files.update(ui_files(catalog, strategic, game))
    files['wh3_personal_trainer_marker.txt'] = b'WH3-PERSONAL-TRAINER-v1'
    files['wh3pt_mode.txt'] = b'ingame-v2'
    shipped = {n.split('\\')[1] for n in files if n.startswith('db\\')}
    files[MANIFEST] = make_manifest(game, shipped | STRATEGIC_TABLES | INCOME_TABLES, VERSION)
    payload = write_pack(files)
    report = dict(
        mode='ingame-v2', features=catalog, addons=enabled_addons, sha256=sha(payload),
        strategic_counts={k: len(v) for k, v in strategic.items()},
        warnings=warnings, validation='Offline only; native UI and game effects require in-game verification',
    )
    return payload, report


def rebuild_ui_only(pack_path):
    """Swap the UI into an existing pack; keeps its catalog and tables."""
    files = read_pack(pack_path)
    current = files['script\\campaign\\mod\\wh3pt_panel.lua'].decode('utf-8')
    head = 'local CATALOG = '
    if not current.startswith(head):
        raise ValueError('现有 Pack 中找不到功能目录')
    catalog_line = current[:current.index('\n')]
    for name in [n for n in files if n.startswith('ui\\wh3pt\\')]:
        del files[name]
    files.update(ui_files([]))
    fresh = files['script\\campaign\\mod\\wh3pt_panel.lua'].decode('utf-8')
    files['script\\campaign\\mod\\wh3pt_panel.lua'] = (catalog_line + fresh[fresh.index('\n'):]).encode('utf-8')
    return write_pack(files)


def write_previews(payload):
    """Skipped quietly where the preview-only dependencies are missing."""
    try:
        from ingame_preview import SCREEN_1440, SCREEN_4K, render_sheet
        render_sheet(payload, OUTPUT / 'ingame/面板离线预览.png', SCREEN_4K)
        render_sheet(payload, OUTPUT / 'ingame/面板离线预览-150.png', SCREEN_1440, shrink=0.75)
    except ImportError:
        pass


if __name__ == '__main__':
    target = OUTPUT / 'ingame' / PACK_NAME
    if '--ui-only' in sys.argv:
        payload = rebuild_ui_only(target)
        atomic_write(target, payload)
        report_path = OUTPUT / 'ingame/build-report.json'
        report = json.loads(report_path.read_text(encoding='utf-8'))
        report['sha256'] = sha(payload)
        atomic_write(report_path, json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8'))
        write_previews(payload)
        print('Rebuilt panel UI only:', len(payload), 'bytes')
        raise SystemExit
    payload, report = build_ingame()
    atomic_write(target, payload)
    atomic_write(
        OUTPUT / 'ingame/build-report.json',
        json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8'),
    )
    write_previews(payload)
    print('Built in-game panel:', len(report['features']), 'features,', len(payload), 'bytes')
