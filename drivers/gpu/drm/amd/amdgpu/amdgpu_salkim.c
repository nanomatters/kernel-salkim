// SPDX-License-Identifier: GPL-2.0-only
/* Private Salkim operations share the DRM file's handles and access checks. */

#include <linux/build_bug.h>
#include <linux/uaccess.h>
#include <drm/drm_ioctl.h>

#include "amdgpu.h"
#include "amdgpu_salkim.h"
#include "amdgpu_userq.h"

#define AMDGPU_SALKIM_MAX_PAYLOAD 4096

/* Catch accidental ABI changes when the underlying upstream types evolve. */
static_assert(sizeof(struct drm_amdgpu_salkim_request) == 32);
static_assert(offsetof(struct drm_amdgpu_salkim_request, payload) == 16);
static_assert(offsetof(struct drm_amdgpu_salkim_request, reserved) == 28);
static_assert(sizeof(struct drm_amdgpu_salkim_caps) == 64);
static_assert(offsetof(struct drm_amdgpu_salkim_caps, features) == 16);
static_assert(offsetof(struct drm_amdgpu_salkim_caps, relative_priority_ip_mask) == 36);
static_assert(sizeof(struct drm_amdgpu_gem_va_batch) == 32);
static_assert(offsetof(struct drm_amdgpu_gem_va_batch, reserved) == 16);
static_assert(sizeof(struct drm_amdgpu_gem_va_batch_op) == 32);
static_assert(sizeof(struct drm_amdgpu_gem_va) == 64);
static_assert(offsetof(struct drm_amdgpu_gem_va, vm_timeline_point) == 40);
static_assert(sizeof(union drm_amdgpu_userq) == 72);
static_assert(sizeof(struct drm_amdgpu_salkim_userq_create) == 80);
static_assert(offsetof(struct drm_amdgpu_salkim_userq_create, relative_priority) == 72);

static int amdgpu_salkim_validate_request(const struct drm_amdgpu_salkim_request *request)
{
	u32 minimum;

	if (request->magic != AMDGPU_SALKIM_MAGIC || request->reserved)
		return -EINVAL;
	if (request->abi != AMDGPU_SALKIM_ABI)
		return -EOPNOTSUPP;

	switch (request->op) {
	case AMDGPU_SALKIM_OP_CAPS:
		minimum = sizeof(struct drm_amdgpu_salkim_caps);
		break;
	case AMDGPU_SALKIM_OP_VA_BATCH:
		minimum = sizeof(struct drm_amdgpu_gem_va_batch);
		break;
	case AMDGPU_SALKIM_OP_UNMAP:
		minimum = sizeof(struct drm_amdgpu_gem_va);
		break;
	case AMDGPU_SALKIM_OP_USERQ_CREATE:
		minimum = sizeof(struct drm_amdgpu_salkim_userq_create);
		break;
	default:
		return -ENOTTY;
	}

	if (!request->payload || request->payload_size < minimum ||
	    request->payload_size > AMDGPU_SALKIM_MAX_PAYLOAD)
		return -EINVAL;

	return 0;
}

static void amdgpu_salkim_get_caps(struct drm_device *dev, struct drm_file *filp,
				   struct drm_amdgpu_salkim_caps *caps)
{
	struct amdgpu_device *adev = drm_to_adev(dev);
	struct amdgpu_fpriv *fpriv = filp->driver_priv;
	u32 ip;

	*caps = (struct drm_amdgpu_salkim_caps) {
		.magic = AMDGPU_SALKIM_MAGIC,
		.abi = AMDGPU_SALKIM_ABI,
		.size = sizeof(*caps),
		.features = AMDGPU_SALKIM_CAP_EXPLICIT_UNMAP,
	};
	if (amdgpu_gem_va_batch_supported(adev, &fpriv->vm, false))
		caps->features |= AMDGPU_SALKIM_CAP_BATCH_LEGACY;
	if (amdgpu_gem_va_batch_supported(adev, &fpriv->vm, true))
		caps->features |= AMDGPU_SALKIM_CAP_BATCH_TIMELINE;
	if (caps->features & (AMDGPU_SALKIM_CAP_BATCH_LEGACY |
			      AMDGPU_SALKIM_CAP_BATCH_TIMELINE)) {
		caps->max_ops = AMDGPU_GEM_VA_BATCH_MAX_OPS;
		caps->batch_features = AMDGPU_GEM_VA_BATCH_WAIT_COMPLETE |
			AMDGPU_GEM_VA_BATCH_VM_TIMELINE |
			AMDGPU_GEM_VA_BATCH_CLEAR;
		if (amdgpu_gem_va_batch_no_cs_wait_supported(adev, &fpriv->vm))
			caps->batch_features |= AMDGPU_GEM_VA_BATCH_NO_CS_WAIT;
	}
	for (ip = 0; ip < AMDGPU_HW_IP_NUM; ++ip) {
		if (amdgpu_userq_supports_relative_priority(adev, ip))
			caps->relative_priority_ip_mask |= BIT(ip);
	}
}

static int amdgpu_salkim_ioctl(struct drm_device *dev, void *data,
			       struct drm_file *filp)
{
	const struct drm_amdgpu_salkim_request *request = data;
	void __user *payload = u64_to_user_ptr(request->payload);
	int r;

	r = amdgpu_salkim_validate_request(request);
	if (r)
		return r;

	switch (request->op) {
	case AMDGPU_SALKIM_OP_CAPS: {
		struct drm_amdgpu_salkim_caps caps;

		amdgpu_salkim_get_caps(dev, filp, &caps);
		return copy_to_user(payload, &caps, sizeof(caps)) ? -EFAULT : 0;
	}
	case AMDGPU_SALKIM_OP_VA_BATCH: {
		struct drm_amdgpu_gem_va_batch args;

		r = copy_struct_from_user(&args, sizeof(args), payload, request->payload_size);
		if (r)
			return r;
		return amdgpu_gem_va_batch_ioctl(dev, &args, filp);
	}
	case AMDGPU_SALKIM_OP_UNMAP: {
		struct drm_amdgpu_gem_va args;

		r = copy_struct_from_user(&args, sizeof(args), payload, request->payload_size);
		if (r)
			return r;
		return amdgpu_gem_va_unmap_ioctl(dev, &args, filp);
	}
	case AMDGPU_SALKIM_OP_USERQ_CREATE: {
		struct drm_amdgpu_salkim_userq_create args;
		struct amdgpu_usermode_queue *queue;

		r = copy_struct_from_user(&args, sizeof(args), payload, request->payload_size);
		if (r)
			return r;
		if (args.reserved)
			return -EINVAL;
		r = amdgpu_userq_create_relative(dev, &args.args, filp,
						 args.relative_priority, &queue);
		if (r)
			return r;
		if (copy_to_user(payload, &args, sizeof(args))) {
			/* Never remove a replacement queue after concurrent ID reuse. */
			amdgpu_userq_create_rollback(filp, args.args.out.queue_id, queue);
			r = -EFAULT;
		}
		amdgpu_userq_put(queue);
		return r;
	}
	default:
		return -ENOTTY;
	}
}

long amdgpu_salkim_dispatch(struct file *filp, unsigned long arg)
{
	struct drm_amdgpu_salkim_request request;

	if (copy_from_user(&request, (void __user *)arg, sizeof(request)))
		return -EFAULT;

	return drm_ioctl_kernel(filp, amdgpu_salkim_ioctl, &request,
				DRM_AUTH | DRM_RENDER_ALLOW);
}
