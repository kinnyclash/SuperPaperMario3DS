"""GX register field decoding (CP, BP and XF), host side.

Every field layout here was read from libogc's gx.c (devkitPro, public source), which packs
these registers before writing them to the GX FIFO:
  * VCD (CP 0x50 low, 0x60 high): __SETVCDATTR
  * VAT groups 0/1/2 (CP 0x70+n, 0x80+n, 0x90+n): __SETVCDFMT
  * Array base and stride (CP 0xA0+i, 0xB0+i, i = attribute - GX_VA_POS): GX_SetArray
  * Matrix index (CP 0x30, XF 0x1018): __GX_SetMatrixIndex, GX_SetCurrentMtx
  * Gen mode (BP 0x00): GX_SetNumTexGens, GX_SetNumChans, GX_SetNumTevStages, GX_SetCullMode
  * Z mode (BP 0x40): GX_SetZMode;  blend mode (BP 0x41): GX_SetBlendMode
  * TEV colour/alpha environment (BP 0xC0+2s, 0xC1+2s): GX_SetTevColorIn/Op, GX_SetTevAlphaIn/Op
  * TEV order (BP 0x28 + s/2): GX_SetTevOrder;  TEV registers (BP 0xE0+2r, 0xE1+2r): GX_SetTevColor
  * Projection (XF 0x1020, 7 words): GX_LoadProjectionMtx;  viewport (XF 0x101A): GX_SetViewport
MIT License.
"""
from dataclasses import dataclass, field

# VCD attribute order: the order attributes appear inside a vertex in the FIFO.
VCD_ATTRS = (["pnmtxidx"] + [f"tex{i}mtxidx" for i in range(8)]
             + ["pos", "nrm", "clr0", "clr1"] + [f"tex{i}" for i in range(8)])
NONE, DIRECT, INDEX8, INDEX16 = 0, 1, 2, 3

COMP_SIZE = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4}        # GX_U8, S8, U16, S16, F32
COMP_SIGNED = {0: False, 1: True, 2: False, 3: True, 4: None}
COLOR_SIZE = {0: 2, 1: 3, 2: 4, 3: 2, 4: 3, 5: 4}  # RGB565, RGB8, RGBX8, RGBA4, RGBA6, RGBA8
ARRAY_INDEX = {"pos": 0, "nrm": 1, "clr0": 2, "clr1": 3,
               **{f"tex{i}": 4 + i for i in range(8)}}

CULL_HW = {0: "none", 1: "back", 2: "front", 3: "all"}  # libogc cm2hw = {0, 2, 1, 3}
COMPARE = {0: "never", 1: "less", 2: "equal", 3: "lequal", 4: "greater", 5: "nequal",
           6: "gequal", 7: "always"}
BLEND_FACTOR = {0: "zero", 1: "one", 2: "srcclr_or_dstclr", 3: "inv_srcclr_or_dstclr",
                4: "srcalpha", 5: "invsrcalpha", 6: "dstalpha", 7: "invdstalpha"}


def bits(value, shift, width):
    return (value >> shift) & ((1 << width) - 1)


def decode_vcd(lo, hi):
    """Return {attribute: NONE/DIRECT/INDEX8/INDEX16}."""
    out = {"pnmtxidx": bits(lo, 0, 1)}
    for i in range(8):
        out[f"tex{i}mtxidx"] = bits(lo, 1 + i, 1)
    out["pos"] = bits(lo, 9, 2)
    out["nrm"] = bits(lo, 11, 2)
    out["clr0"] = bits(lo, 13, 2)
    out["clr1"] = bits(lo, 15, 2)
    for i in range(8):
        out[f"tex{i}"] = bits(hi, 2 * i, 2)
    return out


@dataclass
class AttrFormat:
    comptype: int = 0   # POS: 0 XY, 1 XYZ; CLR: 0 RGB, 1 RGBA; TEX: 0 S, 1 ST; NRM: 0 NRM, 1 NBT
    compsize: int = 0   # component type (U8..F32) or colour format (RGB565..RGBA8)
    frac: int = 0


@dataclass
class VertexAttrTable:
    attrs: dict = field(default_factory=dict)
    byte_dequant: bool = False
    normal_index3: bool = False


def decode_vat(g0, g1, g2):
    """Decode the three VAT group registers of one vertex format."""
    a = {
        "pos": AttrFormat(bits(g0, 0, 1), bits(g0, 1, 3), bits(g0, 4, 5)),
        "nrm": AttrFormat(bits(g0, 9, 1), bits(g0, 10, 3), 0),
        "clr0": AttrFormat(bits(g0, 13, 1), bits(g0, 14, 3), 0),
        "clr1": AttrFormat(bits(g0, 17, 1), bits(g0, 18, 3), 0),
        "tex0": AttrFormat(bits(g0, 21, 1), bits(g0, 22, 3), bits(g0, 25, 5)),
        "tex1": AttrFormat(bits(g1, 0, 1), bits(g1, 1, 3), bits(g1, 4, 5)),
        "tex2": AttrFormat(bits(g1, 9, 1), bits(g1, 10, 3), bits(g1, 13, 5)),
        "tex3": AttrFormat(bits(g1, 18, 1), bits(g1, 19, 3), bits(g1, 22, 5)),
        "tex4": AttrFormat(bits(g1, 27, 1), bits(g1, 28, 3), bits(g2, 0, 5)),
        "tex5": AttrFormat(bits(g2, 5, 1), bits(g2, 6, 3), bits(g2, 9, 5)),
        "tex6": AttrFormat(bits(g2, 14, 1), bits(g2, 15, 3), bits(g2, 18, 5)),
        "tex7": AttrFormat(bits(g2, 23, 1), bits(g2, 24, 3), bits(g2, 27, 5)),
    }
    return VertexAttrTable(a, bool(bits(g0, 30, 1)), bool(bits(g0, 31, 1)))


@dataclass
class GenMode:
    num_texgens: int
    num_chans: int
    num_tev_stages: int
    cull: str


def decode_genmode(v):
    return GenMode(bits(v, 0, 4), bits(v, 4, 3), bits(v, 10, 4) + 1, CULL_HW[bits(v, 14, 2)])


@dataclass
class ZMode:
    enable: bool
    func: str
    update: bool


def decode_zmode(v):
    return ZMode(bool(bits(v, 0, 1)), COMPARE[bits(v, 1, 3)], bool(bits(v, 4, 1)))


@dataclass
class BlendMode:
    enable: bool
    logic: bool
    subtract: bool
    src: int
    dst: int
    logic_op: int


def decode_blend(v):
    return BlendMode(bool(bits(v, 0, 1)), bool(bits(v, 1, 1)), bool(bits(v, 11, 1)),
                     bits(v, 8, 3), bits(v, 5, 3), bits(v, 12, 4))


@dataclass
class TevEnv:
    a: int
    b: int
    c: int
    d: int
    subtract: bool
    bias: int       # 0 zero, 1 +0.5, 2 -0.5, 3 compare mode
    scale: int      # 0 x1, 1 x2, 2 x4, 3 x0.5 (in compare mode: the comparison kind)
    clamp: bool
    dest: int       # 0 PREV, 1 REG0, 2 REG1, 3 REG2


def decode_tev_color_env(v):
    return TevEnv(bits(v, 12, 4), bits(v, 8, 4), bits(v, 4, 4), bits(v, 0, 4),
                  bool(bits(v, 18, 1)), bits(v, 16, 2), bits(v, 20, 2), bool(bits(v, 19, 1)),
                  bits(v, 22, 2))


def decode_tev_alpha_env(v):
    return TevEnv(bits(v, 13, 3), bits(v, 10, 3), bits(v, 7, 3), bits(v, 4, 3),
                  bool(bits(v, 18, 1)), bits(v, 16, 2), bits(v, 20, 2), bool(bits(v, 19, 1)),
                  bits(v, 22, 2))


@dataclass
class TevOrder:
    texmap: int
    texcoord: int
    tex_enable: bool
    color: int


def decode_tev_order(v, stage):
    """BP 0x28 + stage // 2 holds two stages; odd stages use the upper half."""
    s = 12 if stage & 1 else 0
    return TevOrder(bits(v, s, 3), bits(v, s + 3, 3), bool(bits(v, s + 6, 1)), bits(v, s + 7, 3))


def decode_tev_reg_pair(lo_word, hi_word):
    """BP 0xE0+2r (alpha bits 12..19, red bits 0..7) and 0xE1+2r (green 12..19, blue 0..7).

    The hardware fields are 11-bit signed; values written by GX_SetTevColor fit in 8 bits.
    Returns (r, g, b, a) as integers."""
    def s11(x):
        x &= 0x7FF
        return x - 0x800 if x & 0x400 else x
    return (s11(bits(lo_word, 0, 11)), s11(bits(hi_word, 12, 11)), s11(bits(hi_word, 0, 11)),
            s11(bits(lo_word, 12, 11)))


def projection_matrix(words):
    """XF 0x1020..0x1026: six floats and a type word (0 perspective, 1 orthographic)."""
    p0, p1, p2, p3, p4, p5 = words[:6]
    if words[6] == 0:
        return [[p0, 0.0, p1, 0.0], [0.0, p2, p3, 0.0], [0.0, 0.0, p4, p5], [0.0, 0.0, -1.0, 0.0]]
    return [[p0, 0.0, 0.0, p1], [0.0, p2, 0.0, p3], [0.0, 0.0, p4, p5], [0.0, 0.0, 0.0, 1.0]]


def decode_color(raw, fmt):
    """Decode a vertex colour of the given GX format to (r, g, b, a) in 0..255."""
    def x5(v):
        return (v << 3) | (v >> 2)

    def x6(v):
        return (v << 2) | (v >> 4)

    if fmt == 0:   # RGB565
        v = int.from_bytes(raw[:2], "big")
        return (x5(v >> 11), x6((v >> 5) & 63), x5(v & 31), 255)
    if fmt == 1:   # RGB8
        return (raw[0], raw[1], raw[2], 255)
    if fmt == 2:   # RGBX8
        return (raw[0], raw[1], raw[2], 255)
    if fmt == 3:   # RGBA4
        v = int.from_bytes(raw[:2], "big")
        return tuple(((v >> s) & 15) * 17 for s in (12, 8, 4, 0))
    if fmt == 4:   # RGBA6
        v = int.from_bytes(raw[:3], "big")
        return tuple(x6((v >> s) & 63) for s in (18, 12, 6, 0))
    return (raw[0], raw[1], raw[2], raw[3])  # RGBA8


# ---- texture maps (libogc GX_InitTexObj, GX_LoadTexObjPreloaded) -------------------------
# BP register numbers per texture map 0..7: _gxtexmode0ids, _gxteximg0ids, _gxteximg3ids.
TEX_MODE0 = (0x80, 0x81, 0x82, 0x83, 0xA0, 0xA1, 0xA2, 0xA3)
TEX_IMAGE0 = (0x88, 0x89, 0x8A, 0x8B, 0xA8, 0xA9, 0xAA, 0xAB)
TEX_IMAGE3 = (0x94, 0x95, 0x96, 0x97, 0xB4, 0xB5, 0xB6, 0xB7)
# GX_TF_* (gc/ogc/gx.h) for the formats spm3ds/gx/textures.py decodes; CI4/CI8/CI14 need
# palettes (TLUT) and are not supported yet.
TEX_FORMATS = {0: "I4", 1: "I8", 2: "IA4", 3: "IA8", 4: "RGB565", 5: "RGB5A3", 6: "RGBA8",
               14: "CMPR"}
WRAP = {0: "clamp", 1: "repeat", 2: "mirror"}  # GX_CLAMP, GX_REPEAT, GX_MIRROR


@dataclass
class TexImage:
    width: int
    height: int
    fmt: int
    address: int      # physical


def decode_tex_mode0(v):
    """(wrap_s, wrap_t, mag_linear, min_filter) from texture mode 0."""
    return WRAP.get(bits(v, 0, 2), "clamp"), WRAP.get(bits(v, 2, 2), "clamp"), \
        bool(bits(v, 4, 1)), bits(v, 5, 3)


def decode_tex_image(image0, image3):
    """Size and format from image 0; the physical address (shifted right by 5) from image 3."""
    return TexImage(bits(image0, 0, 10) + 1, bits(image0, 10, 10) + 1, bits(image0, 20, 4),
                    (image3 & 0xFFFFFF) << 5)
