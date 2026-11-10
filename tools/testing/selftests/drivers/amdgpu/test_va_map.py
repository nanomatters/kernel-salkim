#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import os
import pathlib
import re
import shlex
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[5]
AMD = ROOT / 'drivers/gpu/drm/amd/amdgpu'


class VaMapTests(unittest.TestCase):
    def test_preallocated_insertion_and_overlap(self):
        source = (AMD / 'amdgpu_vm.c').read_text()
        functions = []
        for name in ('amdgpu_vm_bo_insert_map', 'amdgpu_vm_verify_parameters',
                     'amdgpu_vm_bo_map_validate', 'amdgpu_vm_bo_map_prealloc'):
            function = re.search(r'^(?:static )?(?:void|int) ' + name + r'\(.*?^\}',
                                 source, re.M | re.S)
            self.assertIsNotNone(function, name)
            functions.append(function.group())
        flags = shlex.split(os.environ.get('CC', 'cc'))
        flags += ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                  '-Wno-unused-parameter']
        if os.environ.get('USERQ_TEST_SANITIZERS'):
            flags += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                      '-fno-sanitize-recover=all']
        with tempfile.TemporaryDirectory(prefix='va-map-test-') as temporary:
            directory = pathlib.Path(temporary)
            program, binary = directory / 'test.c', directory / 'test'
            program.write_text(HARNESS.replace('ACTUAL_FUNCTIONS', '\n'.join(functions)))
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

#define AMDGPU_GPU_PAGE_SIZE 4096ULL
#define AMDGPU_GPU_PAGE_SHIFT 12
#define AMDGPU_GPU_PAGE_MASK 4095ULL
#define check_add_overflow __builtin_add_overflow
#define amdgpu_vm_assert_locked(vm) assert(vm_locked)
#define AMDGPU_VM_PAGE_PRT (1u << 4)
#define container_of(p, t, member) ((t *)((char *)(p) - offsetof(t, member)))
struct list_head { struct list_head *next, *prev; };
#define LIST_HEAD(name) struct list_head name = {&name, &name}
#define INIT_LIST_HEAD(head) ((head)->next = (head)->prev = (head))
#define list_for_each_entry_safe(entry, tmp, head, member) \
    for (struct list_head *list_node = (head)->next, *list_next; \
         list_node != (head) && ((list_next = list_node->next), \
         (entry = container_of(list_node, typeof(*entry), member)), \
         (tmp = list_next != (head) ? container_of(list_next, typeof(*tmp), member) : NULL), true); list_node = list_next)
static void list_add(struct list_head *node, struct list_head *head)
{ node->next = head->next; node->prev = head; head->next->prev = node; head->next = node; }
static void list_del(struct list_head *node)
{ node->next->prev = node->prev; node->prev->next = node->next; }
static bool list_empty(const struct list_head *head) { return head->next == head; }
struct drm_gem_object { bool imported; };
struct amdgpu_bo {
    bool always_valid; uint64_t size;
    struct { struct drm_gem_object base; void *ttm; } tbo;
};
struct amdgpu_vm;
struct amdgpu_vm_bo_base { struct amdgpu_vm *vm; struct amdgpu_bo *bo; bool moved; unsigned updates; };
struct amdgpu_bo_va { struct amdgpu_vm_bo_base base; struct list_head invalids; };
struct amdgpu_bo_va_mapping {
    struct list_head list;
    uint64_t start, last, offset, flags;
    struct amdgpu_bo_va *bo_va;
};
struct interval_tree { unsigned count; struct amdgpu_bo_va_mapping *items[32]; };
struct amdgpu_vm { struct interval_tree va; };
struct amdgpu_device { struct { uint64_t max_pfn; } vm_manager; };
static bool vm_locked;
static unsigned live_allocations, allocations, prt_gets, traces, cases;
static struct amdgpu_device device;
static struct amdgpu_vm vm;
static struct amdgpu_bo bos[2];
static struct amdgpu_bo_va owners[3];
static struct list_head retired;
struct page { struct amdgpu_bo_va *owner; uint64_t offset, flags; };
static struct page expected[80];
static void *allocate(size_t size)
{ void *p = calloc(1, size); assert(p); allocations++; live_allocations++; return p; }
static void release(void *p) { assert(p && live_allocations); live_allocations--; free(p); }
#define kfree release
static struct amdgpu_bo_va_mapping *amdgpu_vm_it_iter_first(struct interval_tree *tree,
                                                            uint64_t start, uint64_t last)
{
    for (unsigned i = 0; i < tree->count; i++)
        if (tree->items[i]->start <= last && tree->items[i]->last >= start) return tree->items[i];
    return NULL;
}
static void amdgpu_vm_it_insert(struct amdgpu_bo_va_mapping *m, struct interval_tree *tree)
{
    assert(tree->count < 32 && m->bo_va && m->start <= m->last);
    unsigned i = 0;
    while (i < tree->count && tree->items[i]->start < m->start) i++;
    assert(!i || tree->items[i - 1]->last < m->start);
    assert(i == tree->count || m->last < tree->items[i]->start);
    memmove(&tree->items[i + 1], &tree->items[i], (tree->count - i) * sizeof(m));
    tree->items[i] = m; tree->count++;
}
static void amdgpu_vm_it_remove(struct amdgpu_bo_va_mapping *m, struct interval_tree *tree)
{
    unsigned i = 0;
    while (i < tree->count && tree->items[i] != m) i++;
    assert(i < tree->count);
    tree->count--;
    memmove(&tree->items[i], &tree->items[i + 1], (tree->count - i) * sizeof(m));
}
static uint64_t amdgpu_bo_size(struct amdgpu_bo *bo) { return bo->size; }
static void amdgpu_vm_prt_get(struct amdgpu_device *dev) { prt_gets++; }
static void amdgpu_vm_bo_needs_update(struct amdgpu_vm_bo_base *base) { base->updates++; }
static void trace_amdgpu_vm_bo_map(struct amdgpu_bo_va *owner, struct amdgpu_bo_va_mapping *m)
{ assert(owner && owner == m->bo_va); traces++; }


ACTUAL_FUNCTIONS

static void setup(void)
{
    assert(!live_allocations);
    memset(&vm, 0, sizeof(vm)); memset(bos, 0, sizeof(bos));
    memset(owners, 0, sizeof(owners)); memset(expected, 0, sizeof(expected));
    INIT_LIST_HEAD(&retired);
    for (unsigned i = 0; i < 3; i++) INIT_LIST_HEAD(&owners[i].invalids);
    for (unsigned i = 0; i < 2; i++) { owners[i].base.bo = &bos[i]; bos[i].always_valid = true; }
    allocations = prt_gets = traces = 0;
    vm_locked = true; device.vm_manager.max_pfn = 80;
    for (unsigned i = 0; i < 3; i++) owners[i].base.vm = &vm;
    for (unsigned i = 0; i < 2; i++) bos[i].size = 64 * AMDGPU_GPU_PAGE_SIZE;
}
static void verify_pages(void)
{
    for (unsigned i = 0; i < 80; i++) {
        struct amdgpu_bo_va_mapping *m = amdgpu_vm_it_iter_first(&vm.va, i, i);
        assert(!!m == !!expected[i].owner);
        if (m) {
            assert(m->bo_va == expected[i].owner && m->flags == expected[i].flags);
            assert(m->offset + (i - m->start) * AMDGPU_GPU_PAGE_SIZE == expected[i].offset);
        }
    }
}
static void map_range(unsigned owner, uint64_t start, uint64_t last, uint64_t offset, uint32_t flags)
{
    struct amdgpu_bo_va *bo_va = &owners[owner];
    uint64_t address = start * AMDGPU_GPU_PAGE_SIZE, size = (last - start + 1) * AMDGPU_GPU_PAGE_SIZE;
    assert(!amdgpu_vm_verify_parameters(&device, bo_va->base.bo, address, offset, size));
    assert(!amdgpu_vm_bo_map_validate(&vm, address, size));
    struct amdgpu_bo_va_mapping *mapping = allocate(sizeof(*mapping));
    unsigned count = allocations;
    amdgpu_vm_bo_map_prealloc(&device, bo_va, address, offset, size, flags, mapping);
    assert(allocations == count);
    for (uint64_t i = start; i <= last; i++) {
        assert(!expected[i].owner);
        expected[i] = (struct page){bo_va, offset + (i - start) * AMDGPU_GPU_PAGE_SIZE, flags};
    }
    verify_pages();
}
static void cleanup(void)
{
    while (vm.va.count) {
        struct amdgpu_bo_va_mapping *m = vm.va.items[0];
        amdgpu_vm_it_remove(m, &vm.va); list_del(&m->list); release(m);
    }
    while (!list_empty(&retired)) {
        struct amdgpu_bo_va_mapping *m = container_of(retired.next, struct amdgpu_bo_va_mapping, list);
        list_del(&m->list); release(m);
    }
    for (unsigned i = 0; i < 3; i++) assert(list_empty(&owners[i].invalids));
    assert(!live_allocations); cases++;
}
int main(void)
{
    setup();
    map_range(0, 10, 19, 4096, 2);
    map_range(0, 20, 29, 4096, 6);
    map_range(2, 30, 39, 0, AMDGPU_VM_PAGE_PRT);
    map_range(1, 45, 49, 8192, 4);
    assert(vm.va.count == 4 && prt_gets == 1 && traces == 4);
    assert(owners[0].base.updates == 2 && owners[1].base.updates == 1 && !owners[2].base.updates);
    const unsigned ranges[][2] = {{10, 10}, {9, 10}, {19, 20}, {11, 18}, {1, 60}, {29, 30}};
    for (unsigned i = 0; i < sizeof(ranges) / sizeof(ranges[0]); i++) {
        assert(amdgpu_vm_bo_map_validate(&vm, ranges[i][0] * AMDGPU_GPU_PAGE_SIZE,
                                        (ranges[i][1] - ranges[i][0] + 1) * AMDGPU_GPU_PAGE_SIZE) == -EINVAL);
        assert(vm.va.count == 4 && traces == 4); verify_pages(); cases++;
    }
    assert(!amdgpu_vm_bo_map_validate(&vm, 40 * AMDGPU_GPU_PAGE_SIZE, 5 * AMDGPU_GPU_PAGE_SIZE));
    assert(!amdgpu_vm_bo_map_validate(&vm, AMDGPU_GPU_PAGE_SIZE, 9 * AMDGPU_GPU_PAGE_SIZE));
    cleanup();
    for (unsigned bad = 0; bad < 8; bad++) {
        setup();
        uint64_t address = 10 * AMDGPU_GPU_PAGE_SIZE, size = AMDGPU_GPU_PAGE_SIZE, offset = 0;
        switch (bad) {
        case 0: size = 0; break;
        case 1: address++; break;
        case 2: size++; break;
        case 3: offset++; break;
        case 4: offset = UINT64_MAX - AMDGPU_GPU_PAGE_MASK; break;
        case 5: address = UINT64_MAX - AMDGPU_GPU_PAGE_MASK; break;
        case 6: offset = bos[0].size; break;
        case 7: address = device.vm_manager.max_pfn * AMDGPU_GPU_PAGE_SIZE; break;
        }
        assert(amdgpu_vm_verify_parameters(&device, &bos[0], address, offset, size) == -EINVAL);
        assert(!vm.va.count && !allocations); cleanup();
    }
    setup(); owners[0].base.moved = true;
    map_range(0, 10, 19, 0, 2); assert(!owners[0].base.updates); cleanup();
    printf("VA map: %u cases passed\n", cases);
    return 0;
}
'''


if __name__ == '__main__':
    unittest.main()
