#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise UserQ priority validation and real MES packets without GPU access."""
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
            if (source / 'amdgpu_userq.c').is_file():
                return source
    raise RuntimeError('Set AMDGPU_TEST_ROOT to the kernel or module source tree')


AMD = source_directory()
ROOT = AMD.parents[4]


def function(source, name):
    match = re.search(r'^(?:static )?(?:int|bool|void)\s+' + name + r'\(.*?^\}', source, re.M | re.S)
    if not match:
        raise AssertionError(name)
    return match.group()


def declaration(source, kind, name):
    return re.search(r'^' + kind + ' ' + name + r' \{.*?^\};', source, re.M | re.S).group()


def run(program):
    flags = shlex.split(os.environ.get('CC', 'cc'))
    flags += ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
              '-Wno-unused-parameter', '-Wno-unused-function',
              '-I' + str(ROOT / 'include/uapi/drm'), '-I' + str(AMD.parent / 'include')]
    flags += shlex.split(subprocess.check_output(['pkg-config', '--cflags', 'libdrm'], text=True))
    if os.environ.get('USERQ_TEST_SANITIZERS'):
        flags += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-fno-sanitize-recover=all']
    with tempfile.TemporaryDirectory(prefix='userq-priority-') as temporary:
        path = pathlib.Path(temporary)
        source, binary = path / 'test.c', path / 'test'
        source.write_text(program)
        subprocess.run(flags + [str(source), '-o', str(binary)], check=True, timeout=30)
        subprocess.run([str(binary)], check=True, timeout=30)


class UserqPriorityTests(unittest.TestCase):
    def test_flags_and_permissions(self):
        source = (AMD / 'amdgpu_userq.c').read_text()
        names = ('amdgpu_userq_supports_relative_priority', 'amdgpu_userq_priority_permit',
                 'amdgpu_userq_input_args_validate', 'amdgpu_userq_create_relative')
        actual = '\n'.join(function(source, name) for name in names)
        enum = declaration((AMD / 'amdgpu_mes.h').read_text(), 'enum', 'amdgpu_mes_priority_level')
        run(COMMON + enum + VALIDATION.replace('ACTUAL_SOURCE', actual))

    def test_mes_packets(self):
        header = (AMD / 'amdgpu_mes.h').read_text()
        declarations = declaration(header, 'enum', 'amdgpu_mes_priority_level')
        declarations += declaration(header, 'struct', 'mes_add_queue_input')
        declarations += declaration(header, 'struct', 'mes_change_gang_priority_input')
        for version in ('11_0', '12_0'):
            with self.subTest(version=version):
                source = (AMD / f'mes_v{version}.c').read_text()
                actual = '\n'.join(function(source, name) for name in
                                   ('convert_to_mes_queue_type', 'convert_to_mes_priority_level',
                                    f'mes_v{version}_add_hw_queue'))
                if version == '12_0':
                    actual += function(source, 'mes_v12_0_change_gang_priority')
                run(COMMON + '#define MES_VERSION ' + version.split('_')[0] + '\n' + declarations +
                    PACKETS.replace('ACTUAL_SOURCE', actual))

    def test_failed_copy_keeps_reused_queue_id(self):
        source = (AMD / 'amdgpu_userq.c').read_text()
        actual = function(source, 'amdgpu_userq_create_rollback')
        run(COMMON + ROLLBACK.replace('ACTUAL_SOURCE', actual))

    def test_stored_priority_survives_restore(self):
        source = (AMD / 'amdgpu_userq.c').read_text()
        self.assertRegex(source, r'queue->relative_priority = relative_priority \? \*relative_priority :\s*'
                         r'AMDGPU_MES_PRIORITY_LEVEL_NORMAL;')
        self.assertIn('queue->relative_priority_set = relative_priority;', source)
        self.assertIn('amdgpu_userq_create(filp, args, NULL, NULL)', source)
        map_source = (AMD / 'mes_userqueue.c').read_text()
        self.assertIn('queue_input.inprocess_gang_priority = queue->relative_priority;', map_source)
        self.assertIn('queue_input.raw_inprocess_priority = queue->relative_priority_set;', map_source)
        self.assertRegex(map_source, r'\.map\s*=\s*mes_userq_map')
        self.assertIn('userq_funcs->map(queue)', source)
        caps = (AMD / 'amdgpu_salkim.c').read_text()
        self.assertRegex(caps, r'if \(amdgpu_userq_supports_relative_priority\(adev, ip\)\)\s*'
                         r'caps->relative_priority_ip_mask \|= BIT\(ip\);')
        kfd = (AMD.parent / 'amdkfd/kfd_device_queue_manager.c').read_text()
        self.assertIn('memset(&queue_input, 0x0, sizeof(struct mes_add_queue_input));', kfd)


COMMON = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stddef.h>
#include <string.h>
#define __user
#include "amdgpu_drm.h"
typedef uint32_t u32;
typedef int32_t s32;
typedef uint64_t u64;
'''

VALIDATION = r'''
struct amdgpu_userq_funcs { int unused; };
static const struct amdgpu_userq_funcs userq_mes_funcs = {1}, other_funcs = {2};
struct amdgpu_device { const struct amdgpu_userq_funcs *userq_funcs[AMDGPU_HW_IP_NUM]; };
struct drm_device { struct amdgpu_device *adev; };
struct drm_file { bool master; };
struct amdgpu_usermode_queue { int unused; };
static struct amdgpu_usermode_queue queue;
static bool privileged, enabled = true;
static unsigned creates;
static int last_relative;
#define drm_to_adev(dev) ((dev)->adev)
#define CAP_SYS_NICE 1
#define capable(cap) (privileged)
#define drm_is_current_master(filp) ((filp)->master)
#define drm_file_err(...) ((void)0)
#define amdgpu_is_tmz(adev) ((void)(adev), false)
#define AMDGPU_BO_INVALID_OFFSET UINT64_MAX
#define AMDGPU_GPU_PAGE_SIZE 4096
#define IS_ALIGNED(value, alignment) (!((value) & ((alignment) - 1)))
static bool is_power_of_2(uint64_t v) { return v && !(v & (v - 1)); }
static bool amdgpu_userq_enabled(struct drm_device *dev) { return enabled; }
static int amdgpu_userq_create(struct drm_file *filp, union drm_amdgpu_userq *args,
                              const int *relative, struct amdgpu_usermode_queue **created);
ACTUAL_SOURCE
static int amdgpu_userq_create(struct drm_file *filp, union drm_amdgpu_userq *args,
                              const int *relative, struct amdgpu_usermode_queue **created)
{
    assert(relative && created);
    ++creates;
    last_relative = *relative;
    int r = amdgpu_userq_priority_permit(filp, args->in.flags & AMDGPU_USERQ_CREATE_FLAGS_QUEUE_PRIORITY_MASK);
    if (!r) *created = &queue;
    return r;
}
int main(void)
{
    struct amdgpu_device adev = {0};
    struct drm_device dev = {&adev};
    struct drm_file filp = {0};
    struct amdgpu_usermode_queue *created = NULL;
    union drm_amdgpu_userq args = {.in = {.op = AMDGPU_USERQ_OP_CREATE,
        .ip_type = AMDGPU_HW_IP_GFX, .queue_va = 0x10000, .queue_size = 4096,
        .wptr_va = 0x20000, .rptr_va = 0x30000}};
    unsigned checks = 0;
    /* Unsupported IPs and another backend must not advertise or accept it. */
    for (unsigned ip = 0; ip <= AMDGPU_HW_IP_NUM + 1; ++ip) {
        args.in.ip_type = ip;
        args.in.flags = 0;
        bool valid_ip = ip == AMDGPU_HW_IP_GFX || ip == AMDGPU_HW_IP_COMPUTE || ip == AMDGPU_HW_IP_DMA;
        assert(!amdgpu_userq_supports_relative_priority(&adev, ip));
        assert(amdgpu_userq_create_relative(&dev, &args, &filp, 0, &created) ==
               (valid_ip ? -EOPNOTSUPP : -EINVAL));
        if (ip < AMDGPU_HW_IP_NUM) adev.userq_funcs[ip] = &other_funcs;
        assert(!amdgpu_userq_supports_relative_priority(&adev, ip));
        assert(amdgpu_userq_create_relative(&dev, &args, &filp, 0, &created) ==
               (valid_ip ? -EOPNOTSUPP : -EINVAL));
        assert(!creates);
        ++checks;
    }
    for (unsigned ip = 0; ip < AMDGPU_HW_IP_NUM; ++ip) adev.userq_funcs[ip] = &userq_mes_funcs;
    for (unsigned ip = 0; ip < AMDGPU_HW_IP_NUM; ++ip) {
        bool supported = ip == AMDGPU_HW_IP_GFX || ip == AMDGPU_HW_IP_COMPUTE;
        assert(amdgpu_userq_supports_relative_priority(&adev, ip) == supported);
    }
    for (unsigned ip = 0; ip < 2; ++ip) {
        args.in.ip_type = ip ? AMDGPU_HW_IP_COMPUTE : AMDGPU_HW_IP_GFX;
        for (int relative = -8; relative <= 8; ++relative) {
            bool valid = relative >= -7 && relative <= 7;
            for (unsigned global = 0; global < 4; ++global) {
                args.in.flags = global;
                assert(!amdgpu_userq_input_args_validate(&dev, &args, &filp));
                creates = 0; created = NULL;
                assert(amdgpu_userq_create_relative(&dev, &args, &filp, relative, &created) ==
                       (!valid ? -EINVAL : global == 3 ? -EACCES : 0));
                assert(creates == valid);
                assert(created == (valid && global != 3 ? &queue : NULL));
                if (valid) assert(last_relative == relative);
                assert(amdgpu_userq_priority_permit(&filp, global) == (global == 3 ? -EACCES : 0));
                privileged = true;
                assert(amdgpu_userq_create_relative(&dev, &args, &filp, relative, &created) == (valid ? 0 : -EINVAL));
                privileged = false;
                filp.master = true;
                assert(amdgpu_userq_create_relative(&dev, &args, &filp, relative, &created) == (valid ? 0 : -EINVAL));
                filp.master = false;
                ++checks;
            }
        }
    }
    /* Former private flag bits are rejected on both standard and private paths. */
    for (unsigned bit = 3; bit < 32; ++bit) {
        args.in.flags = 1u << bit;
        assert(amdgpu_userq_input_args_validate(&dev, &args, &filp) == -EINVAL);
        assert(amdgpu_userq_create_relative(&dev, &args, &filp, 0, &created) == -EINVAL);
        ++checks;
    }
    args.in.flags = 0;
    enabled = false;
    assert(amdgpu_userq_create_relative(&dev, &args, &filp, 0, &created) == -EOPNOTSUPP);
    enabled = true;
    args.in.flags = 1u << 31;
    args.in.op = AMDGPU_USERQ_OP_FREE;
    assert(amdgpu_userq_create_relative(&dev, &args, &filp, 0, &created) == -EINVAL);
    assert(amdgpu_userq_input_args_validate(&dev, &args, &filp) == -EINVAL);
    args = (union drm_amdgpu_userq){.in = {.op = AMDGPU_USERQ_OP_FREE, .queue_id = 1}};
    assert(!amdgpu_userq_input_args_validate(&dev, &args, &filp));
    printf("UserQ priority: %u validation combinations passed\n", checks);
    return 0;
}
'''

PACKETS = r'''
#if MES_VERSION == 11
#include "mes_v11_api_def.h"
#define ADD mes_v11_0_add_hw_queue
#else
#include "mes_v12_api_def.h"
#define ADD mes_v12_0_add_hw_queue
#endif
#define AMDGPU_RING_TYPE_GFX 0
#define AMDGPU_RING_TYPE_COMPUTE 1
#define AMDGPU_RING_TYPE_SDMA 2
#define AMDGPU_RING_TYPE_MES 3
#define WARN(...) ((void)0)
#define AMDGPU_GFXHUB(n) (n)
#define AMDGPU_MES_API_VERSION_MASK 0x00ff0000
#define AMDGPU_MES_API_VERSION_SHIFT 16
#define AMDGPU_MES_SCHED_PIPE 1
struct amdgpu_device;
struct amdgpu_mes { struct amdgpu_device *adev; uint32_t sched_version; };
struct amdgpu_vmhub { uint32_t vm_cntx_cntl; };
struct amdgpu_device { struct amdgpu_mes mes; struct amdgpu_vmhub vmhub[1]; };
static union MESAPI__ADD_QUEUE add_packet;
#if MES_VERSION == 12
static union MESAPI__CHANGE_GANG_PRIORITY_LEVEL change_packet;
#endif
static int capture_packet(const void *data, size_t size)
{
    const union MESAPI__ADD_QUEUE *p = data;
    assert(p->header.type == MES_API_TYPE_SCHEDULER);
    assert(size == 4 * API_FRAME_SIZE_IN_DWORDS && p->header.dwsize == API_FRAME_SIZE_IN_DWORDS);
    if (p->header.opcode == MES_SCH_API_ADD_QUEUE)
        memcpy(&add_packet, data, size);
#if MES_VERSION == 12
    else if (p->header.opcode == MES_SCH_API_CHANGE_GANG_PRORITY)
        memcpy(&change_packet, data, size);
#endif
    else assert(false);
    return 0;
}
static int mes_v11_0_submit_pkt_and_poll_completion(struct amdgpu_mes *mes,
    const void *packet, size_t size, size_t offset) { return capture_packet(packet, size); }
static int mes_v12_0_submit_pkt_and_poll_completion(struct amdgpu_mes *mes, int pipe,
    const void *packet, size_t size, size_t offset) { return capture_packet(packet, size); }
ACTUAL_SOURCE
int main(void)
{
    struct amdgpu_device adev = {0};
    struct amdgpu_mes mes = {.adev = &adev};
    struct mes_add_queue_input input = {.process_id = 123, .gang_context_addr = 0x12340000,
        .gang_quantum = 12500, .queue_type = AMDGPU_RING_TYPE_GFX};
    unsigned checks = 0;
    for (int relative = -7; relative <= 7; ++relative) {
        input.inprocess_gang_priority = (uint32_t)relative;
        input.raw_inprocess_priority = true;
        for (unsigned global = 0; global <= AMDGPU_MES_PRIORITY_LEVEL_REALTIME; ++global) {
            input.gang_global_priority_level = global;
            assert(!ADD(&mes, &input));
            assert(add_packet.inprocess_gang_priority == (uint32_t)relative);
            assert(add_packet.gang_global_priority_level == (uint32_t)convert_to_mes_priority_level(global));
            assert(add_packet.process_id == 123 && add_packet.gang_context_addr == 0x12340000);
            /* Remapping rebuilds the same request without losing its signed value. */
            assert(!ADD(&mes, &input));
            assert(add_packet.inprocess_gang_priority == (uint32_t)relative);
#if MES_VERSION == 12
            struct mes_change_gang_priority_input change = {.gang_context_addr = input.gang_context_addr,
                .gang_quantum = input.gang_quantum, .gang_global_priority_level = global,
                .raw_inprocess_priority = true, .inprocess_gang_priority = relative};
            assert(!mes_v12_0_change_gang_priority(&mes, &change));
            assert(change_packet.inprocess_gang_priority == (uint32_t)relative);
            assert(change_packet.gang_global_priority_level == global);
            assert(change_packet.gang_context_addr == input.gang_context_addr);
            change.raw_inprocess_priority = false;
            assert(!mes_v12_0_change_gang_priority(&mes, &change));
            assert(change_packet.inprocess_gang_priority == AMDGPU_MES_PRIORITY_LEVEL_NORMAL);
#endif
            ++checks;
        }
    }
    /* Old UserQ and KFD callers keep their enum conversion, including values above REALTIME. */
    for (unsigned priority = 0; priority < 16; ++priority) {
        input.raw_inprocess_priority = false;
        input.inprocess_gang_priority = priority;
        assert(!ADD(&mes, &input));
        assert(add_packet.inprocess_gang_priority == (uint32_t)convert_to_mes_priority_level(priority));
        ++checks;
    }
    printf("MES %d: %u priority packet combinations passed\n", MES_VERSION, checks);
    return 0;
}
'''

ROLLBACK = r'''
struct amdgpu_usermode_queue { unsigned refs, destroyed; };
struct xarray { struct amdgpu_usermode_queue *slots[4]; bool locked; };
struct amdgpu_fpriv { struct { struct xarray userq_xa; } userq_mgr; };
struct drm_file { struct amdgpu_fpriv *driver_priv; };
static void xa_lock(struct xarray *array) { assert(!array->locked); array->locked = true; }
static void xa_unlock(struct xarray *array) { assert(array->locked); array->locked = false; }
static struct amdgpu_usermode_queue *xa_load(struct xarray *array, u32 id)
{
    assert(array->locked && id < 4);
    return array->slots[id];
}
static void __xa_erase(struct xarray *array, u32 id)
{
    assert(array->locked && id < 4);
    array->slots[id] = NULL;
}
static void amdgpu_userq_put(struct amdgpu_usermode_queue *queue)
{
    assert(queue->refs && !queue->destroyed);
    if (!--queue->refs) ++queue->destroyed;
}
ACTUAL_SOURCE
int main(void)
{
    for (unsigned race = 0; race < 3; ++race) {
        struct amdgpu_usermode_queue original = {.refs = 2}, replacement = {.refs = 1};
        struct amdgpu_fpriv priv = {0};
        struct drm_file filp = {.driver_priv = &priv};
        struct xarray *array = &priv.userq_mgr.userq_xa;
        array->slots[1] = &original;
        /* Caller retains the pin while another thread frees or replaces the ID. */
        if (race) {
            array->slots[1] = race == 2 ? &replacement : NULL;
            amdgpu_userq_put(&original);
        }
        /* An unrelated ID must never release the registry reference. */
        amdgpu_userq_create_rollback(&filp, 2, &original);
        assert(original.refs == (race ? 1u : 2u) && !original.destroyed);
        amdgpu_userq_create_rollback(&filp, 1, &original);
        assert(!array->locked && original.refs == 1 && !original.destroyed);
        assert(array->slots[1] == (race == 2 ? &replacement : NULL));
        assert(replacement.refs == 1 && !replacement.destroyed);
        /* Rechecking cannot drop the caller's reference or the replacement. */
        amdgpu_userq_create_rollback(&filp, 1, &original);
        assert(original.refs == 1 && replacement.refs == 1);
        amdgpu_userq_put(&original);
        assert(original.destroyed == 1);
        amdgpu_userq_put(&replacement);
        assert(replacement.destroyed == 1);
    }
    puts("UserQ creation rollback: normal, concurrent FREE and ID reuse passed");
    return 0;
}
'''

if __name__ == '__main__':
    unittest.main()
