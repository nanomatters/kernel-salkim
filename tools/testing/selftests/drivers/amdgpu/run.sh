#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu

test_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source_root=$(CDPATH= cd -- "$test_dir/../../../../.." && pwd)
if [ ! -f "$source_root/drivers/gpu/drm/amd/amdgpu/amdgpu_userq.c" ]; then
    echo "AMDGPU regression tests require the kernel source tree # SKIP"
    exit 4
fi
export PYTHONDONTWRITEBYTECODE=1
exec python3 -m unittest discover -s "$test_dir" -p 'test_*.py' -v
