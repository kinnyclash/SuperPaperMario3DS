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
