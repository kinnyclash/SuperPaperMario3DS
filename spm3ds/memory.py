"""Guest memory map: named, non-overlapping regions with big-endian access.

Wii layout used as the default reference: MEM1 at 0x80000000 (24 MiB) and MEM2 at
0x90000000 (64 MiB). The 3DS port will back these with its own allocations; the guest
addresses stay the same.
"""
from dataclasses import dataclass

ADDRESS_SPACE = 1 << 32
WII_MEM1 = (0x80000000, 24 << 20)
WII_MEM2 = (0x90000000, 64 << 20)


class GuestFault(Exception):
    """Access to unmapped guest memory or code."""


@dataclass
class Region:
    name: str
    base: int
    data: bytearray

    @property
    def size(self):
        return len(self.data)

    @property
    def end(self):
        return self.base + self.size


class MemoryMap:
    def __init__(self):
        self.regions = []
        self.mmio = None   # spm3ds.hw.Hardware for addresses no region maps, or None

    def add_region(self, name, base, size):
        if size <= 0 or base < 0 or base + size > ADDRESS_SPACE:
            raise ValueError(f"region {name} does not fit in the 32-bit address space")
        for other in self.regions:
            if base < other.end and other.base < base + size:
                raise ValueError(f"region {name} overlaps {other.name}")
        region = Region(name, base, bytearray(size))
        self.regions.append(region)
        return region

    @classmethod
    def wii_default(cls, mem1_size=WII_MEM1[1], mem2_size=WII_MEM2[1]):
        memory = cls()
        memory.add_region("MEM1", WII_MEM1[0], mem1_size)
        memory.add_region("MEM2", WII_MEM2[0], mem2_size)
        return memory

    def locate(self, ea, n):
        ea &= 0xFFFFFFFF
        for region in self.regions:
            offset = ea - region.base
            if 0 <= offset and offset + n <= region.size:
                return region, offset
        raise GuestFault(f"access of {n} bytes at {ea:#010x} is not mapped")

    def read(self, ea, n):
        try:
            region, offset = self.locate(ea, n)
        except GuestFault:
            if self.mmio is not None and self.mmio.handles(ea & 0xFFFFFFFF, n):
                return self.mmio.read(ea & 0xFFFFFFFF, n)
            raise
        return bytes(region.data[offset:offset + n])

    def write(self, ea, data):
        try:
            region, offset = self.locate(ea, len(data))
        except GuestFault:
            if self.mmio is not None and self.mmio.handles(ea & 0xFFFFFFFF, len(data)):
                self.mmio.write(ea & 0xFFFFFFFF, data)
                return
            raise
        region.data[offset:offset + len(data)] = data

    def read_uint(self, ea, n):
        return int.from_bytes(self.read(ea, n), "big")

    def write_uint(self, ea, n, value):
        self.write(ea, (value & ((1 << (8 * n)) - 1)).to_bytes(n, "big"))
