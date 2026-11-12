/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef __AMDGPU_SALKIM_INTERNAL_H__
#define __AMDGPU_SALKIM_INTERNAL_H__

#include <drm/amdgpu_salkim.h>

struct file;

long amdgpu_salkim_dispatch(struct file *filp, unsigned long arg);

#endif
