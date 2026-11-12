// SPDX-License-Identifier: GPL-2.0-only
/* Private Salkim operations share the DRM file's handles and access checks. */

#include <linux/build_bug.h>
#include <linux/uaccess.h>
#include <drm/drm_ioctl.h>

#include "amdgpu.h"
#include "amdgpu_salkim.h"

#define AMDGPU_SALKIM_MAX_PAYLOAD 4096

/* Catch accidental ABI changes when the underlying upstream types evolve. */
static_assert(sizeof(struct drm_amdgpu_salkim_request) == 32);
static_assert(offsetof(struct drm_amdgpu_salkim_request, payload) == 16);
static_assert(offsetof(struct drm_amdgpu_salkim_request, reserved) == 28);
static_assert(sizeof(struct drm_amdgpu_salkim_caps) == 64);
static_assert(offsetof(struct drm_amdgpu_salkim_caps, features) == 16);
static_assert(offsetof(struct drm_amdgpu_salkim_caps, relative_priority_ip_mask) == 36);

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

	*caps = (struct drm_amdgpu_salkim_caps) {
		.magic = AMDGPU_SALKIM_MAGIC,
		.abi = AMDGPU_SALKIM_ABI,
		.size = sizeof(*caps),
	};

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
