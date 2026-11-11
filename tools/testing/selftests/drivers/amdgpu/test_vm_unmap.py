#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise standard VA flags, explicit mapping retirement and fence selection."""
import os
import pathlib
import re
import shlex
import subprocess
import tempfile
import unittest


def source_directory():
    roots = ([pathlib.Path(os.environ['AMDGPU_TEST_ROOT'])] if 'AMDGPU_TEST_ROOT' in os.environ
             else pathlib.Path(__file__).resolve().parents)
    for root in roots:
        for prefix in ('', 'amdgpu'):
            source = root / prefix / 'drivers/gpu/drm/amd/amdgpu'
            if (source / 'amdgpu_vm.c').is_file():
                return root / prefix, source
    raise RuntimeError('Set AMDGPU_TEST_ROOT to the kernel or module source tree')


ROOT, AMD = source_directory()


def function(source, name):
    match = re.search(r'^(?:static )?(?:inline )?(?:int|bool|void \*|struct amdgpu_userq_fence \*)\s*' + name + r'\(.*?^\}', source, re.M | re.S)
    if not match:
        raise AssertionError('Missing production function ' + name)
    return match.group()


class VmUnmapTests(unittest.TestCase):
    def test_retirement_and_dependencies(self):
        code = '\n'.join(function((AMD / filename).read_text(), name) for filename, name in (
            ('amdgpu_gem.c', 'amdgpu_gem_va_flags_valid'),
            ('amdgpu_vm.c', 'amdgpu_vm_bo_unmap'),
            ('amdgpu_userq_fence.c', 'to_amdgpu_userq_fence'),
            ('amdgpu_userq_fence.c', 'amdgpu_userq_fence_is_same_device'),
            ('amdgpu_sync.c', 'amdgpu_sync_get_owner'),
            ('amdgpu_sync.c', 'amdgpu_sync_test_fence')))
        command = shlex.split(os.environ.get('CC', 'cc'))
        command += ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', '-D__user=', '-I' + str(ROOT / 'include/uapi/drm')]
        command += shlex.split(subprocess.check_output(['pkg-config', '--cflags', 'libdrm'], text=True))
        if os.environ.get('USERQ_TEST_SANITIZERS'):
            command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-fno-sanitize-recover=all']
        with tempfile.TemporaryDirectory(prefix='vm-unmap-') as temporary:
            path = pathlib.Path(temporary)
            source, binary = path / 'test.c', path / 'test'
            source.write_text(HARNESS.replace('ACTUAL_SOURCE', code))
            subprocess.run(command + [str(source), '-o', str(binary)], check=True, timeout=30)
            subprocess.run([str(binary)], check=True, timeout=30)


HARNESS = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "amdgpu_drm.h"

typedef uint32_t u32;
typedef uint64_t u64;
#define AMDGPU_GPU_PAGE_SIZE 4096u
#define unlikely(x) (x)
#define container_of(p, t, m) ((t *)((char *)(p) - offsetof(t, m)))
struct list_head { struct list_head *next, *prev; };
#define INIT_LIST_HEAD(h) ((h)->next = (h)->prev = (h))
#define list_for_each_entry(e, h, m) \
    for ((e) = container_of((h)->next, typeof(*(e)), m); &(e)->m != (h); \
         (e) = container_of((e)->m.next, typeof(*(e)), m))
static void list_add(struct list_head *n, struct list_head *h)
{ n->next = h->next; n->prev = h; h->next->prev = n; h->next = n; }
static void list_del(struct list_head *n)
{ n->prev->next = n->next; n->next->prev = n->prev; }
static bool list_empty(struct list_head *h) { return h->next == h; }
static unsigned list_count(struct list_head *h)
{ unsigned count = 0; for (struct list_head *p = h->next; p != h; p = p->next) ++count; return count; }
struct amdgpu_device { unsigned unused; };
struct drm_sched_fence;
struct dma_fence_ops { unsigned unused; };
static const struct dma_fence_ops amdgpu_userq_fence_ops, unknown_ops;
struct dma_fence { const struct dma_fence_ops *ops; struct drm_sched_fence *sched; bool same_dev, kfd; };
struct drm_sched_fence { void *owner; };
struct amdgpu_amdkfd_fence { unsigned unused; };
struct amdgpu_userq_fence_driver { struct amdgpu_device *adev; };
struct amdgpu_userq_fence { struct dma_fence base; struct amdgpu_userq_fence_driver *fence_drv; };
struct amdgpu_bo_va;
struct amdgpu_bo_va_mapping { struct list_head list; uint64_t start; struct amdgpu_bo_va *bo_va; bool in_tree, freed; };
struct amdgpu_vm { struct list_head freed, freed_explicit; unsigned va; };
struct amdgpu_bo_va { struct { struct amdgpu_vm *vm; } base; struct list_head valids, invalids; bool userq_va_mapped; struct dma_fence *last_pt_update; };
static unsigned cases, traces, removed, userq_validations, freed;
static struct amdgpu_vm vm;
static struct amdgpu_device device;
static struct dma_fence previous_update;
static void amdgpu_vm_it_remove(struct amdgpu_bo_va_mapping *m, unsigned *tree)
{ assert(m->in_tree && tree == &vm.va); m->in_tree = false; ++removed; }
static void trace_amdgpu_vm_bo_unmap(struct amdgpu_bo_va *owner, struct amdgpu_bo_va_mapping *m)
{ assert(owner && !m->bo_va && !m->in_tree); ++traces; }
static void amdgpu_vm_free_mapping(struct amdgpu_device *dev, struct amdgpu_vm *v,
                                   struct amdgpu_bo_va_mapping *m, struct dma_fence *f)
{ assert(v == &vm && !m->in_tree && !m->bo_va && f == &previous_update && !m->freed); m->freed = true; ++freed; }
static void amdgpu_userq_gem_va_unmap_validate(struct amdgpu_device *dev, struct amdgpu_bo_va_mapping *m)
{ assert(m->in_tree && m->bo_va); ++userq_validations; }
#define AMDGPU_FENCE_OWNER_UNDEFINED ((void *)1)
#define AMDGPU_FENCE_OWNER_KFD ((void *)2)
#define AMDGPU_FENCE_OWNER_VM ((void *)3)
#define WARN(...) ((void)0)
enum amdgpu_sync_mode { AMDGPU_SYNC_ALWAYS, AMDGPU_SYNC_NE_OWNER, AMDGPU_SYNC_EQ_OWNER, AMDGPU_SYNC_EXPLICIT, AMDGPU_SYNC_EXPLICIT_UNMAP };
static struct drm_sched_fence *to_drm_sched_fence(struct dma_fence *f) { return f->sched; }
static struct amdgpu_amdkfd_fence *to_amdgpu_amdkfd_fence(struct dma_fence *f)
{ static struct amdgpu_amdkfd_fence kfd; return f->kfd ? &kfd : NULL; }
static bool amdgpu_sync_same_dev(struct amdgpu_device *dev, struct dma_fence *f) { return f->same_dev; }

ACTUAL_SOURCE

static void flags(void)
{
    for (unsigned op = 0; op < 6; ++op) {
        for (unsigned f = 0; f < 4096; ++f) {
            unsigned ordinary = AMDGPU_VM_DELAY_UPDATE | AMDGPU_VM_PAGE_READABLE |
                AMDGPU_VM_PAGE_WRITEABLE | AMDGPU_VM_PAGE_EXECUTABLE | AMDGPU_VM_MTYPE_MASK | AMDGPU_VM_PAGE_NOALLOC;
            bool expected = !(f & ~ordinary) || !(f & ~(AMDGPU_VM_DELAY_UPDATE | AMDGPU_VM_PAGE_PRT));
            assert(amdgpu_gem_va_flags_valid(op, f) == expected); ++cases;
        }
    }
}
static void retire(void)
{
    for (unsigned explicit_sync = 0; explicit_sync < 2; ++explicit_sync)
    for (unsigned valid = 0; valid < 2; ++valid)
    for (unsigned userq = 0; userq < 2; ++userq) {
        struct amdgpu_bo_va owner = {.base.vm = &vm, .userq_va_mapped = userq, .last_pt_update = &previous_update};
        struct amdgpu_bo_va_mapping a = {.start = 4, .bo_va = &owner, .in_tree = true};
        struct amdgpu_bo_va_mapping b = {.start = 8, .bo_va = &owner, .in_tree = true};
        INIT_LIST_HEAD(&vm.freed); INIT_LIST_HEAD(&vm.freed_explicit);
        INIT_LIST_HEAD(&owner.valids); INIT_LIST_HEAD(&owner.invalids);
        list_add(&a.list, valid ? &owner.valids : &owner.invalids);
        list_add(&b.list, &owner.valids);
        traces = removed = userq_validations = freed = 0;
        assert(amdgpu_vm_bo_unmap(&device, &owner, 16 * AMDGPU_GPU_PAGE_SIZE, explicit_sync) == -ENOENT);
        assert(!traces && !removed && !freed);
        assert(!amdgpu_vm_bo_unmap(&device, &owner, 4 * AMDGPU_GPU_PAGE_SIZE, explicit_sync));
        assert(!a.bo_va && !a.in_tree && a.freed == !valid);
        assert(list_count(&vm.freed) == (valid && !explicit_sync));
        assert(list_count(&vm.freed_explicit) == (valid && explicit_sync));
        assert(freed == !valid && traces == 1 && removed == 1 && userq_validations == userq);
        assert(b.bo_va == &owner && b.in_tree && !b.freed && list_count(&owner.valids) == 1);
        /* A following legacy retirement cannot inherit an earlier opt-in. */
        assert(!amdgpu_vm_bo_unmap(&device, &owner, 8 * AMDGPU_GPU_PAGE_SIZE, false));
        assert(list_count(&vm.freed) == 1u + (valid && !explicit_sync));
        assert(list_count(&vm.freed_explicit) == (valid && explicit_sync));
        assert(list_empty(&owner.valids) && list_empty(&owner.invalids));
        ++cases;
    }
}
static void dependencies(void)
{
    void *owner = &vm, *other_owner = &device;
    struct amdgpu_device foreign_device;
    struct amdgpu_userq_fence_driver local_drv = {.adev = &device}, foreign_drv = {.adev = &foreign_device};
    struct amdgpu_userq_fence local = {.base.ops = &amdgpu_userq_fence_ops, .fence_drv = &local_drv};
    struct amdgpu_userq_fence remote = {.base.ops = &amdgpu_userq_fence_ops, .fence_drv = &foreign_drv};
    struct dma_fence unknown = {.ops = &unknown_ops};
    assert(!amdgpu_userq_fence_is_same_device(&device, NULL));
    for (unsigned same = 0; same < 2; ++same) {
        struct drm_sched_fence move_sched = {.owner = AMDGPU_FENCE_OWNER_UNDEFINED};
        struct drm_sched_fence render_sched = {.owner = owner};
        struct drm_sched_fence foreign_sched = {.owner = other_owner};
        struct drm_sched_fence update_sched = {.owner = AMDGPU_FENCE_OWNER_VM};
        struct dma_fence move = {.sched = &move_sched, .same_dev = same};
        struct dma_fence render = {.sched = &render_sched, .same_dev = same};
        struct dma_fence foreign = {.sched = &foreign_sched, .same_dev = same};
        struct dma_fence update = {.sched = &update_sched, .same_dev = same};
        struct dma_fence kfd = {.kfd = true, .same_dev = same};
        for (enum amdgpu_sync_mode mode = AMDGPU_SYNC_ALWAYS; mode <= AMDGPU_SYNC_EXPLICIT_UNMAP; ++mode) {
            assert(amdgpu_sync_test_fence(&device, mode, owner, &move));
            assert(amdgpu_sync_test_fence(&device, mode, owner, &unknown));
            assert(amdgpu_sync_test_fence(&device, mode, owner, &remote.base));
            assert(amdgpu_sync_test_fence(&device, mode, owner, &local.base) == (mode != AMDGPU_SYNC_EXPLICIT_UNMAP));
            assert(!amdgpu_sync_test_fence(&device, mode, owner, &update));
            assert(!amdgpu_sync_test_fence(&device, mode, owner, &kfd));
            bool explicit_sync = mode == AMDGPU_SYNC_EXPLICIT || mode == AMDGPU_SYNC_EXPLICIT_UNMAP;
            bool render_expected = !explicit_sync && !(same && mode == AMDGPU_SYNC_NE_OWNER);
            bool foreign_expected = !explicit_sync && !(same && mode == AMDGPU_SYNC_EQ_OWNER);
            assert(amdgpu_sync_test_fence(&device, mode, owner, &render) == render_expected);
            assert(amdgpu_sync_test_fence(&device, mode, owner, &foreign) == foreign_expected);
            ++cases;
        }
    }
}
int main(void)
{
    flags(); retire(); dependencies();
    printf("VM explicit unmap: %u cases passed\n", cases);
    return 0;
}
'''

if __name__ == '__main__':
    unittest.main()
