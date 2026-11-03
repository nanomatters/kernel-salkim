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


def function(source, name):
    match = re.search(r'^(?:static )?(?:inline )?(?:int|void) ' + name + r'\([^;]*?^\{.*?^\}', source, re.M | re.S)
    if not match:
        raise AssertionError('Missing function ' + name)
    return match.group()


class VmCriticalTests(unittest.TestCase):
    def test_update_and_allocation_lifetime(self):
        vm = (AMD / 'amdgpu_vm.c').read_text()
        pt = (AMD / 'amdgpu_vm_pt.c').read_text()
        internal = (AMD / 'amdgpu_vm_internal.h').read_text()
        actual = '\n'.join([
            function(internal, 'amdgpu_vm_begin_critical'),
            function(internal, 'amdgpu_vm_end_critical'),
            function(pt, 'amdgpu_vm_pt_free'),
            function(pt, 'amdgpu_vm_pt_alloc'),
            function(vm, 'amdgpu_vm_update_begin'),
            function(vm, 'amdgpu_vm_update_end'),
            function(vm, 'amdgpu_vm_update_range'),
        ])
        command = shlex.split(os.environ.get('CC', 'cc'))
        command += ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter']
        if os.environ.get('USERQ_TEST_SANITIZERS'):
            command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-fno-sanitize-recover=all']
        with tempfile.TemporaryDirectory(prefix='vm-critical-') as temporary:
            program = pathlib.Path(temporary) / 'test.c'
            binary = pathlib.Path(temporary) / 'test'
            program.write_text(HARNESS.replace('ACTUAL_SOURCE', actual))
            subprocess.run(command + [str(program), '-o', str(binary)], check=True, timeout=30)
            subprocess.run([str(binary)], check=True, timeout=30)


HARNESS = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint64_t dma_addr_t;
struct list_head { bool linked; };
struct dma_fence { bool signaled; unsigned refs; };
struct amdgpu_vm;
struct amdgpu_bo {
    unsigned refs, pins, xcp_id;
    struct amdgpu_bo *parent;
    struct amdgpu_vm_bo_base *vm_bo;
    struct { void *resource; bool bulk; } tbo;
};
struct amdgpu_bo_vm { struct amdgpu_bo bo; };
struct amdgpu_vm_bo_base {
    struct amdgpu_bo *bo;
    struct amdgpu_vm *vm;
    struct list_head vm_status;
};
struct amdgpu_device {
    struct { struct { unsigned num_physical_nodes; } xgmi; bool override_pte; } gmc;
    unsigned version;
};
struct amdgpu_sync { unsigned unused; };
struct amdgpu_vm_tlb_seq_struct { unsigned unused; };
struct amdgpu_vm_update_params {
    struct amdgpu_device *adev;
    struct amdgpu_vm *vm;
    bool immediate, unlocked, needs_flush, override_pte;
    unsigned saved_flags;
    struct list_head tlb_flush_waitlist;
};
struct amdgpu_vm_update_funcs {
    int (*prepare)(struct amdgpu_vm_update_params *, struct amdgpu_sync *, uint64_t);
    int (*commit)(struct amdgpu_vm_update_params *, struct dma_fence **);
};
struct amdgpu_vm {
    bool evicting, eviction_lock, is_npa;
    struct amdgpu_vm_bo_base root;
    struct dma_fence *last_unlocked;
    const struct amdgpu_vm_update_funcs *update_funcs;
};
struct amdgpu_vm_update_batch {
    struct amdgpu_vm_update_params params;
    struct amdgpu_vm_tlb_seq_struct *tlb_cb;
    int idx;
};
struct amdgpu_vm_pt_cursor {
    struct amdgpu_vm_bo_base *entry, *parent;
    unsigned level;
};
struct ttm_resource { unsigned unused; };
static unsigned gfp_flags, allocations, live_device, entered, left, prepares, commits;
static unsigned clear_calls, creates, unpins, full_frees, stat_entries, tlb_flushes, free_lists;
static unsigned cases;
static int prepare_error, commit_error, pages_error, create_error, clear_error;
static bool device_present, allocation_fail, evict_during_alloc;
static struct amdgpu_bo_vm allocated;
static struct dma_fence stub = { true, 1 };
#define GC_HWIP 0
#define IP_VERSION(a,b,c) (((a) << 16) | ((b) << 8) | (c))
#define AMDGPU_KERNEL_JOB_ID_VM_UPDATE_RANGE 1
#define adev_to_drm(p) (p)
#define amdgpu_ip_version(p,ip,index) ((p)->version)
#define swap(a,b) do { __typeof__(a) tmp__ = (a); (a) = (b); (b) = tmp__; } while (0)
static void mutex_lock(bool *lock) { assert(!*lock); *lock = true; entered++; }
static void mutex_unlock(bool *lock) { assert(*lock); *lock = false; left++; }
static unsigned memalloc_noreclaim_save(void)
{ unsigned previous = gfp_flags; gfp_flags |= 1; return previous; }
static void memalloc_noreclaim_restore(unsigned flags) { assert(gfp_flags & 1); gfp_flags = flags; }
static bool drm_dev_enter(struct amdgpu_device *adev, int *idx)
{ if (!device_present) return false; *idx = 7; live_device++; return true; }
static void drm_dev_exit(int idx) { assert(idx == 7 && live_device); live_device--; }
static void *allocate(size_t size)
{ if (allocation_fail) return NULL; allocations++; return calloc(1, size); }
#define kmalloc_obj(value) allocate(sizeof(value))
static void kfree(void *p) { if (p) { assert(allocations); allocations--; free(p); } }
#define INIT_LIST_HEAD(p) ((p)->linked = false)
static void list_del(struct list_head *p) { assert(p->linked); p->linked = false; }
static bool dma_fence_is_signaled(struct dma_fence *f) { return !f || f->signaled; }
static struct dma_fence *dma_fence_get_stub(void) { stub.refs++; return &stub; }
static void dma_fence_put(struct dma_fence *f) { if (f) { assert(f->refs); f->refs--; } }
static void amdgpu_bo_fence(struct amdgpu_bo *bo, struct dma_fence *f, bool shared)
{ assert(bo && f && shared); }
static struct amdgpu_bo *amdgpu_bo_ref(struct amdgpu_bo *bo) { bo->refs++; return bo; }
static void amdgpu_bo_unpin(struct amdgpu_bo *bo) { assert(bo->pins); bo->pins--; unpins++; }
static void amdgpu_bo_unref(struct amdgpu_bo **bo)
{
    assert(*bo && (*bo)->refs);
    if (!--(*bo)->refs) {
        assert(!(*bo)->pins);
        if ((*bo)->parent) amdgpu_bo_unref(&(*bo)->parent);
    }
    *bo = NULL;
}
static void amdgpu_vm_update_stats(struct amdgpu_vm_bo_base *entry, void *resource, int change)
{ assert(change == -1 && stat_entries); stat_entries--; full_frees++; }
static void ttm_bo_set_bulk_move(void *tbo, void *bulk)
{ ((struct amdgpu_bo *)((char *)tbo - __builtin_offsetof(struct amdgpu_bo, tbo)))->tbo.bulk = false; }
static int amdgpu_vm_pt_create(struct amdgpu_device *adev, struct amdgpu_vm *vm,
                               int level, bool immediate, struct amdgpu_bo_vm **pt, int xcp)
{
    assert(!vm->eviction_lock && !(gfp_flags & 1)); creates++;
    if (evict_during_alloc) vm->evicting = true;
    if (create_error) return create_error;
    allocated = (struct amdgpu_bo_vm){ .bo = { .refs = 1, .pins = vm->is_npa } };
    *pt = &allocated;
    return 0;
}
static void amdgpu_vm_bo_base_init(struct amdgpu_vm_bo_base *entry, struct amdgpu_vm *vm,
                                   struct amdgpu_bo *bo)
{
    assert(vm->eviction_lock && (gfp_flags & 1) && !entry->bo);
    entry->bo = bo; entry->vm = vm; entry->vm_status.linked = true;
    bo->vm_bo = entry; bo->tbo.bulk = true; stat_entries++;
}
static int amdgpu_vm_pt_clear(struct amdgpu_device *adev, struct amdgpu_vm *vm,
                              struct amdgpu_bo_vm *pt, bool immediate)
{ assert(vm->eviction_lock && (gfp_flags & 1) && pt->bo.vm_bo); clear_calls++; return clear_error; }
static int prepare(struct amdgpu_vm_update_params *p, struct amdgpu_sync *sync, uint64_t id)
{ assert(p->vm->eviction_lock && (gfp_flags & 1) && id == 1); prepares++; return prepare_error; }
static int commit(struct amdgpu_vm_update_params *p, struct dma_fence **fence)
{ assert(p->vm->eviction_lock && (gfp_flags & 1)); commits++; return commit_error; }
static void amdgpu_vm_tlb_flush(struct amdgpu_vm_update_params *p, struct dma_fence *f,
                                struct amdgpu_vm_tlb_seq_struct *cb)
{ assert(cb && p->vm->eviction_lock && (gfp_flags & 1)); tlb_flushes++; kfree(cb); }
static void amdgpu_vm_pt_free_list(struct amdgpu_device *adev, struct amdgpu_vm_update_params *p)
{ assert(p->vm->eviction_lock && (gfp_flags & 1)); free_lists++; }
static int amdgpu_vm_update_pages(struct amdgpu_vm_update_params *p, uint64_t start,
    uint64_t last, uint64_t flags, uint64_t offset, uint64_t base,
    struct ttm_resource *res, dma_addr_t *pages)
{ assert(p->vm->eviction_lock && (gfp_flags & 1)); return pages_error; }
ACTUAL_SOURCE
static const struct amdgpu_vm_update_funcs ops = { prepare, commit };
struct fixture {
    struct amdgpu_device dev;
    struct amdgpu_vm vm;
    struct amdgpu_bo parent;
    struct amdgpu_vm_bo_base entry;
    struct amdgpu_vm_update_params params;
    struct amdgpu_vm_pt_cursor cursor;
};
static void init(struct fixture *f)
{
    assert(!allocations && !live_device && !stat_entries);
    *f = (struct fixture){0};
    gfp_flags = 0x80; entered = left = prepares = commits = 0;
    create_error = clear_error = prepare_error = commit_error = pages_error = 0;
    clear_calls = creates = unpins = full_frees = tlb_flushes = free_lists = 0;
    device_present = true; allocation_fail = evict_during_alloc = false;
    f->dev.version = IP_VERSION(12, 0, 1);
    f->parent.refs = 1;
    f->vm.root.bo = &f->parent; f->vm.update_funcs = &ops;
    f->params.adev = &f->dev; f->params.vm = &f->vm;
    f->cursor.entry = &f->entry; f->cursor.parent = &f->vm.root;
}
static void balanced(struct fixture *f)
{
    assert(gfp_flags == 0x80 && !f->vm.eviction_lock && entered == left);
    assert(!allocations && !live_device && !stat_entries && f->parent.refs == 1);
    cases++;
}
static void test_begin_end(void)
{
    struct fixture f;
    struct amdgpu_vm_update_batch batch;
    init(&f); device_present = false;
    assert(amdgpu_vm_update_begin(&batch,&f.dev,&f.vm,0,0,0,0,NULL) == -ENODEV);
    assert(!entered); balanced(&f);
    init(&f); allocation_fail = true;
    assert(amdgpu_vm_update_begin(&batch,&f.dev,&f.vm,0,0,0,0,NULL) == -ENOMEM);
    assert(!entered); balanced(&f);
    init(&f); f.vm.evicting = true;
    assert(amdgpu_vm_update_begin(&batch,&f.dev,&f.vm,0,0,0,0,NULL) == -EBUSY);
    assert(entered == 1 && !prepares); balanced(&f);
    init(&f); prepare_error = -ENOMEM;
    assert(amdgpu_vm_update_begin(&batch,&f.dev,&f.vm,0,0,0,0,NULL) == -ENOMEM);
    assert(prepares == 1); balanced(&f);
    for (unsigned fail = 0; fail < 2; fail++) for (unsigned flush = 0; flush < 2; flush++) {
        init(&f); f.dev.gmc.override_pte = true; commit_error = fail ? -EIO : 0;
        assert(!amdgpu_vm_update_begin(&batch,&f.dev,&f.vm,1,1,flush,1,NULL));
        assert(batch.params.override_pte && batch.params.immediate && batch.params.unlocked);
        assert(amdgpu_vm_update_end(&batch,NULL) == commit_error);
        assert(commits == 1 && tlb_flushes == (!fail && flush) && free_lists == !fail);
        balanced(&f);
    }
    init(&f); pages_error = -ENOMEM; commit_error = -EIO;
    assert(amdgpu_vm_update_range(&f.dev,&f.vm,0,0,0,0,NULL,0,1,0,0,0,NULL,NULL,NULL) == -ENOMEM);
    assert(commits == 1); balanced(&f);
    init(&f); commit_error = -EIO;
    assert(amdgpu_vm_update_range(&f.dev,&f.vm,0,0,0,0,NULL,0,1,0,0,0,NULL,NULL,NULL) == -EIO);
    balanced(&f);
}
static void test_alloc(void)
{
    struct fixture f;
    for (unsigned npa = 0; npa < 2; npa++) {
        for (unsigned scenario = 0; scenario < 5; scenario++) {
            init(&f); f.vm.is_npa = npa;
            assert(!amdgpu_vm_begin_critical(&f.params));
            if (scenario == 0) create_error = -ENOMEM;
            if (scenario == 1) evict_during_alloc = true;
            if (scenario == 2) clear_error = -EIO;
            if (scenario == 4) { create_error = -ENOMEM; evict_during_alloc = true; }
            int expected = create_error ?: (evict_during_alloc ? -EBUSY : clear_error);
            assert(amdgpu_vm_pt_alloc(&f.params,&f.cursor) == expected);
            assert(f.vm.eviction_lock && gfp_flags == 0x81);
            assert(entered == 2 && left == 1);
            if (scenario == 3) {
                assert(f.entry.bo && f.parent.refs == 2 && stat_entries == 1);
                assert(!amdgpu_vm_pt_alloc(&f.params,&f.cursor) && creates == 1);
                amdgpu_vm_pt_free(&f.entry);
            } else assert(!f.entry.bo);
            assert(unpins == (unsigned)(npa && !create_error));
            assert(full_frees == (scenario == 2 || scenario == 3));
            assert(clear_calls == (scenario == 2 || scenario == 3));
            assert(!allocated.bo.vm_bo || create_error);
            amdgpu_vm_end_critical(&f.params); balanced(&f);
        }
    }
}
int main(void)
{
    test_begin_end(); test_alloc();
    printf("VM critical and PT lifetime: %u cases passed\n", cases);
    return 0;
}
'''

if __name__ == '__main__':
    unittest.main()
