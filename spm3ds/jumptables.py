"""Declared jump tables for bctr dispatch inside a function.

Compilers emit a switch as a table of code addresses and a bctr through CTR. Finding the
table by pattern matching is unreliable, so the table is declared explicitly. A declaration
says where the bctr is, where its table is, and how many entries the table has:

    # comment
    JUMPTABLE <bctr address> <table address> <entry count>

Addresses are hexadecimal, with or without 0x. The count is decimal or 0x-hexadecimal. Each
table entry is a big-endian 32-bit guest address, read from the loaded image. Entries are
treated as local branch targets of the function that contains the bctr.
"""
from collections import namedtuple

Declaration = namedtuple("Declaration", "branch table count")

MAX_ENTRIES = 4096


class JumpTableError(ValueError):
    pass


def parse_jump_tables(text):
    """Parse the declaration file and return a list of Declaration."""
    declarations = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 4 or parts[0].upper() != "JUMPTABLE":
            raise JumpTableError(f"line {number}: expected 'JUMPTABLE <bctr> <table> <count>'")
        try:
            branch = int(parts[1], 16)
            table = int(parts[2], 16)
            count = int(parts[3], 0)
        except ValueError:
            raise JumpTableError(f"line {number}: addresses must be hexadecimal and the count a number")
        if not 0 < count <= MAX_ENTRIES:
            raise JumpTableError(f"line {number}: count must be between 1 and {MAX_ENTRIES}")
        declarations.append(Declaration(branch, table, count))
    return declarations


def resolve(declarations, read_word):
    """Return {bctr address: [target, ...]}, reading each entry with read_word(address)."""
    resolved = {}
    for d in declarations:
        if d.branch in resolved:
            raise JumpTableError(f"{d.branch:#010x} has more than one jump table declaration")
        resolved[d.branch] = [read_word(d.table + 4 * i) for i in range(d.count)]
    return resolved
