"""Function discovery, branch classification and problem reporting.

Classification rule (shared by the interpreter, the C emitter and this module):
  * `b` to an external (HLE) address                  -> tail_external
  * `b` to another function's entry                    -> tail
  * `b` to any other address, including this function's own entry -> local (goto)
  * `bl` to an external address                        -> call_external
  * `bl` to any other address                          -> call
The own-entry case is local. Treating it as a tail call would make a loop recurse forever.
"""
from collections import Counter, namedtuple
from dataclasses import dataclass

from .ppc import Unsupported, decode

# A problem is fatal for compilation. 'indirect' sites are compiled through a dispatcher
# and are reported for review only.
FATAL_KINDS = ("unsupported", "missing", "unresolved_call")

Problem = namedtuple("Problem", "addr kind message")


def sweep(image):
    """Decode every text word regardless of control flow.

    Returns [(address, word, message)] for each word that is not a supported instruction.
    Data stored in the text section can appear here, so treat results as candidates.
    """
    failures = []
    for addr in sorted(image):
        try:
            decode(image[addr], addr)
        except Unsupported as exc:
            failures.append((addr, image[addr], str(exc)))
    return failures


class CompileError(Exception):
    def __init__(self, problems):
        self.problems = list(problems)
        lines = [f"{p.addr:#010x} {p.kind}: {p.message}" for p in self.problems[:50]]
        more = len(self.problems) - len(lines)
        if more > 0:
            lines.append(f"... and {more} more")
        super().__init__("recompilation blocked by:\n  " + "\n  ".join(lines))


def classify(insn, fn_start, entries, external):
    """Return how a `b` or `bl` is compiled: local, tail, tail_external, call, call_external."""
    if insn.op == "bl":
        return "call_external" if insn.target in external else "call"
    if insn.op == "b":
        if insn.target in external:
            return "tail_external"
        if insn.target in entries and insn.target != fn_start:
            return "tail"
        return "local"
    raise ValueError(f"classify called on {insn.op}")


def reachable(image, start, entries, external, jump_targets=None):
    """Decode the intraprocedural body of the function at `start`.

    Returns (decoded, problems). `decoded` maps address to Insn for every decodable address
    reached. A decode failure ends that path and is recorded as a problem.

    `jump_targets` maps the address of a bctr to the table entries it can jump to. Those
    entries are local to the function and are followed like any other branch target.
    """
    jump_targets = jump_targets or {}
    decoded = {}
    problems = []
    seen = set()
    work = [start]
    while work:
        pc = work.pop()
        if pc in seen:
            continue
        seen.add(pc)
        if pc not in image:
            problems.append(Problem(pc, "missing", f"control reaches {pc:#010x}, outside the text"))
            continue
        try:
            insn = decode(image[pc], pc)
        except Unsupported as exc:
            problems.append(Problem(pc, "unsupported", str(exc)))
            continue
        decoded[pc] = insn
        op = insn.op
        if op == "bclr":
            if insn.bo == 20:
                continue
            work.append(pc + 4)
        elif op == "bcctr":
            if pc in jump_targets:
                if insn.bo != 20 or insn.link:
                    problems.append(Problem(pc, "unsupported",
                                            "a jump table needs an unconditional bctr without link"))
                    continue
                for target in jump_targets[pc]:
                    if target not in image:
                        problems.append(Problem(pc, "missing",
                                                f"jump table entry {target:#010x} is outside the text"))
                    else:
                        work.append(target)
                continue
            if insn.bo == 20 and not insn.link:
                continue
            work.append(pc + 4)
        elif op == "b":
            if classify(insn, start, entries, external) in ("tail", "tail_external"):
                continue
            work.append(insn.target)
        elif op == "bl":
            work.append(pc + 4)
        elif op == "bc":
            work.append(insn.target)
            work.append(pc + 4)
        else:
            work.append(pc + 4)
    return decoded, problems


@dataclass
class Analysis:
    entries: frozenset
    bodies: dict          # entry address -> {address: Insn}
    problems: list
    external: frozenset

    @property
    def fatal(self):
        return [p for p in self.problems if p.kind in FATAL_KINDS]

    def raise_if_fatal(self):
        if self.fatal:
            raise CompileError(self.fatal)

    def histogram(self):
        """Count of each mnemonic across all analysed function bodies."""
        counts = Counter()
        seen = set()
        for body in self.bodies.values():
            for addr, insn in body.items():
                if addr not in seen:
                    seen.add(addr)
                    counts[insn.op] += 1
        return counts

    def instruction_count(self):
        return sum(len(body) for body in self.bodies.values())


def analyze(image, entry, external=frozenset(), seeds=(), jump_targets=None):
    """Find every function reachable from `entry`, decode each body and collect problems.

    Entries are the program entry, any `seeds` (for example function addresses from a
    symbol file, which indirect calls need), and each `bl` target that is analysed code.
    The set is grown to a fixed point, because classifying a `b` depends on the entry set.
    `jump_targets` is {bctr address: [target, ...]} for declared jump tables.
    """
    external = frozenset(external)
    jump_targets = dict(jump_targets or {})
    entries = {entry} | {s for s in seeds if s in image}
    while True:
        found = set(entries)
        for f in sorted(entries):
            if f not in image:
                continue
            decoded, _ = reachable(image, f, entries, external, jump_targets)
            for insn in decoded.values():
                if insn.op == "bl" and insn.target not in external and insn.target in image:
                    found.add(insn.target)
        if found == entries:
            break
        entries = found

    bodies = {}
    problems = {}

    def note(problem):
        problems.setdefault((problem.addr, problem.kind), problem)

    if entry not in image:
        note(Problem(entry, "missing", "program entry point is outside the text"))
    for f in sorted(entries):
        if f not in image:
            note(Problem(f, "missing", f"function {f:#010x} is outside the text"))
            continue
        decoded, probs = reachable(image, f, entries, external, jump_targets)
        bodies[f] = decoded
        for problem in probs:
            note(problem)

    for body in bodies.values():
        for insn in body.values():
            if insn.op == "bl" and insn.target not in external and insn.target not in entries:
                note(Problem(insn.addr, "unresolved_call",
                             f"call to {insn.target:#010x}, which is neither analysed code "
                             f"nor a known symbol"))
            if insn.op == "bcctr" and insn.addr not in jump_targets:
                note(Problem(insn.addr, "indirect",
                             "indirect branch through CTR (dispatched at runtime)"))

    return Analysis(entries=frozenset(entries), bodies=bodies,
                    problems=sorted(problems.values(), key=lambda p: (p.addr, p.kind)),
                    external=external)
