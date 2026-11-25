"""Extended PowerPC instructions: overflow forms, CR logic, cache and sync, more memory forms,
FPSCR access, extra floating point and paired-single operations, and generic SPRs.

Each instruction has three parts kept side by side here: the decoder (decode_ext), the
reference interpreter rule (exec_ext) and the C translation (ext_lines). The base decoder in
ppc.py calls decode_ext first; decode_ext only claims encodings the base decoder rejects, so
the two never disagree about an instruction.

Sources: the encodings and semantics follow the PowerPC Microprocessor Family Programming
Environments manual (IBM) as summarised in public references; the Gekko paired-single
additions follow the same public descriptions used for the base paired-single support.

Simplifications, all documented in docs/PPC_COVERAGE.md:
  * FPSCR is stored and moved (mffs, mtfsf, mtfsb0/1, mtfsfi, mcrfs) but exception bits are
    not computed by arithmetic, and the rounding mode is not applied (always nearest even).
  * fres, frsqrte, ps_res and ps_rsqrte return exact results, not the hardware estimates.
  * mftb returns a counter the runtime sets (c->tb / Machine.tb); it does not advance alone.
  * SPRs other than LR, CTR, XER and the GQRs are plain storage.
MIT License.
"""
import math

M32 = 0xFFFFFFFF
SO_BIT = 1 << 31
OV_BIT = 1 << 30
CA_BIT = 1 << 29

# Base XO-form names that have an overflow-enable (OE=1) variant, keyed by 9-bit xo.
XO_OE = {266: "add", 40: "subf", 235: "mullw", 491: "divw", 459: "divwu", 104: "neg",
         10: "addc", 8: "subfc", 138: "adde", 136: "subfe", 202: "addze", 200: "subfze",
         234: "addme", 232: "subfme"}
OE_UNARY = {"neg", "addze", "subfze", "addme", "subfme"}
OE_OPS = {name + "o" for name in XO_OE.values()}

CR_LOGIC = {257: "crand", 129: "crandc", 289: "creqv", 225: "crnand", 33: "crnor",
            449: "cror", 417: "crorc", 193: "crxor"}
CR_LOGIC_PY = {
    "crand": lambda a, b: a & b,
    "crandc": lambda a, b: a & (b ^ 1),
    "creqv": lambda a, b: (a ^ b) ^ 1,
    "crnand": lambda a, b: (a & b) ^ 1,
    "crnor": lambda a, b: (a | b) ^ 1,
    "cror": lambda a, b: a | b,
    "crorc": lambda a, b: a | (b ^ 1),
    "crxor": lambda a, b: a ^ b,
}
CR_LOGIC_C = {
    "crand": "({a} & {b})", "crandc": "({a} & ({b} ^ 1u))", "creqv": "(({a} ^ {b}) ^ 1u)",
    "crnand": "(({a} & {b}) ^ 1u)", "crnor": "(({a} | {b}) ^ 1u)", "cror": "({a} | {b})",
    "crorc": "({a} | ({b} ^ 1u))", "crxor": "({a} ^ {b})",
}

# Instructions with no effect on emulated state (caches are not modelled).
NOPS_31 = {86: "dcbf", 54: "dcbst", 278: "dcbt", 246: "dcbtst", 470: "dcbi", 982: "icbi",
           598: "sync", 854: "eieio"}

# GPR loads: name -> (size, sign-extend, indexed, update, byte-reversed)
EXT_LOAD = {
    "lhau": (2, True, False, True, False),
    "lhax": (2, True, True, False, False),
    "lhaux": (2, True, True, True, False),
    "lwzux": (4, False, True, True, False),
    "lbzux": (1, False, True, True, False),
    "lhzux": (2, False, True, True, False),
    "lwbrx": (4, False, True, False, True),
    "lhbrx": (2, False, True, False, True),
}
# GPR stores: name -> (size, indexed, update, byte-reversed)
EXT_STORE = {
    "stwux": (4, True, True, False),
    "stbux": (1, True, True, False),
    "sthux": (2, True, True, False),
    "stwbrx": (4, True, False, True),
    "sthbrx": (2, True, False, True),
}
# FP loads: name -> (single, indexed, update)
EXT_FLOAD = {
    "lfsu": (True, False, True), "lfdu": (False, False, True),
    "lfsx": (True, True, False), "lfsux": (True, True, True),
    "lfdx": (False, True, False), "lfdux": (False, True, True),
}
# FP stores: name -> (kind, indexed, update); kind "s" single, "d" double, "iw" low word
EXT_FSTORE = {
    "stfsu": ("s", False, True), "stfdu": ("d", False, True),
    "stfsx": ("s", True, False), "stfsux": ("s", True, True),
    "stfdx": ("d", True, False), "stfdux": ("d", True, True),
    "stfiwx": ("iw", True, False),
}
X_LOAD_XO = {343: "lhax", 375: "lhaux", 55: "lwzux", 119: "lbzux", 311: "lhzux",
             534: "lwbrx", 790: "lhbrx"}
X_STORE_XO = {183: "stwux", 247: "stbux", 439: "sthux", 662: "stwbrx", 918: "sthbrx"}
X_FLOAD_XO = {535: "lfsx", 567: "lfsux", 599: "lfdx", 631: "lfdux"}
X_FSTORE_XO = {663: "stfsx", 695: "stfsux", 727: "stfdx", 759: "stfdux", 983: "stfiwx"}
D_EXT = {43: "lhau", 49: "lfsu", 51: "lfdu", 53: "stfsu", 55: "stfdu", 46: "lmw", 47: "stmw"}

FP_EXT_59 = {28: "fmsubs", 29: "fmadds", 30: "fnmsubs", 31: "fnmadds"}
PS_EXT_A = {10: "ps_sum0", 11: "ps_sum1", 12: "ps_muls0", 13: "ps_muls1",
            14: "ps_madds0", 15: "ps_madds1", 23: "ps_sel", 24: "ps_res", 26: "ps_rsqrte"}
PS_CMP = {0: "ps_cmpu0", 32: "ps_cmpo0", 64: "ps_cmpu1", 96: "ps_cmpo1"}
PSQ_X = {6: "psq_lx", 38: "psq_lux", 7: "psq_stx", 39: "psq_stux"}

SPR_XER = 1
SPR_TBL = 268
SPR_TBU = 269

EXT_OPS = (OE_OPS | set(CR_LOGIC.values()) | set(NOPS_31.values()) | {"isync"}
           | set(EXT_LOAD) | set(EXT_STORE) | set(EXT_FLOAD) | set(EXT_FSTORE)
           | {"lmw", "stmw", "dcbz", "mcrf", "mcrxr", "mtcrf", "mfmsr", "mtmsr", "mftb",
              "mfspr_x", "mtspr_x", "fres", "frsqrte", "fsel", "fctiw", "mffs", "mtfsf",
              "mtfsb0", "mtfsb1", "mtfsfi", "mcrfs"}
           | set(FP_EXT_59.values()) | set(PS_EXT_A.values()) | set(PS_CMP.values())
           | set(PSQ_X.values()))


# ---------------------------------------------------------------- decoder

def decode_ext(w, addr):
    """Return an Insn for an encoding only this module handles, or None."""
    from .ppc import SPR_CTR, SPR_GQR0, SPR_LR, Insn, Unsupported, _spr, sext

    op = w >> 26
    d = (w >> 21) & 31
    a = (w >> 16) & 31
    b = (w >> 11) & 31
    c = (w >> 6) & 31
    rc = bool(w & 1)

    def ins(name, **fields):
        return Insn(addr, name, **fields)

    def bad(what):
        return Unsupported(f"{addr:#010x} [{w:08x}]: {what}")

    if op in D_EXT:
        name = D_EXT[op]
        imm = sext(w, 16)
        if name == "lmw":
            if d <= a <= 31 and not (a == 0 and d != 0):
                raise bad("lmw with rA in the loaded range")
            return ins("lmw", rd=d, ra=a, imm=imm)
        if name == "stmw":
            return ins("stmw", rd=d, ra=a, imm=imm)
        if a == 0:
            raise bad(f"{name} with rA = 0")
        if name == "lhau" and a == d:
            raise bad("lhau with rA = rD")
        if name in EXT_LOAD:
            return ins(name, rd=d, ra=a, imm=imm)
        return ins(name, fd=d, ra=a, imm=imm)

    if op == 19:
        xo = (w >> 1) & 0x3FF
        if xo in CR_LOGIC:
            return ins(CR_LOGIC[xo], rd=d, ra=a, rb=b)
        if xo == 0:
            return ins("mcrf", crf=(w >> 23) & 7, crfs=(w >> 18) & 7)
        if xo == 150:
            return ins("isync")
        return None

    if op == 31:
        x9 = (w >> 1) & 0x1FF
        if (w >> 10) & 1 and x9 in XO_OE:
            name = XO_OE[x9] + "o"
            if XO_OE[x9] in OE_UNARY:
                return ins(name, rd=d, ra=a, rc=rc)
            return ins(name, rd=d, ra=a, rb=b, rc=rc)
        x10 = (w >> 1) & 0x3FF
        if x10 in NOPS_31:
            return ins(NOPS_31[x10], ra=a, rb=b)
        if x10 == 1014:
            return ins("dcbz", ra=a, rb=b)
        if x10 in X_LOAD_XO or x10 in X_STORE_XO:
            name = X_LOAD_XO.get(x10) or X_STORE_XO[x10]
            is_update = EXT_LOAD[name][3] if name in EXT_LOAD else EXT_STORE[name][2]
            if is_update and (a == 0 or (name in EXT_LOAD and a == d)):
                raise bad(f"{name} with invalid base register")
            return ins(name, rd=d, ra=a, rb=b)
        if x10 in X_FLOAD_XO or x10 in X_FSTORE_XO:
            name = X_FLOAD_XO.get(x10) or X_FSTORE_XO[x10]
            spec = EXT_FLOAD.get(name) or EXT_FSTORE[name]
            if spec[2] and a == 0:
                raise bad(f"{name} with rA = 0")
            return ins(name, fd=d, ra=a, rb=b)
        if x10 == 144:
            if (w >> 20) & 1:
                raise bad("mtocrf (one-field form) is not a Gekko instruction")
            return ins("mtcrf", rd=d, crm=(w >> 12) & 0xFF)
        if x10 == 512:
            return ins("mcrxr", crf=(w >> 23) & 7)
        if x10 == 83:
            return ins("mfmsr", rd=d)
        if x10 == 146:
            return ins("mtmsr", rd=d)
        if x10 == 371:
            tbr = _spr(w)
            if tbr not in (SPR_TBL, SPR_TBU):
                raise bad(f"mftb of TBR {tbr}")
            return ins("mftb", rd=d, spr=tbr)
        if x10 in (339, 467):
            spr = _spr(w)
            if spr in (SPR_LR, SPR_CTR) or SPR_GQR0 <= spr <= SPR_GQR0 + 7 or rc:
                return None  # the base decoder handles (or rejects) these
            return ins("mfspr_x" if x10 == 339 else "mtspr_x", rd=d, spr=spr)
        return None

    if op == 59:
        x5 = (w >> 1) & 31
        if x5 in FP_EXT_59 or x5 == 24:
            if rc:
                raise bad("Rc on FP arithmetic")
            if x5 == 24:
                return ins("fres", fd=d, fb=b)
            return ins(FP_EXT_59[x5], fd=d, fa=a, fb=b, fc=c)
        return None

    if op == 63:
        x5 = (w >> 1) & 31
        if x5 in (23, 26):
            if rc:
                raise bad("Rc on FP")
            if x5 == 23:
                return ins("fsel", fd=d, fa=a, fb=b, fc=c)
            return ins("frsqrte", fd=d, fb=b)
        x10 = (w >> 1) & 0x3FF
        if x10 in (14, 583, 711, 70, 38, 134, 64) and rc:
            raise bad("Rc on FPSCR or FP conversion")
        if x10 == 14:
            return ins("fctiw", fd=d, fb=b)
        if x10 == 583:
            return ins("mffs", fd=d)
        if x10 == 711:
            return ins("mtfsf", crm=(w >> 17) & 0xFF, fb=b)
        if x10 == 70:
            return ins("mtfsb0", rd=d)
        if x10 == 38:
            return ins("mtfsb1", rd=d)
        if x10 == 134:
            return ins("mtfsfi", crf=(w >> 23) & 7, imm=(w >> 12) & 0xF)
        if x10 == 64:
            return ins("mcrfs", crf=(w >> 23) & 7, crfs=(w >> 18) & 7)
        return None

    if op == 4:
        x5 = (w >> 1) & 31
        if x5 in PS_EXT_A:
            if rc:
                raise bad("Rc on paired-single arithmetic")
            return ins(PS_EXT_A[x5], fd=d, fa=a, fb=b, fc=c)
        x6 = (w >> 1) & 63
        if x6 in PSQ_X:
            name = PSQ_X[x6]
            if name in ("psq_lux", "psq_stux") and a == 0:
                raise bad(f"{name} with rA = 0")
            return ins(name, fd=d, ra=a, rb=b, wbit=(w >> 10) & 1, gqr=(w >> 7) & 7)
        x10 = (w >> 1) & 0x3FF
        if x10 in PS_CMP:
            if (w >> 21) & 3:
                raise bad("ps_cmp with reserved bits")
            return ins(PS_CMP[x10], crf=(w >> 23) & 7, fa=a, fb=b)
        return None

    return None


# ---------------------------------------------------------------- shared helpers

def crbit(cr, i):
    return (cr >> (31 - i)) & 1


def set_crbit(cr, i, v):
    sh = 31 - i
    return (cr & ~(1 << sh) & M32) | ((v & 1) << sh)


def field_mask(fm):
    """8-bit field mask (bit 7 = field 0) -> 32-bit mask."""
    m = 0
    for i in range(8):
        if fm & (0x80 >> i):
            m |= 0xF << (28 - 4 * i)
    return m


def add_ov(a, b, value):
    """Signed overflow of a + b (+ carry in) giving value."""
    return (((a ^ value) & (b ^ value)) >> 31) & 1


def fres(b):
    from .ppc import fdiv, round_single
    return round_single(fdiv(1.0, b))


def frsqrte(b):
    if math.isnan(b):
        return b
    if b < 0.0:
        return math.nan
    if b == 0.0:
        return math.copysign(math.inf, b)
    if math.isinf(b):
        return 0.0
    return 1.0 / math.sqrt(b)


def fctiw_bits(x):
    """Round to nearest, ties to even (FPSCR RN is not modelled), saturate, NaN -> INT32_MIN."""
    if math.isnan(x):
        v = -0x80000000
    elif math.isinf(x):
        v = 0x7FFFFFFF if x > 0 else -0x80000000
    else:
        r = round(x)  # Python rounds halves to even
        v = max(-0x80000000, min(0x7FFFFFFF, r))
    return 0xFFF8000000000000 | (v & M32)


def bswap(value, size):
    return int.from_bytes((value & ((1 << (8 * size)) - 1)).to_bytes(size, "big"), "little")


# ---------------------------------------------------------------- interpreter

def exec_ext(m, insn):
    """Execute an extended instruction on Machine m."""
    from .ppc import (CARRY_OPS, add_carry, bits_to_double, divw, divwu, double_to_bits, fma,
                      fp_cmp_field, round_single, s32, sext, single_bits, single_value)
    op = insn.op
    r, f = m.r, m.f

    if op in OE_OPS:
        base = op[:-1]
        ra_v = r[insn.ra]
        rb_v = r[insn.rb]
        if base in CARRY_OPS:
            ca = (m.xer >> 29) & 1
            a_inv, b_src, cin_src = CARRY_OPS[base]
            a = (~ra_v if a_inv else ra_v) & M32
            b = {"rb": rb_v, "minus1": M32, "zero": 0}[b_src]
            cin = {"zero": 0, "one": 1, "ca": ca}[cin_src]
            value, carry = add_carry(a, b, cin)
            m._set_ca(carry)
            ov = add_ov(a, b, value)
        elif base in ("add", "subf"):
            a = ra_v if base == "add" else (~ra_v & M32)
            cin = 0 if base == "add" else 1
            value = (a + rb_v + cin) & M32
            ov = add_ov(a, rb_v, value)
        elif base == "neg":
            value = (-ra_v) & M32
            ov = 1 if ra_v == 0x80000000 else 0
        elif base == "mullw":
            p = s32(ra_v) * s32(rb_v)
            value = p & M32
            ov = 0 if -0x80000000 <= p <= 0x7FFFFFFF else 1
        elif base == "divw":
            value = divw(ra_v, rb_v) & M32
            ov = 1 if rb_v == 0 or (ra_v == 0x80000000 and rb_v == M32) else 0
        else:  # divwu
            value = divwu(ra_v, rb_v) & M32
            ov = 1 if rb_v == 0 else 0
        r[insn.rd] = value
        m.xer = (m.xer | OV_BIT | SO_BIT) if ov else (m.xer & ~OV_BIT & M32)
        if insn.rc:
            m._set_cr0(value)
        return

    if op in CR_LOGIC_PY:
        v = CR_LOGIC_PY[op](crbit(m.cr, insn.ra), crbit(m.cr, insn.rb))
        m.cr = set_crbit(m.cr, insn.rd, v)
        return
    if op == "mcrf":
        field = (m.cr >> (28 - 4 * insn.crfs)) & 0xF
        sh = 28 - 4 * insn.crf
        m.cr = (m.cr & ~(0xF << sh) & M32) | (field << sh)
        return
    if op == "mcrxr":
        sh = 28 - 4 * insn.crf
        m.cr = (m.cr & ~(0xF << sh) & M32) | (((m.xer >> 28) & 0xF) << sh)
        m.xer &= 0x0FFFFFFF
        return
    if op == "mtcrf":
        mask = field_mask(insn.crm)
        m.cr = (r[insn.rd] & mask) | (m.cr & ~mask & M32)
        return
    if op in NOPS_31.values() or op == "isync":
        return
    if op == "dcbz":
        base = r[insn.ra] if insn.ra else 0
        ea = (base + r[insn.rb]) & M32 & ~31
        for i in range(0, 32, 4):
            m.mem.write_uint((ea + i) & M32, 4, 0)
        return
    if op == "mfmsr":
        r[insn.rd] = m.msr
        return
    if op == "mtmsr":
        m.msr = r[insn.rd]
        return
    if op == "mftb":
        r[insn.rd] = (m.tb >> 32) & M32 if insn.spr == SPR_TBU else m.tb & M32
        return
    if op == "mfspr_x":
        r[insn.rd] = m.xer if insn.spr == SPR_XER else m.sprs.get(insn.spr, 0)
        return
    if op == "mtspr_x":
        if insn.spr == SPR_XER:
            m.xer = r[insn.rd]
        else:
            m.sprs[insn.spr] = r[insn.rd]
        return

    def ea_of(indexed, update):
        if update:
            base = r[insn.ra]
        else:
            base = r[insn.ra] if insn.ra else 0
        return (base + (r[insn.rb] if indexed else insn.imm)) & M32

    if op in EXT_LOAD:
        size, signed, indexed, update, rev = EXT_LOAD[op]
        ea = ea_of(indexed, update)
        value = m.mem.read_uint(ea, size)
        if rev:
            value = bswap(value, size)
        if signed:
            value = sext(value, 8 * size) & M32
        r[insn.rd] = value
        if update:
            r[insn.ra] = ea
        return
    if op in EXT_STORE:
        size, indexed, update, rev = EXT_STORE[op]
        ea = ea_of(indexed, update)
        value = r[insn.rd] & ((1 << (8 * size)) - 1)
        m.mem.write_uint(ea, size, bswap(value, size) if rev else value)
        if update:
            r[insn.ra] = ea
        return
    if op in EXT_FLOAD:
        single, indexed, update = EXT_FLOAD[op]
        ea = ea_of(indexed, update)
        if single:
            f[insn.fd] = double_to_bits(single_value(m.mem.read_uint(ea, 4)))
        else:
            f[insn.fd] = m.mem.read_uint(ea, 8)
        if update:
            r[insn.ra] = ea
        return
    if op in EXT_FSTORE:
        kind, indexed, update = EXT_FSTORE[op]
        ea = ea_of(indexed, update)
        if kind == "s":
            m.mem.write_uint(ea, 4, single_bits(round_single(bits_to_double(f[insn.fd]))))
        elif kind == "d":
            m.mem.write_uint(ea, 8, f[insn.fd])
        else:
            m.mem.write_uint(ea, 4, f[insn.fd] & M32)
        if update:
            r[insn.ra] = ea
        return
    if op == "lmw":
        ea = ea_of(False, False)
        for i in range(insn.rd, 32):
            r[i] = m.mem.read_uint(ea, 4)
            ea = (ea + 4) & M32
        return
    if op == "stmw":
        ea = ea_of(False, False)
        for i in range(insn.rd, 32):
            m.mem.write_uint(ea, 4, r[i])
            ea = (ea + 4) & M32
        return

    # floating point
    def d(n):
        return bits_to_double(f[n])

    if op in ("fmadds", "fmsubs", "fnmadds", "fnmsubs"):
        a, c, b = d(insn.fa), d(insn.fc), d(insn.fb)
        value = {"fmadds": fma(a, c, b), "fmsubs": fma(a, c, -b),
                 "fnmadds": -fma(a, c, b), "fnmsubs": -fma(a, c, -b)}[op]
        f[insn.fd] = double_to_bits(round_single(value))
        return
    if op == "fres":
        f[insn.fd] = double_to_bits(fres(d(insn.fb)))
        return
    if op == "frsqrte":
        f[insn.fd] = double_to_bits(frsqrte(d(insn.fb)))
        return
    if op == "fsel":
        f[insn.fd] = f[insn.fc] if d(insn.fa) >= 0.0 else f[insn.fb]
        return
    if op == "fctiw":
        f[insn.fd] = fctiw_bits(d(insn.fb))
        return
    if op == "mffs":
        f[insn.fd] = 0xFFF8000000000000 | m.fpscr
        return
    if op == "mtfsf":
        mask = field_mask(insn.crm)
        m.fpscr = (f[insn.fb] & mask) | (m.fpscr & ~mask & M32)
        return
    if op in ("mtfsb0", "mtfsb1"):
        m.fpscr = set_crbit(m.fpscr, insn.rd, 1 if op == "mtfsb1" else 0)
        return
    if op == "mtfsfi":
        sh = 28 - 4 * insn.crf
        m.fpscr = (m.fpscr & ~(0xF << sh) & M32) | ((insn.imm & 0xF) << sh)
        return
    if op == "mcrfs":
        field = (m.fpscr >> (28 - 4 * insn.crfs)) & 0xF
        sh = 28 - 4 * insn.crf
        m.cr = (m.cr & ~(0xF << sh) & M32) | (field << sh)
        return

    # paired singles
    def lanes(n):
        v = f[n]
        return single_value(v >> 32), single_value(v & M32)

    def pack(hi_bits, lo_bits):
        return ((hi_bits & M32) << 32) | (lo_bits & M32)

    if op in PS_EXT_A.values():
        a0, a1 = lanes(insn.fa)
        b0, b1 = lanes(insn.fb)
        c0, c1 = lanes(insn.fc)
        if op == "ps_sum0":
            f[insn.fd] = pack(single_bits(a0 + b1), f[insn.fc] & M32)
        elif op == "ps_sum1":
            f[insn.fd] = pack(f[insn.fc] >> 32, single_bits(a0 + b1))
        elif op in ("ps_muls0", "ps_muls1"):
            k = c0 if op == "ps_muls0" else c1
            f[insn.fd] = pack(single_bits(a0 * k), single_bits(a1 * k))
        elif op in ("ps_madds0", "ps_madds1"):
            k = c0 if op == "ps_madds0" else c1
            f[insn.fd] = pack(single_bits(fma(a0, k, b0)), single_bits(fma(a1, k, b1)))
        elif op == "ps_sel":
            hi = (f[insn.fc] if a0 >= 0.0 else f[insn.fb]) >> 32
            lo = (f[insn.fc] if a1 >= 0.0 else f[insn.fb]) & M32
            f[insn.fd] = pack(hi, lo)
        elif op == "ps_res":
            f[insn.fd] = pack(single_bits(fres(b0)), single_bits(fres(b1)))
        else:  # ps_rsqrte
            f[insn.fd] = pack(single_bits(frsqrte(b0)), single_bits(frsqrte(b1)))
        return
    if op in PS_CMP.values():
        a0, a1 = lanes(insn.fa)
        b0, b1 = lanes(insn.fb)
        if op in ("ps_cmpu0", "ps_cmpo0"):
            m.cr = fp_cmp_field(m.cr, insn.crf, a0, b0)
        else:
            m.cr = fp_cmp_field(m.cr, insn.crf, a1, b1)
        return
    if op in PSQ_X.values():
        import dataclasses
        update = op in ("psq_lux", "psq_stux")
        ea = ea_of(True, update)
        base_op = "psq_l" if op in ("psq_lx", "psq_lux") else "psq_st"
        m._exec_paired(dataclasses.replace(insn, op=base_op, ra=0, imm=ea))
        if update:
            r[insn.ra] = ea
        return
    raise AssertionError(f"exec_ext: no rule for {op}")


# ---------------------------------------------------------------- C translation

def _r(n):
    return f"c->r[{n}]"


def _f(n):
    return f"c->f[{n}]"


def _lit(v):
    return f"0x{v & M32:X}u"


def _rc(insn, dst):
    if insn.rc:
        return [f"c->cr = ppc_cmp_s(c->cr, 0u, (int32_t){_r(dst)}, 0, c->xer);"]
    return []


def _ea_c(insn, indexed, update):
    base = _r(insn.ra) if (update or insn.ra) else "0u"
    disp = _r(insn.rb) if indexed else _lit(insn.imm)
    return f"({base} + {disp})"


def ext_lines(insn):
    """C statements for an extended instruction."""
    from .ppc import CARRY_OPS
    op = insn.op
    rd, ra, rb = insn.rd, insn.ra, insn.rb

    if op in OE_OPS:
        base = op[:-1]
        if base in CARRY_OPS or base in ("add", "subf"):
            if base in CARRY_OPS:
                a_inv, b_src, cin_src = CARRY_OPS[base]
            else:
                a_inv, b_src, cin_src = (base == "subf"), "rb", ("one" if base == "subf" else "zero")
            a = f"~{_r(ra)}" if a_inv else _r(ra)
            b = {"rb": _r(rb), "minus1": "0xFFFFFFFFu", "zero": "0u"}[b_src]
            cin = {"zero": "0u", "one": "1u", "ca": "ppc_ca(c)"}[cin_src]
            calc = "ppc_addx(c, a_, b_, cin_)" if base in CARRY_OPS else "a_ + b_ + cin_"
            lines = [f"{{ uint32_t a_ = {a}, b_ = {b}, cin_ = {cin}; uint32_t v_ = {calc};",
                     "  ppc_set_ov(c, ((a_ ^ v_) & (b_ ^ v_)) >> 31);",
                     f"  {_r(rd)} = v_; }}"]
        elif base == "neg":
            lines = [f"{{ uint32_t a_ = {_r(ra)}; {_r(rd)} = 0u - a_; "
                     "ppc_set_ov(c, a_ == 0x80000000u); }"]
        elif base == "mullw":
            lines = [f"{{ int64_t p_ = (int64_t)(int32_t){_r(ra)} * (int64_t)(int32_t){_r(rb)}; "
                     f"{_r(rd)} = (uint32_t)p_; ppc_set_ov(c, p_ != (int64_t)(int32_t)p_); }}"]
        elif base == "divw":
            lines = [f"{{ uint32_t a_ = {_r(ra)}, b_ = {_r(rb)}; {_r(rd)} = ppc_divw(a_, b_); "
                     "ppc_set_ov(c, b_ == 0u || (a_ == 0x80000000u && b_ == 0xFFFFFFFFu)); }"]
        else:
            lines = [f"{{ uint32_t a_ = {_r(ra)}, b_ = {_r(rb)}; {_r(rd)} = ppc_divwu(a_, b_); "
                     "ppc_set_ov(c, b_ == 0u); }"]
        return lines + _rc(insn, rd)

    if op in CR_LOGIC_C:
        expr = CR_LOGIC_C[op].format(a=f"ppc_crbit(c->cr, {ra}u)", b=f"ppc_crbit(c->cr, {rb}u)")
        return [f"c->cr = ppc_set_crbit(c->cr, {rd}u, {expr});"]
    if op == "mcrf":
        return [f"c->cr = ppc_set_crfield(c->cr, {insn.crf}u, "
                f"(c->cr >> {28 - 4 * insn.crfs}) & 0xFu);"]
    if op == "mcrxr":
        return [f"c->cr = ppc_set_crfield(c->cr, {insn.crf}u, (c->xer >> 28) & 0xFu); "
                "c->xer &= 0x0FFFFFFFu;"]
    if op == "mtcrf":
        mask = field_mask(insn.crm)
        return [f"c->cr = ({_r(rd)} & {_lit(mask)}) | (c->cr & {_lit(~mask)});"]
    if op in NOPS_31.values() or op == "isync":
        return [f"/* {op}: no effect (caches are not modelled) */"]
    if op == "dcbz":
        return [f"ppc_dcbz(c, {_ea_c(insn, True, False)});"]
    if op == "mfmsr":
        return [f"{_r(rd)} = c->msr;"]
    if op == "mtmsr":
        return [f"c->msr = {_r(rd)};"]
    if op == "mftb":
        shift = " >> 32" if insn.spr == SPR_TBU else ""
        return [f"{_r(rd)} = (uint32_t)(c->tb{shift});"]
    if op == "mfspr_x":
        src = "c->xer" if insn.spr == SPR_XER else f"c->spr[{insn.spr}]"
        return [f"{_r(rd)} = {src};"]
    if op == "mtspr_x":
        dst = "c->xer" if insn.spr == SPR_XER else f"c->spr[{insn.spr}]"
        return [f"{dst} = {_r(rd)};"]

    getters = {1: "ppc_r8", 2: "ppc_r16", 4: "ppc_r32"}
    setters = {1: "ppc_w8", 2: "ppc_w16", 4: "ppc_w32"}
    if op in EXT_LOAD:
        size, signed, indexed, update, rev = EXT_LOAD[op]
        v = f"{getters[size]}(c, ea_)"
        if rev:
            v = f"ppc_bswap{8 * size}({v})"
        if signed:
            v = f"(uint32_t)(int32_t)(int16_t){v}"
        tail = f" {_r(ra)} = ea_;" if update else ""
        return [f"{{ uint32_t ea_ = {_ea_c(insn, indexed, update)}; {_r(rd)} = {v};{tail} }}"]
    if op in EXT_STORE:
        size, indexed, update, rev = EXT_STORE[op]
        v = _r(rd)
        if rev:
            v = f"ppc_bswap{8 * size}({v})"
        tail = f" {_r(ra)} = ea_;" if update else ""
        return [f"{{ uint32_t ea_ = {_ea_c(insn, indexed, update)}; "
                f"{setters[size]}(c, ea_, {v});{tail} }}"]
    if op in EXT_FLOAD:
        single, indexed, update = EXT_FLOAD[op]
        v = "ppc_from_single_bits(ppc_r32(c, ea_))" if single else "ppc_r64(c, ea_)"
        tail = f" {_r(ra)} = ea_;" if update else ""
        return [f"{{ uint32_t ea_ = {_ea_c(insn, indexed, update)}; {_f(insn.fd)} = {v};{tail} }}"]
    if op in EXT_FSTORE:
        kind, indexed, update = EXT_FSTORE[op]
        store = {"s": f"ppc_w32(c, ea_, ppc_single_bits({_f(insn.fd)}))",
                 "d": f"ppc_w64(c, ea_, {_f(insn.fd)})",
                 "iw": f"ppc_w32(c, ea_, (uint32_t){_f(insn.fd)})"}[kind]
        tail = f" {_r(ra)} = ea_;" if update else ""
        return [f"{{ uint32_t ea_ = {_ea_c(insn, indexed, update)}; {store};{tail} }}"]
    if op == "lmw":
        return [f"{{ uint32_t ea_ = {_ea_c(insn, False, False)}; unsigned i_;",
                f"  for (i_ = {rd}u; i_ < 32u; i_++) {{ c->r[i_] = ppc_r32(c, ea_); ea_ += 4u; }} }}"]
    if op == "stmw":
        return [f"{{ uint32_t ea_ = {_ea_c(insn, False, False)}; unsigned i_;",
                f"  for (i_ = {rd}u; i_ < 32u; i_++) {{ ppc_w32(c, ea_, c->r[i_]); ea_ += 4u; }} }}"]

    def d(n):
        return f"ppc_d({_f(n)})"

    if op in ("fmadds", "fmsubs", "fnmadds", "fnmsubs"):
        a, c_, b = d(insn.fa), d(insn.fc), d(insn.fb)
        expr = {"fmadds": f"fma({a}, {c_}, {b})", "fmsubs": f"fma({a}, {c_}, -{b})",
                "fnmadds": f"-fma({a}, {c_}, {b})", "fnmsubs": f"-fma({a}, {c_}, -{b})"}[op]
        return [f"{_f(insn.fd)} = ppc_b(ppc_round_single({expr}));"]
    if op == "fres":
        return [f"{_f(insn.fd)} = ppc_b(ppc_fres({d(insn.fb)}));"]
    if op == "frsqrte":
        return [f"{_f(insn.fd)} = ppc_b(ppc_frsqrte({d(insn.fb)}));"]
    if op == "fsel":
        return [f"{_f(insn.fd)} = ({d(insn.fa)} >= 0.0) ? {_f(insn.fc)} : {_f(insn.fb)};"]
    if op == "fctiw":
        return [f"{_f(insn.fd)} = ppc_fctiw({d(insn.fb)});"]
    if op == "mffs":
        return [f"{_f(insn.fd)} = 0xFFF8000000000000ull | c->fpscr;"]
    if op == "mtfsf":
        mask = field_mask(insn.crm)
        return [f"c->fpscr = ((uint32_t){_f(insn.fb)} & {_lit(mask)}) | (c->fpscr & {_lit(~mask)});"]
    if op in ("mtfsb0", "mtfsb1"):
        return [f"c->fpscr = ppc_set_crbit(c->fpscr, {rd}u, {1 if op == 'mtfsb1' else 0}u);"]
    if op == "mtfsfi":
        return [f"c->fpscr = ppc_set_crfield(c->fpscr, {insn.crf}u, {insn.imm & 0xF}u);"]
    if op == "mcrfs":
        return [f"c->cr = ppc_set_crfield(c->cr, {insn.crf}u, "
                f"(c->fpscr >> {28 - 4 * insn.crfs}) & 0xFu);"]

    def hi(n):
        return f"ppc_s_d((uint32_t)({_f(n)} >> 32))"

    def lo(n):
        return f"ppc_s_d((uint32_t){_f(n)})"

    def pack(h, l):
        return f"{_f(insn.fd)} = ((uint64_t)({h}) << 32) | (uint64_t)({l});"

    fa, fb, fc = insn.fa, insn.fb, insn.fc
    if op == "ps_sum0":
        return [pack(f"ppc_s_b({hi(fa)} + {lo(fb)})", f"(uint32_t){_f(fc)}")]
    if op == "ps_sum1":
        return [pack(f"(uint32_t)({_f(fc)} >> 32)", f"ppc_s_b({hi(fa)} + {lo(fb)})")]
    if op in ("ps_muls0", "ps_muls1"):
        k = hi(fc) if op == "ps_muls0" else lo(fc)
        return [pack(f"ppc_s_b({hi(fa)} * {k})", f"ppc_s_b({lo(fa)} * {k})")]
    if op in ("ps_madds0", "ps_madds1"):
        k = hi(fc) if op == "ps_madds0" else lo(fc)
        return [pack(f"ppc_s_b(fma({hi(fa)}, {k}, {hi(fb)}))",
                     f"ppc_s_b(fma({lo(fa)}, {k}, {lo(fb)}))")]
    if op == "ps_sel":
        return [pack(f"(uint32_t)((({hi(fa)}) >= 0.0 ? {_f(fc)} : {_f(fb)}) >> 32)",
                     f"(uint32_t)(({lo(fa)}) >= 0.0 ? {_f(fc)} : {_f(fb)})")]
    if op == "ps_res":
        return [pack(f"ppc_s_b(ppc_fres({hi(fb)}))", f"ppc_s_b(ppc_fres({lo(fb)}))")]
    if op == "ps_rsqrte":
        return [pack(f"ppc_s_b(ppc_frsqrte({hi(fb)}))", f"ppc_s_b(ppc_frsqrte({lo(fb)}))")]
    if op in PS_CMP.values():
        lane = hi if op in ("ps_cmpu0", "ps_cmpo0") else lo
        return [f"c->cr = ppc_fcmp(c->cr, {insn.crf}u, {lane(fa)}, {lane(fb)});"]
    if op in PSQ_X.values():
        update = op in ("psq_lux", "psq_stux")
        w = 1 if insn.wbit else 0
        lines = [f"{{ uint32_t ea_ = {_ea_c(insn, True, update)};"]
        if op in ("psq_lx", "psq_lux"):
            lines.append(f"  {_f(insn.fd)} = ppc_psq_load(c, ea_, {insn.gqr}u, {w});")
        else:
            lines.append(f"  ppc_psq_store(c, ea_, {_f(insn.fd)}, {insn.gqr}u, {w});")
        if update:
            lines.append(f"  {_r(ra)} = ea_;")
        lines.append("}")
        return lines
    raise NotImplementedError(f"no C translation for {op}")
