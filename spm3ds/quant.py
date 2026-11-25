"""Paired-single quantization (psq_l / psq_st with GQR types 4..7).

GQR layout (WiiBrew "Paired single"): store type bits 0..2, store scale bits 8..13,
load type bits 16..18, load scale bits 24..29. Scales are signed 6-bit values.

Types: 0 float (no conversion), 4 unsigned 8-bit, 5 unsigned 16-bit, 6 signed 8-bit,
7 signed 16-bit. Loads divide the integer by 2^scale; stores multiply by 2^scale.

UNVERIFIED: the rounding rule on store. We truncate toward zero and saturate to the type's
range; NaN stores 0. The sources checked give the scale rule but not the rounding rule.
The C runtime (runtime/ppc_ctx.h) implements the same rules, and tests compare the two.
"""
import math

TYPE_FORMATS = {4: (1, False), 5: (2, False), 6: (1, True), 7: (2, True)}


def sext6(v):
    v &= 63
    return v - 64 if v & 32 else v


def load_fields(gqr):
    return (gqr >> 16) & 7, sext6(gqr >> 24)


def store_fields(gqr):
    return gqr & 7, sext6(gqr >> 8)


def dequantize(raw, size, signed, scale):
    """Integer `raw` (unsigned bit pattern of `size` bytes) to a float value."""
    bits = size * 8
    if signed and raw & (1 << (bits - 1)):
        raw -= 1 << bits
    return raw * (2.0 ** -scale)


def quantize(x, size, signed, scale):
    """Float to the integer bit pattern (masked to `size` bytes) that a store writes."""
    if math.isnan(x):
        return 0
    bits = size * 8
    lo, hi = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1)
    y = min(max(x * (2.0 ** scale), float(lo)), float(hi))
    return int(y) & ((1 << bits) - 1)
