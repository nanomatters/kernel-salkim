#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest


def source_root():
    if os.environ.get('AMDGPU_TEST_ROOT'):
        return Path(os.environ['AMDGPU_TEST_ROOT'])
    for root in Path(__file__).resolve().parents:
        if (root / 'drivers/gpu/drm/amd/amdgpu/amdgpu_eviction_fence.c').is_file():
            return root
    raise RuntimeError('Set AMDGPU_TEST_ROOT to the kernel source directory')


ROOT = source_root()
AMD = ROOT / 'drivers/gpu/drm/amd/amdgpu'


class UserqRearmTests(unittest.TestCase):
    def test_prepare_and_publication(self):
        production = (AMD / 'amdgpu_eviction_fence.c').read_text()
        functions = []
        for name in ('amdgpu_evf_mgr_prepare', 'amdgpu_evf_mgr_rearm'):
            match = re.search(r'^(?:void|struct dma_fence \*)\s*' + name +
                              r'\([^;]*?\)\n\{.*?^\}', production, re.M | re.S)
            self.assertIsNotNone(match, name)
            functions.append(match.group())
        source = HARNESS.replace('ACTUAL_FUNCTIONS', '\n'.join(functions))
        command = shlex.split(os.environ.get('CC', 'cc'))
        command += ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter']
        if os.environ.get('AMDGPU_TEST_SANITIZERS') or os.environ.get('USERQ_TEST_SANITIZERS'):
            command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
        with tempfile.TemporaryDirectory(prefix='amdgpu-userq-rearm-') as temporary:
            directory = Path(temporary)
            program, binary = directory / 'test.c', directory / 'test'
            program.write_text(source)
            subprocess.run(command + [str(program), '-o', str(binary)], check=True, timeout=30)
            subprocess.run([str(binary)], check=True, timeout=30)


HARNESS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define DMA_RESV_USAGE_BOOKKEEP 3
#define MAX_OBJECTS 8
struct dma_fence_ops { int unused; };
struct dma_fence {
    unsigned refs;
    uint64_t context, seqno;
    const struct dma_fence_ops *ops;
};
struct dma_resv {
    struct dma_fence *fence;
    bool locked;
    unsigned slots, usage, updates;
};
struct drm_gem_object { struct dma_resv *resv; };
struct drm_exec {
    unsigned count;
    struct drm_gem_object *objects[MAX_OBJECTS];
};
struct amdgpu_eviction_fence_mgr {
    uint64_t ev_fence_ctx;
    unsigned ev_fence_seq;
    struct dma_fence *ev_fence;
};
struct amdgpu_eviction_fence {
    struct dma_fence base;
    int lock;
    char timeline_name[16];
    struct amdgpu_eviction_fence_mgr *evf_mgr;
};
static const struct dma_fence_ops amdgpu_eviction_fence_ops = {0};
static unsigned allocations, frees, publication_calls, add_calls;
static bool fail_allocation, notifier_locked;

static void *allocate(size_t size)
{
    assert(!notifier_locked);
    allocations++;
    return fail_allocation ? NULL : calloc(1, size);
}
#define kzalloc_obj(object) allocate(sizeof(object))
#define current NULL
static void get_task_comm(char *name, void *task)
{
    strcpy(name, "restore-test");
}
static void spin_lock_init(int *lock) { *lock = 0; }
static unsigned atomic_inc_return(unsigned *value) { return ++*value; }
static void dma_fence_init64(struct dma_fence *fence, const struct dma_fence_ops *ops,
                             int *lock, uint64_t context, uint64_t seqno)
{
    assert(!notifier_locked);
    *fence = (struct dma_fence){.refs = 1, .ops = ops, .context = context, .seqno = seqno};
}
static struct dma_fence *dma_fence_get(struct dma_fence *fence)
{
    if (fence) {
        assert(fence->refs);
        fence->refs++;
    }
    return fence;
}
static void dma_fence_put(struct dma_fence *fence)
{
    if (!fence)
        return;
    assert(fence->refs);
    if (!--fence->refs) {
        frees++;
        free(fence);
    }
}
static struct dma_fence *replace_pointer(struct dma_fence **pointer, struct dma_fence *value)
{
    assert(notifier_locked);
    struct dma_fence *old = *pointer;
    *pointer = value;
    publication_calls++;
    return old;
}
#define rcu_replace_pointer(pointer, value, condition) \
    ((void)assert(condition), replace_pointer(&(pointer), (value)))
#define drm_exec_for_each_locked_object(exec, obj) \
    for (unsigned iter = 0; iter < (exec)->count && ((obj) = (exec)->objects[iter], true); iter++)

/* Model the reservation's ownership of one fence context and reserved slot. */
static void dma_resv_add_fence(struct dma_resv *resv, struct dma_fence *fence, unsigned usage)
{
    assert(resv->locked && resv->slots);
    assert(usage == DMA_RESV_USAGE_BOOKKEEP);
    if (resv->fence) {
        assert(resv->fence->context == fence->context);
        assert(resv->fence->seqno <= fence->seqno);
    }
    struct dma_fence *old = resv->fence;
    resv->fence = dma_fence_get(fence);
    resv->usage = usage;
    resv->updates++;
    add_calls++;
    dma_fence_put(old);
}

/* These operations must never run during protected publication. */
static int __attribute__((unused)) amdgpu_evf_mgr_attach_fence(
        struct amdgpu_eviction_fence_mgr *manager, void *bo)
{
    assert(!"rearm must not validate or attach indirectly");
    return -1;
}
static int __attribute__((unused)) ttm_bo_validate(void *bo, void *placement, void *context)
{
    assert(!"rearm must not validate memory");
    return -1;
}

ACTUAL_FUNCTIONS

static void check_publication(unsigned object_count, bool shared_reservation, bool initial)
{
    struct amdgpu_eviction_fence_mgr manager = {.ev_fence_ctx = 77, .ev_fence_seq = 100};
    struct dma_resv reservations[MAX_OBJECTS] = {0};
    struct drm_gem_object objects[MAX_OBJECTS];
    struct drm_exec exec = {.count = object_count};
    unsigned distinct = shared_reservation ? 1 : object_count;
    struct dma_fence *old = NULL;
    unsigned before_frees = frees;
    unsigned before_publications = publication_calls;

    for (unsigned i = 0; i < object_count; i++) {
        objects[i].resv = &reservations[shared_reservation ? 0 : i];
        objects[i].resv->locked = true;
        objects[i].resv->slots = 1;
        exec.objects[i] = &objects[i];
    }
    if (!initial) {
        old = amdgpu_evf_mgr_prepare(&manager);
        assert(old && old->refs == 1);
        manager.ev_fence = dma_fence_get(old);
        for (unsigned i = 0; i < distinct; i++)
            dma_resv_add_fence(&reservations[i], old, DMA_RESV_USAGE_BOOKKEEP);
        assert(old->refs == 2 + distinct);
    }

    /* Allocation failure cannot publish, change reservations or consume refs. */
    unsigned sequence = manager.ev_fence_seq;
    unsigned before_adds = add_calls;
    fail_allocation = true;
    assert(!amdgpu_evf_mgr_prepare(&manager));
    fail_allocation = false;
    assert(manager.ev_fence == old && manager.ev_fence_seq == sequence);
    assert(publication_calls == before_publications && add_calls == before_adds);
    if (old)
        assert(old->refs == 2 + distinct);

    struct dma_fence *prepared = amdgpu_evf_mgr_prepare(&manager);
    assert(prepared && prepared->refs == 1 && manager.ev_fence == old);
    assert(prepared->seqno == sequence + 1 && prepared->context == manager.ev_fence_ctx);
    assert(prepared->ops == &amdgpu_eviction_fence_ops);
    struct amdgpu_eviction_fence *container = (void *)prepared;
    assert(container->evf_mgr == &manager && !strcmp(container->timeline_name, "restore-test"));
    unsigned before_allocations = allocations;
    notifier_locked = true;
    amdgpu_evf_mgr_rearm(&manager, &exec, prepared);
    notifier_locked = false;
    assert(allocations == before_allocations);
    assert(publication_calls == before_publications + 1);
    assert(add_calls == before_adds + object_count);
    assert(manager.ev_fence == prepared && prepared->refs == 2 + distinct);
    for (unsigned i = 0; i < distinct; i++) {
        assert(reservations[i].fence == prepared);
        assert(reservations[i].usage == DMA_RESV_USAGE_BOOKKEEP);
    }
    if (old) {
        assert(old->refs == 1);
        dma_fence_put(old);
    }

    /* The caller still owns prepared; publication took separate references. */
    dma_fence_put(prepared);
    assert(manager.ev_fence->refs == 1 + distinct);
    dma_fence_put(manager.ev_fence);
    manager.ev_fence = NULL;
    for (unsigned i = 0; i < distinct; i++)
        dma_fence_put(reservations[i].fence);
    assert(frees == before_frees + (initial ? 1 : 2));
}

int main(void)
{
    for (unsigned count = 0; count <= MAX_OBJECTS; count++) {
        check_publication(count, false, false);
        check_publication(count, false, true);
        if (count)
            check_publication(count, true, false);
    }
    /* A prepared fence abandoned before publication has only its caller ref. */
    struct amdgpu_eviction_fence_mgr manager = {.ev_fence_ctx = 42};
    unsigned before_frees = frees;
    struct dma_fence *prepared = amdgpu_evf_mgr_prepare(&manager);
    assert(prepared && prepared->refs == 1 && !manager.ev_fence);
    dma_fence_put(prepared);
    assert(frees == before_frees + 1 && !manager.ev_fence);
    puts("UserQ rearm:26 publication cases,allocation failure,no allocation/validation under notifier lock,BOOKKEEP refs and abandoned preparation passed");
    return 0;
}
'''


if __name__ == '__main__':
    unittest.main()
