#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import os
import pathlib
import re
import shlex
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(os.environ['USERQ_TEST_ROOT']) if 'USERQ_TEST_ROOT' in os.environ else pathlib.Path(__file__).resolve().parents[5]
AMD = ROOT / 'drivers/gpu/drm/amd/amdgpu'


class UserqRestoreWaitTests(unittest.TestCase):
    def test_interrupted_restore_wait(self):
        source = (AMD / 'amdgpu_userq.c').read_text()
        match = re.search(r'^int\s+amdgpu_userq_ensure_ev_fence\([^;]*?\)\n\{.*?^\}',
                          source, re.M | re.S)
        self.assertIsNotNone(match)
        create = re.search(r'^static int\s+amdgpu_userq_create\([^;]*?\)\n\{.*?^\}',
                           source, re.M | re.S)
        self.assertIsNotNone(create)
        start = create.group().index('\tr = amdgpu_userq_ensure_ev_fence(')
        end = create.group().index('\n\t/* Hold an exact identity', start)
        program = HARNESS.replace('ACTUAL_ENSURE', match.group())
        program = program.replace('ACTUAL_CREATE_GATE', create.group()[start:end])
        command = shlex.split(os.environ.get('CC', 'cc'))
        command += ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter']
        if os.environ.get('USERQ_TEST_SANITIZERS'):
            command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
        with tempfile.TemporaryDirectory(prefix='userq-restore-wait-') as temporary:
            directory = pathlib.Path(temporary)
            src, binary = directory / 'test.c', directory / 'test'
            src.write_text(program)
            subprocess.run(command + [str(src), '-o', str(binary)], check=True, timeout=30)
            subprocess.run([str(binary)], check=True, timeout=30)


HARNESS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <errno.h>
#define ERESTARTSYS 512
#define AMDGPU_HW_IP_GFX 0
#define AMDGPU_HW_IP_COMPUTE 1
#define current NULL
#define drm_file_err(...) ((void)0)
#define trace_amdgpu_userq_create_end(...) ((void)0)
struct mutex { bool held; };
struct work { bool pending; };
struct dma_fence { bool signaled; unsigned refs; };
struct amdgpu_userq_mgr { struct mutex userq_mutex; struct work resume_work; int userq_count[2]; };
struct amdgpu_eviction_fence_mgr { struct dma_fence fence; };
struct amdgpu_fpriv { struct amdgpu_userq_mgr userq_mgr; struct amdgpu_eviction_fence_mgr evf_mgr; };
struct amdgpu_device { bool userq_halt_for_enforce_isolation; };
struct amdgpu_usermode_queue { unsigned queue_type; };
static struct amdgpu_fpriv fpriv;
static unsigned flushes, schedules, ready_at, kill_at, lock_calls, fail_lock_at;
static unsigned maps, cleanups, cases;
static bool fatal, map_error;
static bool fatal_signal_pending(void *task) { return fatal; }
static int mutex_lock_killable(struct mutex *mutex)
{
    assert(!mutex->held);
    if (++lock_calls == fail_lock_at)
        return -EINTR;
    mutex->held = true;
    return 0;
}
static void mutex_unlock(struct mutex *mutex) { assert(mutex->held); mutex->held = false; }
static bool flush_delayed_work(struct work *work)
{
    assert(!fpriv.userq_mgr.userq_mutex.held && ++flushes < 64);
    work->pending = false;
    if (flushes == ready_at)
        fpriv.evf_mgr.fence.signaled = false;
    if (flushes == kill_at)
        fatal = true;
    return true;
}
static bool schedule_delayed_work(struct work *work, unsigned delay)
{
    assert(!fpriv.userq_mgr.userq_mutex.held && !delay);
    work->pending = true;
    schedules++;
    return true;
}
static struct dma_fence *amdgpu_evf_mgr_get_fence(struct amdgpu_eviction_fence_mgr *manager)
{
    assert(fpriv.userq_mgr.userq_mutex.held);
    manager->fence.refs++;
    return &manager->fence;
}
static bool dma_fence_is_signaled(struct dma_fence *fence) { return fence->signaled; }
static void dma_fence_put(struct dma_fence *fence) { assert(fence->refs == 2); fence->refs--; }
ACTUAL_ENSURE
static int amdgpu_userq_map_helper(struct amdgpu_usermode_queue *queue)
{
    assert(fpriv.userq_mgr.userq_mutex.held);
    maps++;
    return map_error ? -EIO : 0;
}
static void atomic_inc(int *value) { ++*value; }
static struct amdgpu_fpriv *fixture(void) { return &fpriv; }
static int create_gate(struct amdgpu_device *adev, struct amdgpu_usermode_queue *queue)
{
    struct amdgpu_fpriv *fpriv = fixture();
    struct amdgpu_userq_mgr *uq_mgr = &fpriv->userq_mgr;
    int r;
ACTUAL_CREATE_GATE
    return 0;
erase_doorbell:
    assert(!uq_mgr->userq_mutex.held);
    cleanups++;
    return r;
}
static void setup(bool signaled)
{
    fpriv = (struct amdgpu_fpriv){.evf_mgr.fence = {.signaled = signaled, .refs = 1}};
    flushes = schedules = ready_at = kill_at = lock_calls = fail_lock_at = 0;
    maps = cleanups = 0;
    fatal = map_error = false;
}
static void finish(bool success)
{
    assert(fpriv.evf_mgr.fence.refs == 1);
    assert(fpriv.userq_mgr.userq_mutex.held == success);
    if (success)
        mutex_unlock(&fpriv.userq_mgr.userq_mutex);
    cases++;
}
int main(void)
{
    setup(false);
    assert(!amdgpu_userq_ensure_ev_fence(&fpriv.userq_mgr, &fpriv.evf_mgr));
    assert(flushes == 1 && !schedules);
    finish(true);
    for (unsigned attempt = 1; attempt <= 8; attempt++) {
        setup(true);
        ready_at = attempt;
        assert(!amdgpu_userq_ensure_ev_fence(&fpriv.userq_mgr, &fpriv.evf_mgr));
        assert(flushes == attempt && schedules == attempt - 1);
        finish(true);
    }
    setup(true);
    fatal = true;
    assert(amdgpu_userq_ensure_ev_fence(&fpriv.userq_mgr, &fpriv.evf_mgr) == -ERESTARTSYS);
    assert(!flushes && !schedules && !lock_calls);
    finish(false);
    for (unsigned attempt = 1; attempt <= 8; attempt++) {
        /* Persistent restore errors must not trap a thread after SIGKILL. */
        setup(true);
        kill_at = attempt;
        assert(amdgpu_userq_ensure_ev_fence(&fpriv.userq_mgr, &fpriv.evf_mgr) == -ERESTARTSYS);
        assert(flushes == attempt && schedules == attempt - 1);
        finish(false);
        setup(true);
        fail_lock_at = attempt;
        assert(amdgpu_userq_ensure_ev_fence(&fpriv.userq_mgr, &fpriv.evf_mgr) == -ERESTARTSYS);
        assert(lock_calls == attempt && schedules == attempt - 1);
        finish(false);
    }
    for (unsigned halted = 0; halted < 2; halted++) {
        for (unsigned ip = 0; ip < 2; ip++) {
            for (unsigned mode = 0; mode < 3; mode++) {
                setup(false);
                struct amdgpu_device device = {.userq_halt_for_enforce_isolation = halted};
                struct amdgpu_usermode_queue queue = {.queue_type = ip};
                fatal = mode == 1;
                map_error = mode == 2;
                int ret = create_gate(&device, &queue);
                int expected = fatal ? -ERESTARTSYS : map_error && !halted ? -EIO : 0;
                assert(ret == expected);
                assert(cleanups == (expected != 0));
                assert(maps == (!fatal && !halted));
                assert(fpriv.userq_mgr.userq_count[ip] == !expected);
                assert(!fpriv.userq_mgr.userq_mutex.held);
                finish(false);
            }
        }
    }
    printf("UserQ restore wait: %u recovery, fatal-signal, lock-interruption and create-cleanup cases passed\n", cases);
    return 0;
}
'''
if __name__ == '__main__':
    unittest.main()
