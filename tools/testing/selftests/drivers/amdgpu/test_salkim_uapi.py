#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check private ioctl admission, acceptance and copy-fault rollback without a GPU."""
import os
import pathlib
import re
import shlex
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(os.environ['AMDGPU_TEST_ROOT']) if 'AMDGPU_TEST_ROOT' in os.environ else next(
    root for root in pathlib.Path(__file__).resolve().parents
    if (root / 'drivers/gpu/drm/amd/amdgpu/amdgpu_salkim.c').is_file())
AMD = ROOT / 'drivers/gpu/drm/amd/amdgpu'


def function(source, name):
    match = re.search(r'^(?:static )?(?:int|void|long)\s+' + name + r'\(.*?^\}', source, re.M | re.S)
    if not match:
        raise AssertionError(name)
    return match.group()


def flags():
    result = shlex.split(os.environ.get('CC', 'cc'))
    result += ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
               '-Wno-unused-parameter', '-D__user=', '-I' + str(ROOT / 'include/uapi/drm')]
    result += shlex.split(subprocess.check_output(['pkg-config', '--cflags', 'libdrm'], text=True))
    return result


class SalkimUapiTests(unittest.TestCase):
    def test_compat_layout(self):
        with tempfile.TemporaryDirectory(prefix='salkim-abi-') as temporary:
            path = pathlib.Path(temporary)
            (path / 'abi.c').write_text(ABI)
            for bits in (32, 64):
                with self.subTest(bits=bits):
                    subprocess.run(flags() + [f'-m{bits}', '-c', str(path / 'abi.c'),
                                   '-o', str(path / f'abi{bits}.o')], check=True, timeout=30)

    def test_dispatch_and_copy_failures(self):
        source = (AMD / 'amdgpu_salkim.c').read_text()
        actual = '\n'.join(function(source, name) for name in (
            'amdgpu_salkim_validate_request', 'amdgpu_salkim_get_caps',
            'amdgpu_salkim_ioctl', 'amdgpu_salkim_dispatch'))
        with tempfile.TemporaryDirectory(prefix='salkim-dispatch-') as temporary:
            path = pathlib.Path(temporary)
            (path / 'test.c').write_text(HARNESS.replace('ACTUAL_SOURCE', actual))
            command = flags()
            if os.environ.get('USERQ_TEST_SANITIZERS'):
                command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                            '-fno-sanitize-recover=all']
            subprocess.run(command + [str(path / 'test.c'), '-o', str(path / 'test')],
                           check=True, timeout=30)
            subprocess.run([str(path / 'test')], check=True, timeout=30)

    def test_compat_command_routing(self):
        actual = function((AMD / 'amdgpu_ioc32.c').read_text(), 'amdgpu_kms_compat_ioctl')
        with tempfile.TemporaryDirectory(prefix='salkim-compat-') as temporary:
            path = pathlib.Path(temporary)
            (path / 'test.c').write_text(COMPAT.replace('ACTUAL_SOURCE', actual))
            subprocess.run(flags() + [str(path / 'test.c'), '-o', str(path / 'test')],
                           check=True, timeout=30)
            subprocess.run([str(path / 'test')], check=True, timeout=30)

    def test_only_exact_private_command_is_intercepted(self):
        source = (AMD / 'amdgpu_drv.c').read_text()
        actual = function(source, 'amdgpu_drm_ioctl')
        self.assertRegex(actual, r'if \(cmd == AMDGPU_SALKIM_IOCTL\)\s*'
                         r'ret = amdgpu_salkim_dispatch\(filp, arg\);\s*else\s*'
                         r'ret = drm_ioctl\(filp, cmd, arg\);')
        self.assertLess(actual.index('pm_runtime_get_sync'), actual.index('amdgpu_salkim_dispatch'))
        self.assertGreater(actual.index('pm_runtime_put_autosuspend'), actual.index('amdgpu_salkim_dispatch'))
        compat = function((AMD / 'amdgpu_ioc32.c').read_text(), 'amdgpu_kms_compat_ioctl')
        self.assertRegex(compat, r'if \(cmd == AMDGPU_SALKIM_IOCTL\)\s*'
                         r'return amdgpu_drm_ioctl\(filp, cmd, \(unsigned long\)compat_ptr\(arg\)\);')
        standard = (ROOT / 'include/uapi/drm/amdgpu_drm.h').read_text()
        for name in ('AMDGPU_INFO_GEM_VA_BATCH', 'DRM_AMDGPU_GEM_VA_BATCH',
                     'AMDGPU_IDS_FLAGS_VM_UNMAP_EXPLICIT_SYNC', 'AMDGPU_VM_UNMAP_EXPLICIT_SYNC',
                     'AMDGPU_HW_IP_CAP_USERQ_RELATIVE_PRIORITY',
                     'AMDGPU_USERQ_CREATE_FLAGS_RELATIVE_PRIORITY'):
            self.assertNotIn(name, standard)


ABI = r'''
#include <stddef.h>
#include "amdgpu_salkim.h"
_Static_assert(sizeof(struct drm_amdgpu_salkim_request) == 32, "envelope size");
_Static_assert(offsetof(struct drm_amdgpu_salkim_request, payload) == 16, "payload pointer");
_Static_assert(offsetof(struct drm_amdgpu_salkim_request, payload_size) == 24, "payload size");
_Static_assert(sizeof(struct drm_amdgpu_salkim_caps) == 64, "capability size");
_Static_assert(offsetof(struct drm_amdgpu_salkim_caps, features) == 16, "feature bits");
_Static_assert(offsetof(struct drm_amdgpu_salkim_caps, reserved) == 40, "future extension");
_Static_assert(_IOC_TYPE(AMDGPU_SALKIM_IOCTL) != DRM_IOCTL_BASE, "separate namespace");
_Static_assert(_IOC_NR(AMDGPU_SALKIM_IOCTL) == 0x4b, "fixed command");
_Static_assert(_IOC_SIZE(AMDGPU_SALKIM_IOCTL) == 32, "fixed envelope");
_Static_assert(_IOC_DIR(AMDGPU_SALKIM_IOCTL) == _IOC_WRITE, "no envelope copyout");
'''

COMPAT = r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include "amdgpu_salkim.h"
#define DRM_IOCTL_NR(cmd) _IOC_NR(cmd)
#define DRM_IOCTL_TYPE(cmd) _IOC_TYPE(cmd)
#define compat_ptr(arg) ((void *)(uintptr_t)(uint32_t)(arg))
struct file { int unused; };
static unsigned native_calls, compat_calls;
static unsigned long native_arg;
static long amdgpu_drm_ioctl(struct file *filp, unsigned cmd, unsigned long arg)
{ ++native_calls; native_arg = arg; return 101; }
static long drm_compat_ioctl(struct file *filp, unsigned cmd, unsigned long arg)
{ ++compat_calls; return 102; }
ACTUAL_SOURCE
int main(void)
{
    struct file filp = {0};
    assert(amdgpu_kms_compat_ioctl(&filp, AMDGPU_SALKIM_IOCTL, 0xfeedface00001234UL) == 101);
    assert(native_arg == 0x1234 && native_calls == 1 && !compat_calls);
    for (unsigned nr = 0; nr < 256; ++nr) {
        assert(amdgpu_kms_compat_ioctl(&filp, _IO('S', nr), 0) == -ENOTTY);
        assert(amdgpu_kms_compat_ioctl(&filp, _IOWR('S', nr, uint32_t), 0) == -ENOTTY);
    }
    assert(native_calls == 1 && !compat_calls);
    assert(amdgpu_kms_compat_ioctl(&filp, _IO('d', 0), 0) == 102);
    assert(amdgpu_kms_compat_ioctl(&filp, _IO('d', DRM_COMMAND_BASE), 0) == 101);
    assert(native_calls == 2 && compat_calls == 1);
    return 0;
}
'''

HARNESS = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "amdgpu_salkim.h"
typedef uint32_t u32;
#define AMDGPU_SALKIM_MAX_PAYLOAD 4096
#define BIT(n) (1u << (n))
#define DRM_AUTH 1
#define DRM_RENDER_ALLOW 2
#define u64_to_user_ptr(value) ((void *)(uintptr_t)(value))
struct amdgpu_vm { int unused; };
struct amdgpu_fpriv { struct amdgpu_vm vm; };
struct amdgpu_device { int unused; };
struct drm_device { struct amdgpu_device adev; };
struct drm_file { struct amdgpu_fpriv *driver_priv; };
struct file { struct drm_device *dev; struct drm_file *private_data; };
#define drm_to_adev(dev) (&(dev)->adev)
static unsigned copies_in, copies_out, batch_calls, unmap_calls, create_calls, free_calls;
static int backend_result, permit_result;
static bool fail_in, fail_out, mutate_user_request;
static struct drm_amdgpu_salkim_request *userspace_request;
static size_t copy_from_user(void *dst, const void *src, size_t len)
{
    ++copies_in;
    if (fail_in) return len;
    memcpy(dst, src, len);
    return 0;
}
static size_t copy_to_user(void *dst, const void *src, size_t len)
{
    ++copies_out;
    if (fail_out) return len;
    memcpy(dst, src, len);
    return 0;
}
static int copy_struct_from_user(void *dst, size_t size, const void *src, size_t usize)
{
    ++copies_in;
    if (fail_in) return -EFAULT;
    assert(usize >= size && usize <= AMDGPU_SALKIM_MAX_PAYLOAD);
    for (size_t i = size; i < usize; ++i)
        if (((const unsigned char *)src)[i]) return -E2BIG;
    memcpy(dst, src, size);
    return 0;
}
static bool amdgpu_gem_va_batch_supported(struct amdgpu_device *adev, struct amdgpu_vm *vm,
                                         bool timeline) { return true; }
static int amdgpu_gem_va_batch_ioctl(struct drm_device *dev, void *data, struct drm_file *filp)
{
    ++batch_calls;
    return backend_result;
}

static int drm_ioctl_kernel(struct file *file,
                            int (*callback)(struct drm_device *, void *, struct drm_file *),
                            void *data, unsigned flags)
{
    assert(flags == (DRM_AUTH | DRM_RENDER_ALLOW));
    if (permit_result) return permit_result;
    if (mutate_user_request) {
        userspace_request->op = 0xffffffff;
        userspace_request->payload = 0;
    }
    return callback(file->dev, data, file->private_data);
}
ACTUAL_SOURCE
static void reset_counters(void)
{
    copies_in = copies_out = batch_calls = unmap_calls = create_calls = free_calls = 0;
    backend_result = permit_result = 0;
    fail_in = fail_out = mutate_user_request = false;
}
static void no_backend(void)
{ assert(!batch_calls && !unmap_calls && !create_calls && !free_calls); }
int main(void)
{
    struct drm_device dev = {0};
    struct amdgpu_fpriv fpriv = {0};
    struct drm_file filp = {&fpriv};
    struct file file = {&dev, &filp};
    union {
        uint64_t alignment;
        unsigned char bytes[4096];
        struct drm_amdgpu_salkim_caps caps;
        struct drm_amdgpu_gem_va_batch batch;
    } payload = {0};
    const unsigned sizes[] = {0, sizeof(payload.caps), sizeof(payload.batch), 0, 0};
    struct drm_amdgpu_salkim_request req = {.magic = AMDGPU_SALKIM_MAGIC,
        .abi = AMDGPU_SALKIM_ABI, .op = AMDGPU_SALKIM_OP_CAPS,
        .payload = (uintptr_t)&payload, .payload_size = sizeof(payload.caps)};
    userspace_request = &req;
    struct drm_amdgpu_salkim_request good = req;
    req.magic ^= 1;
    assert(amdgpu_salkim_ioctl(&dev, &req, &filp) == -EINVAL);
    req = good; req.abi++;
    assert(amdgpu_salkim_ioctl(&dev, &req, &filp) == -EOPNOTSUPP);
    req = good; req.op = 99;
    assert(amdgpu_salkim_ioctl(&dev, &req, &filp) == -ENOTTY);
    req = good; req.reserved = 1;
    assert(amdgpu_salkim_ioctl(&dev, &req, &filp) == -EINVAL);
    req = good; req.payload = 0;
    assert(amdgpu_salkim_ioctl(&dev, &req, &filp) == -EINVAL);
    no_backend(); assert(!copies_in && !copies_out);
    for (unsigned op = 1; op <= 4; ++op) {
        if (!sizes[op]) {
            req = good; req.op = op;
            assert(amdgpu_salkim_ioctl(&dev, &req, &filp) == -ENOTTY);
            continue;
        }
        req = good; req.op = op;
        for (unsigned size = 0; size < sizes[op]; ++size) {
            req.payload_size = size;
            assert(amdgpu_salkim_ioctl(&dev, &req, &filp) == -EINVAL);
        }
        req.payload_size = 4097;
        assert(amdgpu_salkim_ioctl(&dev, &req, &filp) == -EINVAL);
    }
    no_backend(); assert(!copies_in && !copies_out);
    req = good;
    fail_in = true;
    assert(amdgpu_salkim_dispatch(&file, (uintptr_t)&req) == -EFAULT);
    reset_counters(); permit_result = -EACCES;
    assert(amdgpu_salkim_dispatch(&file, (uintptr_t)&req) == -EACCES);
    no_backend(); assert(copies_in == 1 && !copies_out);
    reset_counters(); permit_result = -ENODEV;
    assert(amdgpu_salkim_dispatch(&file, (uintptr_t)&req) == -ENODEV);
    no_backend();
    reset_counters(); mutate_user_request = true;
    assert(!amdgpu_salkim_dispatch(&file, (uintptr_t)&req));
    assert(payload.caps.magic == AMDGPU_SALKIM_MAGIC && payload.caps.abi == AMDGPU_SALKIM_ABI);
    assert(payload.caps.size == sizeof(payload.caps));
    assert(!payload.caps.relative_priority_ip_mask);
    for (unsigned i = 0; i < 3; ++i) assert(!payload.caps.reserved[i]);
    no_backend(); assert(copies_in == 1 && copies_out == 1);
    reset_counters(); req = good; fail_out = true;
    assert(amdgpu_salkim_ioctl(&dev, &req, &filp) == -EFAULT);
    no_backend();
    /* Unknown output tail is left untouched, so a zeroed caller has a known prefix. */
    reset_counters(); memset(&payload, 0, sizeof(payload)); req.payload_size = sizeof(payload);
    assert(!amdgpu_salkim_ioctl(&dev, &req, &filp));
    for (unsigned i = sizeof(payload.caps); i < sizeof(payload); ++i) assert(!payload.bytes[i]);
    for (unsigned op = 2; op <= 4; ++op) {
        if (!sizes[op]) continue;
        reset_counters(); memset(&payload, 0, sizeof(payload));
        req = good; req.op = op; req.payload_size = sizes[op];
        fail_in = true;
        assert(amdgpu_salkim_ioctl(&dev, &req, &filp) == -EFAULT);
        no_backend(); assert(!copies_out);
        reset_counters(); req.payload_size++;
        payload.bytes[sizes[op]] = 1;
        assert(amdgpu_salkim_ioctl(&dev, &req, &filp) == -E2BIG);
        no_backend(); assert(!copies_out);
        reset_counters(); payload.bytes[sizes[op]] = 0;
        backend_result = -EBUSY;
        assert(amdgpu_salkim_ioctl(&dev, &req, &filp) == -EBUSY);
        assert(!copies_out && !free_calls);
        reset_counters(); backend_result = -EIO;
        assert(amdgpu_salkim_ioctl(&dev, &req, &filp) == -EIO);
        assert(!copies_out && !free_calls);
        reset_counters(); fail_out = true;
        assert(amdgpu_salkim_ioctl(&dev, &req, &filp) == (op == 4 ? -EFAULT : 0));
        assert(copies_out == (op == 4));
        assert(free_calls == (op == 4));
        assert(batch_calls + unmap_calls + create_calls == 1);
    }
    puts("Private ioctl validation, admission, zero-tail extension, acceptance and copy-fault checks passed");
    return 0;
}
'''

if __name__ == '__main__':
    unittest.main()
