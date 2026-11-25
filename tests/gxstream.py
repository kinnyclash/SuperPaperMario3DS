"""Test-side GX FIFO builder that packs registers the way libogc's gx.c does.

Each method is named after the libogc function it mirrors and uses the same shifts and
register numbers (read from devkitPro libogc, libogc/gx.c). It does not use spm3ds.gx.regs,
so the decoder gets an independent check. Only the calls the tests need are implemented.
"""
import struct

# libogc constants (gc/ogc/gx.h)
GX_NONE, GX_DIRECT, GX_INDEX8, GX_INDEX16 = 0, 1, 2, 3
GX_VA_PTNMTXIDX, GX_VA_POS, GX_VA_NRM, GX_VA_CLR0, GX_VA_CLR1, GX_VA_TEX0 = 0, 9, 10, 11, 12, 13
GX_U8, GX_S8, GX_U16, GX_S16, GX_F32 = 0, 1, 2, 3, 4
GX_RGB565, GX_RGB8, GX_RGBX8, GX_RGBA4, GX_RGBA6, GX_RGBA8 = 0, 1, 2, 3, 4, 5
GX_POS_XY, GX_POS_XYZ = 0, 1
GX_CLR_RGB, GX_CLR_RGBA = 0, 1
GX_TEX_S, GX_TEX_ST = 0, 1
GX_QUADS, GX_TRIANGLES, GX_TRIANGLESTRIP, GX_TRIANGLEFAN = 0x80, 0x90, 0x98, 0xA0
GX_CULL_NONE, GX_CULL_FRONT, GX_CULL_BACK, GX_CULL_ALL = 0, 1, 2, 3
GX_PERSPECTIVE, GX_ORTHOGRAPHIC = 0, 1
GX_NEVER, GX_LESS, GX_EQUAL, GX_LEQUAL, GX_GREATER, GX_NEQUAL, GX_GEQUAL, GX_ALWAYS = range(8)
GX_BM_NONE, GX_BM_BLEND, GX_BM_LOGIC, GX_BM_SUBTRACT = 0, 1, 2, 3
GX_BL_ZERO, GX_BL_ONE, GX_BL_SRCCLR, GX_BL_INVSRCCLR, GX_BL_SRCALPHA, GX_BL_INVSRCALPHA = range(6)
GX_CC = {"CPREV": 0, "APREV": 1, "C0": 2, "A0": 3, "C1": 4, "A1": 5, "C2": 6, "A2": 7,
         "TEXC": 8, "TEXA": 9, "RASC": 10, "RASA": 11, "ONE": 12, "HALF": 13, "KONST": 14,
         "ZERO": 15}
GX_CA = {"APREV": 0, "A0": 1, "A1": 2, "A2": 3, "TEXA": 4, "RASA": 5, "KONST": 6, "ZERO": 7}
GX_TEV_ADD, GX_TEV_SUB = 0, 1
GX_TEVPREV, GX_TEVREG0, GX_TEVREG1, GX_TEVREG2 = 0, 1, 2, 3
GX_COLOR0A0, GX_COLORNULL = 4, 0xFF
_GXTEVCOLID = {4: 0, 5: 1}  # libogc _gxtevcolid for COLOR0A0 / COLOR1A1


def _sh(v, s, w):
    return (v & ((1 << w) - 1)) << s


class GXStream:
    def __init__(self):
        self.out = bytearray()
        self.vcd_lo = 0
        self.vcd_hi = 0
        self.vat = [[0, 0, 0] for _ in range(8)]
        self.gen_mode = 0
        self.mtx_idx_lo = 0
        self.tev_color = [0] * 16
        self.tev_alpha = [0] * 16
        self.tev_order = [0] * 8

    # ---- raw FIFO commands (libogc GX_LOAD_* macros) ----
    def cp(self, reg, value):
        self.out += bytes([0x08, reg]) + struct.pack(">I", value & 0xFFFFFFFF)

    def bp(self, word):
        self.out += b"\x61" + struct.pack(">I", word & 0xFFFFFFFF)

    def xf(self, base, words):
        self.out += b"\x10" + struct.pack(">I", ((len(words) - 1) << 16) | base)
        for w in words:
            self.out += struct.pack(">I", w & 0xFFFFFFFF)

    def xf_f(self, base, floats):
        self.xf(base, [struct.unpack(">I", struct.pack(">f", f))[0] for f in floats])

    # ---- vertex description ----
    def SetVtxDesc(self, attr, kind):
        if attr == GX_VA_PTNMTXIDX:
            self.vcd_lo = (self.vcd_lo & ~0x1) | (kind & 1)
        elif attr == GX_VA_POS:
            self.vcd_lo = (self.vcd_lo & ~0x600) | _sh(kind, 9, 2)
        elif attr == GX_VA_NRM:
            self.vcd_lo = (self.vcd_lo & ~0x1800) | _sh(kind, 11, 2)
        elif attr == GX_VA_CLR0:
            self.vcd_lo = (self.vcd_lo & ~0x6000) | _sh(kind, 13, 2)
        elif attr == GX_VA_CLR1:
            self.vcd_lo = (self.vcd_lo & ~0x18000) | _sh(kind, 15, 2)
        elif GX_VA_TEX0 <= attr < GX_VA_TEX0 + 8:
            i = attr - GX_VA_TEX0
            self.vcd_hi = (self.vcd_hi & ~(3 << (2 * i))) | _sh(kind, 2 * i, 2)
        self.cp(0x50, self.vcd_lo)
        self.cp(0x60, self.vcd_hi)

    def SetVtxAttrFmt(self, fmt, attr, comptype, compsize, frac):
        g = self.vat[fmt & 7]
        if attr == GX_VA_POS:
            g[0] = (g[0] & ~0x1FF) | (comptype & 1) | _sh(compsize, 1, 3) | _sh(frac, 4, 5)
            if frac:
                g[0] |= 0x40000000
        elif attr == GX_VA_NRM:
            g[0] = (g[0] & ~0x1E00) | _sh(compsize, 10, 3)
        elif attr == GX_VA_CLR0:
            g[0] = (g[0] & ~0x1E000) | _sh(comptype, 13, 1) | _sh(compsize, 14, 3)
        elif attr == GX_VA_TEX0:
            g[0] = (g[0] & ~0x3FE00000) | _sh(comptype, 21, 1) | _sh(compsize, 22, 3) \
                | _sh(frac, 25, 5)
            if frac:
                g[0] |= 0x40000000
        for k in range(3):
            self.cp(0x70 + 0x10 * k + (fmt & 7), g[k])

    def SetArray(self, attr, phys_addr, stride):
        idx = attr - GX_VA_POS
        self.cp(0xA0 + idx, phys_addr)
        self.cp(0xB0 + idx, stride)

    # ---- transform ----
    def LoadProjectionMtx(self, mt, kind):
        tmp = [mt[0][0], 0.0, mt[1][1], 0.0, mt[2][2], mt[2][3]]
        if kind == GX_PERSPECTIVE:
            tmp[1], tmp[3] = mt[0][2], mt[1][2]
        else:
            tmp[1], tmp[3] = mt[0][3], mt[1][3]
        words = [struct.unpack(">I", struct.pack(">f", f))[0] for f in tmp] + [kind]
        self.xf(0x1020, words)

    def LoadPosMtxImm(self, mt, pnidx):
        self.xf_f(pnidx << 2, [mt[r][c] for r in range(3) for c in range(4)])

    def SetCurrentMtx(self, mtx):
        self.mtx_idx_lo = (self.mtx_idx_lo & ~0x3F) | (mtx & 0x3F)
        self.cp(0x30, self.mtx_idx_lo)
        self.xf(0x1018, [self.mtx_idx_lo])

    def SetViewport(self, x, y, wd, ht, near, far):
        # GX_SetViewport calls GX_SetViewportJitter with field = 1: no half-line offset.
        self.xf_f(0x101A, [wd * 0.5, -ht * 0.5, 16777215.0 * far - 16777215.0 * near,
                           x + wd * 0.5 + 342.0, y + ht * 0.5 + 342.0, 16777215.0 * far])

    # ---- BP state ----
    def SetCullMode(self, mode):
        cm2hw = [0, 2, 1, 3]
        self.gen_mode = (self.gen_mode & ~0xC000) | _sh(cm2hw[mode], 14, 2)
        self.bp(self.gen_mode)  # register 0x00: the top byte is zero

    def SetNumTevStages(self, num):
        self.gen_mode = (self.gen_mode & ~0x3C00) | _sh(num - 1, 10, 4)
        self.bp(self.gen_mode)

    def SetZMode(self, enable, func, update):
        self.bp((0x40 << 24) | (enable & 1) | _sh(func, 1, 3) | _sh(update, 4, 1))

    def SetBlendMode(self, kind, src, dst, op=0):
        v = 0
        if kind in (GX_BM_BLEND, GX_BM_SUBTRACT):
            v |= 1
        if kind == GX_BM_SUBTRACT:
            v |= 0x800
        if kind == GX_BM_LOGIC:
            v |= 2
        v |= _sh(op, 12, 4) | _sh(dst, 5, 3) | _sh(src, 8, 3)
        self.bp((0x41 << 24) | v)

    def SetTevColorIn(self, stage, a, b, c, d):
        r = self.tev_color[stage] & ~0xFFFF
        self.tev_color[stage] = r | _sh(GX_CC[a], 12, 4) | _sh(GX_CC[b], 8, 4) \
            | _sh(GX_CC[c], 4, 4) | _sh(GX_CC[d], 0, 4)
        self.bp(((0xC0 + 2 * stage) << 24) | self.tev_color[stage])

    def SetTevAlphaIn(self, stage, a, b, c, d):
        r = self.tev_alpha[stage] & ~0xFFF0
        self.tev_alpha[stage] = r | _sh(GX_CA[a], 13, 3) | _sh(GX_CA[b], 10, 3) \
            | _sh(GX_CA[c], 7, 3) | _sh(GX_CA[d], 4, 3)
        self.bp(((0xC1 + 2 * stage) << 24) | self.tev_alpha[stage])

    def _op(self, value, op, bias, scale, clamp, dest):
        value &= ~0xFF0000
        value |= _sh(op, 18, 1) | _sh(scale, 20, 2) | _sh(bias, 16, 2) | _sh(clamp, 19, 1) \
            | _sh(dest, 22, 2)
        return value

    def SetTevColorOp(self, stage, op, bias, scale, clamp, dest):
        self.tev_color[stage] = self._op(self.tev_color[stage], op, bias, scale, clamp, dest)
        self.bp(((0xC0 + 2 * stage) << 24) | self.tev_color[stage])

    def SetTevAlphaOp(self, stage, op, bias, scale, clamp, dest):
        self.tev_alpha[stage] = self._op(self.tev_alpha[stage], op, bias, scale, clamp, dest)
        self.bp(((0xC1 + 2 * stage) << 24) | self.tev_alpha[stage])

    def SetTevOrder(self, stage, texcoord, texmap, color):
        reg = stage >> 1
        enable = 0 if texmap == 0xFF else 1
        texm = 0 if texmap == 0xFF else texmap
        colid = 7 if color == GX_COLORNULL else _GXTEVCOLID[color]
        s = 12 if stage & 1 else 0
        v = self.tev_order[reg] & ~(0x3FF << s)
        v |= _sh(texm, s, 3) | _sh(texcoord, s + 3, 3) | _sh(enable, s + 6, 1) | _sh(colid, s + 7, 3)
        self.tev_order[reg] = v
        self.bp(((0x28 + reg) << 24) | v)

    def SetTevColor(self, reg, rgba):
        r, g, b, a = rgba
        self.bp(((0xE0 + 2 * reg) << 24) | _sh(a, 12, 8) | (r & 0xFF))
        self.bp(((0xE1 + 2 * reg) << 24) | _sh(g, 12, 8) | (b & 0xFF))

    # ---- drawing ----
    def Begin(self, prim, fmt, count):
        self.out += bytes([prim | (fmt & 7)]) + struct.pack(">H", count)

    def raw(self, fmt, *values):
        self.out += struct.pack(">" + fmt, *values)

    def data(self):
        return bytes(self.out)
