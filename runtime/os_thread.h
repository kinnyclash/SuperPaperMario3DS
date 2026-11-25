/* Wii OS threads, message queues, mutexes, condition variables and alarms for translated
 * code (HLE).
 *
 * Model. Every guest thread runs on its own host thread (pthreads on the host, libctru
 * threads on the 3DS) so translated code keeps an ordinary C call stack. A single scheduler
 * lock passes a baton: exactly one guest thread runs translated code at any time, which
 * matches the Wii's single CPU. The highest-priority ready thread runs (0 is highest, as on
 * the Wii); equal priorities run in FIFO order. Switches happen inside OS calls (resume,
 * suspend, sleep, wakeup, yield, exit, join, blocking message, mutex and condition calls)
 * and when an alarm fires. There is no timer preemption between OS calls.
 *
 * Alarms fire when the scheduler runs (any of the calls above, or spm3ds_os_poll) and while
 * every thread is blocked, in which case the host sleeps until the next alarm is due. The
 * handler runs on the current thread's context, as an interrupt would, with r3 = the alarm
 * and r4 = 0 (no OSContext); OS calls made from a handler do not switch threads.
 *
 * Calling convention: PowerPC SVR4/EABI. 64-bit arguments (OSTime) use an aligned register
 * pair starting at an odd register, so OSSetAlarm(alarm, tick, handler) reads the alarm in
 * r3, the tick in r5:r6 and the handler in r7.
 *
 * UNVERIFIED: the Nintendo SDK function names and argument orders come from community
 * decompilation references and are not checked against a public header here. The guest
 * OSThread, OSMessageQueue, OSMutex, OSCond and OSAlarm structures are treated as opaque
 * keys; only the message array given to OSInitMessageQueue is written, in order.
 * MIT License. */
#ifndef SPM3DS_OS_THREAD_H
#define SPM3DS_OS_THREAD_H

#include <stdint.h>

#include "ppc_ctx.h"

#define SPM3DS_OS_MAX_THREADS 32
#define SPM3DS_OS_MAX_OBJECTS 128   /* message queues, mutexes and alarms, each */
#define SPM3DS_OS_MESSAGE_BLOCK 1u  /* OS_MESSAGE_BLOCK flag */

/* Registers the calling host thread as the guest main thread (OSThread at guest address
 * `main_thread`, priority 16). Must be called once before any other function here. */
void spm3ds_os_threads_init(ppc_ctx *main_ctx, uint32_t main_thread);
/* Fires due alarms and lets a higher-priority ready thread run. Call it from the platform
 * loop (for example once per frame) when the game is not making OS calls. */
void spm3ds_os_poll(ppc_ctx *c);
/* Host stack size for new guest threads (default 256 KiB on the host, 64 KiB on the 3DS). */
void spm3ds_os_set_thread_stack(uint32_t bytes);

/* Threads */
void hle_OSCreateThread(ppc_ctx *c);     /* (thread, func, param, stack, size, prio, attr) */
void hle_OSResumeThread(ppc_ctx *c);     /* (thread) -> previous suspend count */
void hle_OSSuspendThread(ppc_ctx *c);    /* (thread) -> previous suspend count */
void hle_OSSleepThread(ppc_ctx *c);      /* (queue) */
void hle_OSWakeupThread(ppc_ctx *c);     /* (queue) */
void hle_OSInitThreadQueue(ppc_ctx *c);  /* (queue) */
void hle_OSYieldThread(ppc_ctx *c);
void hle_OSExitThread(ppc_ctx *c);       /* (value) */
void hle_OSJoinThread(ppc_ctx *c);       /* (thread, value_out) -> BOOL */
void hle_OSDetachThread(ppc_ctx *c);     /* (thread) */
void hle_OSGetCurrentThread(ppc_ctx *c); /* -> thread */
void hle_OSIsThreadTerminated(ppc_ctx *c);
void hle_OSIsThreadSuspended(ppc_ctx *c);
void hle_OSSetThreadPriority(ppc_ctx *c);/* (thread, prio) -> BOOL */
void hle_OSGetThreadPriority(ppc_ctx *c);/* (thread) -> prio */
/* Interrupt masking: bookkeeping only (MSR[EE]), because there is no preemption. */
void hle_OSDisableInterrupts(ppc_ctx *c);
void hle_OSEnableInterrupts(ppc_ctx *c);
void hle_OSRestoreInterrupts(ppc_ctx *c);
/* Message queues */
void hle_OSInitMessageQueue(ppc_ctx *c); /* (queue, msg_array, count) */
void hle_OSSendMessage(ppc_ctx *c);      /* (queue, msg, flags) -> BOOL */
void hle_OSJamMessage(ppc_ctx *c);       /* (queue, msg, flags) -> BOOL */
void hle_OSReceiveMessage(ppc_ctx *c);   /* (queue, msg_out, flags) -> BOOL */
/* Mutexes (recursive) and condition variables */
void hle_OSInitMutex(ppc_ctx *c);
void hle_OSLockMutex(ppc_ctx *c);
void hle_OSUnlockMutex(ppc_ctx *c);
void hle_OSTryLockMutex(ppc_ctx *c);     /* -> BOOL */
void hle_OSInitCond(ppc_ctx *c);
void hle_OSWaitCond(ppc_ctx *c);         /* (cond, mutex) */
void hle_OSSignalCond(ppc_ctx *c);       /* (cond): wakes every waiter */
/* Alarms */
void hle_OSCreateAlarm(ppc_ctx *c);      /* (alarm) */
void hle_OSSetAlarm(ppc_ctx *c);         /* (alarm, OSTime tick in r5:r6, handler r7) */
void hle_OSSetPeriodicAlarm(ppc_ctx *c); /* (alarm, start r5:r6, period r7:r8, handler r9) */
void hle_OSCancelAlarm(ppc_ctx *c);      /* (alarm) */

#endif /* SPM3DS_OS_THREAD_H */
