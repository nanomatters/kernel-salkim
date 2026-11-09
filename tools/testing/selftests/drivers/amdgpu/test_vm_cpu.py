#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the production CPU page-table backend without GPU access."""
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


class VmCpuTests(unittest.TestCase):
    def test_cpu_page_tables_and_completion(self):
        source = (AMD / 'amdgpu_vm_cpu.c').read_text()
        functions = []
        for name in ('map_table', 'prepare', 'update', 'commit'):
            match = re.search(r'^static int amdgpu_vm_cpu_' + name + r'\(.*?^\}', source, re.M | re.S)
            self.assertIsNotNone(match, name)
            functions.append(match.group())
        command = shlex.split(os.environ.get('CC', 'cc'))
        command += ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-function']
        if os.environ.get('USERQ_TEST_SANITIZERS'):
            command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-fno-sanitize-recover=all']
        with tempfile.TemporaryDirectory(prefix='vm-cpu-') as temporary:
            path = pathlib.Path(temporary)
            program, binary = path / 'test.c', path / 'test'
            program.write_text(HARNESS.replace('ACTUAL_SOURCE', '\n'.join(functions)))
            subprocess.run(command + [str(program), '-o', str(binary)], check=True, timeout=30)
            subprocess.run([str(binary)], check=True, timeout=30)


HARNESS = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stddef.h>

typedef uint64_t u64;
typedef uint64_t dma_addr_t;
#define AMDGPU_GEM_CREATE_CPU_ACCESS_REQUIRED 1
#define DMA_RESV_USAGE_KERNEL 0
#define MAX_SCHEDULE_TIMEOUT 0x7fffffff
#define trace_amdgpu_vm_set_ptes(...) ((void)0)
#define atomic64_inc(p) (++*(p))
struct dma_fence { unsigned refs; bool signaled; };
struct dma_resv { unsigned unused; };
struct amdgpu_bo { unsigned flags; u64 *mapping; struct { struct { struct dma_resv *resv; } base; } tbo; };
struct amdgpu_bo_vm { struct amdgpu_bo bo; };
struct amdgpu_vm { u64 tlb_seq; };
struct amdgpu_sync { unsigned unused; };
struct amdgpu_device { struct reset_domain { int sem; } *reset_domain; };
struct amdgpu_vm_update_params {
    struct amdgpu_device *adev;
    struct amdgpu_vm *vm;
    bool immediate, needs_flush, override_pte;
    dma_addr_t *pages_addr;
};
static unsigned cases, sync_waits, resv_waits, writes, barriers, flushes, overrides;
static bool reset_running, sync_done, visible, publishing;
static int sync_error, resv_error, kmap_error;
static struct dma_fence stub, old;
static struct dma_resv reservation;
static struct amdgpu_device device;
static struct reset_domain reset_domain;
static struct amdgpu_vm vm;
static struct amdgpu_bo_vm table;
static u64 entries[8];
static struct dma_fence *dma_fence_get_stub(void)
{ assert(!publishing || (barriers && visible)); ++stub.refs; return &stub; }
static void dma_fence_put(struct dma_fence *f)
{ if (f) { assert(f->refs); --f->refs; } }
static int amdgpu_bo_kmap(struct amdgpu_bo *bo, void **mapping) { return kmap_error; }
static void *amdgpu_bo_kptr(struct amdgpu_bo *bo) { return bo->mapping; }
static int amdgpu_sync_wait(struct amdgpu_sync *sync, bool interruptible)
{ assert(interruptible && !writes); ++sync_waits; if (sync_error) return sync_error; sync_done = true; return 0; }
static long dma_resv_wait_timeout(struct dma_resv *resv, unsigned usage, bool intr, long timeout)
{ assert(resv == &reservation && usage == DMA_RESV_USAGE_KERNEL && intr); ++resv_waits; return resv_error ? resv_error : 1; }
static u64 amdgpu_vm_map_gart(dma_addr_t *pages, u64 address)
{ return pages[address >> 12] + (address & 4095); }
static void amdgpu_gmc_override_vm_pte_flags(struct amdgpu_device *adev, struct amdgpu_vm *v, u64 addr, u64 *flags)
{ *flags |= 0x80; ++overrides; }
static void amdgpu_gmc_set_pte_pde(struct amdgpu_device *adev, void *ptr, unsigned index, u64 value, u64 flags)
{ assert(sync_done && resv_waits && !visible); ((u64 *)ptr)[index] = value | flags; ++writes; }
static void memory_barrier(void) { ++barriers; }
#define mb memory_barrier
static bool down_read_trylock(int *sem) { return !reset_running; }
static void up_read(int *sem) {}
static void amdgpu_device_flush_hdp(struct amdgpu_device *adev, void *ring)
{ assert(barriers); visible = true; ++flushes; }

ACTUAL_SOURCE

static void init(void)
{
    assert(!stub.refs && !old.refs);
    sync_waits = resv_waits = writes = barriers = flushes = overrides = 0;
    sync_error = resv_error = kmap_error = 0;
    reset_running = sync_done = visible = publishing = false;
    device = (struct amdgpu_device){.reset_domain = &reset_domain};
    vm = (struct amdgpu_vm){0};
    table = (struct amdgpu_bo_vm){.bo = {.mapping = entries, .tbo.base.resv = &reservation}};
    stub.signaled = true;
    for (unsigned i = 0; i < 8; ++i) entries[i] = 0xdead;
}
static void waits_and_writes(void)
{
    for (unsigned failure = 0; failure < 3; ++failure) {
        init();
        struct amdgpu_vm_update_params p = {.adev = &device, .vm = &vm};
        struct amdgpu_sync sync;
        if (failure == 1) sync_error = -EINTR;
        if (failure == 2) resv_error = -EINTR;
        int r = amdgpu_vm_cpu_prepare(&p, &sync, 0);
        if (!r) r = amdgpu_vm_cpu_update(&p, &table, 8, 0x10000, 3, 4096, 1);
        assert(r == (failure ? -EINTR : 0));
        assert(writes == (failure ? 0 : 3));
        assert(entries[0] == 0xdead && entries[4] == 0xdead);
        if (!failure) assert(entries[1] == 0x10001 && entries[2] == 0x11001 && entries[3] == 0x12001);
        ++cases;
    }
    init();
    struct amdgpu_vm_update_params p = {.adev = &device, .vm = &vm};
    assert(!amdgpu_vm_cpu_prepare(&p, NULL, 0) && !sync_waits);
    assert(!amdgpu_vm_cpu_map_table(&table));
    assert(table.bo.flags & AMDGPU_GEM_CREATE_CPU_ACCESS_REQUIRED);
    kmap_error = -ENOMEM;
    assert(amdgpu_vm_cpu_map_table(&table) == -ENOMEM);
    ++cases;
}
static void gart_and_overrides(void)
{
    for (unsigned paged = 0; paged < 2; ++paged) {
        for (unsigned override = 0; override < 2; ++override) {
            init();
            dma_addr_t pages[] = {0x20000, 0x50000, 0x80000};
            struct amdgpu_vm_update_params p = {.adev = &device, .vm = &vm,
                .pages_addr = paged ? pages : NULL, .override_pte = override};
            struct amdgpu_sync sync;
            assert(!amdgpu_vm_cpu_prepare(&p, &sync, 0));
            assert(!amdgpu_vm_cpu_update(&p, &table, 0, 0, 3, 4096, 1));
            for (unsigned i = 0; i < 3; ++i)
                assert(entries[i] == ((paged ? pages[i] : i * 4096) | 1 | (override ? 0x80 : 0)));
            assert(overrides == (override ? (paged ? 3 : 1) : 0));
            ++cases;
        }
    }
}
static void completion_ordering(void)
{
    for (unsigned reset = 0; reset < 2; ++reset) {
        for (unsigned flush = 0; flush < 2; ++flush) {
            for (unsigned prior = 0; prior < 3; ++prior) {
                init();
                struct amdgpu_vm_update_params p = {.adev = &device, .vm = &vm, .needs_flush = flush};
                struct dma_fence *fence = NULL;
                if (prior) { old.refs = 1; old.signaled = prior == 1; fence = &old; }
                reset_running = reset;
                visible = reset;
                publishing = true;
                assert(!amdgpu_vm_cpu_commit(&p, &fence));
                publishing = false;
                assert(barriers == 1 && flushes == !reset && vm.tlb_seq == flush);
                assert(fence == &stub && fence->signaled && !old.refs);
                dma_fence_put(fence);
                ++cases;
            }
        }
    }
    init();
    struct amdgpu_vm_update_params p = {.adev = &device, .vm = &vm};
    publishing = true;
    assert(!amdgpu_vm_cpu_commit(&p, NULL));
    publishing = false;
    assert(barriers == 1 && flushes == 1 && !stub.refs);
    ++cases;
}
int main(void)
{
    waits_and_writes();
    gart_and_overrides();
    completion_ordering();
    printf("CPU VM backend: %u scenarios passed\n", cases);
    return 0;
}
'''

if __name__ == '__main__':
    unittest.main()
