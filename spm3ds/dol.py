"""Parser for the Nintendo DOL executable format.

Layout (all fields big-endian):
  0x000  7 text file offsets      0x1C  11 data file offsets
  0x048  7 text load addresses    0x064 11 data load addresses
  0x090  7 text sizes             0x0AC 11 data sizes
  0x0D8  bss address, bss size, entry point
"""
import struct
from dataclasses import dataclass

HEADER_SIZE = 0x100
TEXT_SLOTS = 7
DATA_SLOTS = 11
ADDRESS_LIMIT = 1 << 32


class DolError(ValueError):
    """The file is not a well-formed DOL image."""


@dataclass(frozen=True)
class Section:
    index: int
    address: int
    data: bytes

    @property
    def size(self):
        return len(self.data)

    @property
    def end(self):
        return self.address + self.size


@dataclass(frozen=True)
class Dol:
    entry: int
    text: tuple
    data: tuple
    bss_address: int
    bss_size: int

    def read(self, address, length):
        """Return `length` bytes at a load address, from a text or data section."""
        for section in self.text + self.data:
            if section.address <= address and address + length <= section.end:
                start = address - section.address
                return section.data[start:start + length]
        raise DolError(f"address {address:#010x}+{length} is not in any section")

    def load_into(self, memory):
        """Copy every text and data section into a MemoryMap at its load address."""
        for section in self.text + self.data:
            memory.write(section.address, section.data)

    def text_words(self):
        """Map every text address to its big-endian 32-bit instruction word."""
        image = {}
        for section in self.text:
            if section.size % 4:
                raise DolError(f"text section {section.index} size is not a multiple of 4")
            for offset in range(0, section.size, 4):
                image[section.address + offset] = int.from_bytes(section.data[offset:offset + 4], "big")
        return image


def _sections(blob, offsets, addresses, sizes, kind):
    result = []
    for index, (offset, address, size) in enumerate(zip(offsets, addresses, sizes)):
        if size == 0:
            continue
        if offset + size > len(blob):
            raise DolError(f"{kind} section {index} runs past end of file")
        if address + size > ADDRESS_LIMIT:
            raise DolError(f"{kind} section {index} exceeds the 32-bit address space")
        result.append(Section(index, address, bytes(blob[offset:offset + size])))
    return tuple(result)


def parse_dol(blob):
    """Parse a DOL image from bytes. Raises DolError for malformed input."""
    if len(blob) < HEADER_SIZE:
        raise DolError("file is shorter than the 0x100-byte DOL header")

    text_offsets = struct.unpack_from(f">{TEXT_SLOTS}I", blob, 0x00)
    data_offsets = struct.unpack_from(f">{DATA_SLOTS}I", blob, 0x1C)
    text_addresses = struct.unpack_from(f">{TEXT_SLOTS}I", blob, 0x48)
    data_addresses = struct.unpack_from(f">{DATA_SLOTS}I", blob, 0x64)
    text_sizes = struct.unpack_from(f">{TEXT_SLOTS}I", blob, 0x90)
    data_sizes = struct.unpack_from(f">{DATA_SLOTS}I", blob, 0xAC)
    bss_address, bss_size, entry = struct.unpack_from(">III", blob, 0xD8)

    text = _sections(blob, text_offsets, text_addresses, text_sizes, "text")
    data = _sections(blob, data_offsets, data_addresses, data_sizes, "data")

    if not any(s.address <= entry < s.end for s in text):
        raise DolError(f"entry point {entry:#010x} is not inside a text section")
    return Dol(entry=entry, text=text, data=data, bss_address=bss_address, bss_size=bss_size)
