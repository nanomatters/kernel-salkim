#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import os
import pathlib
import re
import shlex
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(os.environ.get('VA_BATCH_TEST_ROOT', pathlib.Path(__file__).resolve().parents[5]))
AMD = ROOT / 'drivers/gpu/drm/amd/amdgpu'


class UserqResetTests(unittest.TestCase):
    def test_compute_resume_failures(self):
        source = (AMD / 'amdgpu_gfx.c').read_text()
        match = re.search(r'^int amdgpu_gfx_reset_mes_compute\(.*?^\}', source, re.M | re.S)
        self.assertIsNotNone(match)
        command = shlex.split(os.environ.get('CC', 'cc'))
        command += ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-sign-compare']
        if os.environ.get('USERQ_TEST_SANITIZERS'):
            command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-fno-sanitize-recover=all']
        with tempfile.TemporaryDirectory(prefix='compute-resume-') as temporary:
            source, binary = pathlib.Path(temporary) / 'test.c', pathlib.Path(temporary) / 'test'
            source.write_text(HARNESS.replace('ACTUAL_SOURCE', match.group()))
            subprocess.run(command + [str(source), '-o', str(binary)], check=True, timeout=30)
            subprocess.run([str(binary)], check=True, timeout=30)


HARNESS = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#define AMDGPU_MAX_COMPUTE_RINGS 4
#define AMDGPU_RING_TYPE_COMPUTE 1
#define dev_err(...) ((void)0)
#define guard(type) __attribute__((cleanup(unlock_guard))) int *guard_ = lock_guard
static int *lock_guard(int *lock) { assert(!*lock); ++*lock; return lock; }
static void unlock_guard(int **lock) { assert(**lock == 1); --**lock; }
struct amdgpu_fence { int unused; };
struct amdgpu_ring { int unused; };
struct amdgpu_usermode_queue { int unused; };
struct amdgpu_gfx_deferred_entry { struct amdgpu_ring *ring; struct amdgpu_fence *fence; };
struct amdgpu_mes_hung_queue_hqd_info { int pipe_index, queue_index, queue_type; };
struct mes_remove_queue_input { int unused; };
struct amdgpu_mes {
    unsigned hung_queue_hqd_info_offset;
    const struct mes_funcs { int (*remove_hw_queue)(struct amdgpu_mes *, struct mes_remove_queue_input *); } *funcs;
};
struct amdgpu_device {
    struct { struct { bool use_mmio_for_reset; int reset_mutex; unsigned mes_hung_db_array[32]; } mec; } gfx;
    struct amdgpu_mes mes;
    struct reset_domain { int sem; } *reset_domain;
};
static unsigned stops, starts, resumes, replays, resets;
static int resume_result, reset_result;
static void amdgpu_gfx_reset_stop_compute_scheds(struct amdgpu_device *adev, struct amdgpu_ring *ring)
{ (void)adev; (void)ring; stops++; }
static void amdgpu_gfx_reset_start_compute_scheds(struct amdgpu_device *adev, struct amdgpu_ring *ring)
{ (void)adev; (void)ring; starts++; }
static int amdgpu_mes_suspend(struct amdgpu_device *adev, unsigned id)
{ (void)adev; assert(!id); return 0; }
static int amdgpu_mes_resume(struct amdgpu_device *adev, unsigned id)
{ (void)adev; assert(!id); resumes++; return resume_result; }
static int amdgpu_mes_detect_and_reset_hung_queues(struct amdgpu_device *adev, int type, bool detect,
                                                  unsigned *count, unsigned *array, unsigned id)
{ (void)adev; (void)type; (void)detect; (void)count; (void)array; (void)id; assert(false); return -EIO; }
static int amdgpu_gfx_mes_reset_queue_start(struct amdgpu_ring *ring, unsigned id,
                                            struct amdgpu_fence *fence, bool mmio)
{ (void)fence; assert(ring && !id && mmio); resets++; return reset_result; }
static int amdgpu_gfx_reset_mes_kcq(struct amdgpu_device *adev, struct amdgpu_ring *ring, unsigned db,
                                   struct amdgpu_ring **out, struct amdgpu_fence **fence)
{ (void)adev; (void)ring; (void)db; (void)out; (void)fence; assert(false); return -EIO; }
static int amdgpu_amdkfd_reset_mes_queue(struct amdgpu_device *adev, unsigned id, int type, int pipe,
                                        int queue, unsigned db)
{ (void)adev; (void)id; (void)type; (void)pipe; (void)queue; (void)db; assert(false); return -EIO; }
static int mes_userq_reset_queue(struct amdgpu_device *adev, struct amdgpu_usermode_queue *uq,
                                  int type, int pipe, int queue, unsigned db)
{ (void)adev; (void)uq; (void)type; (void)pipe; (void)queue; (void)db; assert(false); return -EIO; }
static int mes_userq_reset(struct amdgpu_usermode_queue *queue)
{ (void)queue; assert(false); return -EIO; }
static int amdgpu_ring_reset_helper_end(struct amdgpu_ring *ring, struct amdgpu_fence *fence)
{ (void)fence; assert(ring && resumes && !resume_result); replays++; return 0; }
static void amdgpu_mes_lock(struct amdgpu_mes *mes) { assert(mes); }
static void amdgpu_mes_unlock(struct amdgpu_mes *mes) { assert(mes); }
static int down_read_trylock(int *sem) { assert(sem); return 1; }
static void up_read(int *sem) { assert(sem); }
static int remove_hw_queue(struct amdgpu_mes *mes, struct mes_remove_queue_input *input)
{ assert(mes && input); return 0; }
ACTUAL_SOURCE
int main(void)
{
    struct amdgpu_device dev = {.gfx.mec.use_mmio_for_reset = true};
    struct amdgpu_ring ring = {0};
    struct amdgpu_fence fence = {0};
    struct mes_remove_queue_input input = {0};
    const struct mes_funcs funcs = {.remove_hw_queue = remove_hw_queue};
    struct reset_domain domain = {0};
    dev.mes.funcs = &funcs; dev.reset_domain = &domain;
    assert(amdgpu_gfx_reset_mes_compute(&dev, &ring, &fence, NULL, NULL, NULL) == 0);
    assert(starts == 1 && replays == 1 && !dev.gfx.mec.reset_mutex);
    resume_result = -ETIMEDOUT;
    assert(amdgpu_gfx_reset_mes_compute(&dev, &ring, &fence, NULL, NULL, NULL) == -ETIMEDOUT);
    assert(starts == 1 && replays == 1 && !dev.gfx.mec.reset_mutex);
    reset_result = -EIO;
    assert(amdgpu_gfx_reset_mes_compute(&dev, &ring, &fence, NULL, NULL, NULL) == -EIO);
    assert(starts == 1 && replays == 1 && !dev.gfx.mec.reset_mutex);
    reset_result = 0;
    assert(amdgpu_gfx_reset_mes_compute(&dev, NULL, NULL, NULL, NULL, &input) == -ETIMEDOUT);
    assert(starts == 1 && replays == 1 && !dev.gfx.mec.reset_mutex);
    resume_result = 0;
    assert(amdgpu_gfx_reset_mes_compute(&dev, NULL, NULL, NULL, NULL, &input) == 0);
    assert(starts == 2 && replays == 1 && stops == 5 && resumes == 5 && resets == 3);
    puts("Compute recovery: five resume and error-precedence cases passed");
}
'''

if __name__ == '__main__':
    unittest.main()
