#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the driver's VM validation order with a bounded placement budget."""
import os
import pathlib
import re
import shlex
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(os.environ.get('AMDGPU_TEST_ROOT', pathlib.Path(__file__).resolve().parents[5]))
AMD = ROOT / 'drivers/gpu/drm/amd/amdgpu'


def function(source, name):
    return re.search(r'^(?:static )?(?:int|void) ' + name + r'\(.*?^\}', source, re.M | re.S).group()


def run(program, sources=()):
    with tempfile.TemporaryDirectory(prefix='bo-priority-') as temporary:
        path = pathlib.Path(temporary)
        (path / 'test.c').write_text(program)
        flags = shlex.split(os.environ.get('CC', 'cc'))
        flags += ['-std=gnu11', '-O1', '-g', '-Wall', '-Werror', '-I' + str(ROOT / 'tools/include')]
        if os.environ.get('USERQ_TEST_SANITIZERS'):
            flags += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-fno-sanitize-recover=all']
        subprocess.run(flags + [str(path / 'test.c'), *sources, '-o', str(path / 'test')], check=True, timeout=30)
        subprocess.run([str(path / 'test')], check=True, timeout=30)


class BoPriorityTests(unittest.TestCase):
    def test_native_bo_list_priority(self):
        source = (AMD / 'amdgpu_bo_list.c').read_text()
        comparator = function(source, 'amdgpu_bo_list_entry_cmp')
        run(NATIVE.replace('ACTUAL_SOURCE', comparator))

    def test_evicted_placement_order(self):
        source = (AMD / 'amdgpu_vm.c').read_text()
        functions = '\n'.join(function(source, name) for name in (
            'amdgpu_vm_bo_evicted', 'amdgpu_vm_bo_needs_update',
            'amdgpu_vm_evicted_priority_cmp', 'amdgpu_vm_validate'))
        run(VM.replace('ACTUAL_SOURCE', functions), [str(ROOT / 'tools/lib/list_sort.c')])


NATIVE = r'''
#include <assert.h>
#include <limits.h>
#include <stdlib.h>
#define BUILD_BUG_ON(condition) _Static_assert(!(condition), #condition)
#define AMDGPU_BO_LIST_MAX_PRIORITY 32u
struct amdgpu_bo_list_entry { unsigned priority; };
ACTUAL_SOURCE
int main(void)
{
    struct amdgpu_bo_list_entry bos[] = {{0}, {14}, {32}, {14}, {1}};
    const unsigned expected[] = {32, 14, 14, 1, 0};
    assert(!amdgpu_bo_list_entry_cmp(&bos[1], &bos[3]));
    qsort(bos, 5, sizeof(*bos), amdgpu_bo_list_entry_cmp);
    for (unsigned i = 0; i < 5; i++)
        assert(bos[i].priority == expected[i]);
}
'''


VM = r'''
#include <assert.h>
#include <errno.h>
#include <linux/list.h>
#include <linux/list_sort.h>

struct amdgpu_device { int unused; };
struct ww_acquire_ctx { int unused; };
struct amdgpu_bo {
    int vm_priority_offset, id;
    struct { struct { struct ww_acquire_ctx *resv; } base; } tbo;
};
struct amdgpu_vm_bo_status { struct list_head evicted, needs_update, idle; };
struct amdgpu_vm {
    struct amdgpu_vm_bo_status kernel, always_valid, individual;
    struct { struct amdgpu_bo *bo; } root;
    struct { void (*map_table)(struct amdgpu_bo *bo); } *update_funcs;
    uint64_t generation;
    bool sort_evicted, evicting, individual_lock;
};
struct amdgpu_vm_bo_base {
    struct list_head vm_status;
    struct amdgpu_vm_bo_status *lists;
    struct amdgpu_vm *vm;
    struct amdgpu_bo *bo;
    bool moved;
};
#define amdgpu_vm_bo_lock_lists(base) ((base)->lists)
#define amdgpu_vm_bo_unlock_lists(base) ((void)0)
#define dma_resv_assert_held(resv) ((void)0)
#define amdgpu_vm_generation(adev, vm) 0ULL
#define amdgpu_vm_bo_reset_state_machine(vm) assert(0)
#define amdgpu_vm_fini_entities(vm) assert(0)
#define amdgpu_vm_init_entities(adev, vm) 0
#define to_amdgpu_bo_vm(bo) (bo)
#define amdgpu_vm_pt_clear(adev, vm, bo, clear) 0
#define scoped_guard(...) for (int guard = 1; guard; guard = 0)
#define spin_lock(lock) do { assert(!*(lock)); *(lock) = true; } while (0)
#define spin_unlock(lock) do { assert(*(lock)); *(lock) = false; } while (0)
#define dma_resv_locking_ctx(resv) (resv)
ACTUAL_SOURCE
struct budget {
    unsigned used, capacity;
    int ids[16];
};
static int validate(void *data, struct amdgpu_bo *bo)
{
    struct budget *b = data;
    if (b->used == b->capacity)
        return -ENOMEM;
    b->ids[b->used++] = bo->id;
    return 0;
}
static void map_table(struct amdgpu_bo *bo) { assert(bo->id == 0); }

int main(void)
{
    struct amdgpu_vm vm = {0};
    typeof(*vm.update_funcs) updates = {map_table};
    struct ww_acquire_ctx ticket;
    struct amdgpu_vm_bo_status *groups[] = {&vm.kernel, &vm.always_valid, &vm.individual};
    struct amdgpu_bo bos[8] = {0};
    struct amdgpu_vm_bo_base nodes[8] = {0};
    struct budget b = {.capacity = 3};
    vm.update_funcs = &updates;
    for (unsigned i = 0; i < 3; ++i) {
        INIT_LIST_HEAD(&groups[i]->evicted);
        INIT_LIST_HEAD(&groups[i]->needs_update);
        INIT_LIST_HEAD(&groups[i]->idle);
    }
    for (unsigned i = 0; i < 8; ++i) {
        bos[i].id = i;
        bos[i].tbo.base.resv = &ticket;
        nodes[i].bo = &bos[i];
        nodes[i].vm = &vm;
        nodes[i].lists = i == 0 ? &vm.kernel : i == 7 ? &vm.individual : &vm.always_valid;
        INIT_LIST_HEAD(&nodes[i].vm_status);
        list_move(&nodes[i].vm_status, &nodes[i].lists->idle);
    }
    /* Private 1, 2, 3, 4 move to the head in that order; 5 and 6 stay idle. */
    int priorities[] = {0, -14, 17, 17, 0};
    for (unsigned i = 0; i < 5; ++i) {
        bos[i].vm_priority_offset = priorities[i];
        amdgpu_vm_bo_evicted(&nodes[i]);
    }
    amdgpu_vm_bo_evicted(&nodes[7]);
    assert(vm.sort_evicted);
    assert(amdgpu_vm_validate(NULL, &vm, NULL, validate, &b) == -ENOMEM);
    assert(b.ids[0] == 0 && b.ids[1] == 3 && b.ids[2] == 2);
    assert(!vm.sort_evicted);
    assert(list_first_entry(&vm.always_valid.evicted, struct amdgpu_vm_bo_base, vm_status) == &nodes[4]);
    /* A new low-priority head must be sorted even after partial progress. */
    amdgpu_vm_bo_evicted(&nodes[1]);
    assert(vm.sort_evicted);
    b.capacity = 16;
    assert(!amdgpu_vm_validate(NULL, &vm, NULL, validate, &b));
    assert(b.used == 5 && b.ids[3] == 4 && b.ids[4] == 1);
    assert(!list_empty(&vm.individual.evicted));
    assert(!amdgpu_vm_validate(NULL, &vm, &ticket, validate, &b));
    assert(b.used == 6 && b.ids[5] == 7 && list_empty(&vm.individual.evicted));
    /* Ordinary GEM BOs keep their existing list order and skip sorting. */
    amdgpu_vm_bo_evicted(&nodes[5]);
    amdgpu_vm_bo_evicted(&nodes[6]);
    assert(!vm.sort_evicted);
    assert(!amdgpu_vm_validate(NULL, &vm, NULL, validate, &b));
    assert(b.used == 8 && b.ids[6] == 6 && b.ids[7] == 5);
}
'''

if __name__ == '__main__':
    unittest.main()
