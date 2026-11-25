"""Reference model of the GX TEV colour combiner.

The PICA200 translation (docs/GX_PICA_DESIGN.md) is tested against this model. The formula
below is the standard GX colour combiner, reproduced from the description in public GX and
Gekko references. Check the constants against the GX documentation before relying on them
for game output; the tests here verify this model's own arithmetic, not the hardware.

Per channel, with inputs in [0, 1]:
    lerp   = (1 - c) * a + c * b
    value  = d + lerp            (or d - lerp when `subtract` is set)
    value  = (value + bias) * scale
    result = clamp(value, 0, 1)  (when `clamp` is set)
"""

# GX bias encodings: zero, add half, subtract half.
BIAS_VALUES = {0: 0.0, 1: 0.5, 2: -0.5}
# GX scale encodings: 1, 2, 4, one half.
SCALE_VALUES = {0: 1.0, 1: 2.0, 2: 4.0, 3: 0.5}


def clamp01(x):
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def tev_combine(d, a, b, c, subtract=False, bias=0, scale=0, clamp=True):
    """Evaluate one colour channel of a TEV stage.

    d, a, b, c are the four inputs; `bias` and `scale` are GX encodings (see the tables above).
    """
    lerp = (1.0 - c) * a + c * b
    value = d - lerp if subtract else d + lerp
    value = (value + BIAS_VALUES[bias]) * SCALE_VALUES[scale]
    return clamp01(value) if clamp else value


def tev_combine_rgb(d, a, b, c, **options):
    """Apply tev_combine per channel. Each argument is an (r, g, b) triple."""
    return tuple(tev_combine(d[i], a[i], b[i], c[i], **options) for i in range(3))


# ---- multi-stage TEV evaluation from decoded BP registers (see cp.py, regs.py) ----------
#
# Colour inputs (libogc GX_CC_*): 0 CPREV, 1 APREV, 2 C0, 3 A0, 4 C1, 5 A1, 6 C2, 7 A2,
# 8 TEXC, 9 TEXA, 10 RASC, 11 RASA, 12 ONE, 13 HALF, 14 KONST, 15 ZERO.
# Alpha inputs (GX_CA_*): 0 APREV, 1 A0, 2 A1, 3 A2, 4 TEXA, 5 RASA, 6 KONST, 7 ZERO.
# Register order: 0 PREV, 1 REG0 (C0/A0), 2 REG1, 3 REG2 (libogc GX_TEVPREV = 0).
#
# Simplifications: inputs a, b and c are clamped to [0, 1] (the hardware uses their low
# 8 bits); unclamped results are limited to [-4, 4] instead of the 11-bit signed range;
# konst inputs and compare modes are recorded as unsupported (konst reads as 1, compare
# mode passes d through). Colour channel ids other than COLOR0A0 (0) and ZERO (7) read white.

def _color_input(i, regs_, tex, ras):
    if i < 8:
        r = regs_[i // 2]
        return (r[3],) * 3 if i & 1 else tuple(r[:3])
    if i == 8:
        return tuple(tex[:3])
    if i == 9:
        return (tex[3],) * 3
    if i == 10:
        return tuple(ras[:3])
    if i == 11:
        return (ras[3],) * 3
    if i == 12:
        return (1.0,) * 3
    if i == 13:
        return (0.5,) * 3
    if i == 14:
        return (1.0,) * 3
    return (0.0,) * 3


def _alpha_input(i, regs_, tex, ras):
    if i < 4:
        return regs_[i][3]
    return {4: tex[3], 5: ras[3], 6: 1.0, 7: 0.0}[i]


def _apply(env, d, a, b, c):
    if env.bias == 3:
        return d
    a, b, c = clamp01(a), clamp01(b), clamp01(c)
    v = tev_combine(d, a, b, c, subtract=env.subtract, bias=env.bias, scale=env.scale,
                    clamp=env.clamp)
    return v if env.clamp else max(-4.0, min(4.0, v))


def make_shader(stages, reg_file, unsupported):
    """Return shader(ras_rgba, texel_or_None) -> rgba for the given decoded TEV stages."""
    for color_env, alpha_env, order in stages:
        if 14 in (color_env.a, color_env.b, color_env.c, color_env.d) or \
                6 in (alpha_env.a, alpha_env.b, alpha_env.c, alpha_env.d):
            unsupported["tev_konst"] += 1
        if color_env.bias == 3 or alpha_env.bias == 3:
            unsupported["tev_compare"] += 1
        if order.color not in (0, 7):
            unsupported["tev_color_channel"] += 1

    def shader(ras_in, texel):
        regs_ = [list(r) for r in reg_file]
        for color_env, alpha_env, order in stages:
            if order.tex_enable:
                tex = texel if texel is not None else (1.0, 1.0, 1.0, 1.0)
            else:
                tex = (0.0, 0.0, 0.0, 0.0)
            ras = ras_in if order.color == 0 else (
                (0.0, 0.0, 0.0, 0.0) if order.color == 7 else (1.0, 1.0, 1.0, 1.0))
            a = _color_input(color_env.a, regs_, tex, ras)
            b = _color_input(color_env.b, regs_, tex, ras)
            c = _color_input(color_env.c, regs_, tex, ras)
            d = _color_input(color_env.d, regs_, tex, ras)
            rgb = [_apply(color_env, d[k], a[k], b[k], c[k]) for k in range(3)]
            alpha = _apply(alpha_env, _alpha_input(alpha_env.d, regs_, tex, ras),
                           _alpha_input(alpha_env.a, regs_, tex, ras),
                           _alpha_input(alpha_env.b, regs_, tex, ras),
                           _alpha_input(alpha_env.c, regs_, tex, ras))
            regs_[color_env.dest][:3] = rgb
            regs_[alpha_env.dest][3] = alpha
        return tuple(clamp01(v) for v in regs_[0])

    return shader
