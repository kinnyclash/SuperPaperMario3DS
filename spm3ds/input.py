"""Wii input to 3DS input mapping (foundation). Pure data plus a converter, host-tested.

Button bits are the Wii Remote's 16-bit button report as documented on WiiBrew (Wii Remote
buttons). 3DS key bits are libctru's KEY_* values (KEY_A = BIT(0) ... KEY_Y = BIT(11)).

Only the Wii Remote is mapped here. The Classic Controller and Nunchuk reports use different
bit layouts that are NOT mapped yet; they need their own table from a verified source.
Missing mappings are reported, never guessed.
"""

# Wii Remote button bits (WiiBrew "Wii Remote"; checked against the WiiBrew table).
WIIMOTE = {
    "Left": 0x0001,
    "Right": 0x0002,
    "Down": 0x0004,
    "Up": 0x0008,
    "Plus": 0x0010,
    "Two": 0x0100,
    "One": 0x0200,
    "B": 0x0400,
    "A": 0x0800,
    "Minus": 0x1000,
    "Home": 0x8000,
}

# 3DS KEY_* bits (libctru). Names are the libctru constant names without the KEY_ prefix.
KEY = {
    "A": 1 << 0, "B": 1 << 1, "SELECT": 1 << 2, "START": 1 << 3,
    "DRIGHT": 1 << 4, "DLEFT": 1 << 5, "DUP": 1 << 6, "DDOWN": 1 << 7,
    "R": 1 << 8, "L": 1 << 9, "X": 1 << 10, "Y": 1 << 11,
}

# Default mapping: the d-pad uses the Wii Remote held sideways (the usual layout), so Left/Right
# and Up/Down are swapped relative to the button names. Home is not mapped.
DEFAULT_MAP = {
    "Left": "DLEFT", "Right": "DRIGHT", "Up": "DUP", "Down": "DDOWN",
    "A": "A", "B": "B", "One": "X", "Two": "Y",
    "Plus": "START", "Minus": "SELECT",
}


def convert(wii_bits, mapping=DEFAULT_MAP):
    """Return (three_ds_keys, unmapped_names) for a Wii Remote button report."""
    keys = 0
    unmapped = []
    for name, bit in WIIMOTE.items():
        if not wii_bits & bit:
            continue
        target = mapping.get(name)
        if target is None:
            unmapped.append(name)
        else:
            keys |= KEY[target]
    return keys, unmapped


def validate(mapping=DEFAULT_MAP):
    """Every target must be a real KEY name and every source a known Wii Remote button."""
    for src, dst in mapping.items():
        if src not in WIIMOTE:
            raise ValueError(f"unknown Wii Remote button {src!r}")
        if dst not in KEY:
            raise ValueError(f"unknown 3DS key {dst!r}")
