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


class VaClearTests(unittest.TestCase):
    def test_preallocated_removal_and_splits(self):
        source = (AMD / 'amdgpu_vm.c').read_text()
        function = re.search(r'^(?:static )?void amdgpu_vm_bo_clear_mappings_prealloc\(.*?^\}',
                             source, re.M | re.S)
        self.assertIsNotNone(function)
        flags = shlex.split(os.environ.get('CC', 'cc'))
        flags += ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                  '-Wno-unused-parameter', '-Wno-unused-but-set-variable']
        if os.environ.get('USERQ_TEST_SANITIZERS'):
            flags += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                      '-fno-sanitize-recover=all']
        with tempfile.TemporaryDirectory(prefix='va-clear-test-') as temporary:
            directory = pathlib.Path(temporary)
            program, binary = directory / 'test.c', directory / 'test'
            program.write_text(HARNESS.replace('ACTUAL_FUNCTION', function.group()))
            subprocess.run(flags + [str(program), '-o', str(binary)], check=True, timeout=30)
            subprocess.run([str(binary)], check=True, timeout=30)


HARNESS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define AMDGPU_GPU_PAGE_SIZE 4096ULL
#define AMDGPU_GPU_PAGE_SHIFT 12
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
    bool always_valid;
    struct { struct drm_gem_object base; void *ttm; } tbo;
};
struct amdgpu_vm_bo_base { struct amdgpu_bo *bo; bool moved; unsigned updates; };
struct amdgpu_bo_va { struct amdgpu_vm_bo_base base; struct list_head invalids; };
struct amdgpu_bo_va_mapping {
    struct list_head list;
    uint64_t start, last, offset, flags;
    struct amdgpu_bo_va *bo_va;
};
struct interval_tree { unsigned count; struct amdgpu_bo_va_mapping *items[32]; };
struct amdgpu_vm { struct interval_tree va; };
struct amdgpu_device { unsigned unused; };
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
static struct amdgpu_bo_va_mapping *amdgpu_vm_it_iter_next(struct amdgpu_bo_va_mapping *old,
                                                          uint64_t start, uint64_t last)
{
    bool found = false;
    for (unsigned i = 0; i < vm.va.count; i++) {
        struct amdgpu_bo_va_mapping *m = vm.va.items[i];
        if (found && m->start <= last && m->last >= start) return m;
        found |= m == old;
    }
    assert(found);
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
static bool amdgpu_vm_is_bo_always_valid(struct amdgpu_vm *v, struct amdgpu_bo *bo)
{ return bo && bo->always_valid; }
static bool drm_gem_is_imported(struct drm_gem_object *obj) { return obj->imported; }
static void *amdgpu_ttm_tt_get_usermm(void *ttm) { return ttm; }
static void amdgpu_vm_prt_get(struct amdgpu_device *dev) { prt_gets++; }
static void amdgpu_vm_bo_needs_update(struct amdgpu_vm_bo_base *base) { base->updates++; }
static void trace_amdgpu_vm_bo_unmap(struct amdgpu_bo_va *owner, struct amdgpu_bo_va_mapping *m)
{ assert(!owner && !m->bo_va); traces++; }

ACTUAL_FUNCTION

static void setup(void)
{
    assert(!live_allocations);
    memset(&vm, 0, sizeof(vm)); memset(bos, 0, sizeof(bos));
    memset(owners, 0, sizeof(owners)); memset(expected, 0, sizeof(expected));
    INIT_LIST_HEAD(&retired);
    for (unsigned i = 0; i < 3; i++) INIT_LIST_HEAD(&owners[i].invalids);
    for (unsigned i = 0; i < 2; i++) { owners[i].base.bo = &bos[i]; bos[i].always_valid = true; }
    allocations = prt_gets = traces = 0;
}
static void add_mapping(unsigned owner, uint64_t start, uint64_t last, uint64_t offset, uint64_t flags)
{
    struct amdgpu_bo_va_mapping *m = allocate(sizeof(*m));
    *m = (struct amdgpu_bo_va_mapping){.start = start, .last = last, .offset = offset,
                                     .flags = flags, .bo_va = &owners[owner]};
    list_add(&m->list, &owners[owner].invalids);
    amdgpu_vm_it_insert(m, &vm.va);
    for (uint64_t i = start; i <= last; i++) {
        assert(i < 80 && !expected[i].owner);
        expected[i] = (struct page){m->bo_va, offset + (i - start) * AMDGPU_GPU_PAGE_SIZE, flags};
    }
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
static void clear_range(uint64_t start, uint64_t last, bool *explicit_sync)
{
    struct amdgpu_bo_va_mapping *before = allocate(sizeof(*before)), *after = allocate(sizeof(*after));
    unsigned count = allocations;
    amdgpu_vm_bo_clear_mappings_prealloc(&device, &vm, start * AMDGPU_GPU_PAGE_SIZE,
                                        (last - start + 1) * AMDGPU_GPU_PAGE_SIZE,
                                        before, after, &retired, explicit_sync);
    assert(allocations == count);
    for (uint64_t i = start; i <= last; i++) expected[i] = (struct page){0};
    verify_pages();
    for (struct list_head *n = retired.next; n != &retired; n = n->next) {
        struct amdgpu_bo_va_mapping *m = container_of(n, struct amdgpu_bo_va_mapping, list);
        assert(!m->bo_va && m->start <= m->last);
    }
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
    /* Every shape, including holes and different owners, retains exact offsets. */
    const unsigned ranges[][2] = {{1, 4}, {10, 19}, {10, 14}, {15, 19}, {13, 16},
                                  {15, 29}, {1, 60}, {43, 46}, {18, 41}};
    for (unsigned i = 0; i < sizeof(ranges) / sizeof(ranges[0]); i++) {
        setup();
        add_mapping(0, 10, 19, 4096, 2);
        add_mapping(1, 25, 34, 8192, 4);
        add_mapping(2, 40, 49, 0, AMDGPU_VM_PAGE_PRT);
        bool explicit_sync = true;
        clear_range(ranges[i][0], ranges[i][1], &explicit_sync);
        assert(explicit_sync);
        if (i == 0) assert(!traces && vm.va.count == 3);
        if (i == 4) assert(owners[0].base.updates == 2);
        if (i == 7) assert(prt_gets == 2);
        clear_range(ranges[i][0], ranges[i][1], &explicit_sync);
        clear_range(12, 45, &explicit_sync);
        clear_range(1, 60, NULL);
        assert(!vm.va.count);
        cleanup();
    }
    /* Ineligible retired backing must disable explicit retirement for the batch. */
    for (unsigned backing = 0; backing < 6; backing++) {
        setup();
        if (backing == 1) bos[0].always_valid = false;
        if (backing == 2) bos[0].tbo.base.imported = true;
        if (backing == 3) bos[0].tbo.ttm = &bos[0];
        add_mapping(backing < 4 ? 0 : 2, 10, 19, 0, backing == 4 ? AMDGPU_VM_PAGE_PRT : 2);
        bool explicit_sync = true;
        clear_range(12, 17, &explicit_sync);
        assert(explicit_sync == (backing == 0 || backing == 4));
        cleanup();
    }
    setup(); owners[0].base.moved = true;
    add_mapping(0, 10, 19, 0, 2); clear_range(12, 17, NULL);
    assert(!owners[0].base.updates); cleanup();
    printf("VA clear: %u cases passed\n", cases);
    return 0;
}
'''


if __name__ == '__main__':
    unittest.main()
