#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import os
import pathlib
import re
import shlex
import subprocess
import tempfile
import unittest


AMD = 'drivers/gpu/drm/amd/amdgpu/'
for ROOT in pathlib.Path(__file__).resolve().parents:
    if (ROOT / AMD / 'amdgpu_userq.c').is_file():
        break
    if (ROOT / 'amdgpu' / AMD / 'amdgpu_userq.c').is_file():
        AMD = 'amdgpu/' + AMD
        break
else:
    raise RuntimeError('AMDGPU source tree not found')


def source(file):
    revision = os.environ.get('USERQ_TEST_REVISION')
    if revision:
        return subprocess.check_output(['git', 'show', revision + ':' + AMD + file],
                                       cwd=ROOT, text=True)
    return (ROOT / AMD / file).read_text()


def function(file, name):
    match = re.search(r'^(?:static )?(?:int|void)\s+' + name + r'\([^;]*?\)\n\{.*?^\}',
                      source(file), re.M | re.S)
    if match is None:
        raise AssertionError(name)
    return match.group()


def run_program(body):
    flags = shlex.split(os.environ.get('CC', 'cc'))
    flags += ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
              '-Wno-unused-parameter']
    if os.environ.get('USERQ_TEST_SANITIZERS'):
        flags += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    with tempfile.TemporaryDirectory(prefix='amdgpu-core-upstream-') as temporary:
        directory = pathlib.Path(temporary)
        program, binary = directory / 'test.c', directory / 'test'
        program.write_text(body)
        subprocess.run(flags + [str(program), '-o', str(binary)], check=True, timeout=30)
        subprocess.run([str(binary)], check=True, timeout=30)


class CoreUpstreamTests(unittest.TestCase):
    def test_gang_dependency_ownership(self):
        body = function('amdgpu_cs.c', 'amdgpu_cs_submit')
        start = body.index('\tfor (i = 0; i < p->gang_size; ++i) {')
        end = body.index('\n\tif (p->gang_size > 1)', start)
        run_program(GANG_HARNESS.replace('ACTUAL_DEPENDENCIES', body[start:end]))

    def test_reset_mapping_rewrite(self):
        reset = function('amdgpu_vm.c', 'amdgpu_vm_bo_reset_state_machine')
        update = function('amdgpu_vm.c', 'amdgpu_vm_bo_needs_update')
        body = function('amdgpu_vm.c', 'amdgpu_vm_bo_update_internal')
        start = body.index('\tif (!clear && bo_va->base.moved)')
        end = body.index('\n\tbatching =', start)
        harness = RESET_HARNESS.replace('ACTUAL_NEEDS_UPDATE', update).replace('ACTUAL_RESET', reset)
        run_program(harness.replace('ACTUAL_INVALIDATE', body[start:end]))


GANG_HARNESS = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
struct dma_fence { unsigned refs; };
struct drm_sched_fence { struct dma_fence scheduled; };
struct drm_sched_job {
    struct drm_sched_fence *s_fence;
    struct dma_fence *dependencies[8];
    unsigned count;
};
struct amdgpu_job { struct drm_sched_job base; };
struct amdgpu_cs_parser {
    struct amdgpu_job *jobs[8], *gang_leader;
    unsigned gang_size;
};
static unsigned calls, fail_at;
static void dma_fence_get(struct dma_fence *fence) { assert(fence->refs); fence->refs++; }
static void dma_fence_put(struct dma_fence *fence) { assert(fence->refs); fence->refs--; }
static int drm_sched_job_add_dependency(struct drm_sched_job *job, struct dma_fence *fence)
{
    /* The scheduler consumes the reference on both success and failure. */
    if (++calls == fail_at) {
        dma_fence_put(fence);
        return -ENOMEM;
    }
    assert(job->count < 8);
    job->dependencies[job->count++] = fence;
    return 0;
}
static int add_dependencies(struct amdgpu_cs_parser *p)
{
    struct amdgpu_job *leader = p->gang_leader;
    unsigned i;
    int r;
ACTUAL_DEPENDENCIES
    return 0;
}
int main(void)
{
    unsigned cases = 0;
    for (unsigned count = 1; count <= 8; count++) {
        for (unsigned leader = 0; leader < count; leader++) {
            for (unsigned fail = 0; fail < count; fail++) {
                struct amdgpu_job jobs[8] = {0};
                struct drm_sched_fence fences[8] = {0};
                struct amdgpu_cs_parser parser = {.gang_size = count, .gang_leader = &jobs[leader]};
                calls = 0;
                fail_at = fail;
                for (unsigned i = 0; i < count; i++) {
                    fences[i].scheduled.refs = 1;
                    jobs[i].base.s_fence = &fences[i];
                    parser.jobs[i] = &jobs[i];
                }
                assert(add_dependencies(&parser) == (fail ? -ENOMEM : 0));
                assert(calls == (fail ? fail : count - 1));
                struct drm_sched_job *owner = &jobs[leader].base;
                assert(owner->count == (fail ? fail - 1 : count - 1));
                for (unsigned i = 0; i < owner->count; i++)
                    dma_fence_put(owner->dependencies[i]);
                for (unsigned i = 0; i < count; i++) {
                    assert(fences[i].scheduled.refs == 1);
                    dma_fence_put(&fences[i].scheduled);
                }
                cases++;
            }
        }
    }
    printf("Gang dependencies: %u success and allocation-failure cases passed\n", cases);
    return 0;
}
'''


RESET_HARNESS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#define container_of(ptr, type, field) ((type *)((char *)(ptr) - offsetof(type, field)))
struct list_head { struct list_head *next, *prev; };
static void list_init(struct list_head *head) { head->next = head->prev = head; }
static bool list_empty(const struct list_head *head) { return head->next == head; }
static void list_add(struct list_head *node, struct list_head *head)
{
    node->next = head->next;
    node->prev = head;
    head->next->prev = node;
    head->next = node;
}
static void list_move(struct list_head *node, struct list_head *head)
{
    node->prev->next = node->next;
    node->next->prev = node->prev;
    list_add(node, head);
}
static void list_splice_init(struct list_head *from, struct list_head *to)
{
    while (!list_empty(from))
        list_move(from->prev, to);
}
#define list_for_each_entry(pos, head, field) \
    for (struct list_head *node = (head)->next; node != (head) && \
         ((pos) = container_of(node, __typeof__(*(pos)), field), true); node = node->next)
#define list_for_each_entry_safe(pos, tmp, head, field) \
    for (struct list_head *node = (head)->next, *next = node->next; node != (head) && \
         ((pos) = container_of(node, __typeof__(*(pos)), field), \
          (tmp) = next == (head) ? NULL : container_of(next, __typeof__(*(tmp)), field), (void)(tmp), true); \
         node = next, next = node->next)
struct amdgpu_bo { int unused; };
struct amdgpu_vm_bo_status { struct list_head idle, needs_update, evicted; };
struct amdgpu_vm {
    struct { struct amdgpu_bo *bo; } root;
    struct amdgpu_vm_bo_status kernel, always_valid, individual;
    bool individual_lock, root_locked;
};
struct amdgpu_vm_bo_base {
    struct list_head vm_status;
    struct amdgpu_bo *bo;
    struct amdgpu_vm *vm;
    struct amdgpu_vm_bo_status *lists;
    bool moved;
};
struct amdgpu_bo_va {
    struct amdgpu_vm_bo_base base;
    struct list_head valids, invalids;
    bool cleared;
};
static void spin_lock(bool *lock) { assert(!*lock); *lock = true; }
static void spin_unlock(bool *lock) { assert(*lock); *lock = false; }
static void amdgpu_vm_assert_locked(struct amdgpu_vm *vm) { assert(vm->root_locked); }
static struct amdgpu_vm_bo_status *amdgpu_vm_bo_lock_lists(struct amdgpu_vm_bo_base *base)
{
    assert(base->vm->root_locked);
    return base->lists;
}
static void amdgpu_vm_bo_unlock_lists(struct amdgpu_vm_bo_base *base) { assert(base->vm->root_locked); }
ACTUAL_NEEDS_UPDATE
ACTUAL_RESET
static void invalidate_mappings(struct amdgpu_bo_va *bo_va)
{
    bool clear = false, flush_tlb = false;
ACTUAL_INVALIDATE
    assert(flush_tlb);
}
static unsigned list_count(struct list_head *head)
{
    unsigned count = 0;
    for (struct list_head *node = head->next; node != head; node = node->next) {
        assert(node->next->prev == node && node->prev->next == node);
        assert(++count <= 20);
    }
    return count;
}
int main(void)
{
    struct amdgpu_vm vm = {.root_locked = true};
    struct amdgpu_vm_bo_status *groups[] = {&vm.kernel, &vm.always_valid, &vm.individual};
    struct amdgpu_bo objects[10] = {0};
    struct amdgpu_bo_va mappings[10] = {0};
    struct list_head ranges[10][3];
    for (unsigned g = 0; g < 3; g++) {
        list_init(&groups[g]->idle);
        list_init(&groups[g]->needs_update);
        list_init(&groups[g]->evicted);
    }
    vm.root.bo = &objects[9];
    for (unsigned i = 0; i < 10; i++) {
        struct amdgpu_bo_va *va = &mappings[i];
        va->base.bo = &objects[i];
        va->base.vm = &vm;
        va->base.lists = groups[i == 9 ? 0 : i / 3];
        list_init(&va->valids);
        list_init(&va->invalids);
        list_add(&ranges[i][0], &va->valids);
        list_add(&ranges[i][1], &va->valids);
        list_add(&ranges[i][2], &va->invalids);
        struct amdgpu_vm_bo_status *group = va->base.lists;
        struct list_head *state = i % 3 == 0 ? &group->idle :
                                  i % 3 == 1 ? &group->needs_update : &group->evicted;
        list_add(&va->base.vm_status, state);
    }
    for (unsigned pass = 0; pass < 2; pass++) {
        /* A full rewrite is needed even if some mappings were already pending. */
        amdgpu_vm_bo_reset_state_machine(&vm);
        assert(!vm.individual_lock && vm.root_locked && !mappings[9].base.moved);
        assert(list_count(&vm.kernel.idle) == 1 && vm.kernel.idle.next == &mappings[9].base.vm_status);
        for (unsigned g = 0; g < 3; g++) {
            assert(list_count(&groups[g]->needs_update) == 2);
            assert(list_count(&groups[g]->evicted) == 1);
            for (unsigned state = 0; state < 3; state++) {
                struct amdgpu_bo_va *va = &mappings[g * 3 + state];
                assert(va->base.moved == (state != 2));
                if (state == 2)
                    continue;
                invalidate_mappings(va);
                assert(list_count(&va->valids) == 0 && list_count(&va->invalids) == 3);
                list_splice_init(&va->invalids, &va->valids);
                va->base.moved = false;
                list_move(&va->base.vm_status, &groups[g]->idle);
            }
        }
    }
    puts("VM reset: pending and idle mappings, root PD and repeated reset cases passed");
    return 0;
}
'''


if __name__ == '__main__':
    unittest.main()
