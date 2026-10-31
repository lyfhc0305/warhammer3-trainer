"""What the WH3 UI engine does to our templates, as measured from real screenshots.

Measured at 3840x2160 ui_scale=1.6 and 1280x800 ui_scale=1.0 (zh-CN), see
output/ui-redesign-20261030/README.md. Pure stdlib: the build uses compensate()
and the offline preview uses engine_rgb(), so both share one model.

* Fonts: even font_m_size values follow the UI scale. Odd ones collapse to a
  small fallback that ignores it (13 and 17 both drew 13px while 12 drew 24px).
* 9-slice margins scale with ui_scale, not with the size of the component, and
  a component shorter than both margins draws them flipped over each other.
* Colours: the value channel is lifted (dark x1.5-1.8, clips near 200) and bright
  fills gain saturation, so a palette has to be authored for the final result.
"""
from bisect import bisect_right

# Only sizes seen rendering correctly in-game. Anything else needs a new check.
FONT_SIZES = (12, 14, 16, 18)

# Value channel in -> out, fitted from solid fills and text colours.
_LIFT_IN = (0, 4, 8, 11, 16, 22, 33, 45, 56, 66, 105, 142, 170, 196, 229, 255)
_LIFT_OUT = (0, 9, 17, 22, 28, 37, 53, 70, 85, 99, 159, 213, 240, 253, 254, 255)
# Dark colours also lose a little saturation: channels drift toward the value.
_DARK_DRIFT = 0.12


def _interp(x, xs, ys):
    if x <= xs[0]:
        return float(ys[0])
    if x >= xs[-1]:
        return float(ys[-1])
    i = bisect_right(xs, x)
    x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def lift(value):
    return _interp(value, _LIFT_IN, _LIFT_OUT)


def drift(value):
    """Strength of the dark desaturation at a lifted value."""
    if value <= 110:
        return _DARK_DRIFT
    if value >= 200:
        return 0.0
    return _DARK_DRIFT * (200 - value) / 90


def engine_rgb(rgb, is_image=True):
    """Colour on screen for a template colour."""
    peak = max(rgb)
    if peak <= 0:
        return tuple(float(c) for c in rgb)
    out_peak = lift(peak)
    gain, pull = out_peak / peak, drift(out_peak)
    result = [c * gain for c in rgb]
    result = [c + pull * (out_peak - c) for c in result]
    if is_image and peak > 150:
        # Bright fills gain saturation (#E5B554 drew as #FEB33F).
        k = 1 + 0.3 * min(1.0, (peak - 150) / 40)
        result = [out_peak - (out_peak - c) * k for c in result]
    return tuple(max(0.0, min(255.0, c)) for c in result)


def compensate(rgb):
    """Template colour that lands on `rgb` on screen (inverse of engine_rgb for text and dark fills)."""
    peak = max(rgb)
    if peak <= 0:
        return tuple(int(c) for c in rgb)
    pull = drift(peak)
    undrifted = [(c - pull * peak) / (1 - pull) for c in rgb]
    scale = _interp(peak, _LIFT_OUT, _LIFT_IN) / peak
    return tuple(max(0, min(255, int(round(c * scale)))) for c in undrifted)


def hex_rgb(colour):
    colour = colour.lstrip('#')
    return tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))


def rgb_hex(rgb, alpha='FF'):
    return '#%02X%02X%02X%s' % (*(int(round(c)) for c in rgb), alpha)


def on_screen(colour, alpha='FF'):
    """Template '#RRGGBBAA' for a design colour given as the on-screen '#RRGGBB'."""
    return rgb_hex(compensate(hex_rgb(colour)), alpha)
