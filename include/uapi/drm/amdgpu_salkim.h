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
#define AMDGPU_SALKIM_OP_VA_BATCH 2

#define AMDGPU_SALKIM_CAP_BATCH_LEGACY (1ULL << 0)

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


#define AMDGPU_GEM_VA_BATCH_MAX_OPS	4096

/* The caller has completed the dependencies of the replaced ranges.
 * Eligible retirements may omit implicit rendering waits. CS update waits,
 * movement dependencies and invalidation rules are unchanged.
 */
#define AMDGPU_GEM_VA_BATCH_WAIT_COMPLETE	(1U << 0)

/* REPLACE operations, in array order. PRT with handle 0 unbinds a range.
 * DELAY_UPDATE is not accepted; the kernel manages pending updates.
 */
struct drm_amdgpu_gem_va_batch_op {
	__u32 handle;
	__u32 flags;
	__u64 va_address;
	__u64 offset_in_bo;
	__u64 map_size;
};

/* Batched GEM_VA acceptance for kernel-queue VMs, not async VM_BIND.
 * Probe SALKIM capabilities before use. Optional flags must be advertised
 * in batch_features. Reserved fields must be zero. KFD, user-queue and CPU-updated VMs
 * must use the legacy GEM_VA path.
 *
 * A negative ioctl return leaves every mapping unchanged. A zero return accepts
 * the entire array, in order. The request is input-only, so no copy-out can fail
 * after acceptance. There is no partial count, secondary status or safe replay
 * of a successful request.
 *
 * Acceptance does not mean GPU completion. As with GEM_VA, page-table updates
 * may remain pending when the VM is evicted or an update fails. The kernel keeps
 * that work for command-submission validation, which must complete preparation
 * successfully before submitting commands. Existing update-fence dependencies
 * and TLB-invalidation rules remain in effect. Persistent update failures are
 * reported by command submission, not by replaying the accepted batch.
 * No userspace semaphore is signaled by this ioctl.
 */
struct drm_amdgpu_gem_va_batch {
	__u64 operations;
	__u32 count;
	__u32 flags;
	__u64 reserved[2];
};

#endif
