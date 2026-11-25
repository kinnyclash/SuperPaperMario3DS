# PowerPC coverage

What the decoder, interpreter (`spm3ds/ppc.py`) and C generator (`spm3ds/recomp.py`) handle.
Base instructions live in `ppc.py`; the extended set is in `spm3ds/ppc_ext.py`, which keeps
decoder, interpreter rule and C translation side by side for each instruction.

Every instruction below is checked by the interpreter-versus-compiled-C tests
(`tests/test_random.py`, `tests/test_ppc_ext.py`), and most also by hand-computed results.

## Integer
- Arithmetic: add, subf, neg, mullw, mulli, mulhw, mulhwu, divw, divwu, addi, addis
- Carry: addc, adde, addze, addme, subfc, subfe, subfze, subfme, addic, addic., subfic
- **Overflow (OE=1) forms** of all of the above that have one: XER.OV is set or cleared,
  XER.SO is sticky. mulhw/mulhwu have no OE form and stay rejected.
- Logical, shifts, rotates: and, andc, or, orc, xor, nor, nand, eqv, andi., andis., ori, oris,
  xori, xoris, slw, srw, sraw, srawi, rlwinm, rlwimi, rlwnm, cntlzw, extsb, extsh
- Rc=1 forms update CR0, including the SO bit copied from XER.
- Compares: cmp, cmpl, cmpi, cmpli (SO copied into the field).

## Condition register and special registers
- mfcr, mtcrf, mcrf, mcrxr, crand, crandc, creqv, crnand, crnor, cror, crorc, crxor
- mfspr/mtspr: LR, CTR, XER, GQR0-7, and any other SPR as plain storage (`c->spr[n]`).
- mfmsr/mtmsr: stored, interrupts not modelled.
- mftb: reads `c->tb`, which the platform layer advances.

## Memory
- Loads: lwz, lwzu, lwzx, lwzux, lbz, lbzu, lbzx, lbzux, lhz, lhzu, lhzx, lhzux, lha, lhau,
  lhax, lhaux, lwbrx, lhbrx, lmw
- Stores: stw, stwu, stwx, stwux, stb, stbu, stbx, stbux, sth, sthu, sthx, sthux, stwbrx,
  sthbrx, stmw
- Cache and ordering: dcbz (zeroes the 32-byte block); dcbf, dcbst, dcbt, dcbtst, dcbi, icbi,
  sync, eieio, isync have no effect because caches are not modelled.

## Floating point
- Loads/stores: lfs, lfsu, lfsx, lfsux, lfd, lfdu, lfdx, lfdux, stfs, stfsu, stfsx, stfsux,
  stfd, stfdu, stfdx, stfdux, stfiwx
- Arithmetic: fadd, fsub, fmul, fdiv, fmadd, fmsub, fnmadd, fnmsub and their single forms
  (fadds, fsubs, fmuls, fdivs, fmadds, fmsubs, fnmadds, fnmsubs)
- fres, frsqrte, fsel, frsp, fctiw, fctiwz, fmr, fneg, fabs, fnabs, fcmpu, fcmpo
- FPSCR: mffs, mtfsf, mtfsfi, mtfsb0, mtfsb1, mcrfs

## Paired singles (Gekko)
- psq_l, psq_lu, psq_st, psq_stu, psq_lx, psq_lux, psq_stx, psq_stux, all quantization types
- ps_add, ps_sub, ps_mul, ps_div, ps_madd, ps_msub, ps_nmadd, ps_nmsub, ps_neg, ps_abs,
  ps_nabs, ps_mr, ps_merge00/01/10/11, ps_sum0, ps_sum1, ps_muls0, ps_muls1, ps_madds0,
  ps_madds1, ps_sel, ps_res, ps_rsqrte, ps_cmpu0, ps_cmpo0, ps_cmpu1, ps_cmpo1

## Branches
- b, bl, bc, bclr (blr and conditional forms), bcctr, bcctrl

## Known simplifications
- FPSCR exception bits are not computed by arithmetic, and the rounding mode field is not
  applied: arithmetic and fctiw always round to nearest, ties to even.
- fres, frsqrte, ps_res and ps_rsqrte return exact results. The hardware returns estimates
  (about 12 bits for fres), so results may differ in the low bits from a real console.
- Paired-single fused multiply-add uses a correctly rounded fma per lane. Dolphin's notes
  describe Gekko rounding frC differently; this has not been checked against hardware.
- mftb does not advance by itself.

## Not supported (rejected by the decoder with the address and opcode)
- Absolute branches (AA=1), bclrl, bcl/bcctr with CTR decrement
- lswi, lswx, stswi, stswx (string instructions), lwarx/stwcx. (reservations), mfsr/mtsr,
  tlb instructions, rfi, sc, tw/twi, dcbz_l (locked cache), and 64-bit forms.
