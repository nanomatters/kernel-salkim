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
    match = re.search(r'^(?:static )?(?:int|bool)\s+' + name + r'\(.*?^\}', source, re.M | re.S)
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
    def test_mes_priority_update(self):
        header = (AMD / 'amdgpu_mes.h').read_text()
        declarations = declaration(header, 'enum', 'amdgpu_mes_priority_level')
        declarations += declaration(header, 'struct', 'mes_change_gang_priority_input')
        actual = function((AMD / 'mes_v12_0.c').read_text(), 'mes_v12_0_change_gang_priority')
        run(COMMON + declarations + PACKETS.replace('ACTUAL_SOURCE', actual))


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

PACKETS = r'''

#include "mes_v12_api_def.h"
#define AMDGPU_MES_SCHED_PIPE 1
struct amdgpu_mes { int unused; };
static union MESAPI__CHANGE_GANG_PRIORITY_LEVEL captured;
static int mes_v12_0_submit_pkt_and_poll_completion(struct amdgpu_mes *mes, int pipe,
    const void *packet, size_t size, size_t offset)
{
    assert(pipe == AMDGPU_MES_SCHED_PIPE && size == sizeof(captured));
    assert(offset == offsetof(union MESAPI__CHANGE_GANG_PRIORITY_LEVEL, api_status));
    memcpy(&captured, packet, size);
    assert(captured.header.type == MES_API_TYPE_SCHEDULER);
    assert(captured.header.opcode == MES_SCH_API_CHANGE_GANG_PRORITY);
    assert(captured.header.dwsize == API_FRAME_SIZE_IN_DWORDS);
    return 0;
}
ACTUAL_SOURCE
int main(void)
{
    struct amdgpu_mes mes = {0};
    struct mes_change_gang_priority_input input = {
        .gang_context_addr = UINT64_C(0x123456780000), .gang_quantum = 10000,
        .gang_context_array_index = 7,
    };
    unsigned checks = 0;
    for (int relative = -7; relative <= 7; ++relative) {
        input.inprocess_gang_priority = relative;
        for (unsigned global = 0; global <= AMDGPU_MES_PRIORITY_LEVEL_REALTIME; ++global) {
            input.gang_global_priority_level = global;
            for (unsigned raw = 0; raw < 2; ++raw) {
                input.raw_inprocess_priority = raw;
                assert(!mes_v12_0_change_gang_priority(&mes, &input));
                assert(captured.inprocess_gang_priority == (uint32_t)
                       (raw ? relative : AMDGPU_MES_PRIORITY_LEVEL_NORMAL));
                assert(captured.gang_global_priority_level == global);
                assert(captured.gang_quantum == input.gang_quantum);
                assert(captured.gang_context_addr == input.gang_context_addr);
                assert(captured.gang_context_array_index == input.gang_context_array_index);
                ++checks;
            }
        }
    }
    printf("MES priority update: %u combinations passed\n", checks);
    return 0;
}
'''

if __name__ == '__main__':
    unittest.main()
