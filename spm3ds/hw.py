"""Emulated Wii hardware registers and GX FIFO capture (Python mirror of runtime/hw.c).

Stores to the write-gather pipe at 0xCC008000 are appended to `fifo` in order, which is how
the game's GX library sends commands to the GPU (libogc gx.c: wgPipe = 0xCC008000). Other
addresses in 0xCC000000..0xCC00FFFF and 0xCD000000..0xCD00FFFF are plain storage. Status
registers that real hardware updates by itself are not modelled.
"""
WGPIPE = 0xCC008000
WGPIPE_SIZE = 0x20
BLOCKS = {0xCC000000: 0x10000, 0xCD000000: 0x10000}


class Hardware:
    def __init__(self):
        self.fifo = bytearray()
        self.regs = {base: bytearray(size) for base, size in BLOCKS.items()}

    def _reg(self, ea):
        for base, size in BLOCKS.items():
            if 0 <= ea - base < size:
                return self.regs[base], ea - base
        return None, None

    def handles(self, ea, n):
        """True when all n bytes from ea belong to emulated hardware."""
        for i in range(n):
            a = (ea + i) & 0xFFFFFFFF
            if 0 <= a - WGPIPE < WGPIPE_SIZE:
                continue
            if self._reg(a)[0] is None:
                return False
        return True

    def read(self, ea, n):
        out = bytearray()
        for i in range(n):
            a = (ea + i) & 0xFFFFFFFF
            if 0 <= a - WGPIPE < WGPIPE_SIZE:
                out.append(0)
            else:
                block, off = self._reg(a)
                out.append(block[off])
        return bytes(out)

    def write(self, ea, data):
        for i, b in enumerate(data):
            a = (ea + i) & 0xFFFFFFFF
            if 0 <= a - WGPIPE < WGPIPE_SIZE:
                self.fifo.append(b)
            else:
                block, off = self._reg(a)
                block[off] = b

    def take_fifo(self):
        data = bytes(self.fifo)
        self.fifo.clear()
        return data
