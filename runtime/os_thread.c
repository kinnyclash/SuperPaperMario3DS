/* Wii OS threads, message queues, mutexes, condition variables and alarms. See os_thread.h.
 * MIT License. */
#if !defined(__3DS__) && !defined(_POSIX_C_SOURCE)
#  define _POSIX_C_SOURCE 200809L   /* nanosleep, pthreads under -std=c99 */
#endif
#include "os_thread.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "hle.h"

void ppc_dispatch(ppc_ctx *c, uint32_t target); /* provided by the generated code */

/* ---- host primitives: libctru on the 3DS, pthreads elsewhere ---- */

#ifdef __3DS__
#include <3ds.h>
typedef LightLock host_lock;
typedef CondVar host_cond;
static void hl_init(host_lock *l) { LightLock_Init(l); }
static void hl_lock(host_lock *l) { LightLock_Lock(l); }
static void hl_unlock(host_lock *l) { LightLock_Unlock(l); }
static void hc_init(host_cond *c) { CondVar_Init(c); }
static void hc_wait(host_cond *c, host_lock *l) { CondVar_Wait(c, l); }
static void hc_signal(host_cond *c) { CondVar_WakeUp(c, 1); }
static void host_sleep_ns(uint64_t ns) { svcSleepThread((s64)ns); }
static uint32_t host_stack = 64u * 1024u;
static void thread_entry(void *arg);
static int host_spawn(void *arg) {
    return threadCreate(thread_entry, arg, host_stack, 0x30, -2, true) != NULL;
}
#else
#include <pthread.h>
#include <time.h>
typedef pthread_mutex_t host_lock;
typedef pthread_cond_t host_cond;
static void hl_init(host_lock *l) { pthread_mutex_init(l, NULL); }
static void hl_lock(host_lock *l) { pthread_mutex_lock(l); }
static void hl_unlock(host_lock *l) { pthread_mutex_unlock(l); }
static void hc_init(host_cond *c) { pthread_cond_init(c, NULL); }
static void hc_wait(host_cond *c, host_lock *l) { pthread_cond_wait(c, l); }
static void hc_signal(host_cond *c) { pthread_cond_signal(c); }
static void host_sleep_ns(uint64_t ns) {
    struct timespec ts;
    ts.tv_sec = (time_t)(ns / 1000000000ull);
    ts.tv_nsec = (long)(ns % 1000000000ull);
    nanosleep(&ts, NULL);
}
static uint32_t host_stack = 256u * 1024u;
static void thread_entry(void *arg);
static void *pthread_entry(void *arg) {
    thread_entry(arg);
    return NULL;
}
static int host_spawn(void *arg) {
    pthread_t t;
    pthread_attr_t attr;
    int ok;
    pthread_attr_init(&attr);
    pthread_attr_setstacksize(&attr, host_stack);
    pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED);
    ok = pthread_create(&t, &attr, pthread_entry, arg) == 0;
    pthread_attr_destroy(&attr);
    return ok;
}
#endif

void spm3ds_os_set_thread_stack(uint32_t bytes) {
    host_stack = bytes;
}

/* ---- state ---- */

typedef struct {
    int used;
    uint32_t guest;       /* OSThread address */
    ppc_ctx *ctx;
    int prio;
    uint32_t entry, param, stack_top;
    int suspend;          /* suspend count; > 0 means suspended */
    uint32_t wait_key;    /* nonzero while blocked */
    int exited, detached, started;
    uint32_t exit_value;
    uint64_t seq;         /* FIFO order among equal priorities */
    host_cond cond;
} os_thread;

typedef struct {
    uint32_t guest;
    uint32_t array, cap, head, count;
} os_mqueue;

typedef struct {
    uint32_t guest;
    int owner;            /* thread index, or -1 */
    uint32_t count;
} os_mutex;

typedef struct {
    uint32_t guest;
    int armed;
    uint64_t deadline, period;
    uint32_t handler;
} os_alarm;

static host_lock lock;
static os_thread threads[SPM3DS_OS_MAX_THREADS];
static os_mqueue queues[SPM3DS_OS_MAX_OBJECTS];
static os_mutex mutexes[SPM3DS_OS_MAX_OBJECTS];
static os_alarm alarms[SPM3DS_OS_MAX_OBJECTS];
static int current = -1;
static uint64_t seq_counter;
static int in_interrupt;
static int initialised;

/* Wait keys: the object's guest address plus a tag in the low two bits (guest objects are
 * word aligned). Tag 0: thread queue, mutex or condition; 1: join; 2: message queue not
 * empty; 3: message queue not full. */
#define KEY_JOIN(t) ((t) | 1u)
#define KEY_MQ_DATA(q) ((q) | 2u)
#define KEY_MQ_ROOM(q) ((q) | 3u)

static void fatal(ppc_ctx *c, const char *why) {
    fprintf(stderr, "spm3ds OS: %s\n", why);
    ppc_crash_report(c, why);
    abort();
}

static int find_thread(uint32_t guest) {
    int i;
    for (i = 0; i < SPM3DS_OS_MAX_THREADS; i++)
        if (threads[i].used && threads[i].guest == guest) return i;
    return -1;
}

static int ready(const os_thread *t) {
    return t->used && t->started && !t->exited && t->suspend == 0 && t->wait_key == 0;
}

static int pick_ready(void) {
    int i, best = -1;
    for (i = 0; i < SPM3DS_OS_MAX_THREADS; i++) {
        if (!ready(&threads[i])) continue;
        if (best < 0 || threads[i].prio < threads[best].prio ||
            (threads[i].prio == threads[best].prio && threads[i].seq < threads[best].seq))
            best = i;
    }
    return best;
}

static void make_ready_at_back(os_thread *t) {
    t->seq = ++seq_counter;
}

static void wake_key(uint32_t key) {
    int i;
    for (i = 0; i < SPM3DS_OS_MAX_THREADS; i++) {
        if (threads[i].used && threads[i].wait_key == key) {
            threads[i].wait_key = 0;
            make_ready_at_back(&threads[i]);
        }
    }
}

/* ---- alarms ---- */

static int next_alarm(uint64_t *deadline) {
    int i, found = 0;
    for (i = 0; i < SPM3DS_OS_MAX_OBJECTS; i++) {
        if (!alarms[i].armed) continue;
        if (!found || alarms[i].deadline < *deadline) *deadline = alarms[i].deadline;
        found = 1;
    }
    return found;
}

/* Runs every due alarm handler on ctx `c` (lock held on entry and exit). */
static void fire_due_alarms(ppc_ctx *c) {
    int i, fired;
    if (in_interrupt) return;
    do {
        uint64_t now = spm3ds_os_timebase();
        fired = 0;
        for (i = 0; i < SPM3DS_OS_MAX_OBJECTS; i++) {
            os_alarm *a = &alarms[i];
            ppc_ctx saved;
            uint32_t handler;
            if (!a->armed || a->deadline > now) continue;
            handler = a->handler;
            if (a->period) {
                while (a->deadline <= now) a->deadline += a->period;
            } else {
                a->armed = 0;
            }
            if (!handler) continue;
            saved = *c;
            c->r[3] = a->guest;
            c->r[4] = 0;
            c->lr = 0;
            in_interrupt = 1;
            hl_unlock(&lock);
            ppc_dispatch(c, handler);
            hl_lock(&lock);
            in_interrupt = 0;
            /* Restore the interrupted registers but keep memory-side state. */
            memcpy(c->r, saved.r, sizeof c->r);
            memcpy(c->f, saved.f, sizeof c->f);
            c->lr = saved.lr;
            c->ctr = saved.ctr;
            c->cr = saved.cr;
            c->xer = saved.xer;
            c->fpscr = saved.fpscr;
            memcpy(c->gqr, saved.gqr, sizeof c->gqr);
            fired = 1;
        }
    } while (fired);
}

/* ---- scheduling (lock held) ---- */

static void switch_to(int next) {
    current = next;
    hc_signal(&threads[next].cond);
}

/* Lets the best ready thread run. If `self` is not ready (blocked or suspended), waits until
 * it is scheduled again. Fires due alarms; if nothing is ready, sleeps until the next one. */
static void schedule(int self) {
    os_thread *me = &threads[self];
    int next;
    if (in_interrupt) return;
    for (;;) {
        uint64_t deadline;
        fire_due_alarms(me->ctx);
        next = pick_ready();
        if (next >= 0) break;
        if (next_alarm(&deadline)) {
            uint64_t now = spm3ds_os_timebase();
            hl_unlock(&lock);
            if (deadline > now) host_sleep_ns(spm3ds_os_ticks_to_ns(deadline - now));
            hl_lock(&lock);
            continue;
        }
        fatal(me->ctx, "every guest thread is blocked and no alarm is pending (deadlock)");
    }
    if (next == self) return;
    switch_to(next);
    while (current != self) hc_wait(&me->cond, &lock);
}

/* Blocks the calling thread until `key` is woken. */
static void block_on(int self, uint32_t key) {
    threads[self].wait_key = key;
    schedule(self);
}

static int self_of(ppc_ctx *c) {
    if (!initialised || current < 0) fatal(c, "OS thread call before spm3ds_os_threads_init");
    (void)c;
    return current;
}

/* ---- thread start and exit ---- */

static void finish_thread(int self, uint32_t value) {
    os_thread *t = &threads[self];
    int next;
    t->exited = 1;
    t->exit_value = value;
    wake_key(KEY_JOIN(t->guest));
    for (;;) {
        uint64_t deadline;
        fire_due_alarms(t->ctx);
        next = pick_ready();
        if (next >= 0) break;
        if (next_alarm(&deadline)) {
            uint64_t now = spm3ds_os_timebase();
            hl_unlock(&lock);
            if (deadline > now) host_sleep_ns(spm3ds_os_ticks_to_ns(deadline - now));
            hl_lock(&lock);
            continue;
        }
        fatal(t->ctx, "the last runnable guest thread exited");
    }
    if (t->detached) {
        free(t->ctx);
        t->ctx = NULL;
        t->used = 0;
    }
    switch_to(next);
}

static void thread_entry(void *arg) {
    os_thread *t = (os_thread *)arg;
    int self = (int)(t - threads);
    ppc_ctx *c;
    hl_lock(&lock);
    while (current != self) hc_wait(&t->cond, &lock);
    hl_unlock(&lock);
    c = t->ctx;
    c->r[1] = (t->stack_top - 8u) & ~7u;
    c->r[3] = t->param;
    c->lr = 0;
    ppc_dispatch(c, t->entry);
    hl_lock(&lock);
    finish_thread(self, c->r[3]);
    hl_unlock(&lock);
}

void spm3ds_os_threads_init(ppc_ctx *main_ctx, uint32_t main_thread) {
    if (!initialised) {
        hl_init(&lock);
        initialised = 1;
    }
    hl_lock(&lock);
    memset(threads, 0, sizeof threads);
    memset(queues, 0, sizeof queues);
    memset(alarms, 0, sizeof alarms);
    memset(mutexes, 0, sizeof mutexes);
    threads[0].used = 1;
    threads[0].guest = main_thread;
    threads[0].ctx = main_ctx;
    threads[0].prio = 16;
    threads[0].started = 1;
    hc_init(&threads[0].cond);
    current = 0;
    in_interrupt = 0;
    hl_unlock(&lock);
}

void spm3ds_os_poll(ppc_ctx *c) {
    int self;
    hl_lock(&lock);
    self = self_of(c);
    schedule(self);
    hl_unlock(&lock);
}

/* ---- thread HLE ---- */

void hle_OSCreateThread(ppc_ctx *c) {
    uint32_t guest = c->r[3], func = c->r[4], param = c->r[5], stack = c->r[6];
    int prio = (int)c->r[8], i;
    uint32_t attr = c->r[9];
    os_thread *t;
    hl_lock(&lock);
    (void)self_of(c);
    if (prio < 0 || prio > 31 || find_thread(guest) >= 0) {
        c->r[3] = 0;
        hl_unlock(&lock);
        return;
    }
    for (i = 0; i < SPM3DS_OS_MAX_THREADS && threads[i].used; i++) {}
    if (i == SPM3DS_OS_MAX_THREADS) {
        c->r[3] = 0;
        hl_unlock(&lock);
        return;
    }
    t = &threads[i];
    memset(t, 0, sizeof *t);
    t->ctx = (ppc_ctx *)malloc(sizeof *t->ctx);
    if (!t->ctx) {
        c->r[3] = 0;
        hl_unlock(&lock);
        return;
    }
    /* Same memory, hardware and small-data bases as the creator; fresh registers. */
    *t->ctx = *c;
    memset(t->ctx->r, 0, sizeof t->ctx->r);
    memset(t->ctx->f, 0, sizeof t->ctx->f);
    t->ctx->r[2] = c->r[2];
    t->ctx->r[13] = c->r[13];
    t->ctx->cr = t->ctx->ctr = t->ctx->xer = 0;
    t->ctx->icount = 0;
    t->used = 1;
    t->guest = guest;
    t->prio = prio;
    t->entry = func;
    t->param = param;
    t->stack_top = stack;
    t->suspend = 1;               /* threads are created suspended */
    t->detached = (attr & 1u) != 0;
    hc_init(&t->cond);
    if (!host_spawn(t)) {
        free(t->ctx);
        t->used = 0;
        c->r[3] = 0;
        hl_unlock(&lock);
        return;
    }
    t->started = 1;
    c->r[3] = 1;
    hl_unlock(&lock);
}

void hle_OSResumeThread(ppc_ctx *c) {
    int self, i;
    hl_lock(&lock);
    self = self_of(c);
    i = find_thread(c->r[3]);
    if (i < 0) {
        c->r[3] = 0;
        hl_unlock(&lock);
        return;
    }
    c->r[3] = (uint32_t)threads[i].suspend;
    if (threads[i].suspend > 0 && --threads[i].suspend == 0) make_ready_at_back(&threads[i]);
    schedule(self);
    hl_unlock(&lock);
}

void hle_OSSuspendThread(ppc_ctx *c) {
    int self, i;
    hl_lock(&lock);
    self = self_of(c);
    i = find_thread(c->r[3]);
    if (i < 0) {
        c->r[3] = 0;
        hl_unlock(&lock);
        return;
    }
    c->r[3] = (uint32_t)threads[i].suspend;
    threads[i].suspend++;
    schedule(self);
    hl_unlock(&lock);
}

void hle_OSInitThreadQueue(ppc_ctx *c) {
    (void)c;
}

void hle_OSSleepThread(ppc_ctx *c) {
    int self;
    hl_lock(&lock);
    self = self_of(c);
    block_on(self, c->r[3]);
    hl_unlock(&lock);
}

void hle_OSWakeupThread(ppc_ctx *c) {
    int self;
    hl_lock(&lock);
    self = self_of(c);
    wake_key(c->r[3]);
    schedule(self);
    hl_unlock(&lock);
}

void hle_OSYieldThread(ppc_ctx *c) {
    int self;
    hl_lock(&lock);
    self = self_of(c);
    make_ready_at_back(&threads[self]);
    schedule(self);
    hl_unlock(&lock);
}

void hle_OSExitThread(ppc_ctx *c) {
    int self;
    hl_lock(&lock);
    self = self_of(c);
    if (self == 0) fatal(c, "OSExitThread on the main thread is not supported");
    finish_thread(self, c->r[3]);
    hl_unlock(&lock);
#ifdef __3DS__
    threadExit(0);
#else
    pthread_exit(NULL);
#endif
}

void hle_OSJoinThread(ppc_ctx *c) {
    int self, i;
    uint32_t out = c->r[4];
    hl_lock(&lock);
    self = self_of(c);
    i = find_thread(c->r[3]);
    if (i < 0 || i == self || threads[i].detached) {
        c->r[3] = 0;
        hl_unlock(&lock);
        return;
    }
    while (!threads[i].exited) block_on(self, KEY_JOIN(threads[i].guest));
    if (out) ppc_w32(c, out, threads[i].exit_value);
    free(threads[i].ctx);
    threads[i].ctx = NULL;
    threads[i].used = 0;
    c->r[3] = 1;
    hl_unlock(&lock);
}

void hle_OSDetachThread(ppc_ctx *c) {
    int i;
    hl_lock(&lock);
    (void)self_of(c);
    i = find_thread(c->r[3]);
    if (i > 0) {
        threads[i].detached = 1;
        if (threads[i].exited) {
            free(threads[i].ctx);
            threads[i].ctx = NULL;
            threads[i].used = 0;
        }
    }
    hl_unlock(&lock);
}

void hle_OSGetCurrentThread(ppc_ctx *c) {
    hl_lock(&lock);
    c->r[3] = threads[self_of(c)].guest;
    hl_unlock(&lock);
}

void hle_OSIsThreadTerminated(ppc_ctx *c) {
    int i;
    hl_lock(&lock);
    i = find_thread(c->r[3]);
    c->r[3] = (i < 0 || threads[i].exited) ? 1u : 0u;
    hl_unlock(&lock);
}

void hle_OSIsThreadSuspended(ppc_ctx *c) {
    int i;
    hl_lock(&lock);
    i = find_thread(c->r[3]);
    c->r[3] = (i >= 0 && threads[i].suspend > 0) ? 1u : 0u;
    hl_unlock(&lock);
}

void hle_OSSetThreadPriority(ppc_ctx *c) {
    int self, i, prio = (int)c->r[4];
    hl_lock(&lock);
    self = self_of(c);
    i = find_thread(c->r[3]);
    if (i < 0 || prio < 0 || prio > 31) {
        c->r[3] = 0;
        hl_unlock(&lock);
        return;
    }
    threads[i].prio = prio;
    c->r[3] = 1;
    schedule(self);
    hl_unlock(&lock);
}

void hle_OSGetThreadPriority(ppc_ctx *c) {
    int i;
    hl_lock(&lock);
    i = find_thread(c->r[3]);
    c->r[3] = i < 0 ? 0xFFFFFFFFu : (uint32_t)threads[i].prio;
    hl_unlock(&lock);
}

/* MSR[EE] is bit 15 (0x8000). These only record the state. */
void hle_OSDisableInterrupts(ppc_ctx *c) {
    uint32_t was = (c->msr >> 15) & 1u;
    c->msr &= ~0x8000u;
    c->r[3] = was;
}

void hle_OSEnableInterrupts(ppc_ctx *c) {
    uint32_t was = (c->msr >> 15) & 1u;
    c->msr |= 0x8000u;
    c->r[3] = was;
}

void hle_OSRestoreInterrupts(ppc_ctx *c) {
    uint32_t was = (c->msr >> 15) & 1u;
    c->msr = c->r[3] ? (c->msr | 0x8000u) : (c->msr & ~0x8000u);
    c->r[3] = was;
}

/* ---- message queues ---- */

static os_mqueue *queue_of(uint32_t guest, int create) {
    int i, free_slot = -1;
    for (i = 0; i < SPM3DS_OS_MAX_OBJECTS; i++) {
        if (queues[i].guest == guest) return &queues[i];
        if (!queues[i].guest && free_slot < 0) free_slot = i;
    }
    if (!create || free_slot < 0) return NULL;
    queues[free_slot].guest = guest;
    return &queues[free_slot];
}

void hle_OSInitMessageQueue(ppc_ctx *c) {
    os_mqueue *q;
    hl_lock(&lock);
    q = queue_of(c->r[3], 1);
    if (!q) fatal(c, "too many message queues");
    q->array = c->r[4];
    q->cap = c->r[5];
    q->head = q->count = 0;
    hl_unlock(&lock);
}

static void mq_put(ppc_ctx *c, int jam) {
    int self = self_of(c);
    uint32_t guest = c->r[3], msg = c->r[4], flags = c->r[5];
    os_mqueue *q = queue_of(guest, 0);
    if (!q || q->cap == 0) {
        c->r[3] = 0;
        return;
    }
    while (q->count == q->cap) {
        if (!(flags & SPM3DS_OS_MESSAGE_BLOCK)) {
            c->r[3] = 0;
            return;
        }
        block_on(self, KEY_MQ_ROOM(guest));
    }
    if (jam) {
        q->head = (q->head + q->cap - 1u) % q->cap;
        ppc_w32(c, q->array + 4u * q->head, msg);
    } else {
        ppc_w32(c, q->array + 4u * ((q->head + q->count) % q->cap), msg);
    }
    q->count++;
    wake_key(KEY_MQ_DATA(guest));
    c->r[3] = 1;
    schedule(self);
}

void hle_OSSendMessage(ppc_ctx *c) {
    hl_lock(&lock);
    mq_put(c, 0);
    hl_unlock(&lock);
}

void hle_OSJamMessage(ppc_ctx *c) {
    hl_lock(&lock);
    mq_put(c, 1);
    hl_unlock(&lock);
}

void hle_OSReceiveMessage(ppc_ctx *c) {
    int self;
    uint32_t guest = c->r[3], out = c->r[4], flags = c->r[5], msg;
    os_mqueue *q;
    hl_lock(&lock);
    self = self_of(c);
    q = queue_of(guest, 0);
    if (!q || q->cap == 0) {
        c->r[3] = 0;
        hl_unlock(&lock);
        return;
    }
    while (q->count == 0) {
        if (!(flags & SPM3DS_OS_MESSAGE_BLOCK)) {
            c->r[3] = 0;
            hl_unlock(&lock);
            return;
        }
        block_on(self, KEY_MQ_DATA(guest));
    }
    msg = ppc_r32(c, q->array + 4u * q->head);
    q->head = (q->head + 1u) % q->cap;
    q->count--;
    if (out) ppc_w32(c, out, msg);
    wake_key(KEY_MQ_ROOM(guest));
    c->r[3] = 1;
    schedule(self);
    hl_unlock(&lock);
}

/* ---- mutexes and condition variables ---- */

static os_mutex *mutex_of(uint32_t guest) {
    int i, free_slot = -1;
    for (i = 0; i < SPM3DS_OS_MAX_OBJECTS; i++) {
        if (mutexes[i].guest == guest) return &mutexes[i];
        if (!mutexes[i].guest && free_slot < 0) free_slot = i;
    }
    if (free_slot < 0) return NULL;
    mutexes[free_slot].guest = guest;
    mutexes[free_slot].owner = -1;
    mutexes[free_slot].count = 0;
    return &mutexes[free_slot];
}

void hle_OSInitMutex(ppc_ctx *c) {
    os_mutex *m;
    hl_lock(&lock);
    m = mutex_of(c->r[3]);
    if (!m) fatal(c, "too many mutexes");
    m->owner = -1;
    m->count = 0;
    hl_unlock(&lock);
}

static void lock_mutex(ppc_ctx *c, int self, uint32_t guest, uint32_t count) {
    os_mutex *m = mutex_of(guest);
    if (!m) fatal(c, "too many mutexes");
    while (m->owner >= 0 && m->owner != self) block_on(self, guest);
    if (m->owner == self) {
        m->count += count;
    } else {
        m->owner = self;
        m->count = count;
    }
}

void hle_OSLockMutex(ppc_ctx *c) {
    int self;
    hl_lock(&lock);
    self = self_of(c);
    lock_mutex(c, self, c->r[3], 1);
    hl_unlock(&lock);
}

void hle_OSTryLockMutex(ppc_ctx *c) {
    int self;
    os_mutex *m;
    hl_lock(&lock);
    self = self_of(c);
    m = mutex_of(c->r[3]);
    if (!m || (m->owner >= 0 && m->owner != self)) {
        c->r[3] = 0;
    } else {
        if (m->owner == self) m->count++;
        else { m->owner = self; m->count = 1; }
        c->r[3] = 1;
    }
    hl_unlock(&lock);
}

void hle_OSUnlockMutex(ppc_ctx *c) {
    int self;
    os_mutex *m;
    hl_lock(&lock);
    self = self_of(c);
    m = mutex_of(c->r[3]);
    if (m && m->owner == self && --m->count == 0) {
        m->owner = -1;
        wake_key(m->guest);
        schedule(self);
    }
    hl_unlock(&lock);
}

void hle_OSInitCond(ppc_ctx *c) {
    (void)c;
}

void hle_OSWaitCond(ppc_ctx *c) {
    int self;
    uint32_t cond = c->r[3], guest = c->r[4], saved = 0;
    os_mutex *m;
    hl_lock(&lock);
    self = self_of(c);
    m = mutex_of(guest);
    if (m && m->owner == self) {
        saved = m->count;
        m->owner = -1;
        m->count = 0;
        wake_key(guest);
    }
    block_on(self, cond);
    if (saved) lock_mutex(c, self, guest, saved);
    hl_unlock(&lock);
}

void hle_OSSignalCond(ppc_ctx *c) {
    int self;
    hl_lock(&lock);
    self = self_of(c);
    wake_key(c->r[3]);
    schedule(self);
    hl_unlock(&lock);
}

/* ---- alarms ---- */

static os_alarm *alarm_of(uint32_t guest) {
    int i, free_slot = -1;
    for (i = 0; i < SPM3DS_OS_MAX_OBJECTS; i++) {
        if (alarms[i].guest == guest) return &alarms[i];
        if (!alarms[i].guest && free_slot < 0) free_slot = i;
    }
    if (free_slot < 0) return NULL;
    memset(&alarms[free_slot], 0, sizeof alarms[free_slot]);
    alarms[free_slot].guest = guest;
    return &alarms[free_slot];
}

void hle_OSCreateAlarm(ppc_ctx *c) {
    os_alarm *a;
    hl_lock(&lock);
    a = alarm_of(c->r[3]);
    if (!a) fatal(c, "too many alarms");
    a->armed = 0;
    hl_unlock(&lock);
}

void hle_OSSetAlarm(ppc_ctx *c) {
    os_alarm *a;
    uint64_t tick = ((uint64_t)c->r[5] << 32) | c->r[6];
    hl_lock(&lock);
    a = alarm_of(c->r[3]);
    if (!a) fatal(c, "too many alarms");
    a->deadline = spm3ds_os_timebase() + tick;
    a->period = 0;
    a->handler = c->r[7];
    a->armed = 1;
    hl_unlock(&lock);
}

void hle_OSSetPeriodicAlarm(ppc_ctx *c) {
    os_alarm *a;
    uint64_t start = ((uint64_t)c->r[5] << 32) | c->r[6];
    uint64_t period = ((uint64_t)c->r[7] << 32) | c->r[8];
    hl_lock(&lock);
    a = alarm_of(c->r[3]);
    if (!a) fatal(c, "too many alarms");
    a->deadline = start;
    a->period = period ? period : 1u;
    a->handler = c->r[9];
    a->armed = 1;
    hl_unlock(&lock);
}

void hle_OSCancelAlarm(ppc_ctx *c) {
    os_alarm *a;
    hl_lock(&lock);
    a = alarm_of(c->r[3]);
    if (a) a->armed = 0;
    hl_unlock(&lock);
}
