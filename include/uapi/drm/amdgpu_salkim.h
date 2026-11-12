/* SPDX-License-Identifier: GPL-2.0 WITH Linux-syscall-note */
#ifndef __AMDGPU_SALKIM_H__
#define __AMDGPU_SALKIM_H__

#include "amdgpu_drm.h"

/*
 * Private Salkim operations on an AMDGPU DRM file. The 'S' ioctl type is
 * separate from DRM's 'd' namespace, including AMDGPU's standard operations.
 * Handles and synchronization objects belong to this same DRM file.
 *
 * This fixed, input-only envelope never copies output after an operation has
 * been accepted. Only CAPS writes its pointed-to payload.
 * All members use fixed widths and have the same offsets on 32/64-bit systems.
 * Unknown operations, ABI values and reserved bits are rejected. Input payloads
 * may have a zero-filled tail, up to 4096 bytes total. Truncated mandatory
 * fields and nonzero unknown tails are rejected before any operation. CAPS
 * callers provide at least 64 bytes, zero the output buffer and accept its
 * known prefix using size. A future response may report a larger size while
 * copying only the prefix that fits the caller's buffer.
 * Extend through new operation IDs and capability bits. Existing base layouts
 * and meanings are immutable. New payload fields must be appended and preserve
 * existing behaviour when zero. Reserved fields must be zero.
 */
#define AMDGPU_SALKIM_MAGIC 0x53414c4b494d5541ULL
#define AMDGPU_SALKIM_ABI 1

struct drm_amdgpu_salkim_request {
	__u64 magic;
	__u32 abi;
	__u32 op;
	__u64 payload;
	__u32 payload_size;
	__u32 reserved;
};

#define AMDGPU_SALKIM_IOCTL _IOW('S', 0x4b, struct drm_amdgpu_salkim_request)

#define AMDGPU_SALKIM_OP_CAPS 1


/* Snapshot for this DRM file. Operations revalidate mutable VM eligibility. */
struct drm_amdgpu_salkim_caps {
	__u64 magic;
	__u32 abi;
	__u32 size;
	__u64 features;
	__u64 batch_features;
	__u32 max_ops;
	/* Bit AMDGPU_HW_IP_* advertises independent relative queue priority. */
	__u32 relative_priority_ip_mask;
	__u64 reserved[3];
};



#endif
