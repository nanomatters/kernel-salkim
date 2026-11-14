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
        if (root / 'drivers/gpu/drm/amd/amdgpu/amdgpu_hmm.c').is_file():
            return root
    raise RuntimeError('Set AMDGPU_TEST_ROOT to the kernel source directory')


ROOT = source_root()
AMD = ROOT / 'drivers/gpu/drm/amd/amdgpu'


class HmmTests(unittest.TestCase):
    def function(self, file, name):
        source = (AMD / file).read_text()
        match = re.search(r'^(?:int|bool|void|struct amdgpu_hmm_range \*)\s*' +
                          name + r'\([^;]*?\)\n\{.*?^\}', source, re.M | re.S)
        self.assertIsNotNone(match, name)
        return match.group()

    def test_range_retries_and_ownership(self):
        functions = [self.function('amdgpu_hmm.c', name) for name in (
            'amdgpu_hmm_range_get_pages', 'amdgpu_hmm_range_valid',
            'amdgpu_hmm_range_alloc', 'amdgpu_hmm_range_free')]
        functions.append(self.function('amdgpu_ttm.c', 'amdgpu_ttm_tt_set_user_pages'))
        source = HARNESS.replace('ACTUAL_FUNCTIONS', '\n'.join(functions))
        command = shlex.split(os.environ.get('CC', 'cc'))
        command += ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter']
        if os.environ.get('AMDGPU_TEST_SANITIZERS') or os.environ.get('HMM_TEST_SANITIZERS'):
            command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
        with tempfile.TemporaryDirectory(prefix='amdgpu-hmm-') as temporary:
            directory = Path(temporary)
            program, binary = directory / 'test.c', directory / 'test'
            program.write_text(source)
            subprocess.run(command + [str(program), '-o', str(binary)], check=True, timeout=30)
            subprocess.run([str(binary)], check=True, timeout=30)


HARNESS = r'''

#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
typedef uint64_t u64;
#define SZ_2G (1ULL << 31)
#define PAGE_SHIFT 12
#define PAGE_SIZE (1UL << PAGE_SHIFT)
#define GFP_KERNEL 0
#define HMM_PFN_REQ_FAULT 1
#define HMM_PFN_REQ_WRITE 2
#define unlikely(x) (x)
#define min(a, b) ((a) < (b) ? (a) : (b))
#define pr_debug(...) ((void)0)
#define kzalloc_obj(object) calloc(1, sizeof(object))
#define kfree(p) free(p)
struct mmu_interval_notifier {
    unsigned long seq;
};
struct hmm_range {
    struct mmu_interval_notifier *notifier;
    unsigned long default_flags, *hmm_pfns, start, end, notifier_seq;
    void *dev_private_owner;
};
struct amdgpu_bo {
    unsigned refs;
};
struct amdgpu_hmm_range {
    struct hmm_range hmm_range;
    struct amdgpu_bo *bo;
};
struct page {
    int unused;
};
struct ttm_tt {
    unsigned long num_pages;
    struct page **pages;
};
static void *allocs[256];
static unsigned allocated, outstanding, fault_calls, fail_fault_at;
static int fault_error;
static bool fail_alloc, notifier_locked;
static unsigned long generation = 100;
static struct amdgpu_bo *amdgpu_bo_ref(struct amdgpu_bo *bo) {
    if (bo)
        bo->refs++;
    return bo;
}
static void amdgpu_bo_unref(struct amdgpu_bo **bo) {
    if (*bo)
        (*bo)->refs--;
    *bo = NULL;
}
#define kvmalloc_objs(obj, count) kvmalloc_array((count), sizeof(obj), GFP_KERNEL)
static void *kvmalloc_array(size_t n, size_t size, int flags) {
    assert(!notifier_locked);
    if (fail_alloc)
        return NULL;
    assert(allocated < 256);
    void *p = calloc(n, size);
    assert(p);
    allocs[allocated++] = p;
    outstanding++;
    return p;
}
static void kvfree(void *p) {
    if (!p)
        return;
    for (unsigned i = 0; i < allocated; i++)
        if (allocs[i] == p) {
            free(p);
            allocs[i] = NULL;
            assert(outstanding);
            outstanding--;
            return;
        }
    assert(0);
}
static unsigned long mmu_interval_read_begin(struct mmu_interval_notifier *n) {
    assert(n && !notifier_locked);
    return n->seq;
}
static bool mmu_interval_read_retry(struct mmu_interval_notifier *n, unsigned long seq) {
    assert(n && notifier_locked);
    return n->seq != seq;
}
static struct page *hmm_pfn_to_page(unsigned long pfn) {
    return (struct page *)(uintptr_t)(pfn << PAGE_SHIFT);
}
static int hmm_range_fault(struct hmm_range *r) {
    assert(!notifier_locked);
    fault_calls++;
    if (fail_fault_at == fault_calls)
        return fault_error;
    for (unsigned long i = 0; i < (r->end - r->start) / PAGE_SIZE; i++)
        r->hmm_pfns[i] = generation + (r->start / PAGE_SIZE) + i;
    return 0;
}

ACTUAL_FUNCTIONS

static bool valid(struct amdgpu_hmm_range *r) {
    notifier_locked = true;
    bool result = amdgpu_hmm_range_valid(r);
    notifier_locked = false;
    return result;
}
static int get(struct amdgpu_hmm_range *r, struct mmu_interval_notifier *n, unsigned long pages,
               bool readonly) {
    return amdgpu_hmm_range_get_pages(n, 4096, pages, readonly, NULL, r);
}
static void finish(struct amdgpu_hmm_range *r, struct amdgpu_bo *bo) {
    amdgpu_hmm_range_free(r);
    assert(bo->refs == 1);
    assert(!outstanding);
}
int main(void) {
    struct amdgpu_bo bo = {.refs = 1};
    struct mmu_interval_notifier n = {.seq = 2};
    assert(!valid(NULL));
    struct amdgpu_hmm_range *r = amdgpu_hmm_range_alloc(&bo);
    assert(r && bo.refs == 2 && !valid(r));
    assert(!get(r, &n, 4, false));
    assert(outstanding == 1 && valid(r));
    struct page *pages[4];
    struct ttm_tt ttm = {.num_pages = 4, .pages = pages};
    amdgpu_ttm_tt_set_user_pages(&ttm, r);
    struct page *old_page = pages[0];
    for (unsigned i = 0; i < 32; i++) {
        generation++;
        assert(!get(r, &n, 4, i & 1));
        assert(outstanding == 1 && valid(r));
        assert(r->hmm_range.default_flags ==
               (HMM_PFN_REQ_FAULT | ((i & 1) ? 0 : HMM_PFN_REQ_WRITE)));
    }
    assert(pages[0] == old_page);
    amdgpu_ttm_tt_set_user_pages(&ttm, r);
    assert(pages[0] != old_page);
    n.seq++;
    assert(!valid(r));
    generation++;
    assert(!get(r, &n, 4, false));
    assert(valid(r));
    unsigned long *old_pfns = r->hmm_range.hmm_pfns;
    fail_alloc = true;
    assert(get(r, &n, 8, false) == -ENOMEM);
    assert(r->hmm_range.hmm_pfns == old_pfns && outstanding == 1);
    fail_alloc = false;
    assert(!get(r, &n, 8, false));
    assert(outstanding == 1);
    fail_fault_at = fault_calls + 1;
    fault_error = -EINVAL;
    assert(get(r, &n, 8, false) == -EINVAL);
    assert(!r->hmm_range.hmm_pfns && !valid(r) && !outstanding);
    fail_fault_at = 0;
    assert(!get(r, &n, 4, false));
    fail_fault_at = fault_calls + 2;
    fault_error = -EBUSY;
    assert(get(r, &n, (SZ_2G / PAGE_SIZE) + 1, false) == -EAGAIN);
    assert(!r->hmm_range.hmm_pfns && !valid(r) && !outstanding);
    fail_fault_at = 0;
    assert(!get(r, &n, (SZ_2G / PAGE_SIZE) + 1, false));
    assert(r->hmm_range.start == 4096 && outstanding == 1 && valid(r));
    finish(r, &bo);
    amdgpu_hmm_range_free(NULL);
    puts("HMM retry: fresh validity,32 replacements,MMU invalidation,TTM-copy "
         "independence,ENOMEM,first/second chunk errors,EBUSY conversion and cleanup passed");
    return 0;
}
'''


if __name__ == '__main__':
    unittest.main()
