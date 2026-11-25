"""Wii OS threads, message queues, mutexes, condition variables and alarms (runtime/os_thread.c).

The driver's "guest functions" are C functions that call the HLE entry points the way
translated code does (arguments in r3.., results in r3). Only one guest thread runs at a
time, so the event log is deterministic and is compared with an order worked out by hand
from the scheduling rules in runtime/os_thread.h.
"""
import os
import subprocess
import tempfile
import unittest

from tests.support import HAVE_GCC, RUNTIME

DRIVER = r"""
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "hle.h"

#define BASE 0x80100000u
static uint8_t mem[1u << 16];
static ppc_ctx mainc;
static char logbuf[4096];

static void logf_(const char *s) { strcat(logbuf, s); strcat(logbuf, " "); }

static uint64_t wall_ns(void) {
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

typedef void (*hle_fn)(ppc_ctx *);
static uint32_t call(ppc_ctx *c, hle_fn f, uint32_t a, uint32_t b, uint32_t d, uint32_t e) {
    c->r[3] = a; c->r[4] = b; c->r[5] = d; c->r[6] = e; f(c); return c->r[3];
}

/* guest addresses */
#define T_A   (BASE + 0x0000u)
#define T_B   (BASE + 0x0400u)
#define T_C   (BASE + 0x0800u)
#define T_D   (BASE + 0x0C00u)
#define T_MAIN (BASE + 0x1000u)
#define MQ1   (BASE + 0x2000u)
#define MQ1A  (BASE + 0x2100u)
#define MQ2   (BASE + 0x2200u)
#define MQ2A  (BASE + 0x2300u)
#define MQ3   (BASE + 0x2400u)
#define MQ3A  (BASE + 0x2500u)
#define TQ    (BASE + 0x2600u)
#define MTX   (BASE + 0x2700u)
#define CND   (BASE + 0x2800u)
#define ALM   (BASE + 0x2900u)
#define ALM2  (BASE + 0x2A00u)
#define OUT   (BASE + 0x2B00u)
#define FN_A  0x80003000u
#define FN_B  0x80003100u
#define FN_C  0x80003200u
#define FN_D  0x80003300u
#define FN_ALARM 0x80003400u
#define FN_PERIODIC 0x80003500u
#define FN_CONSUMER 0x80003600u

static int shared_value;
static int periodic_hits;

static void create(ppc_ctx *c, uint32_t t, uint32_t fn, uint32_t param, uint32_t prio) {
    c->r[3] = t; c->r[4] = fn; c->r[5] = param; c->r[6] = BASE + 0xF000u; c->r[7] = 0x1000;
    c->r[8] = prio; c->r[9] = 0; hle_OSCreateThread(c);
    if (!c->r[3]) { printf("create failed\n"); exit(1); }
}

static void thread_a(ppc_ctx *c) {               /* priority 10 (higher than main) */
    uint32_t param = c->r[3];
    char buf[32];
    sprintf(buf, "A:start(%u)", (unsigned)param); logf_(buf);
    call(c, hle_OSSendMessage, MQ1, 111, SPM3DS_OS_MESSAGE_BLOCK, 0);
    logf_("A:sent");
    call(c, hle_OSReceiveMessage, MQ2, OUT, SPM3DS_OS_MESSAGE_BLOCK, 0);
    sprintf(buf, "A:reply=%u", (unsigned)ppc_r32(c, OUT)); logf_(buf);
    call(c, hle_OSGetCurrentThread, 0, 0, 0, 0);
    logf_(c->r[3] == T_A ? "A:self-ok" : "A:self-wrong");
    c->r[3] = 42;                                /* return value = exit value */
}

static void thread_b(ppc_ctx *c) {               /* priority 16, same as main */
    logf_("B1"); call(c, hle_OSYieldThread, 0, 0, 0, 0);
    logf_("B2"); call(c, hle_OSYieldThread, 0, 0, 0, 0);
    logf_("B3");
}

static void thread_c(ppc_ctx *c) {
    logf_("C1"); call(c, hle_OSYieldThread, 0, 0, 0, 0);
    logf_("C2"); call(c, hle_OSYieldThread, 0, 0, 0, 0);
    logf_("C3");
    call(c, hle_OSExitThread, 7, 0, 0, 0);       /* explicit exit */
    logf_("C:after-exit-should-not-run");
}

static void thread_d(ppc_ctx *c) {               /* priority 20 (lower than main) */
    logf_("D:sleep");
    call(c, hle_OSSleepThread, TQ, 0, 0, 0);
    logf_("D:woken");
    call(c, hle_OSSendMessage, MQ3, 5, SPM3DS_OS_MESSAGE_BLOCK, 0);
}

static void consumer(ppc_ctx *c) {               /* waits on the condition variable */
    call(c, hle_OSLockMutex, MTX, 0, 0, 0);
    while (shared_value == 0) call(c, hle_OSWaitCond, CND, MTX, 0, 0);
    logf_(shared_value == 9 ? "consumer:9" : "consumer:wrong");
    call(c, hle_OSUnlockMutex, MTX, 0, 0, 0);
}

static void alarm_handler(ppc_ctx *c) {
    logf_(c->r[3] == ALM ? "alarm:fired" : "alarm:wrong");
    call(c, hle_OSSendMessage, MQ3, 77, 0, 0);   /* non-blocking from interrupt context */
}

static void periodic_handler(ppc_ctx *c) {
    (void)c;
    periodic_hits++;
}

void ppc_dispatch(ppc_ctx *c, uint32_t target) {
    switch (target) {
    case FN_A: thread_a(c); break;
    case FN_B: thread_b(c); break;
    case FN_C: thread_c(c); break;
    case FN_D: thread_d(c); break;
    case FN_ALARM: alarm_handler(c); break;
    case FN_PERIODIC: periodic_handler(c); break;
    case FN_CONSUMER: consumer(c); break;
    default: printf("bad dispatch %08x\n", (unsigned)target); exit(1);
    }
}

int main(int argc, char **argv) {
    ppc_ctx *c = &mainc;
    uint64_t t0;
    c->regions[0].base = BASE; c->regions[0].size = sizeof mem; c->regions[0].data = mem;
    c->nregions = 1;
    spm3ds_set_clock(wall_ns);
    spm3ds_os_threads_init(c, T_MAIN);

    if (argc > 1 && strcmp(argv[1], "deadlock") == 0) {
        call(c, hle_OSInitMessageQueue, MQ1, MQ1A, 1, 0);
        call(c, hle_OSReceiveMessage, MQ1, OUT, SPM3DS_OS_MESSAGE_BLOCK, 0);
        printf("not reached\n");
        return 0;
    }

    call(c, hle_OSInitMessageQueue, MQ1, MQ1A, 2, 0);
    call(c, hle_OSInitMessageQueue, MQ2, MQ2A, 2, 0);
    call(c, hle_OSInitMessageQueue, MQ3, MQ3A, 4, 0);

    /* 1. A higher-priority thread preempts its creator when resumed. */
    create(c, T_A, FN_A, 5, 10);
    printf("suspended %u\n", (unsigned)call(c, hle_OSIsThreadSuspended, T_A, 0, 0, 0));
    printf("resume_prev %u\n", (unsigned)call(c, hle_OSResumeThread, T_A, 0, 0, 0));
    logf_("main:after-resume");
    call(c, hle_OSReceiveMessage, MQ1, OUT, SPM3DS_OS_MESSAGE_BLOCK, 0);
    {
        char buf[32]; sprintf(buf, "main:got=%u", (unsigned)ppc_r32(c, OUT)); logf_(buf);
    }
    call(c, hle_OSSendMessage, MQ2, 222, SPM3DS_OS_MESSAGE_BLOCK, 0);
    logf_("main:after-reply");
    {   /* evaluate before printing: argument order is unspecified in C */
        uint32_t ok = call(c, hle_OSJoinThread, T_A, OUT, 0, 0);
        printf("join %u value %u\n", (unsigned)ok, (unsigned)ppc_r32(c, OUT));
    }

    /* 2. Equal priorities take turns at yields, in FIFO order. */
    create(c, T_B, FN_B, 0, 16);
    create(c, T_C, FN_C, 0, 16);
    call(c, hle_OSResumeThread, T_B, 0, 0, 0);
    call(c, hle_OSResumeThread, T_C, 0, 0, 0);
    logf_("M1"); call(c, hle_OSYieldThread, 0, 0, 0, 0);
    logf_("M2"); call(c, hle_OSYieldThread, 0, 0, 0, 0);
    logf_("M3"); call(c, hle_OSYieldThread, 0, 0, 0, 0);
    logf_("M4");
    {
        uint32_t ok = call(c, hle_OSJoinThread, T_C, OUT, 0, 0);
        printf("join_c %u value %u\n", (unsigned)ok, (unsigned)ppc_r32(c, OUT));
    }
    call(c, hle_OSJoinThread, T_B, 0, 0, 0);

    /* 3. A lower-priority thread runs only when main blocks; wakeup makes it ready. */
    create(c, T_D, FN_D, 0, 20);
    call(c, hle_OSResumeThread, T_D, 0, 0, 0);
    logf_("main:D-resumed");
    call(c, hle_OSReceiveMessage, MQ3, OUT, 0, 0);       /* empty: returns at once */
    printf("nonblocking_empty %u\n", (unsigned)c->r[3]);
    call(c, hle_OSYieldThread, 0, 0, 0, 0);               /* D has lower priority: no switch */
    logf_("main:yielded");
    printf("log %s\n", logbuf);
    fflush(stdout);
    /* Main sleeps on a queue nobody wakes; D then runs and sleeps too: a deadlock. */
    call(c, hle_OSSleepThread, 0x1234u, 0, 0, 0);
    printf("unreachable\n");
    return 0;
}
"""

DRIVER3_MAIN = r"""
int main(void) {
    ppc_ctx *c = &mainc;
    uint64_t t0, t1;
    int i;
    c->regions[0].base = BASE; c->regions[0].size = sizeof mem; c->regions[0].data = mem;
    c->nregions = 1;
    spm3ds_set_clock(wall_ns);
    spm3ds_os_threads_init(c, T_MAIN);
    call(c, hle_OSInitMessageQueue, MQ3, MQ3A, 4, 0);

    /* Sleep/wakeup: D sleeps; main wakes it, then blocks on D's message. */
    create(c, T_D, FN_D, 0, 20);
    call(c, hle_OSResumeThread, T_D, 0, 0, 0);
    call(c, hle_OSReceiveMessage, MQ3, OUT, 0, 0);          /* D not run yet: empty */
    logf_(c->r[3] ? "main:early" : "main:empty");
    call(c, hle_OSSendMessage, MQ3, 1, 0, 0);               /* fill one slot ourselves */
    call(c, hle_OSReceiveMessage, MQ3, OUT, SPM3DS_OS_MESSAGE_BLOCK, 0);
    logf_(ppc_r32(c, OUT) == 1 ? "main:own-msg" : "main:wrong");
    /* Block: D runs, logs and sleeps on TQ; nothing is ready, so a deadlock would follow if we
     * did not set an alarm first. The alarm handler sends 77, which wakes main. */
    call(c, hle_OSCreateAlarm, ALM, 0, 0, 0);
    c->r[3] = ALM; c->r[5] = 0; c->r[6] = 60750u * 5u; c->r[7] = FN_ALARM;   /* 5 ms */
    hle_OSSetAlarm(c);
    t0 = wall_ns();
    call(c, hle_OSReceiveMessage, MQ3, OUT, SPM3DS_OS_MESSAGE_BLOCK, 0);
    t1 = wall_ns();
    {
        char buf[48]; sprintf(buf, "main:alarm-msg=%u", (unsigned)ppc_r32(c, OUT)); logf_(buf);
    }
    printf("alarm_waited_ms %llu\n", (unsigned long long)((t1 - t0) / 1000000ull));
    call(c, hle_OSWakeupThread, TQ, 0, 0, 0);               /* D ready but lower priority */
    logf_("main:woke-D");
    call(c, hle_OSReceiveMessage, MQ3, OUT, SPM3DS_OS_MESSAGE_BLOCK, 0); /* D runs, sends 5 */
    {
        char buf[48]; sprintf(buf, "main:D-msg=%u", (unsigned)ppc_r32(c, OUT)); logf_(buf);
    }
    call(c, hle_OSJoinThread, T_D, 0, 0, 0);

    /* Jam puts a message at the front; a full queue refuses non-blocking sends. */
    for (i = 0; i < 4; i++) call(c, hle_OSSendMessage, MQ3, 10 + i, 0, 0);
    printf("full_send %u\n", (unsigned)call(c, hle_OSSendMessage, MQ3, 99, 0, 0));
    call(c, hle_OSReceiveMessage, MQ3, OUT, 0, 0);
    call(c, hle_OSJamMessage, MQ3, 5, 0, 0);
    printf("order");
    for (i = 0; i < 4; i++) {
        call(c, hle_OSReceiveMessage, MQ3, OUT, 0, 0);
        printf(" %u", (unsigned)ppc_r32(c, OUT));
    }
    printf("\n");

    /* Mutex and condition variable: the consumer (higher priority) waits until signalled. */
    call(c, hle_OSInitMutex, MTX, 0, 0, 0);
    call(c, hle_OSInitCond, CND, 0, 0, 0);
    create(c, T_B, FN_CONSUMER, 0, 12);
    call(c, hle_OSResumeThread, T_B, 0, 0, 0);              /* consumer waits on the cond */
    logf_("main:consumer-waiting");
    call(c, hle_OSLockMutex, MTX, 0, 0, 0);
    call(c, hle_OSLockMutex, MTX, 0, 0, 0);                 /* recursive */
    printf("trylock_own %u\n", (unsigned)call(c, hle_OSTryLockMutex, MTX, 0, 0, 0));
    shared_value = 9;
    call(c, hle_OSSignalCond, CND, 0, 0, 0);                /* consumer needs the mutex */
    logf_("main:signalled");
    call(c, hle_OSUnlockMutex, MTX, 0, 0, 0);
    call(c, hle_OSUnlockMutex, MTX, 0, 0, 0);
    logf_("main:unlocked-twice");
    call(c, hle_OSUnlockMutex, MTX, 0, 0, 0);               /* last unlock: consumer runs */
    logf_("main:unlocked");
    call(c, hle_OSJoinThread, T_B, 0, 0, 0);

    /* Periodic alarm: about 4 ms period, polled for about 30 ms, then cancelled. */
    {
        uint64_t now; uint32_t hi, lo;
        call(c, hle_OSGetTime, 0, 0, 0, 0); hi = c->r[3]; lo = c->r[4];
        now = ((uint64_t)hi << 32) | lo;
        call(c, hle_OSCreateAlarm, ALM2, 0, 0, 0);
        c->r[3] = ALM2; c->r[5] = (uint32_t)(now >> 32); c->r[6] = (uint32_t)now;
        c->r[7] = 0; c->r[8] = 60750u * 4u; c->r[9] = FN_PERIODIC;
        hle_OSSetPeriodicAlarm(c);
        t0 = wall_ns();
        while (wall_ns() - t0 < 30000000ull) spm3ds_os_poll(c);
        call(c, hle_OSCancelAlarm, ALM2, 0, 0, 0);
        i = periodic_hits;
        t0 = wall_ns();
        while (wall_ns() - t0 < 10000000ull) spm3ds_os_poll(c);
        printf("periodic %d after_cancel %d\n", i, periodic_hits - i);
    }

    /* Interrupt masking bookkeeping. */
    c->msr = 0x8000u;
    {
        uint32_t d1 = call(c, hle_OSDisableInterrupts, 0, 0, 0, 0);
        uint32_t d2 = call(c, hle_OSDisableInterrupts, 0, 0, 0, 0);
        uint32_t r1 = call(c, hle_OSRestoreInterrupts, 1, 0, 0, 0);
        printf("irq %u %u %u\n", (unsigned)d1, (unsigned)d2, (unsigned)r1);
    }
    printf("prio %u\n", (unsigned)call(c, hle_OSGetThreadPriority, T_MAIN, 0, 0, 0));
    printf("log %s\n", logbuf);
    return 0;
}
"""


def body_without_main(src):
    return src[:src.index("int main(int argc")]


@unittest.skipUnless(HAVE_GCC, "host gcc not available")
class OsThreadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="spm3ds-thr-")
        cls.exes = {}
        for name, src in (("one", DRIVER), ("three", body_without_main(DRIVER) + DRIVER3_MAIN)):
            path = os.path.join(cls.tmp.name, f"{name}.c")
            with open(path, "w") as f:
                f.write(src)
            exe = os.path.join(cls.tmp.name, name)
            subprocess.run(["gcc", "-std=c99", "-Wall", "-Wno-unused-variable",
                            "-Wno-unused-function", "-I", RUNTIME, path,
                            os.path.join(RUNTIME, "os_thread.c"), os.path.join(RUNTIME, "os.c"),
                            os.path.join(RUNTIME, "hle.c"), os.path.join(RUNTIME, "dvd.c"),
                            "-o", exe, "-lpthread"], check=True)
            cls.exes[name] = exe

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_exe(self, name, *args, expect_ok=True):
        proc = subprocess.run([self.exes[name], *args], capture_output=True, text=True,
                              timeout=30)
        if expect_ok:
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc

    def test_preemption_messages_join_and_yield_order(self):
        proc = self.run_exe("one", expect_ok=False)
        out = proc.stdout
        lines = {l.split()[0]: l.split()[1:] for l in out.splitlines()}
        self.assertEqual(lines["suspended"], ["1"])
        self.assertEqual(lines["resume_prev"], ["1"])
        self.assertEqual(lines["join"], ["1", "value", "42"])
        self.assertEqual(lines["join_c"], ["1", "value", "7"])
        self.assertEqual(lines["nonblocking_empty"], ["0"])
        # The program ends by sleeping on a queue nobody wakes: a reported deadlock.
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("deadlock", proc.stderr)
        self.assertNotIn("unreachable", out)
        self.assertEqual(lines["log"], [
            "A:start(5)", "A:sent",          # A (priority 10) preempts main at resume
            "main:after-resume",             # A blocks on MQ2
            "main:got=111",
            "A:reply=222", "A:self-ok",      # the reply wakes A, which preempts main
            "main:after-reply",
            "M1", "B1", "C1", "M2", "B2", "C2", "M3", "B3",   # equal priority: FIFO turns
            "C3",                            # B finished; C runs before main (older turn)
            "M4",
            "main:D-resumed",                # D (priority 20) does not preempt
            "main:yielded",                  # yielding does not run a lower priority
        ])

    def test_event_order(self):
        proc = self.run_exe("three")
        log = proc.stdout.split("log ", 1)[1].split()
        self.assertEqual(log, [
            "main:empty", "main:own-msg",
            "D:sleep",                       # main blocks; D (lower priority) finally runs
            "alarm:fired",                   # nothing ready: the scheduler waits for the alarm
            "main:alarm-msg=77",
            "main:woke-D",                   # D is ready but has lower priority than main
            "D:woken",                       # main blocks on the queue, so D runs
            "main:D-msg=5",
            "main:consumer-waiting",         # consumer (12) ran at resume and waits
            "main:signalled",                # consumer is woken but needs the mutex
            "main:unlocked-twice",           # still held once (count 3 after trylock)
            "consumer:9",                    # last unlock: the consumer preempts main
            "main:unlocked",
        ])

    def test_alarm_queue_and_mutex_results(self):
        proc = self.run_exe("three")
        lines = {l.split()[0]: l.split()[1:] for l in proc.stdout.splitlines()
                 if not l.startswith("log ")}
        self.assertGreaterEqual(int(lines["alarm_waited_ms"][0]), 4)
        self.assertEqual(lines["full_send"], ["0"])
        self.assertEqual(lines["order"], ["5", "11", "12", "13"])
        self.assertEqual(lines["trylock_own"], ["1"])
        hits, after = int(lines["periodic"][0]), int(lines["periodic"][2])
        self.assertTrue(4 <= hits <= 10, hits)          # about 30 ms / 4 ms
        self.assertEqual(after, 0)
        self.assertEqual(lines["irq"], ["1", "0", "0"])
        self.assertEqual(lines["prio"], ["16"])

    def test_deadlock_is_reported(self):
        proc = self.run_exe("one", "deadlock", expect_ok=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("deadlock", proc.stderr)
        self.assertNotIn("not reached", proc.stdout)


if __name__ == "__main__":
    unittest.main()
