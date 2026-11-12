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
SOURCE = 'drivers/gpu/drm/amd/amdgpu/amdgpu_vm.c'


class UserqLockTests(unittest.TestCase):
    def test_snapshot_and_references(self):
        revision = os.environ.get('USERQ_TEST_REVISION')
        if revision:
            source = subprocess.check_output(
                ['git', 'show', revision + ':' + SOURCE], cwd=ROOT, text=True)
        else:
            source = (ROOT / SOURCE).read_text()
        function = re.search(r'^int amdgpu_vm_lock_individual\(.*?^\}',
                             source, re.M | re.S)
        self.assertIsNotNone(function)
        flags = shlex.split(os.environ.get('CC', 'cc'))
        flags += ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror']
        if os.environ.get('USERQ_TEST_SANITIZERS'):
            flags += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                      '-fno-sanitize-recover=all']
        with tempfile.TemporaryDirectory(prefix='userq-lock-test-') as temporary:
            directory = pathlib.Path(temporary)
            program = directory / 'test.c'
            binary = directory / 'test'
            program.write_text(HARNESS.replace('ACTUAL_FUNCTION', function.group()))
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
#include <limits.h>
#define N 9
struct list_head { struct list_head *next, *prev; };
static void init_list(struct list_head *h) { h->next = h->prev = h; }
static void list_add_tail(struct list_head *n, struct list_head *h)
{ n->next = h; n->prev = h->prev; h->prev->next = n; h->prev = n; }
static void list_move_tail(struct list_head *n, struct list_head *h)
{ n->prev->next = n->next; n->next->prev = n->prev; list_add_tail(n, h); }
#define container_of(p,t,m) ((t *)((char *)(p) - offsetof(t,m)))
#define list_entry(p,t,m) container_of(p,t,m)
#define list_for_each_entry(pos, head, member) \
 for (struct list_head *n_ = (head)->next; n_ != (head) && \
 ((pos) = container_of(n_, __typeof__(*(pos)), member), true); n_ = n_->next)
#define list_is_head(p,h) ((p)==(h))
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define unlikely(x) (x)
#define likely(x) (x)
#define GFP_KERNEL 0
#define U32_MAX UINT32_MAX
#define WARN_ON(x) assert(!(x))
struct spinlock { bool held; };
struct dma_resv { bool held; };
struct drm_gem_object { struct dma_resv *resv; unsigned id; };
struct amdgpu_bo { struct { struct drm_gem_object base; } tbo; unsigned refs, gets, puts; };
struct amdgpu_vm_bo_base { struct amdgpu_bo *bo; struct list_head vm_status; };
struct amdgpu_bo_va { struct amdgpu_vm_bo_base base; };
struct amdgpu_vm_bo_status { struct list_head idle, needs_update, evicted; };
struct amdgpu_vm { struct amdgpu_vm_bo_status individual; struct spinlock individual_lock; struct { struct amdgpu_bo *bo; } root; };
struct drm_exec { unsigned count, prepared[N], held[N]; };
static struct amdgpu_vm vm;
static struct amdgpu_bo bo[N], root;
static struct amdgpu_bo_va va[N];
static struct dma_resv resv[N], root_resv;
static bool allocation_failure, move_alloc, move_prepare;
static unsigned total, allocations, frees, fail_id;
static int fail_error;
static void spin_lock(struct spinlock *s) { assert(!s->held); s->held = true; }
static void spin_unlock(struct spinlock *s) { assert(s->held); s->held = false; }
static void dma_resv_assert_held(struct dma_resv *r) { assert(r->held); }
static void amdgpu_vm_assert_locked(struct amdgpu_vm *v) { dma_resv_assert_held(v->root.bo->tbo.base.resv); }
static struct list_head *head(unsigned index)
{ return index % 3 == 0 ? &vm.individual.idle : index % 3 == 1 ? &vm.individual.needs_update : &vm.individual.evicted; }
static struct amdgpu_bo *amdgpu_bo_ref(struct amdgpu_bo *b)
{ assert(vm.individual_lock.held && b->refs); b->refs++; b->gets++; return b; }
static void amdgpu_bo_unref(struct amdgpu_bo **p)
{ struct amdgpu_bo *b=*p; assert(!vm.individual_lock.held && b && b->refs>1); b->refs--; b->puts++; *p=NULL; }
static void *alloc_array(size_t n, size_t bytes, int flags)
{
 (void)flags; assert(!vm.individual_lock.held && root_resv.held); allocations++;
 if (allocation_failure) return NULL;
 if (move_alloc) {
   spin_lock(&vm.individual_lock);
   for (unsigned i=0;i<total;i++) list_move_tail(&va[i].base.vm_status,head(i+1));
   spin_unlock(&vm.individual_lock);
 }
 return calloc(n ? n : 1,bytes);
}
#define kvcalloc alloc_array
#define kcalloc alloc_array
#define kvmalloc_array alloc_array
#define kmalloc_array alloc_array
static void free_array(void *p) { assert(!vm.individual_lock.held); if(p) { frees++; free(p); } }
#define kvfree free_array
#define kfree free_array
static int drm_exec_prepare_obj(struct drm_exec *e,struct drm_gem_object *o,unsigned fences)
{
 assert(!vm.individual_lock.held && root_resv.held && fences==7 && o->id && o->id<=total);
 unsigned i=o->id-1; assert(bo[i].refs==2 && e->count<N);
 for(unsigned j=0;j<e->count;j++) assert(e->prepared[j]!=o->id);
 e->prepared[e->count++]=o->id;
 if(move_prepare) {
   spin_lock(&vm.individual_lock);
   for(unsigned j=0;j<total;j++) list_move_tail(&va[j].base.vm_status,head(j+e->count));
   spin_unlock(&vm.individual_lock);
 }
 if(o->id==fail_id) return fail_error;
 resv[i].held=true; bo[i].refs++; e->held[i]=1;
 return 0;
}
ACTUAL_FUNCTION
static void release_snapshot(struct amdgpu_bo **snapshot,unsigned long count)
{ for(unsigned long i=0;i<count;i++) amdgpu_bo_unref(&snapshot[i]); kvfree(snapshot); }
static void run(unsigned count,bool allocfail,bool movealloc,bool moveprep,unsigned failure,int error)
{
 memset(&vm,0,sizeof(vm)); memset(bo,0,sizeof(bo)); memset(va,0,sizeof(va)); memset(resv,0,sizeof(resv));
 init_list(&vm.individual.idle); init_list(&vm.individual.needs_update); init_list(&vm.individual.evicted);
 root_resv.held=true; root.tbo.base.resv=&root_resv; vm.root.bo=&root;
 total=count; allocations=frees=0; allocation_failure=allocfail; move_alloc=movealloc; move_prepare=moveprep; fail_id=failure; fail_error=error;
 for(unsigned i=0;i<count;i++) { bo[i].tbo.base=(struct drm_gem_object){&resv[i],i+1}; bo[i].refs=1; va[i].base.bo=&bo[i]; list_add_tail(&va[i].base.vm_status,head(i)); }
 struct drm_exec exec={0}; struct amdgpu_bo **snapshot=NULL; unsigned long n=0;
 int r=amdgpu_vm_lock_individual(&vm,&exec,7,&snapshot,&n);
 assert(!vm.individual_lock.held && root_resv.held);
 if(allocfail && count) { assert(r==-ENOMEM && !snapshot && n==0 && exec.count==0); }
 else if(failure) { assert(r==error && !snapshot && n==0); assert(exec.count && exec.prepared[exec.count-1]==failure); }
 else {
   assert(r==0 && n==count && exec.count==count);
   unsigned seen[N]={0}; for(unsigned long i=0;i<n;i++) { unsigned id=snapshot[i]->tbo.base.id; assert(id && id<=count && !seen[id-1]++); }
   release_snapshot(snapshot,n);
 }
 for(unsigned i=0;i<count;i++) {
   assert(bo[i].refs==1+exec.held[i]); assert(bo[i].gets==bo[i].puts);
   assert(bo[i].gets==((allocfail && count)?0:1));
   if(exec.held[i]) { bo[i].refs--; resv[i].held=false; }
   assert(bo[i].refs==1);
 }
 assert(frees==(allocations && !allocfail ? 1u:0u));
}
int main(void)
{
 unsigned count=0; run(0,false,false,false,0,0); count++;
 for(unsigned n=1;n<=N;n++) for(unsigned ma=0;ma<2;ma++) for(unsigned mp=0;mp<2;mp++) {run(n,false,ma,mp,0,0); count++;}
 for(unsigned n=1;n<=N;n++){run(n,true,false,false,0,0);count++;}
 const int errors[]={-ENOMEM,-EDEADLK,-EINTR};
 for(unsigned e=0;e<ARRAY_SIZE(errors);e++) for(unsigned f=1;f<=N;f++) for(unsigned ma=0;ma<2;ma++) for(unsigned mp=0;mp<2;mp++) {run(N,false,ma,mp,f,errors[e]);count++;}
 printf("snapshot lock helper: %u cases passed\n",count);
}
'''


if __name__ == '__main__':
    unittest.main()
