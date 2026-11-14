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
AMD = 'drivers/gpu/drm/amd/amdgpu/'


class UserqRestoreTests(unittest.TestCase):
    def test_moved_bo_reservations(self):
        def function(file, name):
            revision = os.environ.get('USERQ_TEST_REVISION')
            if revision:
                source = subprocess.check_output(['git', 'show', revision + ':' + AMD + file],
                                                 cwd=ROOT, text=True)
            else:
                source = (ROOT / AMD / file).read_text()
            match = re.search(r'^(?:static )?(?:int|void|bool|struct amdgpu_hmm_range \*)\s*' + name + r'\([^;]*?\)\n\{.*?^\}', source, re.M | re.S)
            self.assertIsNotNone(match, name)
            return match.group()

        source = HARNESS.replace('ACTUAL_HANDLE_MOVED', function('amdgpu_vm.c', 'amdgpu_vm_handle_moved'))
        source = source.replace('ACTUAL_UPDATE_DONE', function('amdgpu_vm.c', 'amdgpu_vm_bo_update_done'))
        source = source.replace('ACTUAL_VALIDATE', function('amdgpu_userq.c', 'amdgpu_userq_validate_vm'))
        source = source.replace('ACTUAL_PUT_BOS', function('amdgpu_userq.c', 'amdgpu_userq_put_restore_bos'))
        source = source.replace('ACTUAL_LOCK_BOS', function('amdgpu_vm.c', 'amdgpu_vm_lock_individual'))
        source = source.replace('ACTUAL_ALWAYS_VALID', function('amdgpu_vm.c', 'amdgpu_vm_is_bo_always_valid'))
        source = source.replace('ACTUAL_RESTORE', function('amdgpu_userq.c', 'amdgpu_userq_vm_validate_and_restore_queue'))
        for name in ('amdgpu_hmm_range_get_pages', 'amdgpu_hmm_range_valid', 'amdgpu_hmm_range_alloc', 'amdgpu_hmm_range_free'):
            source = source.replace('ACTUAL_' + name.upper(), function('amdgpu_hmm.c', name))
        flags = shlex.split(os.environ.get('CC', 'cc'))
        flags += ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                  '-Wno-unused-parameter', '-Wno-sign-compare']
        if os.environ.get('USERQ_TEST_SANITIZERS'):
            flags += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
        with tempfile.TemporaryDirectory(prefix='userq-restore-') as temporary:
            directory = pathlib.Path(temporary)
            program = directory / 'test.c'
            binary = directory / 'test'
            program.write_text(source)
            subprocess.run(flags + [str(program), '-o', str(binary)], check=True, timeout=30)
            subprocess.run([str(binary)], check=True, timeout=30)


    def test_restore_worker_retry(self):
        source = (ROOT / AMD / 'amdgpu_userq.c').read_text()
        match = re.search(r'^static void amdgpu_userq_restore_worker\([^;]*?\)\n\{.*?^\}',
                          source, re.M | re.S)
        self.assertIsNotNone(match)
        source = WORKER_HARNESS.replace('ACTUAL_WORKER', match.group())
        flags = shlex.split(os.environ.get('CC', 'cc'))
        flags += ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                  '-Wno-unused-parameter']
        if os.environ.get('USERQ_TEST_SANITIZERS'):
            flags += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
        with tempfile.TemporaryDirectory(prefix='userq-restore-worker-') as temporary:
            directory = pathlib.Path(temporary)
            program = directory / 'test.c'
            binary = directory / 'test'
            program.write_text(source)
            subprocess.run(flags + [str(program), '-o', str(binary)], check=True, timeout=30)
            subprocess.run([str(binary)], check=True, timeout=30)

HARNESS = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint64_t u64;
struct mmu_interval_notifier { unsigned long seq; };
struct hmm_range { struct mmu_interval_notifier *notifier; unsigned long notifier_seq; unsigned long default_flags, start, end; unsigned long *hmm_pfns; void *dev_private_owner; };

struct list_head { struct list_head *next, *prev; };
static void list_init(struct list_head *head) { head->next = head->prev = head; }
static bool list_empty(const struct list_head *head) { return head->next == head; }
static void list_add(struct list_head *entry, struct list_head *head)
{
    entry->next = head->next;
    entry->prev = head;
    head->next->prev = entry;
    head->next = entry;
}
static void list_del(struct list_head *entry)
{
    entry->prev->next = entry->next;
    entry->next->prev = entry->prev;
}
static void list_move(struct list_head *entry, struct list_head *head)
{ list_del(entry); list_add(entry, head); }
#define container_of(pointer, type, member) ((type *)((char *)(pointer) - offsetof(type, member)))
#define list_first_entry(head, type, member) container_of((head)->next, type, member)
#define list_for_each_entry(pos, head, member) \
    for (struct list_head *node = (head)->next; node != (head) && \
         ((pos) = container_of(node, __typeof__(*(pos)), member), true); node = node->next)
#define list_for_each_entry_safe(pos, tmp, head, member) \
    for (struct list_head *node = (head)->next, *next = node->next; node != (head) && \
         ((pos) = container_of(node, __typeof__(*(pos)), member), \
          (tmp) = next == (head) ? NULL : container_of(next, __typeof__(*(tmp)), member), (void)(tmp), true); \
         node = next, next = node->next)

struct ww_acquire_ctx { unsigned id; };
struct dma_resv { struct ww_acquire_ctx *owner; };
struct drm_gem_object { struct dma_resv *resv; bool imported; };
struct ttm_resource { int mem_type; };
struct ttm_tt { bool userptr; unsigned pages_installed; };
struct ttm_buffer_object { struct drm_gem_object base; struct ttm_tt *ttm; struct ttm_resource *resource; };
struct amdgpu_bo { struct mmu_interval_notifier notifier; struct ttm_buffer_object tbo; unsigned preferred_domains, allowed_domains; int placement; unsigned refs; };
struct dma_fence { unsigned refs; bool waited; };
struct amdgpu_vm;
struct amdgpu_vm_bo_base { struct list_head vm_status; struct amdgpu_bo *bo; struct amdgpu_vm *vm; bool moved; };
struct amdgpu_bo_va_mapping { struct list_head list; uint64_t start, last; };
struct amdgpu_bo_va {
    struct amdgpu_vm_bo_base base;
    struct list_head valids, invalids;
    struct amdgpu_vm *vm;
    void *last_pt_update;
    bool cleared;
    bool mapped;
};
struct amdgpu_vm_bo_status { struct list_head idle, needs_update, evicted; };
struct amdgpu_vm {
    struct { struct amdgpu_bo *bo; } root;
    struct amdgpu_vm_bo_status always_valid, individual;
    int individual_lock;
    bool is_compute_context;
    void *last_update, *last_tlb_inv;
};
struct amdgpu_device { bool debug_vm; int notifier_lock; };
struct xarray { void *values[16]; unsigned long keys[16]; };
struct amdgpu_userq_mgr { struct amdgpu_device *adev; struct xarray userq_xa; int userq_mutex; };
struct amdgpu_usermode_queue { struct { struct amdgpu_bo *obj; uint64_t gpu_addr; } wptr_obj;
                              uint64_t wptr_bo_offset; };
struct amdgpu_fpriv { struct amdgpu_userq_mgr userq_mgr; struct amdgpu_vm vm; struct amdgpu_bo_va *prt_va; int evf_mgr; };
struct amdgpu_hmm_range { struct hmm_range hmm_range; struct amdgpu_bo *bo; };
struct ttm_operation_ctx { bool interruptible, no_wait_gpu; };
struct drm_exec { struct ww_acquire_ctx ticket; struct drm_gem_object *objects[16]; unsigned count; };

#define uq_mgr_to_fpriv(mgr) container_of(mgr, struct amdgpu_fpriv, userq_mgr)
#define GFP_KERNEL 0
#define DRM_EXEC_IGNORE_DUPLICATES 1
#define TTM_NUM_MOVE_FENCES 2
#define AMDGPU_GEM_DOMAIN_CPU 1
#define AMDGPU_GEM_DOMAIN_GTT 2
#define TTM_PL_SYSTEM 0
#define unlikely(condition) (condition)
#define likely(condition) (condition)
#define drm_exec_until_all_locked(exec) for (bool once = true; once; once = false)
#define drm_exec_retry_on_contention(exec) ((void)0)
#define xa_for_each(array, index, value) \
    for (unsigned _xa_i = 0; _xa_i < 16; ++_xa_i) \
        if (((index) = (array)->keys[_xa_i], (value) = (array)->values[_xa_i]) != NULL)
#define drm_exec_for_each_locked_object(exec, object) \
    for (unsigned _exec_i = 0; _exec_i < (exec)->count && \
         (((object) = (exec)->objects[_exec_i]), true); ++_exec_i)
#define drm_file_err(...) ((void)0)

static struct amdgpu_bo target_bo, root_bo;
static struct dma_resv root_resv;
static struct amdgpu_bo_va *registered_vas[16];
static unsigned registered_count;
static struct amdgpu_bo_va target_va;
static struct dma_resv target_resv;
static struct ttm_tt target_tt;
static struct ttm_resource target_resource;
static struct ww_acquire_ctx standalone, foreign, temporary_lock;
static struct amdgpu_fpriv fpriv;
static struct amdgpu_bo_va prt;
static struct amdgpu_device device;
static bool move_during_validation;
static unsigned restored, updates, unlocks;
static uint64_t bo_mc_base;
static unsigned trace_count, restore_depth, restore_begin, restore_end;
static int restore_result;
static unsigned range_alloc_calls, ranges_allocated, ranges_freed, store_calls, user_pages_calls;
static unsigned fail_range_allocation;
static unsigned pfn_alloc_calls, pfn_allocated, pfn_freed, hmm_fault_calls, fail_pfn_allocation;
static unsigned notifier_invalidations;
static void *allocated_pfns[128], *allocated_snapshots[128];
static unsigned snapshot_allocated, snapshot_freed, snapshot_alloc_calls, fail_snapshot_allocation;
static bool allocating_pfns;
static unsigned ev_prepare_calls, ev_publish_calls, ev_put_calls;
static bool fail_ev_prepare;
static struct dma_fence prepared_ev_fence;
static struct dma_fence target_pt_fence;
static unsigned final_invalidations, final_pending_updates, final_pending_evictions;
static void (*fault_hook)(void);
static unsigned prepare_calls, fail_prepare_call;
static struct amdgpu_bo_va *fault_add_mapping;
static bool fault_remove_target;
static struct ttm_resource synthesized_resources[16];

static unsigned fail_store_call;
static int user_pages_error;
static bool trace_enabled;
static struct amdgpu_bo_va_mapping *trace_mappings[8];
static bool trace_amdgpu_vm_bo_clear_enabled(void) { return trace_enabled; }
static void trace_amdgpu_vm_bo_clear(struct amdgpu_vm *vm, struct amdgpu_bo_va_mapping *mapping)
{
    assert(vm == &fpriv.vm && trace_count < 8);
    trace_mappings[trace_count++] = mapping;
}
static void trace_amdgpu_userq_restore(struct amdgpu_vm *vm, bool begin, int result)
{
    assert(vm == &fpriv.vm);
    if (begin) {
        assert(!restore_depth && !result);
        restore_depth++;
        restore_begin++;
    } else {
        assert(restore_depth == 1);
        restore_depth--;
        restore_end++;
        restore_result = result;
    }
}
static bool trace_amdgpu_vm_bo_mapping_enabled(void) { return false; }
static void trace_amdgpu_vm_bo_mapping(struct amdgpu_bo_va_mapping *mapping) { }
ACTUAL_ALWAYS_VALID
static unsigned amdgpu_mem_type_to_domain(int type) { return 1u << type; }
static void amdgpu_vm_bo_idle(struct amdgpu_vm_bo_base *base)
{ list_move(&base->vm_status, &base->vm->individual.idle); }
static void list_splice_init(struct list_head *from, struct list_head *to)
{
    while (!list_empty(from))
        list_move(from->prev, to);
}
static void amdgpu_vm_bo_update_done(struct amdgpu_bo_va *bo_va, bool clear);
static int update_error, validation_error;

static void spin_lock(int *lock) { assert(!*lock); *lock = 1; }
static void spin_unlock(int *lock) { assert(*lock); *lock = 0; }
static void mutex_lock(int *lock)
{
    assert(!*lock);
    if (lock == &device.notifier_lock) {
        assert(fpriv.userq_mgr.userq_mutex);
        if (final_invalidations) {
            final_invalidations--;
            target_bo.notifier.seq++;
            list_move(&target_va.base.vm_status, &fpriv.vm.individual.needs_update);
        }
        if (final_pending_updates) {
            final_pending_updates--;
            list_move(&target_va.base.vm_status, &fpriv.vm.individual.needs_update);
        }
        if (final_pending_evictions) {
            final_pending_evictions--;
            list_move(&target_va.base.vm_status, &fpriv.vm.individual.evicted);
        }
    }
    *lock = 1;
}
static void mutex_unlock(int *lock) { assert(*lock); *lock = 0; }
#define ARRAY_SIZE(array) (sizeof(array) / sizeof((array)[0]))

static bool dma_resv_trylock(struct dma_resv *resv)
{
    if (resv->owner)
        return false;
    resv->owner = &temporary_lock;
    return true;
}
static struct ww_acquire_ctx *dma_resv_locking_ctx(struct dma_resv *resv) { return resv->owner; }
static void dma_resv_unlock(struct dma_resv *resv)
{ assert(resv->owner == &temporary_lock); resv->owner = NULL; unlocks++; }
static void *amdgpu_ttm_tt_get_usermm(struct ttm_tt *ttm) { return ttm && ttm->userptr ? ttm : NULL; }
static bool amdgpu_ttm_tt_is_userptr(struct ttm_tt *ttm) { return ttm && ttm->userptr; }
static bool drm_gem_is_imported(struct drm_gem_object *object) { return object->imported; }
static void amdgpu_vm_bo_evicted(void *base) { (void)base; }
static int amdgpu_vm_bo_update(struct amdgpu_device *adev, struct amdgpu_bo_va *bo_va, bool clear)
{
    if (bo_va != &prt) {
        updates++;
        if (update_error)
            return update_error;
        amdgpu_vm_bo_update_done(bo_va, clear);
        bo_va->mapped = !clear && bo_va->base.bo->tbo.resource->mem_type != TTM_PL_SYSTEM;
        list_move(&bo_va->base.vm_status, &bo_va->vm->individual.idle);
    }
    return 0;
}

ACTUAL_UPDATE_DONE

ACTUAL_HANDLE_MOVED

static void drm_exec_init(struct drm_exec *exec, unsigned flags, unsigned count) { *exec = (struct drm_exec){0}; exec->ticket.id = 5; }
#define drm_exec_ticket(exec) (&(exec)->ticket)
static int drm_exec_prepare_obj(struct drm_exec *exec, struct drm_gem_object *object, unsigned count);
static void drm_exec_fini(struct drm_exec *exec)
{
    for (unsigned i = 0; i < exec->count; i++) {
        assert(exec->objects[i]->resv->owner == &exec->ticket);
        exec->objects[i]->resv->owner = NULL;
    }
    exec->count = 0;
}
static int amdgpu_vm_lock_pd(struct amdgpu_vm *vm, struct drm_exec *exec, unsigned count)
{ return drm_exec_prepare_obj(exec, &vm->root.bo->tbo.base, count); }
static int amdgpu_vm_validate(struct amdgpu_device *adev, struct amdgpu_vm *vm,
                              struct ww_acquire_ctx *ticket, int (*callback)(void *, struct amdgpu_bo *), void *data)
{
    if (move_during_validation)
        list_move(&target_va.base.vm_status, &vm->individual.needs_update);
    struct amdgpu_vm_bo_base *base, *tmp;
    list_for_each_entry_safe(base, tmp, &vm->individual.evicted, vm_status) {
        if (base->bo->tbo.base.resv->owner != ticket)
            continue;
        int ret = callback(data, base->bo);
        if (ret)
            return ret;
        list_move(&base->vm_status, &vm->individual.needs_update);
    }
    return 0;
}
static int drm_exec_prepare_obj(struct drm_exec *exec, struct drm_gem_object *object, unsigned count)
{
    assert(!device.notifier_lock && !fpriv.vm.individual_lock);
    if (++prepare_calls == fail_prepare_call)
        return -ENOMEM;
    assert(!object->resv->owner || object->resv->owner == &exec->ticket);
    if (!object->resv->owner) {
        assert(exec->count < 16);
        exec->objects[exec->count++] = object;
    }
    object->resv->owner = &exec->ticket;
    return 0;
}
static struct amdgpu_bo *amdgpu_bo_ref(struct amdgpu_bo *bo) { bo->refs++; return bo; }
static void amdgpu_bo_unref(struct amdgpu_bo **bo) { assert((*bo)->refs > 1); (*bo)->refs--; *bo = NULL; }
static void amdgpu_bo_placement_from_domain(struct amdgpu_bo *bo, unsigned domains) { bo->placement = domains; }
static int ttm_bo_validate(struct ttm_buffer_object *bo, void *placement, struct ttm_operation_ctx *ctx)
{
    assert(!device.notifier_lock);
    assert(bo->base.resv->owner);
    if (validation_error)
        return validation_error;
    bool cpu = *(int *)placement == AMDGPU_GEM_DOMAIN_CPU;
    if (bo->ttm && bo->ttm->userptr && !cpu)
        assert(bo->ttm->pages_installed);
    int destination = cpu ? TTM_PL_SYSTEM : 1;
    bool changed = !bo->resource || bo->resource->mem_type != destination;
    if (!bo->resource) {
        for (unsigned i = 0; i < registered_count; i++) {
            if (registered_vas[i] && &registered_vas[i]->base.bo->tbo == bo)
                bo->resource = &synthesized_resources[i];
        }
        assert(bo->resource);
    }
    if (changed) {
        bo->resource->mem_type = destination;
        for (unsigned i = 0; i < registered_count; i++) {
            struct amdgpu_bo_va *va = registered_vas[i];
            if (va && &va->base.bo->tbo == bo)
                list_move(&va->base.vm_status, &va->vm->individual.needs_update);
        }
    }
    return 0;
}
#define SZ_2G (1ULL << 31)
#define PAGE_SHIFT 12
#define PAGE_SIZE (1UL << PAGE_SHIFT)
#define HMM_PFN_REQ_FAULT 1
#define HMM_PFN_REQ_WRITE 2
#define min(a, b) ((a) < (b) ? (a) : (b))
#define pr_debug(...) ((void)0)
static void *test_range_alloc(size_t size)
{
    assert(!device.notifier_lock);
    assert(size == sizeof(struct amdgpu_hmm_range));
    if (++range_alloc_calls == fail_range_allocation)
        return NULL;
    void *ptr = calloc(1, size);
    assert(ptr);
    ranges_allocated++;
    return ptr;
}
#define kzalloc_obj(value) test_range_alloc(sizeof(value))
#define kvmalloc_objs(obj, count) kvmalloc_array((count), sizeof(obj), GFP_KERNEL)
static void *kvmalloc_array(size_t count, size_t size, unsigned flags)
{
    assert(!device.notifier_lock && !fpriv.vm.individual_lock);
    if (allocating_pfns ? ++pfn_alloc_calls == fail_pfn_allocation :
                          ++snapshot_alloc_calls == fail_snapshot_allocation)
        return NULL;
    void *ptr = calloc(count ? count : 1, size);
    assert(ptr);
    if (allocating_pfns) {
        assert(pfn_allocated < ARRAY_SIZE(allocated_pfns));
        allocated_pfns[pfn_allocated++] = ptr;
    } else {
        assert(snapshot_allocated < ARRAY_SIZE(allocated_snapshots));
        allocated_snapshots[snapshot_allocated++] = ptr;
    }
    return ptr;
}
static void kvfree(void *ptr)
{
    if (!ptr)
        return;
    for (unsigned i = 0; i < pfn_allocated; i++) {
        if (allocated_pfns[i] == ptr) {
            allocated_pfns[i] = NULL;
            pfn_freed++;
            free(ptr);
            return;
        }
    }
    for (unsigned i = 0; i < snapshot_allocated; i++) {
        if (allocated_snapshots[i] == ptr) {
            allocated_snapshots[i] = NULL;
            snapshot_freed++;
            free(ptr);
            return;
        }
    }
    assert(false);
}
static void amdgpu_vm_assert_locked(struct amdgpu_vm *vm)
{ assert(vm->root.bo->tbo.base.resv->owner); }
ACTUAL_LOCK_BOS
static void kfree(void *ptr)
{
    if (!ptr)
        return;
    ranges_freed++;
    free(ptr);
}
static unsigned long mmu_interval_read_begin(struct mmu_interval_notifier *notifier)
{ return notifier->seq; }
static bool mmu_interval_read_retry(struct mmu_interval_notifier *notifier, unsigned long seq)
{ return notifier->seq != seq; }
static int hmm_range_fault(struct hmm_range *range)
{
    assert(!device.notifier_lock);
    hmm_fault_calls++;
    if (fault_hook)
        fault_hook();
    if (user_pages_error)
        return user_pages_error;
    for (unsigned i = 0; i < (range->end - range->start) / PAGE_SIZE; i++)
        range->hmm_pfns[i] = range->notifier_seq + 0x100 + i;
    if (notifier_invalidations) {
        notifier_invalidations--;
        range->notifier->seq++;
    }
    return 0;
}
ACTUAL_AMDGPU_HMM_RANGE_GET_PAGES
ACTUAL_AMDGPU_HMM_RANGE_VALID
ACTUAL_AMDGPU_HMM_RANGE_ALLOC
ACTUAL_AMDGPU_HMM_RANGE_FREE
static void amdgpu_ttm_tt_set_user_pages(struct ttm_tt *ttm, struct amdgpu_hmm_range *range)
{ assert(range && range->hmm_range.hmm_pfns); ttm->pages_installed++; }
static int amdgpu_ttm_tt_get_user_pages(struct amdgpu_bo *bo, struct amdgpu_hmm_range *range)
{
    assert(!device.notifier_lock && !bo->tbo.base.resv->owner);
    user_pages_calls++;
    /* The actual HMM helper allocates the PFN array on every acquisition. */
    allocating_pfns = true;
    int ret = amdgpu_hmm_range_get_pages(&bo->notifier, 0x100000, 4, false, NULL, range);
    allocating_pfns = false;
    return ret;
}
static void xa_init(struct xarray *array)
{
    *array = (struct xarray){0};
    for (unsigned i = 0; i < 16; i++)
        array->keys[i] = i;
}
static void *xa_load(struct xarray *array, unsigned long key)
{
    for (unsigned i = 0; i < 16; i++)
        if (array->keys[i] == key && array->values[i])
            return array->values[i];
    return NULL;
}
static void *xa_store(struct xarray *array, unsigned long key, void *value, unsigned flags)
{
    assert(!device.notifier_lock);
    if (++store_calls == fail_store_call)
        return (void *)(intptr_t)-ENOMEM;
    for (unsigned i = 0; i < 16; i++) {
        if (array->keys[i] == key && array->values[i]) {
            void *old = array->values[i];
            array->values[i] = value;
            return old;
        }
    }
    for (unsigned i = 0; i < 16; i++) {
        if (!array->values[i]) {
            array->keys[i] = key;
            array->values[i] = value;
            return NULL;
        }
    }
    assert(false);
    return NULL;
}
static int xa_err(void *entry) { return (uintptr_t)entry >= (uintptr_t)-4095 ? (int)(intptr_t)entry : 0; }
static void xa_erase(struct xarray *array, unsigned long key)
{
    for (unsigned i = 0; i < 16; i++)
        if (array->keys[i] == key)
            array->values[i] = NULL;
}
static void xa_destroy(struct xarray *array) { }

static struct amdgpu_bo_va *amdgpu_vm_bo_find(struct amdgpu_vm *vm, struct amdgpu_bo *bo)
{
    for (unsigned i = 0; i < registered_count; i++)
        if (registered_vas[i] && registered_vas[i]->base.bo == bo)
            return registered_vas[i];
    return NULL;
}
static int amdgpu_vm_update_pdes(struct amdgpu_device *adev, struct amdgpu_vm *vm, bool immediate) { return 0; }
static int dma_fence_wait(struct dma_fence *fence, bool interruptible)
{ assert(!device.notifier_lock); if (fence) fence->waited = true; return 0; }
static int amdgpu_ttm_alloc_gart(struct ttm_buffer_object *bo) { return 0; }
static uint64_t amdgpu_bo_gpu_offset(struct amdgpu_bo *bo) { return bo_mc_base; }
static struct dma_fence *amdgpu_evf_mgr_prepare(int *manager)
{
    assert(!device.notifier_lock && !fpriv.userq_mgr.userq_mutex);
    ev_prepare_calls++;
    if (fail_ev_prepare)
        return NULL;
    prepared_ev_fence.refs = 1;
    return &prepared_ev_fence;
}
static void amdgpu_evf_mgr_rearm(int *manager, struct drm_exec *exec, struct dma_fence *fence)
{
    assert(device.notifier_lock && fpriv.userq_mgr.userq_mutex && fence->refs == 1);
    assert(list_empty(&fpriv.vm.individual.needs_update));
    assert(list_empty(&fpriv.vm.individual.evicted));
    ev_publish_calls++;
}
static void dma_fence_put(struct dma_fence *fence)
{
    if (!fence)
        return;
    assert(fence->refs == 1);
    fence->refs--;
    ev_put_calls++;
}

static int amdgpu_userq_restore_all(struct amdgpu_userq_mgr *manager)
{
    assert(manager->userq_mutex && !device.notifier_lock && ev_publish_calls);
    assert(target_pt_fence.waited || !amdgpu_vm_bo_find(&fpriv.vm, &target_bo));
    struct amdgpu_usermode_queue *queue;
    unsigned long index;
    xa_for_each(&manager->userq_xa, index, queue)
        assert(queue->wptr_obj.gpu_addr == bo_mc_base + queue->wptr_bo_offset);
    (void)index;
    restored++;
    return 0;
}

ACTUAL_VALIDATE

ACTUAL_PUT_BOS

ACTUAL_RESTORE

static void setup(void)
{
    fpriv = (struct amdgpu_fpriv){0};
    root_resv = (struct dma_resv){0};
    root_bo = (struct amdgpu_bo){.tbo.base.resv = &root_resv, .refs = 1};
    fpriv.vm.root.bo = &root_bo;
    target_resv = (struct dma_resv){0};
    target_tt = (struct ttm_tt){0};
    target_resource = (struct ttm_resource){.mem_type = 1};
    target_bo = (struct amdgpu_bo){.tbo = {.base = {.resv = &target_resv}, .ttm = &target_tt,
                                        .resource = &target_resource}, .refs = 1,
                                        .allowed_domains = AMDGPU_GEM_DOMAIN_GTT};
    target_va = (struct amdgpu_bo_va){.base = {.bo = &target_bo, .vm = &fpriv.vm}, .vm = &fpriv.vm};
    target_pt_fence = (struct dma_fence){0};
    target_va.last_pt_update = &target_pt_fence;
    registered_count = 1;
    registered_vas[0] = &target_va;
    list_init(&target_va.valids);
    list_init(&target_va.invalids);
    device = (struct amdgpu_device){0};
    fpriv.userq_mgr.adev = &device;
    xa_init(&fpriv.userq_mgr.userq_xa);
    fpriv.prt_va = &prt;
    struct amdgpu_vm_bo_status *lists[] = {&fpriv.vm.always_valid, &fpriv.vm.individual};
    for (unsigned i = 0; i < 2; i++) {
        list_init(&lists[i]->idle);
        list_init(&lists[i]->needs_update);
        list_init(&lists[i]->evicted);
    }
    list_add(&target_va.base.vm_status, &fpriv.vm.individual.idle);
    move_during_validation = false;
    updates = restored = unlocks = 0;
    bo_mc_base = 0x100000;
    update_error = validation_error = 0;
    assert(!restore_depth);
    trace_count = restore_begin = restore_end = 0;
    trace_enabled = false;
    restore_result = 0;
    range_alloc_calls = ranges_allocated = ranges_freed = store_calls = user_pages_calls = 0;
    fail_range_allocation = 0;
    fail_store_call = 0;
    user_pages_error = 0;
    pfn_alloc_calls = pfn_allocated = pfn_freed = hmm_fault_calls = fail_pfn_allocation = 0;
    notifier_invalidations = 0;
    memset(allocated_pfns, 0, sizeof(allocated_pfns));
    memset(allocated_snapshots, 0, sizeof(allocated_snapshots));
    snapshot_allocated = snapshot_freed = snapshot_alloc_calls = fail_snapshot_allocation = 0;
    allocating_pfns = false;
    ev_prepare_calls = ev_publish_calls = ev_put_calls = final_invalidations = final_pending_updates = final_pending_evictions = 0;
    fail_ev_prepare = false;
    prepared_ev_fence = (struct dma_fence){0};
    prepare_calls = fail_prepare_call = 0;
    fault_hook = NULL;
    fault_add_mapping = NULL;
    fault_remove_target = false;
    memset(synthesized_resources, 0, sizeof(synthesized_resources));
}

static void change_mapping_during_fault(void)
{
    assert(!root_resv.owner && !target_resv.owner && !device.notifier_lock);
    fault_hook = NULL;
    if (fault_remove_target) {
        assert(target_bo.refs > 1); /* The retained HMM range owns this BO. */
        list_del(&target_va.base.vm_status);
        registered_vas[0] = NULL;
    }
    if (fault_add_mapping) {
        list_add(&fault_add_mapping->base.vm_status, &fpriv.vm.individual.idle);
        registered_vas[registered_count++] = fault_add_mapping;
    }
}

static void check_cleanup(void)
{
    assert(!device.notifier_lock && !fpriv.userq_mgr.userq_mutex);
    assert(!fpriv.vm.individual_lock && !root_resv.owner && !target_resv.owner);
    assert(snapshot_allocated == snapshot_freed);
    assert(ranges_allocated == ranges_freed && pfn_allocated == pfn_freed);
    assert(target_bo.refs == 1 && root_bo.refs == 1);
    assert(ev_put_calls == (fail_ev_prepare ? 0 : ev_prepare_calls));
}

int main(void)
{
    const uint64_t offsets[] = {0, 8, 4096 - sizeof(uint64_t), 65536 - sizeof(uint64_t)};
    for (unsigned i = 0; i < sizeof(offsets) / sizeof(offsets[0]); i++) {
        setup();
        struct amdgpu_usermode_queue moved = {
            .wptr_obj = {.obj = &target_bo, .gpu_addr = bo_mc_base + offsets[i]},
            .wptr_bo_offset = offsets[i],
        };
        fpriv.userq_mgr.userq_xa.values[0] = &moved;
        for (unsigned move = 0; move < 2; move++) {
            bo_mc_base += 0x100000;
            target_resource.mem_type = TTM_PL_SYSTEM;
            assert(amdgpu_userq_vm_validate_and_restore_queue(&fpriv.userq_mgr) == 0);
            assert(moved.wptr_bo_offset == offsets[i]);
            assert(moved.wptr_obj.gpu_addr == bo_mc_base + offsets[i]);
            assert(restored == move + 1 && !target_resv.owner);
        }
    }
    puts("UserQ restore: zero/nonzero WPTR offsets preserved across repeated BO moves");

    setup();
    struct amdgpu_usermode_queue queue = {.wptr_obj.obj = &target_bo};
    fpriv.userq_mgr.userq_xa.values[0] = &queue;
    target_resource.mem_type = TTM_PL_SYSTEM;
    assert(amdgpu_userq_vm_validate_and_restore_queue(&fpriv.userq_mgr) == 0);
    assert(restored == 1 && updates == 1 && target_va.mapped && !target_va.cleared);

    setup();
    move_during_validation = true;
    assert(amdgpu_userq_vm_validate_and_restore_queue(&fpriv.userq_mgr) == 0);
    assert(restored == 1 && updates == 1 && !target_va.cleared && !target_resv.owner);

    setup();
    assert(amdgpu_userq_vm_validate_and_restore_queue(&fpriv.userq_mgr) == 0);
    assert(restored == 1 && !updates && !target_va.cleared);

    for (unsigned evicted = 0; evicted < 2; evicted++) {
        setup();
        list_move(&target_va.base.vm_status, evicted ? &fpriv.vm.individual.evicted :
                                                    &fpriv.vm.individual.needs_update);
        target_resource.mem_type = TTM_PL_SYSTEM;
        assert(amdgpu_userq_vm_validate_and_restore_queue(&fpriv.userq_mgr) == 0);
        assert(restored == 1 && updates == 1 && target_va.mapped && !target_va.cleared);
        assert(target_resource.mem_type != TTM_PL_SYSTEM && target_bo.refs == 1);
    }

    for (unsigned state = 0; state < 6; state++) {
        setup();
        list_move(&target_va.base.vm_status, &fpriv.vm.individual.needs_update);
        struct ww_acquire_ctx *ticket = &standalone;
        if (state == 1 || state == 3 || state == 4)
            target_resv.owner = &standalone;
        else if (state == 2)
            target_resv.owner = &foreign;
        if (state == 3 || state == 5)
            ticket = NULL;
        if (state == 4) {
            device.debug_vm = true;
            target_tt.userptr = true;
        }
        assert(amdgpu_vm_handle_moved(&device, &fpriv.vm, ticket) == 0);
        assert(updates == 1 && !restored);
        assert(target_va.cleared == (state == 2 || state == 3));
        assert(unlocks == (state == 0 || state == 5));
        if (state == 1 || state == 3 || state == 4)
            assert(target_resv.owner == &standalone);
        if (state == 2)
            assert(target_resv.owner == &foreign);
    }

    setup();
    move_during_validation = true;
    update_error = -ENOMEM;
    assert(amdgpu_userq_vm_validate_and_restore_queue(&fpriv.userq_mgr) == -ENOMEM);
    assert(!restored && !target_resv.owner);

    setup();
    list_move(&target_va.base.vm_status, &fpriv.vm.individual.needs_update);
    validation_error = -ENOMEM;
    assert(amdgpu_userq_vm_validate_and_restore_queue(&fpriv.userq_mgr) == -ENOMEM);
    assert(!restored && !updates && !target_resv.owner && target_bo.refs == 1);
    assert(restore_begin == 1 && restore_end == 1 && restore_result == -ENOMEM);
    for (unsigned mode = 0; mode < 4; mode++) {
        setup();
        struct amdgpu_bo_va_mapping ranges[2] = {{.start = 10, .last = 12}, {.start = 20, .last = 25}};
        for (unsigned i = 0; i < 2; i++)
            list_add(&ranges[i].list, &target_va.invalids);
        trace_enabled = mode != 0;
        if (mode == 3)
            update_error = -ENOMEM;
        bool clear = mode != 1;
        assert(amdgpu_vm_bo_update(&device, &target_va, clear) == update_error);
        assert(trace_count == (mode == 2 ? 2 : 0));
        if (mode == 2) {
            assert(trace_mappings[0] == &ranges[1] && trace_mappings[1] == &ranges[0]);
            assert(amdgpu_vm_bo_update(&device, &target_va, true) == 0);
            assert(trace_count == 2);
        }
    }
    setup();
    assert(amdgpu_userq_vm_validate_and_restore_queue(&fpriv.userq_mgr) == 0);
    assert(restore_begin == 1 && restore_end == 1 && !restore_result);
    for (unsigned mode = 0; mode < 5; mode++) {
        setup();
        target_tt.userptr = true;
        /* GEM_USERPTR uses its own reservation and stays on individual.idle. */
        assert(!list_empty(&fpriv.vm.individual.idle));
        assert(list_empty(&fpriv.vm.always_valid.idle));
        struct amdgpu_bo second_bo = target_bo;
        struct dma_resv second_resv = {0};
        struct ttm_tt second_tt = target_tt;
        struct ttm_resource second_resource = target_resource;
        second_bo.tbo.base.resv = &second_resv;
        second_bo.tbo.ttm = &second_tt;
        second_bo.tbo.resource = &second_resource;
        struct amdgpu_bo_va second_va = {.base = {.bo = &second_bo, .vm = &fpriv.vm}, .vm = &fpriv.vm};
        list_init(&second_va.valids);
        list_init(&second_va.invalids);
        assert(!amdgpu_vm_is_bo_always_valid(&fpriv.vm, &target_bo));
        assert(!amdgpu_vm_is_bo_always_valid(&fpriv.vm, &second_bo));
        if (mode != 0) {
            list_add(&second_va.base.vm_status, &target_va.base.vm_status);
            registered_vas[registered_count++] = &second_va;
        }
        /* Fail the second insertion after a previous range has been recorded. */
        if (mode == 2)
            fail_store_call = 2;
        else if (mode == 3)
            fail_range_allocation = 2;
        else if (mode == 4)
            user_pages_error = -EFAULT;
        int expected = mode == 4 ? -EFAULT : mode >= 2 ? -ENOMEM : 0;
        int actual = amdgpu_userq_vm_validate_and_restore_queue(&fpriv.userq_mgr);
        if (actual != expected) fprintf(stderr, "mode=%u got=%d expected=%d\n", mode, actual, expected);
        assert(actual == expected);
        assert(restored == (expected == 0));
        assert(ranges_allocated == ranges_freed);
        assert(pfn_allocated == pfn_freed);
        assert(range_alloc_calls && (expected || user_pages_calls));
        assert(!second_resv.owner);
        assert(target_bo.refs == 1 && second_bo.refs == 1 && !target_resv.owner);
        assert(restore_begin == 1 && restore_end == 1 && restore_result == expected);
        if (mode == 2)
            assert(store_calls == 2 && ranges_allocated == 2 && !user_pages_calls);
        if (mode == 3)
            assert(store_calls == 1 && ranges_allocated == 1 && !user_pages_calls);
    }
    /* MMU invalidation must refault pages before publishing protection. */
    for (unsigned injection = 0; injection < 2; injection++) {
        for (unsigned retries = 1; retries <= 3; retries++) {
            setup();
            target_tt.userptr = true;
            if (injection)
                final_invalidations = retries;
            else
                notifier_invalidations = retries;
            for (unsigned attempt = 0; attempt <= retries; attempt++) {
                int ret = amdgpu_userq_vm_validate_and_restore_queue(&fpriv.userq_mgr);
                assert(ret == (attempt < retries ? -EAGAIN : 0));
                if (ret)
                    assert(!restored && !ev_publish_calls);
                check_cleanup();
            }
            assert(user_pages_calls == retries + 1);
            assert(target_tt.pages_installed == retries + 1);
            assert(ev_prepare_calls == retries + 1 && ev_publish_calls == 1);
            assert(target_va.mapped && !target_va.cleared && restored == 1);
            check_cleanup();
        }
    }
    puts("UserQ restore: MMU invalidation during faults and final publication retries passed");

    /* A pending-list change must yield to the worker, even without userptrs. */
    for (unsigned evicted = 0; evicted < 2; evicted++) {
        setup();
        if (evicted)
            final_pending_evictions = 3;
        else
            final_pending_updates = 3;
        for (unsigned attempt = 0; attempt < 3; attempt++) {
            assert(amdgpu_userq_vm_validate_and_restore_queue(&fpriv.userq_mgr) == -EAGAIN);
            assert(!restored && !ev_publish_calls && !user_pages_calls);
            check_cleanup();
        }
        assert(amdgpu_userq_vm_validate_and_restore_queue(&fpriv.userq_mgr) == 0);
        assert(restored == 1 && ev_publish_calls == 1 && !user_pages_calls);
        check_cleanup();
    }
    puts("UserQ restore: persistent pending lists yield without refaulting or resuming queues");

    /* New/removed mappings while HMM drops reservations cannot alias a range. */
    for (unsigned mutation = 0; mutation < 4; mutation++) {
        setup();
        target_tt.userptr = true;
        struct dma_resv other_resv = {0};
        struct ttm_tt other_tt = {.userptr = true};
        struct ttm_resource other_resource = {.mem_type = 1};
        struct amdgpu_bo other_bo = {.tbo = {.base.resv = &other_resv,
            .ttm = &other_tt, .resource = &other_resource}, .refs = 1,
            .allowed_domains = AMDGPU_GEM_DOMAIN_GTT};
        struct dma_fence other_pt = {0};
        struct amdgpu_bo_va other_va = {.base = {.bo = &other_bo, .vm = &fpriv.vm},
            .vm = &fpriv.vm, .last_pt_update = &other_pt};
        list_init(&other_va.valids);
        list_init(&other_va.invalids);
        if (mutation == 3) {
            /* Imported BOs can share a reservation, unlike GEM_USERPTR BOs. */
            target_tt.userptr = other_tt.userptr = false;
            target_bo.tbo.base.imported = other_bo.tbo.base.imported = true;
            target_resource.mem_type = other_resource.mem_type = TTM_PL_SYSTEM;
            other_bo.tbo.base.resv = &target_resv;
            list_add(&other_va.base.vm_status, &fpriv.vm.individual.evicted);
            registered_vas[registered_count++] = &other_va;
        } else {
            fault_remove_target = mutation != 0;
            fault_add_mapping = mutation == 1 ? NULL : &other_va;
            fault_hook = change_mapping_during_fault;
        }
        assert(amdgpu_userq_vm_validate_and_restore_queue(&fpriv.userq_mgr) == 0);
        assert(restored == 1 && ev_publish_calls == 1);
        assert(other_bo.refs == 1 && !other_resv.owner);
        if (mutation != 1) {
            assert(other_va.mapped && other_pt.waited);
            if (mutation != 3)
                assert(other_tt.pages_installed && ranges_allocated == 2);
        }
        if (mutation == 0)
            assert(user_pages_calls == 3 && target_tt.pages_installed == 1);
        if (mutation == 1 || mutation == 2)
            assert(!target_tt.pages_installed);
        if (mutation == 3) {
            assert(target_va.mapped && target_pt_fence.waited);
            assert(!ranges_allocated && !user_pages_calls);
            assert(!target_tt.pages_installed && !other_tt.pages_installed);
        }
        check_cleanup();
    }
    puts("UserQ restore: new/removed BOs and distinct BOs sharing reservations passed");

    /* Storage restoration is independent of the BO's state-list placement. */
    for (unsigned state = 0; state < 3; state++) {
        for (unsigned missing = 0; missing < 2; missing++) {
            setup();
            struct list_head *states[] = {&fpriv.vm.individual.idle,
                &fpriv.vm.individual.needs_update, &fpriv.vm.individual.evicted};
            list_move(&target_va.base.vm_status, states[state]);
            target_bo.tbo.ttm = NULL;
            if (missing)
                target_bo.tbo.resource = NULL;
            else
                target_resource.mem_type = TTM_PL_SYSTEM;
            assert(amdgpu_userq_vm_validate_and_restore_queue(&fpriv.userq_mgr) == 0);
            assert(target_bo.tbo.resource && target_bo.tbo.resource->mem_type != TTM_PL_SYSTEM);
            assert(restored == 1 && target_va.mapped && !target_va.cleared);
            check_cleanup();
        }
    }
    setup();
    fpriv.vm.is_compute_context = true;
    target_bo.tbo.base.imported = true;
    list_move(&target_va.base.vm_status, &fpriv.vm.individual.evicted);
    assert(amdgpu_userq_vm_validate_and_restore_queue(&fpriv.userq_mgr) == 0);
    assert(list_empty(&fpriv.vm.individual.evicted));
    assert(restored == 1 && target_va.mapped && !target_va.cleared);
    check_cleanup();
    puts("UserQ restore: idle, needs-update, evicted and imported compute BOs passed");

    for (unsigned failure = 0; failure < 5; failure++) {
        setup();
        if (failure == 0)
            fail_snapshot_allocation = 1;
        else if (failure == 1)
            fail_prepare_call = 2;
        else if (failure == 2)
            fail_ev_prepare = true;
        else if (failure == 3) {
            target_tt.userptr = true;
            fail_pfn_allocation = 1;
        } else {
            target_tt.userptr = true;
            user_pages_error = -EBUSY;
        }
        int error = failure == 4 ? -EAGAIN : -ENOMEM;
        assert(amdgpu_userq_vm_validate_and_restore_queue(&fpriv.userq_mgr) == error);
        assert(!restored && !ev_publish_calls);
        check_cleanup();
    }
    puts("UserQ restore: snapshot, reservation, eviction-fence, PFN and HMM retry failures passed");

    /* Exercise the real HMM PFN helper across reacquisition and failures. */
    setup();
    target_tt.userptr = true;
    struct amdgpu_hmm_range *range = amdgpu_hmm_range_alloc(&target_bo);
    assert(range && !amdgpu_hmm_range_valid(range));
    assert(amdgpu_ttm_tt_get_user_pages(&target_bo, range) == 0);
    assert(amdgpu_hmm_range_valid(range));
    assert(amdgpu_ttm_tt_get_user_pages(&target_bo, range) == 0);
    assert(pfn_allocated == 2 && pfn_freed == 1);
    unsigned long *old_pfns = range->hmm_range.hmm_pfns;
    fail_pfn_allocation = pfn_alloc_calls + 1;
    assert(amdgpu_ttm_tt_get_user_pages(&target_bo, range) == -ENOMEM);
    assert(range->hmm_range.hmm_pfns == old_pfns);
    fail_pfn_allocation = 0;
    user_pages_error = -EBUSY;
    assert(amdgpu_ttm_tt_get_user_pages(&target_bo, range) == -EAGAIN);
    assert(!range->hmm_range.hmm_pfns && !amdgpu_hmm_range_valid(range));
    user_pages_error = 0;
    assert(amdgpu_ttm_tt_get_user_pages(&target_bo, range) == 0);
    assert(amdgpu_hmm_range_valid(range));
    target_bo.notifier.seq++;
    assert(!amdgpu_hmm_range_valid(range));
    amdgpu_hmm_range_free(range);
    assert(ranges_allocated == ranges_freed && pfn_allocated == pfn_freed);
    assert(target_bo.refs == 1);
    puts("HMM PFN reacquisition, allocation failure, fault failure and sequence invalidation passed");
    puts("UserQ restore: userptr success, allocation failures and page-acquisition cleanup passed");
    puts("UserQ restore: 13 reservation cases and clear/restore diagnostics passed");
    return 0;
}
'''

WORKER_HARNESS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <errno.h>
struct work_struct { int unused; };
struct delayed_work { struct work_struct work; };
struct dma_fence { bool signaled; unsigned refs; };
struct eviction_mgr { bool shutdown; struct dma_fence fence; };
struct amdgpu_userq_mgr { struct delayed_work resume_work; };
struct amdgpu_fpriv { struct amdgpu_userq_mgr userq_mgr; struct eviction_mgr evf_mgr; };
#define container_of(ptr, type, member) ((type *)((char *)(ptr) - offsetof(type, member)))
#define work_to_uq_mgr(work, member) container_of(work, struct amdgpu_userq_mgr, member)
#define uq_mgr_to_fpriv(mgr) container_of(mgr, struct amdgpu_fpriv, userq_mgr)
#define READ_ONCE(value) (value)
#define DEFAULT_RATELIMIT_INTERVAL 1
#define DEFAULT_RATELIMIT_BURST 1
#define DEFINE_RATELIMIT_STATE(name, interval, burst) int name
#define drm_file_err(...) (++errors)
static struct amdgpu_fpriv fpriv;
static unsigned retries, validations, errors, rate_checks;
static bool allow_log = true;
static bool __ratelimit(int *state) { (void)state; rate_checks++; return allow_log; }
static int restore_error;
static struct dma_fence *amdgpu_evf_mgr_get_fence(struct eviction_mgr *mgr)
{ mgr->fence.refs++; return &mgr->fence; }
static bool dma_fence_is_signaled(struct dma_fence *fence) { return fence->signaled; }
static void dma_fence_put(struct dma_fence *fence) { assert(fence->refs == 2); fence->refs--; }
static int amdgpu_userq_vm_validate_and_restore_queue(struct amdgpu_userq_mgr *mgr)
{ assert(mgr == &fpriv.userq_mgr); validations++; return restore_error; }
static unsigned msecs_to_jiffies(unsigned ms) { assert(ms == 1); return 1; }
static bool schedule_delayed_work(struct delayed_work *work, unsigned delay)
{ assert(work == &fpriv.userq_mgr.resume_work && delay == 1); retries++; return true; }
ACTUAL_WORKER
int main(void)
{
    const int results[] = {0, -EAGAIN, -EFAULT, -ENOMEM, -EBUSY};
    for (unsigned logging = 0; logging < 2; logging++) {
      allow_log = logging;
      for (unsigned active = 0; active < 2; active++) {
        for (unsigned shutdown = 0; shutdown < 2; shutdown++) {
            for (unsigned result = 0; result < sizeof(results) / sizeof(results[0]); result++) {
                fpriv = (struct amdgpu_fpriv){.evf_mgr = {.shutdown = shutdown,
                    .fence = {.signaled = active, .refs = 1}}};
                retries = validations = errors = rate_checks = 0;
                restore_error = results[result];
                amdgpu_userq_restore_worker(&fpriv.userq_mgr.resume_work.work);
                assert(validations == active);
                assert(retries == (active && !shutdown && restore_error == -EAGAIN));
                bool permanent_error = active && restore_error && restore_error != -EAGAIN;
                assert(rate_checks == permanent_error);
                assert(errors == (permanent_error && logging));
                assert(fpriv.evf_mgr.fence.refs == 1);
            }
        }
    }
    }
    puts("UserQ worker: 40 success/error/shutdown/fence-state and rate-limit cases passed");
    return 0;
}
'''


if __name__ == '__main__':
    unittest.main()
