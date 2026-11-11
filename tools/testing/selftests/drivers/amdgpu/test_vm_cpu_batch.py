#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise production CPU page-table updates without GPU access."""
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
            if (source / 'amdgpu_vm_cpu.c').is_file():
                return source
    raise RuntimeError('Set AMDGPU_TEST_ROOT to the kernel or standalone module source tree')


AMD = source_directory()


def function(source, name):
    match = re.search(r'^(?:static )?(?:int|void|bool)\s+' + name + r'\(.*?^\}',
                      source, re.M | re.S)
    if not match:
        raise AssertionError('Missing production function ' + name)
    return match.group()


class VmCpuBatchTests(unittest.TestCase):
    def test_cpu_updates_and_batch_completion(self):
        cpu = (AMD / 'amdgpu_vm_cpu.c').read_text()
        vm = (AMD / 'amdgpu_vm.c').read_text()
        cpu_functions = '\n'.join(function(cpu, name) for name in (
            'amdgpu_vm_cpu_map_table', 'amdgpu_vm_cpu_prepare',
            'amdgpu_vm_cpu_update', 'amdgpu_vm_cpu_commit'))
        vm_functions = '\n'.join(function(vm, name) for name in (
            'amdgpu_vm_tlb_seq_cb', 'amdgpu_vm_tlb_flush',
            'amdgpu_vm_update_begin', 'amdgpu_vm_update_pages',
            'amdgpu_vm_update_end', 'amdgpu_vm_update_range',
            'amdgpu_vm_clear_freed_list', 'amdgpu_vm_clear_freed',
            'amdgpu_vm_bo_update_internal', 'amdgpu_vm_bo_update',
            'amdgpu_vm_bo_can_batch', 'amdgpu_vm_bo_update_batch'))
        program = HARNESS.replace('ACTUAL_CPU', cpu_functions).replace('ACTUAL_VM', vm_functions)
        args = '&device, &vm, bos' + (', false' if 'struct xarray *bos, bool flush_tlb' in vm else '')
        program = program.replace('BO_BATCH_CALL', 'amdgpu_vm_bo_update_batch(' + args + ')')
        command = shlex.split(os.environ.get('CC', 'cc'))
        command += ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', '-Wno-unused-function']
        if os.environ.get('USERQ_TEST_SANITIZERS'):
            command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                        '-fno-sanitize-recover=all']
        with tempfile.TemporaryDirectory(prefix='vm-cpu-batch-') as temporary:
            path = pathlib.Path(temporary)
            source, binary = path / 'test.c', path / 'test'
            source.write_text(program)
            if os.environ.get('VM_CPU_BATCH_SAVE'):
                pathlib.Path(os.environ['VM_CPU_BATCH_SAVE']).write_text(program)
            subprocess.run(command + [str(source), '-o', str(binary)], check=True, timeout=60)
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
typedef uint64_t dma_addr_t;
#define container_of(p, type, member) ((type *)((char *)(p) - offsetof(type, member)))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define swap(a, b) do { typeof(a) swap_value = (a); (a) = (b); (b) = swap_value; } while (0)
#define min(a, b) ((a) < (b) ? (a) : (b))
#define max(a, b) ((a) > (b) ? (a) : (b))
#define IP_VERSION(a, b, c) (((a) << 16) | ((b) << 8) | (c))
#define GC_HWIP 0
#define AMDGPU_KERNEL_JOB_ID_VM_UPDATE_RANGE 1
#define AMDGPU_GEM_CREATE_CPU_ACCESS_REQUIRED 1
#define AMDGPU_GPU_PAGE_SIZE 4096u
#define AMDGPU_GPU_PAGE_SHIFT 12
#define AMDGPU_GPU_PAGES_IN_CPU_PAGE 1
#define PAGE_SHIFT 12
#define PAGE_SIZE 4096u
#define AMDGPU_PTE_VALID 1
#define AMDGPU_PTE_READABLE 4
#define AMDGPU_PTE_WRITEABLE 8
#define AMDGPU_PTE_TMZ 16
#define AMDGPU_VM_PAGE_READABLE 1
#define AMDGPU_VM_PAGE_WRITEABLE 2
#define AMDGPU_VM_PAGE_PRT 4
#define AMDGPU_GEM_CREATE_UNCACHED 2
#define TTM_PL_VRAM 2
#define TTM_PL_TT 1
#define AMDGPU_PL_PREEMPT 3
#define trace_amdgpu_vm_bo_update(...) ((void)0)
#define AMDGPU_PTE_PRT_FLAG(adev) ((void)(adev), 2)
#define DMA_RESV_USAGE_KERNEL 0
#define DMA_RESV_USAGE_BOOKKEEP 3
#define MAX_SCHEDULE_TIMEOUT 0x7fffffff
#define trace_amdgpu_vm_set_ptes(...) ((void)0)
#define atomic64_inc(p) (++*(p))

enum amdgpu_sync_mode { AMDGPU_SYNC_EXPLICIT, AMDGPU_SYNC_EQ_OWNER, AMDGPU_SYNC_EXPLICIT_UNMAP };
struct list_head { struct list_head *next, *prev; };
#define INIT_LIST_HEAD(h) ((h)->next = (h)->prev = (h))
#define LIST_HEAD(name) struct list_head name = { &(name), &(name) }
#define list_for_each_entry(entry, head, member) \
    for (struct list_head *node = (head)->next; \
         node != (head) && ((entry = container_of(node, typeof(*entry), member)), true); \
         node = node->next)
#define list_for_each_entry_safe(entry, next_entry, head, member) \
    for (struct list_head *node = (head)->next, *next_node; \
         node != (head) && ((next_node = node->next), \
         (entry = container_of(node, typeof(*entry), member)), \
         (next_entry = next_node == (head) ? NULL : container_of(next_node, typeof(*entry), member)), (void)(next_entry), true); \
         node = next_node)
static bool list_empty(const struct list_head *head) { return head->next == head; }
static void list_add_tail(struct list_head *node, struct list_head *head)
{ node->next = head; node->prev = head->prev; head->prev->next = node; head->prev = node; }
static void list_del(struct list_head *node)
{ node->next->prev = node->prev; node->prev->next = node->next; }
static void list_move_tail(struct list_head *node, struct list_head *head)
{ list_del(node); list_add_tail(node, head); }
static void list_splice_init(struct list_head *source, struct list_head *target)
{
    if (list_empty(source)) return;
    source->next->prev = target;
    source->prev->next = target->next;
    target->next->prev = source->prev;
    target->next = source->next;
    INIT_LIST_HEAD(source);
}
static unsigned list_count(const struct list_head *head)
{ unsigned count = 0; for (struct list_head *node = head->next; node != head; node = node->next) ++count; return count; }

struct dma_fence;
struct dma_fence_cb { void (*fn)(struct dma_fence *, struct dma_fence_cb *); };
struct dma_fence { unsigned refs; bool signaled; struct dma_fence_cb *cb; };
struct dma_resv { unsigned unused; };
struct dma_buf { void *priv; };
struct dma_buf_attachment { struct dma_buf *dmabuf; };
struct drm_gem_object { struct dma_resv *resv; bool imported; struct dma_buf_attachment *import_attach; };
struct ttm_tt { dma_addr_t *dma_address; };
struct ttm_resource { unsigned mem_type; };
struct amdgpu_bo { unsigned flags; bool always_valid; u64 *mapping;
    struct { struct drm_gem_object base; struct ttm_resource *resource; struct ttm_tt *ttm; void *bdev; } tbo; };
struct amdgpu_bo_vm { struct amdgpu_bo bo; };
struct amdgpu_bo_va_mapping { struct list_head list; u64 start, last, offset, flags; bool freed; };
struct amdgpu_vm;
struct amdgpu_bo_va { struct { struct amdgpu_bo *bo; struct amdgpu_vm *vm; bool moved; } base; bool cleared, is_xgmi; struct list_head invalids, valids; unsigned id, done; struct dma_fence *last_pt_update; };
struct amdgpu_device { struct { u64 vram_base_offset; } vm_manager;
    struct { struct { unsigned num_physical_nodes; } xgmi; bool override_pte; } gmc;
    struct reset_domain { int sem; } *reset_domain;
};
struct amdgpu_sync { bool initialized; };
struct amdgpu_vm_update_params;
struct amdgpu_vm_update_funcs {
    int (*prepare)(struct amdgpu_vm_update_params *, struct amdgpu_sync *, u64);
    int (*update)(struct amdgpu_vm_update_params *, struct amdgpu_bo_vm *, u64, u64, unsigned, uint32_t, u64);
    int (*commit)(struct amdgpu_vm_update_params *, struct dma_fence **);
};
struct amdgpu_vm {
    bool use_cpu_for_update, evicting, need_tlb_fence;
    u64 tlb_seq;
    struct { struct amdgpu_bo *bo; } root;
    struct dma_fence *last_unlocked, *last_seq_flush, *last_tlb_inv, *last_update;
    const struct amdgpu_vm_update_funcs *update_funcs;
    struct list_head freed, freed_explicit;
};
struct amdgpu_vm_update_params {
    struct amdgpu_device *adev;
    struct amdgpu_vm *vm;
    bool immediate, unlocked, needs_flush, override_pte;
    dma_addr_t *pages_addr;
    struct list_head tlb_flush_waitlist;
};
struct amdgpu_vm_tlb_seq_struct { struct amdgpu_vm *vm; struct dma_fence_cb cb; };
struct amdgpu_vm_update_batch { struct amdgpu_vm_update_params params; struct amdgpu_vm_tlb_seq_struct *tlb_cb; int idx; };
struct xarray { unsigned count; struct amdgpu_bo_va *entries[4]; };
#define xa_for_each(xa, index, entry) \
    for ((index) = 0; (index) < (xa)->count && ((entry) = (xa)->entries[index]); (index)++)
struct amdgpu_res_cursor { u64 start, size, remaining; };

static unsigned cases, live_allocations, prepares, commits, sync_waits, resv_waits, barriers, hdp_flushes;
static unsigned pte_writes, pte_calls, freed_mappings, tlb_flushes, resv_adds, pt_frees, begin_calls, end_calls;
static bool device_present, reset_running, eviction_locked, sync_done, barrier_done, hdp_done, fail_alloc;
static int sync_error, resv_error, commit_error, resv_collect_error, kmap_error;
static unsigned fail_pte_call, kfd_waits, resv_collects;
static enum amdgpu_sync_mode collected_modes[32];
static struct dma_fence stub, old_fence, tlb_fence;
static struct dma_resv reservation;
static struct reset_domain reset_domain;
static struct amdgpu_device device;
static struct amdgpu_vm vm;
static struct amdgpu_bo_vm page_table;
static struct amdgpu_bo_va_mapping retired[3];
static u64 entries[32];

static void *allocate(size_t bytes)
{ if (fail_alloc) return NULL; void *p = malloc(bytes); assert(p); ++live_allocations; return p; }
static void release(void *p) { if (p) { assert(live_allocations); --live_allocations; free(p); } }
#define kmalloc_obj(obj) allocate(sizeof(obj))
#define kfree release
static struct dma_fence *dma_fence_get(struct dma_fence *f) { if (f) ++f->refs; return f; }
static void dma_fence_put(struct dma_fence *f) { if (f) { assert(f->refs); --f->refs; } }
static struct dma_fence *dma_fence_get_stub(void) { return dma_fence_get(&stub); }
static bool dma_fence_is_signaled(struct dma_fence *f) { return !f || f->signaled; }
static int dma_fence_add_callback(struct dma_fence *f, struct dma_fence_cb *cb,
                                  void (*fn)(struct dma_fence *, struct dma_fence_cb *))
{ if (f->signaled) return -ENOENT; assert(!f->cb); cb->fn = fn; f->cb = cb; return 0; }
static void signal_fence(struct dma_fence *f)
{ f->signaled = true; if (f->cb) { struct dma_fence_cb *cb = f->cb; f->cb = NULL; cb->fn(f, cb); } }
static int amdgpu_vm_tlb_fence_create(struct amdgpu_device *adev, struct amdgpu_vm *v,
                                      struct dma_fence *dependency, struct dma_fence **out)
{
    assert(barrier_done && hdp_done && dependency && dependency->signaled);
    ++tlb_flushes;
    dma_fence_put(*out);
    tlb_fence.signaled = false;
    *out = dma_fence_get(&tlb_fence);
    return 0;
}
static void dma_resv_add_fence(struct dma_resv *resv, struct dma_fence *f, unsigned usage)
{ assert(resv == &reservation && f == &tlb_fence && usage == DMA_RESV_USAGE_BOOKKEEP); ++resv_adds; }
static void *adev_to_drm(struct amdgpu_device *adev) { return adev; }
static bool drm_dev_enter(void *drm, int *idx) { ++begin_calls; *idx = 1; return device_present; }
static void drm_dev_exit(int idx) { assert(idx == 1); ++end_calls; }
static unsigned amdgpu_ip_version(struct amdgpu_device *adev, unsigned hwip, unsigned instance)
{ return IP_VERSION(12, 0, 0); }
static void amdgpu_vm_eviction_lock(struct amdgpu_vm *v) { assert(!eviction_locked); eviction_locked = true; }
static void amdgpu_vm_eviction_unlock(struct amdgpu_vm *v) { assert(eviction_locked); eviction_locked = false; }
static void amdgpu_bo_fence(struct amdgpu_bo *bo, struct dma_fence *f, bool write) { assert(write); }
static void amdgpu_vm_pt_free_list(struct amdgpu_device *adev, struct amdgpu_vm_update_params *p)
{ assert(barrier_done && hdp_done); if (p->needs_flush && vm.need_tlb_fence && !p->unlocked) assert(resv_adds); ++pt_frees; }
static void amdgpu_sync_create(struct amdgpu_sync *sync) { sync->initialized = true; }
static void amdgpu_sync_free(struct amdgpu_sync *sync) { assert(sync->initialized); sync->initialized = false; }
static int amdgpu_sync_resv(struct amdgpu_device *adev, struct amdgpu_sync *sync,
                            struct dma_resv *resv, enum amdgpu_sync_mode mode, struct amdgpu_vm *v)
{ assert(sync->initialized && resv == &reservation && resv_collects < ARRAY_SIZE(collected_modes)); collected_modes[resv_collects++] = mode; return resv_collect_error; }
static int amdgpu_sync_kfd(struct amdgpu_sync *sync, struct dma_resv *resv)
{ assert(sync->initialized && resv == &reservation); ++kfd_waits; return 0; }
static int amdgpu_sync_wait(struct amdgpu_sync *sync, bool interruptible)
{ assert(sync->initialized && interruptible); ++sync_waits; if (sync_error) return sync_error; sync_done = true; return 0; }
static long dma_resv_wait_timeout(struct dma_resv *resv, unsigned usage, bool intr, long timeout)
{ assert(resv == &reservation && usage == DMA_RESV_USAGE_KERNEL && intr); ++resv_waits; return resv_error ? resv_error : 1; }
static int amdgpu_bo_kmap(struct amdgpu_bo *bo, void **ptr) { return kmap_error; }
static void *amdgpu_bo_kptr(struct amdgpu_bo *bo) { return bo->mapping; }
static u64 amdgpu_vm_map_gart(dma_addr_t *pages, u64 address)
{ return pages[address >> PAGE_SHIFT] + (address & (PAGE_SIZE - 1)); }
static void amdgpu_gmc_override_vm_pte_flags(struct amdgpu_device *adev, struct amdgpu_vm *v, u64 address, u64 *flags)
{ *flags |= 0x80; }
static void amdgpu_gmc_set_pte_pde(struct amdgpu_device *adev, void *base, unsigned index, u64 value, u64 flags)
{ assert(sync_done && resv_waits); barrier_done = hdp_done = false; ((u64 *)base)[index] = value | flags; ++pte_writes; }
static void memory_barrier(void) { barrier_done = true; ++barriers; }
#define mb memory_barrier
static bool down_read_trylock(int *sem) { return !reset_running; }
static void up_read(int *sem) {}
static void amdgpu_device_flush_hdp(struct amdgpu_device *adev, void *ring)
{ assert(barrier_done); hdp_done = true; ++hdp_flushes; }
static struct amdgpu_bo *gem_to_amdgpu_bo(struct drm_gem_object *obj)
{ return container_of(obj, struct amdgpu_bo, tbo.base); }
static u64 amdgpu_ttm_tt_pte_flags(struct amdgpu_device *adev, struct ttm_tt *tt, struct ttm_resource *res)
{ return res ? AMDGPU_PTE_VALID | AMDGPU_PTE_READABLE | AMDGPU_PTE_WRITEABLE : 0; }
static bool amdgpu_bo_encrypted(struct amdgpu_bo *bo) { return false; }
static struct amdgpu_device *amdgpu_ttm_adev(void *bdev) { return &device; }
static void amdgpu_gmc_get_vm_pte(struct amdgpu_device *adev, struct amdgpu_vm *v,
                                  struct amdgpu_bo *bo, u64 map_flags, u64 *pte_flags)
{ if (map_flags & AMDGPU_VM_PAGE_PRT) *pte_flags |= AMDGPU_PTE_PRT_FLAG(adev); }
static bool drm_gem_is_imported(struct drm_gem_object *obj) { return obj->imported; }
static bool amdgpu_vm_is_bo_always_valid(struct amdgpu_vm *v, struct amdgpu_bo *bo)
{ return bo && bo->always_valid; }
static void amdgpu_res_first(struct ttm_resource *res, u64 offset, u64 size, struct amdgpu_res_cursor *cursor)
{ *cursor = (struct amdgpu_res_cursor){ offset, size, size }; }
static void amdgpu_res_next(struct amdgpu_res_cursor *cursor, u64 bytes)
{ assert(bytes <= cursor->remaining); cursor->remaining -= bytes; cursor->start += bytes; cursor->size = cursor->remaining; }
static int amdgpu_vm_ptes_update(struct amdgpu_vm_update_params *p, u64 start, u64 end, u64 address, u64 flags)
{
    assert(eviction_locked && end <= ARRAY_SIZE(entries));
    if (++pte_calls == fail_pte_call) return -ENOMEM;
    return p->vm->update_funcs->update(p, &page_table, start * 8, address, end - start,
                                      AMDGPU_GPU_PAGE_SIZE, flags);
}
static void amdgpu_vm_free_mapping(struct amdgpu_device *adev, struct amdgpu_vm *v,
                                   struct amdgpu_bo_va_mapping *mapping, struct dma_fence *f)
{ assert(hdp_done && f && f->signaled && !mapping->freed); mapping->freed = true; ++freed_mappings; }

static void amdgpu_vm_bo_update_done(struct amdgpu_bo_va *va, bool clear)
{
    assert(hdp_done || list_empty(&va->invalids));
    va->base.moved = false;
    va->cleared = clear;
    list_splice_init(&va->invalids, &va->valids);
    ++va->done;
}

ACTUAL_CPU

static int counted_prepare(struct amdgpu_vm_update_params *p, struct amdgpu_sync *sync, u64 id)
{ ++prepares; return amdgpu_vm_cpu_prepare(p, sync, id); }
static int counted_commit(struct amdgpu_vm_update_params *p, struct dma_fence **f)
{ ++commits; return commit_error ? commit_error : amdgpu_vm_cpu_commit(p, f); }
static const struct amdgpu_vm_update_funcs funcs = {
    .prepare = counted_prepare, .update = amdgpu_vm_cpu_update, .commit = counted_commit,
};

ACTUAL_VM

static void init(void)
{
    assert(!live_allocations && !eviction_locked);
    prepares = commits = sync_waits = resv_waits = barriers = hdp_flushes = pte_writes = pte_calls = 0;
    freed_mappings = tlb_flushes = resv_adds = pt_frees = begin_calls = end_calls = 0;
    sync_error = resv_error = commit_error = resv_collect_error = kmap_error = 0;
    fail_pte_call = kfd_waits = resv_collects = 0;
    device_present = true;
    reset_running = sync_done = barrier_done = hdp_done = fail_alloc = false;
    stub = (struct dma_fence){.signaled = true};
    old_fence = (struct dma_fence){.signaled = true};
    tlb_fence = (struct dma_fence){0};
    device = (struct amdgpu_device){.reset_domain = &reset_domain, .vm_manager.vram_base_offset = 0x20000};
    page_table = (struct amdgpu_bo_vm){.bo = {.mapping = entries, .tbo.base.resv = &reservation}};
    vm = (struct amdgpu_vm){.use_cpu_for_update = true, .root.bo = &page_table.bo,
                           .last_unlocked = dma_fence_get_stub(), .last_seq_flush = dma_fence_get_stub(),
                           .last_tlb_inv = dma_fence_get_stub(), .last_update = dma_fence_get(&old_fence), .update_funcs = &funcs};
    INIT_LIST_HEAD(&vm.freed);
    INIT_LIST_HEAD(&vm.freed_explicit);
    for (unsigned i = 0; i < ARRAY_SIZE(entries); ++i) entries[i] = 0xdead;
    for (unsigned i = 0; i < ARRAY_SIZE(retired); ++i) {
        retired[i] = (struct amdgpu_bo_va_mapping){.start = i * 4, .last = i * 4 + 1};
        INIT_LIST_HEAD(&retired[i].list);
    }
}
static void finish(void)
{
    dma_fence_put(vm.last_unlocked);
    dma_fence_put(vm.last_seq_flush);
    dma_fence_put(vm.last_tlb_inv);
    dma_fence_put(vm.last_update);
    assert(!stub.refs && !old_fence.refs && !tlb_fence.refs && !live_allocations && !eviction_locked);
    ++cases;
}
static void range_mapping(void)
{
    for (unsigned paged = 0; paged < 2; ++paged) {
        init();
        struct amdgpu_sync sync = {.initialized = true};
        struct dma_fence *f = NULL;
        dma_addr_t pages[] = {0x30000, 0x50000, 0x70000};
        assert(!amdgpu_vm_update_range(&device, &vm, false, false, false, true,
                                       &sync, 2, 4, AMDGPU_PTE_VALID, 0, 0x10000,
                                       NULL, paged ? pages : NULL, &f));
        assert(pte_writes == 3 && sync_waits == 1 && prepares == 1 && commits == 1 && hdp_flushes == 1);
        assert(entries[2] == (paged ? 0x30001u : 0x10001u));
        assert(entries[3] == (paged ? 0x50001u : 0x11001u));
        assert(entries[4] == (paged ? 0x70001u : 0x12001u));
        assert(f == &stub && begin_calls == 1 && end_calls == 1);
        dma_fence_put(f);
        finish();
    }
}
static void clear_batches(void)
{
    for (unsigned tlb = 0; tlb < 2; ++tlb) {
        init();
        vm.need_tlb_fence = tlb;
        LIST_HEAD(freed);
        for (unsigned i = 0; i < 3; ++i) list_add_tail(&retired[i].list, &freed);
        struct dma_fence *f = dma_fence_get(&old_fence);
        assert(!amdgpu_vm_clear_freed_list(&device, &vm, &freed, AMDGPU_SYNC_EXPLICIT, &f));
        assert(list_empty(&freed) && freed_mappings == 3 && pte_writes == 6);
        assert(prepares == 1 && sync_waits == 1 && commits == 1 && hdp_flushes == 1);
        assert(begin_calls == 1 && end_calls == 1 && pt_frees == 1);
        assert(tlb_flushes == tlb && resv_adds == tlb && f == &stub);
        if (tlb) assert(vm.last_tlb_inv == &tlb_fence && !tlb_fence.signaled);
        for (unsigned i = 0; i < 3; ++i) {
            assert(!entries[i * 4] && entries[i * 4 + 1] == PAGE_SIZE);
            assert(entries[i * 4 + 2] == 0xdead);
        }
        dma_fence_put(f);
        finish();
    }
}
static void clear_failures(void)
{
    for (unsigned failure = 0; failure < 7; ++failure) {
        init();
        LIST_HEAD(freed);
        for (unsigned i = 0; i < 3; ++i) list_add_tail(&retired[i].list, &freed);
        struct dma_fence *f = NULL;
        int expected;
        switch (failure) {
        case 0: fail_pte_call = 2; expected = -ENOMEM; break;
        case 1: commit_error = -EIO; expected = -EIO; break;
        case 2: sync_error = -EINTR; expected = -EINTR; break;
        case 3: resv_collect_error = -ENOMEM; expected = -ENOMEM; break;
        case 4: vm.evicting = true; expected = -EBUSY; break;
        case 5: fail_alloc = true; expected = -ENOMEM; break;
        default: device_present = false; expected = -ENODEV; break;
        }
        assert(amdgpu_vm_clear_freed_list(&device, &vm, &freed, AMDGPU_SYNC_EQ_OWNER, &f) == expected);
        if (!failure) {
            assert(commits == 1 && hdp_flushes == 1 && pte_writes == 2 && freed_mappings == 1);
            assert(list_count(&freed) == 2 && f == &stub);
        } else if (failure == 1) {
            assert(commits == 1 && !hdp_flushes && pte_writes == 6 && !freed_mappings);
            assert(list_count(&freed) == 3 && !f);
        } else {
            assert(!commits && !pte_writes && !freed_mappings && list_count(&freed) == 3 && !f);
        }
        dma_fence_put(f);
        finish();
    }
    init();
    LIST_HEAD(empty);
    struct dma_fence *f = NULL;
    assert(!amdgpu_vm_clear_freed_list(&device, &vm, &empty, AMDGPU_SYNC_EQ_OWNER, &f));
    assert(!prepares && !commits && !begin_calls && !f);
    finish();
}
static void shared_eligibility(void)
{
    for (unsigned cpu = 0; cpu < 2; ++cpu) {
        for (unsigned type = 0; type < 4; ++type) {
            init();
            vm.use_cpu_for_update = cpu;
            struct amdgpu_bo bo = {.always_valid = type != 2, .tbo.base.imported = type == 3};
            struct amdgpu_bo_va va = {.base.bo = type ? &bo : NULL};
            INIT_LIST_HEAD(&va.invalids);
            INIT_LIST_HEAD(&va.valids);
            assert(!amdgpu_vm_bo_can_batch(&vm, &va));
            va.base.moved = true;
            assert(amdgpu_vm_bo_can_batch(&vm, &va) == (type < 2));
            va.base.moved = false;
            va.cleared = true;
            assert(amdgpu_vm_bo_can_batch(&vm, &va) == (type < 2));
            va.cleared = false;
            struct list_head node;
            list_add_tail(&node, &va.invalids);
            assert(amdgpu_vm_bo_can_batch(&vm, &va) == (type < 2));
            finish();
        }
    }
}
static int run_bo_batch(struct xarray *bos)
{ return BO_BATCH_CALL; }
static void shared_bo_updates(void)
{
    for (unsigned failure = 0; failure < 4; ++failure) {
        init();
        struct ttm_resource resource = {.mem_type = TTM_PL_VRAM};
        struct amdgpu_bo bo = {.always_valid = true, .tbo.base.resv = &reservation, .tbo.resource = &resource};
        struct amdgpu_bo_va private = {.base = {.bo = &bo, .vm = &vm, .moved = true}, .id = 1};
        struct amdgpu_bo_va prt = {.base.vm = &vm, .cleared = true,
                                   .last_pt_update = dma_fence_get(&old_fence), .id = 2};
        INIT_LIST_HEAD(&private.invalids);
        INIT_LIST_HEAD(&private.valids);
        INIT_LIST_HEAD(&prt.invalids);
        INIT_LIST_HEAD(&prt.valids);
        retired[0].flags = AMDGPU_VM_PAGE_READABLE | AMDGPU_VM_PAGE_WRITEABLE;
        retired[1].flags = AMDGPU_VM_PAGE_PRT;
        list_add_tail(&retired[0].list, &private.valids);
        list_add_tail(&retired[1].list, &prt.valids);
        struct xarray bos = {.count = 2, .entries = {&private, &prt}};
        if (failure == 1) fail_pte_call = 2;
        if (failure == 2) commit_error = -EIO;
        if (failure == 3) sync_error = -EINTR;
        int r = run_bo_batch(&bos);
        assert(r == (failure == 1 ? -ENOMEM : failure == 2 ? -EIO : failure == 3 ? -EINTR : 0));
        if (failure == 3) {
            assert(!pte_calls && !commits && !pte_writes);
            assert(vm.last_update == &old_fence && prt.last_pt_update == &old_fence);
        } else {
            assert(pte_calls == 2 && prepares == 1 && sync_waits == 1 && commits == 1);
            assert(hdp_flushes == (failure != 2));
            assert(pte_writes == (failure == 1 ? 2 : 4));
            if (failure != 2) assert(vm.last_update == &stub && prt.last_pt_update == &stub);
        }
        assert(private.done == !failure && prt.done == !failure && resv_collects == 1);
        if (failure) assert(private.base.moved && prt.cleared);
        dma_fence_put(prt.last_pt_update);
        finish();
    }
}
static void per_bo_mapping_batches(void)
{
    for (unsigned clear = 0; clear < 2; ++clear) {
        for (unsigned moved = 0; moved < 2; ++moved) {
            for (unsigned individual = 0; individual < 2; ++individual) {
                for (unsigned failure = 0; failure < 4; ++failure) {
                    init();
                    vm.need_tlb_fence = true;
                    struct ttm_resource resource = {.mem_type = TTM_PL_VRAM};
                    struct amdgpu_bo bo = {.always_valid = !individual,
                                           .tbo.base.resv = &reservation, .tbo.resource = &resource};
                    struct amdgpu_bo_va va = {.base = {.bo = &bo, .vm = &vm, .moved = moved},
                                              .last_pt_update = dma_fence_get(&old_fence)};
                    INIT_LIST_HEAD(&va.invalids);
                    INIT_LIST_HEAD(&va.valids);
                    for (unsigned i = 0; i < 3; ++i) {
                        retired[i].offset = i * 2 * PAGE_SIZE;
                        retired[i].flags = i == 0 ? AMDGPU_VM_PAGE_READABLE | AMDGPU_VM_PAGE_WRITEABLE
                            : i == 1 ? AMDGPU_VM_PAGE_READABLE : AMDGPU_VM_PAGE_WRITEABLE;
                        list_add_tail(&retired[i].list, clear || moved ? &va.valids : &va.invalids);
                    }
                    if (failure == 1) fail_pte_call = 2;
                    if (failure == 2) commit_error = -EIO;
                    if (failure == 3) sync_error = -EINTR;
                    int r = amdgpu_vm_bo_update(&device, &va, clear);
                    assert(r == (failure == 1 ? -ENOMEM : failure == 2 ? -EIO : failure == 3 ? -EINTR : 0));
                    assert(prepares == 1 && sync_waits == 1 && resv_collects == 1 && kfd_waits == clear);
                    assert(commits == (failure != 3));
                    assert(pte_writes == (failure == 1 ? 2 : failure == 3 ? 0 : 6));
                    assert(hdp_flushes == (failure == 0 || failure == 1));
                    assert(va.done == !failure && list_count(&va.invalids) == (failure ? 3 : 0));
                    assert(list_count(&va.valids) == (failure ? 0 : 3));
                    if (!failure) {
                        for (unsigned i = 0; i < 3; ++i) {
                            u64 flags = i == 0 ? 13 : i == 1 ? 5 : 9;
                            assert(entries[i * 4] == (clear ? 0 : 0x20000 + retired[i].offset + flags));
                            assert(entries[i * 4 + 2] == 0xdead);
                        }
                        assert(va.cleared == clear && !va.base.moved);
                    }
                    if (failure == 0 || failure == 1) {
                        assert((clear || !individual ? vm.last_update : va.last_pt_update) == &stub);
                        assert(tlb_flushes == (clear || moved));
                        if (clear || moved) assert(!vm.last_tlb_inv->signaled);
                    }
                    dma_fence_put(va.last_pt_update);
                    finish();
                }
            }
        }
    }
    init();
    struct amdgpu_bo_va va = {.base.vm = &vm};
    INIT_LIST_HEAD(&va.invalids);
    INIT_LIST_HEAD(&va.valids);
    assert(!amdgpu_vm_bo_update(&device, &va, false));
    assert(!prepares && !commits && !pte_writes && va.done == 1);
    finish();
}

static void mixed_retirement_batches(void)
{
    for (unsigned tlb = 0; tlb < 2; ++tlb) {
        for (unsigned failure = 0; failure < 7; ++failure) {
            init();
            vm.need_tlb_fence = tlb;
            list_add_tail(&retired[0].list, &vm.freed);
            list_add_tail(&retired[1].list, &vm.freed_explicit);
            switch (failure) {
            case 1: fail_pte_call = 1; break;
            case 2: fail_pte_call = 2; break;
            case 3: commit_error = -EIO; break;
            case 4: sync_error = -EINTR; break;
            case 5: vm.evicting = true; break;
            case 6: resv_collect_error = -ENOMEM; break;
            }
            int r = amdgpu_vm_clear_freed(&device, &vm, &vm.last_update);
            assert(!!r == !!failure);
            assert(collected_modes[0] == AMDGPU_SYNC_EQ_OWNER);
            if (!failure || failure == 2) {
                assert(collected_modes[1] == AMDGPU_SYNC_EXPLICIT_UNMAP);
                assert(list_empty(&vm.freed));
                assert(list_count(&vm.freed_explicit) == !!failure);
            } else {
                assert(resv_collects == 1);
                assert(list_count(&vm.freed) == 1 && list_count(&vm.freed_explicit) == 1);
            }
            if (failure) {
                /* Retrying must neither lose retirements nor their sync mode. */
                unsigned previous_collects = resv_collects;
                fail_pte_call = commit_error = resv_collect_error = sync_error = 0;
                reset_running = vm.evicting = false;
                assert(!amdgpu_vm_clear_freed(&device, &vm, &vm.last_update));
                assert(collected_modes[previous_collects] ==
                       (failure == 2 ? AMDGPU_SYNC_EXPLICIT_UNMAP : AMDGPU_SYNC_EQ_OWNER));
                assert(collected_modes[resv_collects - 1] == AMDGPU_SYNC_EXPLICIT_UNMAP);
            }
            assert(list_empty(&vm.freed) && list_empty(&vm.freed_explicit));
            assert(freed_mappings == 2 && retired[0].freed && retired[1].freed);
            assert(!entries[0] && !entries[4]);
            assert(vm.last_update == &stub && tlb_flushes >= 2u * tlb);
            finish();
        }
    }
    for (unsigned failure = 0; failure < 2; ++failure) {
        init();
        /* A reused VA cannot be populated until both retirement lists clear. */
        struct ttm_resource resource = {.mem_type = TTM_PL_VRAM};
        struct amdgpu_bo bo = {.always_valid = true,
                               .tbo.base.resv = &reservation, .tbo.resource = &resource};
        struct amdgpu_bo_va va = {.base = {.bo = &bo, .vm = &vm}};
        INIT_LIST_HEAD(&va.invalids); INIT_LIST_HEAD(&va.valids);
        retired[1].start = retired[0].start;
        retired[1].last = retired[0].last;
        retired[1].flags = AMDGPU_VM_PAGE_READABLE | AMDGPU_VM_PAGE_WRITEABLE;
        list_add_tail(&retired[0].list, &vm.freed_explicit);
        list_add_tail(&retired[1].list, &va.invalids);
        if (failure) {
            commit_error = -EIO;
            assert(amdgpu_vm_bo_update(&device, &va, false) == -EIO);
            assert(!va.done && list_count(&va.invalids) == 1 && list_count(&vm.freed_explicit) == 1);
            commit_error = 0;
        }
        assert(!amdgpu_vm_bo_update(&device, &va, false));
        assert(list_empty(&vm.freed_explicit) && retired[0].freed && va.done == 1);
        assert(collected_modes[0] == AMDGPU_SYNC_EXPLICIT_UNMAP);
        assert(entries[0] == 0x2000d && entries[1] == 0x2100d);
        finish();
    }
}

int main(void)
{
    range_mapping();
    clear_batches();
    clear_failures();
    shared_eligibility();
    shared_bo_updates();
    per_bo_mapping_batches();
    mixed_retirement_batches();
    printf("CPU VM batching: %u scenarios passed\n", cases);
    return 0;
}
'''

if __name__ == '__main__':
    unittest.main()
