"""Static recompiler: PowerPC subset -> portable C99.

Each guest function becomes one C function. Branches inside a function become gotos, and
calls use direct C calls. Calls to external (HLE) addresses call the host implementation in
runtime/hle.c and runtime/os.c. A bctr with no declared table goes through a runtime dispatch
table of known functions; a target that is not a known function aborts with a message. A bctr
with a declared jump table (see jumptables.py) becomes a switch whose cases are local labels.

Generated code depends only on runtime/ppc_ctx.h and runtime/hle.h.

Usage:
  python3 -m spm3ds.recomp analyze <main.dol> [--symbols FILE] [--jump-tables FILE] [--auto-jump-tables]
  python3 -m spm3ds.recomp compile <main.dol> <out.c> [--symbols FILE] [--jump-tables FILE]
"""
import argparse
import os
import sys

from .analysis import analyze, classify, sweep
from .dol import parse_dol
from .hle import external_from_symbols, parse_symbols
from .jumpdetect import detect as detect_jump_tables
from .dol import DolError
from .jumptables import JumpTableError, parse_jump_tables, resolve
from .memory import MemoryMap
from .ppc import (CARRY_OPS, D_MEM_LOAD, D_MEM_STORE, INT_BINARY, INT_UNARY, LOGICAL_OPS, M32,
                  PS_MERGE, PS_OPS, SPR_CTR, SPR_GQR0, SPR_LR, UNARY_FROM_RA, UPDATE_FORMS,
                  X_MEM_LOAD, X_MEM_STORE, mask32)
from .ppc_ext import EXT_OPS, ext_lines

# Paired-single arithmetic as C expressions on doubles. {a}, {b}, {c} are the lane operands.
PS_ARITH_C = {
    "ps_add": "({a}) + ({b})",
    "ps_sub": "({a}) - ({b})",
    "ps_mul": "({a}) * ({c})",
    "ps_div": "({a}) / ({b})",
    "ps_madd": "fma({a}, {c}, {b})",
    "ps_msub": "fma({a}, {c}, -({b}))",
    "ps_nmadd": "-fma({a}, {c}, {b})",
    "ps_nmsub": "-fma({a}, {c}, -({b}))",
}

RUNTIME_HEADERS = ('"ppc_ctx.h"', '"hle.h"')


def fn_name(addr):
    return f"fn_{addr:08X}"


def _lit(value):
    return f"0x{value & M32:X}u"


def _r(n):
    return f"c->r[{n}]"


def _f(n):
    return f"c->f[{n}]"


def _base(ra):
    return "0u" if ra == 0 else _r(ra)


def _ea(insn, indexed=False):
    if indexed:
        return f"({_base(insn.ra)} + {_r(insn.rb)})"
    return f"({_base(insn.ra)} + {_lit(insn.imm)})"


def _update_ea(insn, indexed=False):
    """Effective address for update forms, where rA is the base."""
    disp = f"{_r(insn.rb)}" if indexed else _lit(insn.imm)
    return f"({_r(insn.ra)} + {disp})"


def _cond(insn):
    """Return (pre-statements, C condition) for bc, bclr and bcctr. Decrements CTR first."""
    bo, bi = insn.bo, insn.bi
    pre = []
    parts = []
    if not bo & 4:
        pre.append("c->ctr -= 1u;")
        parts.append(f"((c->ctr != 0u) != {1 if bo & 2 else 0})")
    if not bo & 16:
        parts.append(f"((((c->cr >> {31 - bi}) & 1u) != 0u) == {1 if bo & 8 else 0})")
    return pre, (" && ".join(parts) if parts else "1")


def _rc(insn, dst):
    if insn.rc:
        return [f"c->cr = ppc_cmp_s(c->cr, 0u, (int32_t){_r(dst)}, 0, c->xer);"]
    return []


def _spr_expr(spr):
    if spr == SPR_LR:
        return "c->lr"
    if spr == SPR_CTR:
        return "c->ctr"
    return f"c->gqr[{spr - SPR_GQR0}]"


def _paired_lines(insn):
    """C statements for paired-single instructions (see ppc.py for the semantics)."""
    op = insn.op
    fd, fa, fb, fc = insn.fd, insn.fa, insn.fb, insn.fc
    if op in ("psq_l", "psq_lu", "psq_st", "psq_stu"):
        is_load = op in ("psq_l", "psq_lu")
        update = op in ("psq_lu", "psq_stu")
        ea = _update_ea(insn) if update else _ea(insn)
        w = 1 if insn.wbit else 0
        lines = [f"{{ uint32_t ea_ = {ea};"]
        if is_load:
            lines.append(f"  {_f(fd)} = ppc_psq_load(c, ea_, {insn.gqr}u, {w});")
        else:
            lines.append(f"  ppc_psq_store(c, ea_, {_f(fd)}, {insn.gqr}u, {w});")
        if update:
            lines.append(f"  {_r(insn.ra)} = ea_;")
        lines.append("}")
        return lines
    if op in PS_ARITH_C:
        def lane(src, half):
            shift = " >> 32" if half == "hi" else ""
            word = f"((uint32_t)({src}{shift}))" if half == "hi" else f"((uint32_t){src})"
            return f"ppc_s_d({word})"
        def expr(half):
            return PS_ARITH_C[op].format(a=lane(_f(fa), half), c=lane(_f(fc), half),
                                         b=lane(_f(fb), half))
        return [f"{_f(fd)} = ((uint64_t)ppc_s_b({expr('hi')}) << 32) | ppc_s_b({expr('lo')});"]
    if op in ("ps_neg", "ps_nabs", "ps_abs", "ps_mr"):
        mask = {"ps_neg": "0x80000000u", "ps_nabs": "0x80000000u", "ps_abs": "0x7FFFFFFFu"}.get(op)
        v = _f(fb)
        if op == "ps_mr":
            return [f"{_f(fd)} = {v};"]
        if op == "ps_neg":
            return [f"{_f(fd)} = {v} ^ 0x8000000080000000ull;"]
        if op == "ps_nabs":
            return [f"{_f(fd)} = {v} | 0x8000000080000000ull;"]
        return [f"{_f(fd)} = {v} & 0x7FFFFFFF7FFFFFFFull;"]
    if op in PS_MERGE:
        a_src, b_src = PS_MERGE[op]
        a_word = f"((uint32_t)({_f(fa)} >> {32 * (1 - a_src)}))"
        b_word = f"((uint32_t)({_f(fb)} >> {32 * (1 - b_src)}))"
        return [f"{_f(fd)} = ((uint64_t){a_word} << 32) | (uint64_t){b_word};"]
    raise NotImplementedError(f"no C translation for {op}")


def _data_lines(insn):
    """C statements for a non-branch instruction."""
    op = insn.op
    rd, ra, rb = insn.rd, insn.ra, insn.rb
    if op in EXT_OPS:
        return ext_lines(insn)
    if op in PS_OPS or op in ("psq_l", "psq_lu", "psq_st", "psq_stu"):
        return _paired_lines(insn)
    if op in INT_BINARY:
        template = INT_BINARY[op][1]
        if op in LOGICAL_OPS:
            dst, expr = ra, template.format(a=_r(rd), b=_r(rb))
        else:
            dst, expr = rd, template.format(a=_r(ra), b=_r(rb))
        return [f"{_r(dst)} = {expr};"] + _rc(insn, dst)
    if op in INT_UNARY:
        template = INT_UNARY[op][1]
        if op in UNARY_FROM_RA:
            dst, expr = rd, template.format(a=_r(ra))
        else:
            dst, expr = ra, template.format(a=_r(rd))
        return [f"{_r(dst)} = {expr};"] + _rc(insn, dst)
    if op in ("addi", "addis"):
        return [f"{_r(rd)} = {_base(ra)} + {_lit(insn.imm)};"]
    if op in CARRY_OPS:
        a_inv, b_src, cin_src = CARRY_OPS[op]
        a = f"(~{_r(ra)})" if a_inv else _r(ra)
        b = {"rb": _r(rb), "imm": _lit(insn.imm), "minus1": "0xFFFFFFFFu", "zero": "0u"}[b_src]
        cin = {"zero": "0u", "one": "1u", "ca": "ppc_ca(c)"}[cin_src]
        return [f"{_r(rd)} = ppc_addx(c, {a}, {b}, {cin});"] + _rc(insn, rd)
    if op in ("sraw", "srawi"):
        amount = f"({_r(rb)} & 63u)" if op == "sraw" else f"{insn.sh}u"
        return [f"{_r(ra)} = ppc_sraw_ca(c, {_r(rd)}, {amount});"] + _rc(insn, ra)
    if op == "mulli":
        return [f"{_r(rd)} = {_r(ra)} * {_lit(insn.imm)};"]
    if op in ("ori", "xori", "andi"):
        symbol = {"ori": "|", "xori": "^", "andi": "&"}[op]
        return [f"{_r(ra)} = {_r(rd)} {symbol} {_lit(insn.imm)};"] + _rc(insn, ra)
    if op in ("rlwinm", "rlwimi", "rlwnm"):
        m = mask32(insn.mb, insn.me)
        n = f"({_r(rb)} & 31u)" if op == "rlwnm" else f"{insn.sh}u"
        rot = f"ppc_rotl32({_r(rd)}, {n})"
        if op == "rlwimi":
            lines = [f"{_r(ra)} = ({rot} & {_lit(m)}) | ({_r(ra)} & {_lit(~m)});"]
        else:
            lines = [f"{_r(ra)} = {rot} & {_lit(m)};"]
        return lines + _rc(insn, ra)
    if op == "cmp":
        return [f"c->cr = ppc_cmp_s(c->cr, {insn.crf}u, (int32_t){_r(ra)}, (int32_t){_r(rb)}, c->xer);"]
    if op == "cmpl":
        return [f"c->cr = ppc_cmp_u(c->cr, {insn.crf}u, {_r(ra)}, {_r(rb)}, c->xer);"]
    if op == "cmpi":
        return [f"c->cr = ppc_cmp_s(c->cr, {insn.crf}u, (int32_t){_r(ra)}, (int32_t)({insn.imm}), c->xer);"]
    if op == "cmpli":
        return [f"c->cr = ppc_cmp_u(c->cr, {insn.crf}u, {_r(ra)}, {_lit(insn.imm)}, c->xer);"]
    if op == "mfcr":
        return [f"{_r(rd)} = c->cr;"]
    if op == "mfspr":
        return [f"{_r(rd)} = {_spr_expr(insn.spr)};"]
    if op == "mtspr":
        return [f"{_spr_expr(insn.spr)} = {_r(rd)};"]
    if op in D_MEM_LOAD or op in X_MEM_LOAD:
        indexed = op in X_MEM_LOAD
        size = D_MEM_LOAD[op] if op in D_MEM_LOAD else X_MEM_LOAD[op][0]
        getter = {1: "ppc_r8", 2: "ppc_r16", 4: "ppc_r32"}[size]
        if op in UPDATE_FORMS:
            return [f"{{ uint32_t ea_ = {_update_ea(insn, indexed)}; "
                    f"{_r(rd)} = {getter}(c, ea_); {_r(ra)} = ea_; }}"]
        if op == "lha":
            return [f"{_r(rd)} = (uint32_t)(int32_t)(int16_t)ppc_r16(c, {_ea(insn)});"]
        return [f"{_r(rd)} = {getter}(c, {_ea(insn, indexed)});"]
    if op in D_MEM_STORE or op in X_MEM_STORE:
        indexed = op in X_MEM_STORE
        size = D_MEM_STORE[op] if op in D_MEM_STORE else X_MEM_STORE[op]
        setter = {1: "ppc_w8", 2: "ppc_w16", 4: "ppc_w32"}[size]
        if op in UPDATE_FORMS:
            return [f"{{ uint32_t ea_ = {_update_ea(insn, indexed)}; "
                    f"{setter}(c, ea_, {_r(rd)}); {_r(ra)} = ea_; }}"]
        return [f"{setter}(c, {_ea(insn, indexed)}, {_r(rd)});"]
    if op == "lfs":
        return [f"{_f(insn.fd)} = ppc_from_single_bits(ppc_r32(c, {_ea(insn)}));"]
    if op == "lfd":
        return [f"{_f(insn.fd)} = ppc_r64(c, {_ea(insn)});"]
    if op == "stfs":
        return [f"ppc_w32(c, {_ea(insn)}, ppc_single_bits({_f(insn.fd)}));"]
    if op == "stfd":
        return [f"ppc_w64(c, {_ea(insn)}, {_f(insn.fd)});"]
    if op in ("fadd", "fsub", "fdiv"):
        sym = {"fadd": "+", "fsub": "-", "fdiv": "/"}[op]
        return [f"{_f(insn.fd)} = ppc_b(ppc_d({_f(insn.fa)}) {sym} ppc_d({_f(insn.fb)}));"]
    if op in ("fadds", "fsubs", "fmuls", "fdivs"):
        sym = {"fadds": "+", "fsubs": "-", "fmuls": "*", "fdivs": "/"}[op]
        other = insn.fc if op == "fmuls" else insn.fb
        return [f"{_f(insn.fd)} = ppc_b(ppc_round_single(ppc_d({_f(insn.fa)}) {sym} "
                f"ppc_d({_f(other)})));"]
    if op in ("fmul", "fmadd", "fmsub", "fnmadd", "fnmsub"):
        a, c_, b = f"ppc_d({_f(insn.fa)})", f"ppc_d({_f(insn.fc)})", f"ppc_d({_f(insn.fb)})"
        expr = {"fmul": f"{a} * {c_}",
                "fmadd": f"fma({a}, {c_}, {b})",
                "fmsub": f"fma({a}, {c_}, -{b})",
                "fnmadd": f"-fma({a}, {c_}, {b})",
                "fnmsub": f"-fma({a}, {c_}, -{b})"}[op]
        return [f"{_f(insn.fd)} = ppc_b({expr});"]
    if op == "fmr":
        return [f"{_f(insn.fd)} = {_f(insn.fb)};"]
    if op == "fneg":
        return [f"{_f(insn.fd)} = {_f(insn.fb)} ^ 0x8000000000000000ull;"]
    if op == "fabs":
        return [f"{_f(insn.fd)} = {_f(insn.fb)} & 0x7FFFFFFFFFFFFFFFull;"]
    if op == "fnabs":
        return [f"{_f(insn.fd)} = {_f(insn.fb)} | 0x8000000000000000ull;"]
    if op == "frsp":
        return [f"{_f(insn.fd)} = ppc_b(ppc_round_single(ppc_d({_f(insn.fb)})));"]
    if op == "fctiwz":
        return [f"{_f(insn.fd)} = ppc_fctiwz(ppc_d({_f(insn.fb)}));"]
    if op in ("fcmpu", "fcmpo"):
        return [f"c->cr = ppc_fcmp(c->cr, {insn.crf}u, ppc_d({_f(insn.fa)}), "
                f"ppc_d({_f(insn.fb)}));"]
    raise NotImplementedError(f"no C translation for {op}")


def _branch_lines(insn, fn, entries, external, external_names):
    """C statements for a branch. Returns (lines, label_target or None).

    `external_names` maps an HLE address to its C function name (hle_<name>).
    """
    op = insn.op
    if op in ("b", "bl"):
        kind = classify(insn, fn, entries, external)
        if op == "b":
            if kind == "local":
                return [f"goto L_{insn.target:08X};"], insn.target
            if kind == "tail":
                return [f"{fn_name(insn.target)}(c); return;"], None
            return [f"{external_names[insn.target]}(c); return;"], None
        lr = f"c->lr = {_lit(insn.addr + 4)};"
        if kind == "call":
            return [f"{lr} {fn_name(insn.target)}(c);"], None
        return [f"{lr} {external_names[insn.target]}(c);"], None
    pre, cond = _cond(insn)
    if op == "bc":
        if cond == "1":
            return pre + [f"goto L_{insn.target:08X};"], insn.target
        return pre + [f"if ({cond}) goto L_{insn.target:08X};"], insn.target
    if op == "bclr":
        if cond == "1":
            return pre + ["return;"], None
        return pre + [f"if ({cond}) return;"], None
    if op == "bcctr":
        jump = f"ppc_dispatch(c, c->ctr & ~3u);"
        if insn.link:
            body = [f"c->lr = {_lit(insn.addr + 4)};", jump]
            if cond == "1":
                return pre + body, None
            return pre + [f"if ({cond}) {{ " + " ".join(body) + " }"], None
        if cond == "1":
            return pre + [jump, "return;"], None
        return pre + [f"if ({cond}) {{ {jump} return; }}"], None
    raise NotImplementedError(op)


def _table_dispatch_lines(targets):
    """A bctr through a declared jump table: jump to a listed label. Any other value is tried
    as a function (a tail call), and the dispatcher aborts if it is not a known function."""
    cases = " ".join(f"case 0x{t:08X}u: goto L_{t:08X};" for t in sorted(set(targets)))
    return [f"switch (c->ctr & ~3u) {{ {cases} default: break; }}",
            "ppc_dispatch(c, c->ctr & ~3u);",
            "return;"]


def generate_c(image, entry, external_names=None, strict=True, seeds=(), jump_targets=None):
    """Return the C source for every function reachable from `entry`.

    `external_names` maps HLE addresses to names implemented in runtime/hle.c.
    `seeds` are extra function addresses, such as those in a symbol file. Indirect calls
    can only reach functions that are in the dispatch table, so list them here.
    `jump_targets` maps a bctr address to the table entries it can jump to (see jumptables.py).
    With strict=True any fatal problem raises CompileError before code is produced.
    """
    names = dict(external_names or {})
    jump_targets = {int(k): tuple(v) for k, v in (jump_targets or {}).items()}
    result = analyze(image, entry, frozenset(names), seeds, jump_targets)
    if strict:
        result.raise_if_fatal()
    entries = sorted(result.entries)
    external = result.external
    external_names = {addr: f"hle_{name}" for addr, name in names.items()}

    out = ["/* Generated by spm3ds recompiler. Do not edit by hand. */"]
    out += [f"#include {h}" for h in RUNTIME_HEADERS]
    out.append("")
    out.append("void ppc_dispatch(ppc_ctx *c, uint32_t target);")
    for f in entries:
        out.append(f"void {fn_name(f)}(ppc_ctx *c);")

    for f in entries:
        body = result.bodies[f]
        jump_labels = set()
        branch_lines = {}
        for addr, insn in body.items():
            if insn.op == "bcctr" and addr in jump_targets:
                branch_lines[addr] = _table_dispatch_lines(jump_targets[addr])
                jump_labels.update(jump_targets[addr])
                continue
            if insn.op in ("b", "bl", "bc", "bclr", "bcctr"):
                lines, label = _branch_lines(insn, f, result.entries, external, external_names)
                branch_lines[addr] = lines
                if label is not None:
                    jump_labels.add(label)
        out.append("")
        out.append(f"void {fn_name(f)}(ppc_ctx *c) {{")
        for addr in sorted(body):
            insn = body[addr]
            if addr in jump_labels:
                out.append(f"L_{addr:08X}:")
            lines = branch_lines.get(addr) or _data_lines(insn)
            out.append(f"    PPC_DIAG(0x{addr:08X}u);")
            for line in lines:
                out.append("    " + line)
        out.append("}")

    out.append("")
    out.append("typedef struct { uint32_t addr; ppc_fn fn; } ppc_fn_entry;")
    out.append("static const ppc_fn_entry ppc_fn_table[] = {")
    for f in entries:
        out.append(f"    {{ 0x{f:08X}u, {fn_name(f)} }},")
    out.append("};")
    out.append("")
    out.append("void ppc_dispatch(ppc_ctx *c, uint32_t target) {")
    out.append("    unsigned i;")
    out.append("    for (i = 0; i < sizeof ppc_fn_table / sizeof ppc_fn_table[0]; i++) {")
    out.append("        if (ppc_fn_table[i].addr == target) { ppc_fn_table[i].fn(c); return; }")
    out.append("    }")
    out.append('    fprintf(stderr, "indirect branch to unknown function %08x\\n", (unsigned)target);')
    out.append('    ppc_crash_report(c, "indirect branch to an address that is not a function");')
    out.append("    abort();")
    out.append("}")
    return "\n".join(out) + "\n"


def harness_source(entry, memmap, init=(), dumps=()):
    """A C main() that runs `entry` on a fresh context and prints the final state.

    `memmap` supplies the regions. `init` is a list of (address, bytes) written before the
    run. `dumps` is a list of (address, length) printed after the run as "m ADDR BYTES".
    """
    lines = ["#include <stdio.h>", "#include <string.h>", '#include "ppc_ctx.h"', ""]
    for i, region in enumerate(memmap.regions):
        lines.append(f"static uint8_t region_{i}[{region.size}];")
    lines.append("int main(void) {")
    lines.append("    ppc_ctx c;")
    lines.append("    memset(&c, 0, sizeof c);")
    for i, region in enumerate(memmap.regions):
        lines.append(f"    c.regions[{i}].base = 0x{region.base:08X}u;")
        lines.append(f"    c.regions[{i}].size = {region.size}u;")
        lines.append(f"    c.regions[{i}].data = region_{i};")
    lines.append(f"    c.nregions = {len(memmap.regions)};")
    for k, (address, data) in enumerate(init):
        values = ", ".join(f"0x{b:02X}" for b in data)
        lines.append(f"    {{ static const uint8_t init_{k}[] = {{ {values} }}; "
                     f"memcpy(ppc_ptr(&c, 0x{address:08X}u, {len(data)}), init_{k}, {len(data)}); }}")
    lines.append(f"    {fn_name(entry)}(&c);")
    lines.append('    for (int i = 0; i < 32; i++) printf("r%d %08x\\n", i, (unsigned)c.r[i]);')
    lines.append('    for (int i = 0; i < 32; i++) printf("f%d %016llx\\n", i, (unsigned long long)c.f[i]);')
    lines.append('    printf("lr %08x\\nctr %08x\\ncr %08x\\n", (unsigned)c.lr, (unsigned)c.ctr, (unsigned)c.cr);')
    lines.append('    printf("xer %08x\\n", (unsigned)(c.xer & 0xE0000000u));')
    for address, length in dumps:
        lines.append(f"    {{ const uint8_t *p = ppc_ptr(&c, 0x{address:08X}u, {length}); "
                     f'printf("m %08x", 0x{address:08X}u); '
                     f'for (unsigned i = 0; i < {length}; i++) printf(" %02x", p[i]); printf("\\n"); }}')
    lines.append("    return 0;")
    lines.append("}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- command line

def auto_jump_tables(dol):
    """Detect switch tables in every text section. Returns (targets by bcctr, notes)."""
    def read_word(ea):
        try:
            return int.from_bytes(dol.read(ea, 4), "big")
        except DolError:
            return None
    found_targets, notes = {}, []
    for section in dol.text:
        words = [int.from_bytes(section.data[o:o + 4], "big") for o in range(0, section.size, 4)]
        found, rejected = detect_jump_tables(words, section.address, read_word,
                                             (section.address, section.end))
        for d in found:
            found_targets[d.branch] = d.targets
            notes.append(f"auto jump table at bctr 0x{d.branch:08X}: table 0x{d.table:08X}, "
                         f"{d.count} entries ({d.confidence} confidence)")
            notes.extend(f"    - {r}" for r in d.reasons)
        for addr, why in rejected:
            notes.append(f"not detected at 0x{addr:08X}: {why}")
    return found_targets, notes


def _load(dol_path, symbols_path, jump_tables_path=None, auto=False):
    """Return (dol, names, unimplemented, seeds, jump_targets) for the given input files."""
    with open(dol_path, "rb") as f:
        dol = parse_dol(f.read())
    names, unimplemented, seeds = {}, [], []
    if symbols_path:
        with open(symbols_path, encoding="utf-8") as f:
            text = f.read()
        names, unimplemented = external_from_symbols(text)
        seeds = [address for address, _ in parse_symbols(text)]
    jump_targets = {}
    if jump_tables_path:
        with open(jump_tables_path, encoding="utf-8") as f:
            declarations = parse_jump_tables(f.read())
        memory = MemoryMap.wii_default()
        dol.load_into(memory)
        jump_targets = resolve(declarations, lambda ea: memory.read_uint(ea, 4))
    if auto:
        detected, notes = auto_jump_tables(dol)
        for line in notes:
            print(line, file=sys.stderr)
        for bctr, targets in detected.items():
            jump_targets.setdefault(bctr, targets)     # declared tables win
    return dol, names, unimplemented, seeds, jump_targets


def _strip_addr(message):
    """Messages from the decoder start with the address; the report already prints it."""
    head, sep, rest = message.partition(": ")
    return rest if sep and head.startswith("0x") else message


def _print_report(dol, names, unimplemented, result, failures):
    print(f"entry point:          {dol.entry:#010x}")
    print(f"text sections:        {len(dol.text)}")
    print(f"functions found:      {len(result.entries)} (reachable from the entry point)")
    print(f"instructions reached: {result.instruction_count()}")
    print(f"HLE symbols bound:    {len(names)}")
    if unimplemented:
        print(f"symbols without an HLE implementation: {', '.join(sorted(set(unimplemented)))}")

    print()
    print("Reachable problems (these block compilation):")
    fatal = result.fatal
    if not fatal:
        print("  none")
    for p in fatal:
        print(f"  [{p.kind}] {p.addr:#010x}: {_strip_addr(p.message)}")

    indirect = [p for p in result.problems if p.kind == "indirect"]
    print()
    print(f"Indirect branches (dispatched at runtime): {len(indirect)}")
    for p in indirect:
        print(f"  {p.addr:#010x}")

    print()
    print(f"Full linear sweep: {len(failures)} text word(s) do not decode")
    by_opcode = {}
    for addr, word, message in failures:
        by_opcode.setdefault(word >> 26, []).append((addr, word, message))
    for opcode, items in sorted(by_opcode.items(), key=lambda kv: -len(kv[1])):
        print(f"  primary opcode {opcode}: {len(items)} word(s)")
        for addr, word, message in items[:10]:
            print(f"    {addr:#010x} [{word:08x}] {_strip_addr(message)}")
        if len(items) > 10:
            print(f"    ... {len(items) - 10} more")
    print()
    print("Supported instructions reached (mnemonic: count):")
    for name, count in result.histogram().most_common():
        print(f"  {name:10s} {count}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m spm3ds.recomp")
    sub = parser.add_subparsers(dest="command", required=True)
    p_an = sub.add_parser("analyze", help="report every unsupported instruction in a DOL")
    p_an.add_argument("dol")
    p_an.add_argument("--symbols", help="symbol file: lines of 'ADDRESS NAME'")
    p_an.add_argument("--jump-tables", help="declarations: 'JUMPTABLE <bctr> <table> <count>'")
    p_an.add_argument("--auto-jump-tables", action="store_true",
                      help="also detect switch tables from the code (reported on stderr)")
    p_c = sub.add_parser("compile", help="write C source for a DOL")
    p_c.add_argument("dol")
    p_c.add_argument("out")
    p_c.add_argument("--symbols", help="symbol file: lines of 'ADDRESS NAME'")
    p_c.add_argument("--jump-tables", help="declarations: 'JUMPTABLE <bctr> <table> <count>'")
    p_c.add_argument("--auto-jump-tables", action="store_true",
                     help="also detect switch tables from the code (reported on stderr)")
    args = parser.parse_args(argv)

    try:
        dol, names, unimplemented, seeds, jump_targets = _load(args.dol, args.symbols,
                                                               args.jump_tables,
                                                               args.auto_jump_tables)
    except JumpTableError as exc:
        print(f"jump table file: {exc}", file=sys.stderr)
        return 1
    if args.command == "analyze":
        image = dol.text_words()
        result = analyze(image, dol.entry, frozenset(names), seeds, jump_targets)
        _print_report(dol, names, unimplemented, result, sweep(image))
        return 0
    try:
        source = generate_c(dol.text_words(), dol.entry, names, seeds=seeds,
                            jump_targets=jump_targets)
    except Exception as exc:  # CompileError lists every blocking problem
        print(exc, file=sys.stderr)
        return 1
    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(source)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
