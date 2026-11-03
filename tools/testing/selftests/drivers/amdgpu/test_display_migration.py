#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import os
import pathlib
import re
import shlex
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(os.environ.get('DISPLAY_TEST_ROOT', pathlib.Path(__file__).resolve().parents[5]))
DISPLAY = ROOT / 'drivers/gpu/drm/amd/display'


class DisplayMigrationTests(unittest.TestCase):
    def function(self, path, name):
        source = path.read_text()
        match = re.search(r'^(?:(?:static|STATIC_IFN_KUNIT|VISIBLE_IF_KUNIT) )?'
                          r'(?:void|int|bool|unsigned long long) ' + name +
                          r'\(.*?^\}', source, re.M | re.S)
        self.assertIsNotNone(match, name)
        return re.sub(r'^(STATIC_IFN_KUNIT|VISIBLE_IF_KUNIT) ', '', match.group())

    def run_program(self, source):
        with tempfile.TemporaryDirectory() as temporary:
            program = pathlib.Path(temporary) / 'test.c'
            binary = pathlib.Path(temporary) / 'test'
            program.write_text(source)
            command = shlex.split(os.environ.get('CC', 'cc'))
            command += ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter']
            if os.environ.get('DISPLAY_TEST_SANITIZERS'):
                command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
            subprocess.run(command + [str(program), '-o', str(binary)], check=True, timeout=30)
            subprocess.run([str(binary)], check=True, timeout=30)

    def test_hdmi_pcon_capability_selection(self):
        path = DISPLAY / 'amdgpu_dm/amdgpu_dm_connector.c'
        functions = self.function(path, 'get_amd_vsdb') + '\n' + self.function(
            path, 'amdgpu_dm_update_freesync_caps')
        self.run_program(CAPS_HARNESS.replace('ACTUAL_FUNCTIONS', functions))

    def test_active_mode_vrr_maximum(self):
        path = DISPLAY / 'amdgpu_dm/amdgpu_dm_freesync.c'
        functions = self.function(DISPLAY / 'modules/freesync/freesync.c',
                                  'mod_freesync_calc_nominal_field_rate') + '\n'
        functions += self.function(path, 'amdgpu_dm_is_vrr_timing_valid') + '\n'
        functions += self.function(path, 'amdgpu_dm_get_freesync_config_for_crtc')
        threshold = re.search(r'^#define AMDGPU_DM_VRR_MIN_REFRESH_RANGE .*$',
                              path.read_text(), re.M)
        self.assertIsNotNone(threshold)
        self.run_program(MODE_HARNESS.replace('ACTUAL_FUNCTIONS', threshold.group() + '\n' + functions))

    def test_crtc_initialization_cleanup(self):
        function = self.function(DISPLAY / 'amdgpu_dm/amdgpu_dm_crtc.c', 'amdgpu_dm_crtc_init')
        self.run_program(CRTC_HARNESS.replace('ACTUAL_FUNCTION', function))

    def test_panic_kmsg_termination(self):
        function = self.function(ROOT / 'drivers/gpu/drm/drm_panic_helper.c',
                                 'drm_panic_helper_draw_screen_kmsg')
        self.run_program(PANIC_HARNESS.replace('ACTUAL_FUNCTION', function))


CAPS_HARNESS = r'''
#include <assert.h>
#include <stdbool.h>
#include <string.h>
struct edid { int unused; };
struct drm_edid { struct edid raw; };
struct drm_hdmi_vrr_cap { bool supported; unsigned vrr_min, vrr_max; };
struct amdgpu_hdmi_vsdb_info {
    bool replay_mode, freesync_supported;
    unsigned amd_vsdb_version, min_refresh_rate_hz, max_refresh_rate_hz, freesync_mccs_vcp_code;
};
struct drm_connector {
    void *state, *dev;
    struct {
        struct { int min_vfreq, max_vfreq; } monitor_range;
        struct { struct drm_hdmi_vrr_cap vrr_cap; } hdmi;
        struct {
            bool replay_mode, freesync_supported;
            unsigned version, min_frame_rate, max_frame_rate, freesync_vcp_code;
        } amd_vsdb;
    } display_info;
    bool vrr_capable_property, passive_vrr_capable_property;
};
enum adaptive_sync_type { ADAPTIVE_SYNC_TYPE_NONE, ADAPTIVE_SYNC_TYPE_EDP,
                          FREESYNC_TYPE_PCON_IN_WHITELIST };
enum { SIGNAL_TYPE_DISPLAY_PORT, SIGNAL_TYPE_EDP, SIGNAL_TYPE_HDMI_TYPE_A, SIGNAL_TYPE_HDMI_FRL };
struct dc_link {
    enum adaptive_sync_type type;
    struct { bool allow_invalid_MSA_timing_param; } dpcd_caps;
    struct { struct { bool replay_supported; } config; bool replay_feature_enabled; } replay_settings;
};
struct dc_sink {
    int sink_signal;
    struct { int dce_version; } *ctx;
    struct { unsigned freesync_vcp_code; struct { int force_freesync_min_hz; } panel_patch; } edid_caps;
    struct { bool freesync_supported; } mccs_caps;
};
struct amdgpu_dm_connector {
    struct drm_connector base;
    struct dc_sink *dc_sink, *dc_em_sink;
    struct dc_link *dc_link;
    int min_vfreq, max_vfreq;
    bool vrr_max_from_mode, pack_sdp_v1_3;
    enum adaptive_sync_type as_type;
    struct amdgpu_hdmi_vsdb_info vsdb_info;
};
struct dm_connector_state { bool freesync_capable, freesync_on_desktop_capable; };
struct amdgpu_device { struct { bool freesync_module; struct { int ctx; } *dc; } dm; };
#define to_amdgpu_dm_connector(c) ((struct amdgpu_dm_connector *)(c))
#define to_dm_connector_state(s) ((struct dm_connector_state *)(s))
#define drm_to_adev(d) ((struct amdgpu_device *)(d))
#define drm_err(...) ((void)0)
#define drm_dbg_driver(...) ((void)0)
#define drm_edid_connector_update(c, e) ((void)(c), (void)(e))
#define dc_supports_vrr(v) ((v) != 0)
#define drm_edid_raw(e) (&(e)->raw)
#define parse_edid_displayid_vrr(c, e) ((void)(c), (void)(e))
#define dm_get_adaptive_sync_support_type(l) ((l)->type)
static unsigned mccs_reads, mccs_writes;
#define dm_helpers_read_mccs_caps(...) (mccs_reads++)
#define dm_helpers_mccs_vcp_set(...) (mccs_writes++)
#define drm_connector_set_vrr_capable_property(...) ((void)0)
#define drm_connector_set_passive_vrr_capable_property(...) ((void)0)
ACTUAL_FUNCTIONS
static void check(int signal, bool pcon, bool hf, unsigned minimum, unsigned maximum,
                  bool amd, bool vcp, bool do_mccs, bool supported)
{
    struct amdgpu_dm_connector c = {0};
    struct dm_connector_state state = {0};
    struct amdgpu_device device = { .dm.freesync_module = true };
    struct dc_sink sink = { .sink_signal = signal };
    typeof(*sink.ctx) context = { .dce_version = 1 };
    struct dc_link link = { .type = pcon ? FREESYNC_TYPE_PCON_IN_WHITELIST : ADAPTIVE_SYNC_TYPE_NONE };
    struct drm_edid edid = {0};
    c.base.state = &state;
    c.base.dev = &device;
    c.base.display_info.monitor_range.min_vfreq = 50;
    c.base.display_info.monitor_range.max_vfreq = 120;
    c.base.display_info.hdmi.vrr_cap = (struct drm_hdmi_vrr_cap){hf, minimum, maximum};
    c.base.display_info.amd_vsdb.version = amd;
    c.base.display_info.amd_vsdb.freesync_supported = amd;
    c.base.display_info.amd_vsdb.min_frame_rate = 48;
    c.base.display_info.amd_vsdb.max_frame_rate = 144;
    c.base.display_info.amd_vsdb.freesync_vcp_code = vcp ? 1 : 0;
    c.dc_sink = &sink;
    c.dc_link = &link;
    sink.ctx = &context;
    sink.mccs_caps.freesync_supported = true;
    mccs_reads = mccs_writes = 0;
    amdgpu_dm_update_freesync_caps(&c.base, &edid, do_mccs);
    assert(state.freesync_capable == supported);
    assert(state.freesync_on_desktop_capable == supported);
    bool selected_hf = hf && minimum && (pcon || !amd);
    if (selected_hf && supported) {
        assert(c.min_vfreq == (int)minimum && c.max_vfreq == (int)maximum);
        assert(c.vrr_max_from_mode == !maximum);
        assert(!c.vsdb_info.amd_vsdb_version);
        assert(!mccs_reads && !mccs_writes);
        if (!amd) {
            assert(c.base.display_info.monitor_range.min_vfreq == 50);
            assert(c.base.display_info.monitor_range.max_vfreq == 120);
        }
    }
    if (pcon && supported)
        assert(c.pack_sdp_v1_3);
    amdgpu_dm_update_freesync_caps(&c.base, NULL, do_mccs);
    assert(!state.freesync_capable && !c.vrr_max_from_mode && !c.pack_sdp_v1_3);
    assert(!c.min_vfreq && !c.max_vfreq && !c.vsdb_info.amd_vsdb_version);
}
int main(void)
{
    for (unsigned mccs = 0; mccs < 2; mccs++) {
        check(SIGNAL_TYPE_HDMI_TYPE_A, false, true, 48, 120, false, false, mccs, true);
        check(SIGNAL_TYPE_HDMI_FRL, false, true, 48, 0, false, false, mccs, true);
        check(SIGNAL_TYPE_DISPLAY_PORT, true, true, 48, 120, false, false, mccs, true);
        check(SIGNAL_TYPE_DISPLAY_PORT, true, true, 48, 0, false, false, mccs, true);
        check(SIGNAL_TYPE_DISPLAY_PORT, true, true, 48, 120, true, true, mccs, true);
        check(SIGNAL_TYPE_DISPLAY_PORT, true, false, 0, 0, true, true, mccs, true);
        check(SIGNAL_TYPE_DISPLAY_PORT, true, false, 0, 0, true, false, mccs, false);
        check(SIGNAL_TYPE_DISPLAY_PORT, true, false, 0, 0, false, false, mccs, false);
        check(SIGNAL_TYPE_DISPLAY_PORT, false, true, 48, 120, false, false, mccs, false);
        check(SIGNAL_TYPE_HDMI_TYPE_A, false, false, 0, 0, true, false, mccs, false);
        check(SIGNAL_TYPE_HDMI_TYPE_A, false, false, 0, 0, true, true, mccs, true);
        check(SIGNAL_TYPE_HDMI_TYPE_A, false, true, 48, 55, false, false, mccs, false);
    }
    return 0;
}
'''

CRTC_HARNESS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <errno.h>
struct drm_plane { unsigned type; bool registered; };
struct drm_crtc_state { int unused; };
struct drm_crtc;
struct funcs { struct drm_crtc_state *(*atomic_create_state)(struct drm_crtc *); };
struct drm_crtc { const struct funcs *funcs; struct drm_crtc_state *state; bool enabled; };
struct amdgpu_crtc {
    struct drm_crtc base;
    unsigned ism, max_cursor_width, max_cursor_height, crtc_id;
    int otg_inst;
};
struct amdgpu_display_manager {
    int ddev;
    struct {
        struct { struct {
            struct { unsigned max_cursor_size; struct { struct { bool dcn_arch; } dpp; } color; } caps;
            struct { int dce_version; } *ctx;
        } *dc; } dm;
        struct { struct amdgpu_crtc *crtcs[1]; } mode_info;
    } *adev;
};
static unsigned allocation, fail_stage, plane_cleanup, crtc_cleanup, ism_flush, registered;
static struct drm_crtc_state dummy_state;
static void *allocate(size_t size)
{
    allocation++;
    if (allocation == fail_stage)
        return NULL;
    return calloc(1, size);
}
#define kzalloc_obj(obj) allocate(sizeof(obj))
#define kfree free
#define DRM_PLANE_TYPE_CURSOR 1
#define DCN_VERSION_4_01 1
#define MAX_COLOR_LUT_ENTRIES 256
#define MAX_COLOR_LEGACY_LUT_ENTRIES 256
#define IS_ERR(p) ((intptr_t)(p) < 0)
#define PTR_ERR(p) ((int)(intptr_t)(p))
static int amdgpu_dm_plane_init(struct amdgpu_display_manager *dm, struct drm_plane *plane,
                                unsigned unused, void *data)
{
    if (fail_stage == 3)
        return -EINVAL;
    plane->registered = true;
    registered++;
    return 0;
}
static struct drm_crtc_state *create_state(struct drm_crtc *crtc)
{
    return fail_stage == 5 ? (struct drm_crtc_state *)(intptr_t)-ENOMEM : &dummy_state;
}
static const struct funcs amdgpu_dm_crtc_funcs = { create_state };
static int drm_crtc_init_with_planes(int device, struct drm_crtc *crtc, struct drm_plane *plane,
                                     struct drm_plane *cursor, const struct funcs *funcs, void *name)
{
    if (fail_stage == 4)
        return -EINVAL;
    crtc->funcs = funcs;
    return 0;
}
#define amdgpu_dm_ism_init(...) ((void)0)
#define drm_crtc_helper_add(...) ((void)0)
#define drm_crtc_enable_color_mgmt(...) ((void)has_degamma)
#define drm_mode_crtc_set_gamma_size(...) ((void)0)
#define amdgpu_dm_ism_flush(...) (ism_flush++)
#define drm_crtc_cleanup(...) (crtc_cleanup++)
static __attribute__((unused)) void drm_plane_cleanup(struct drm_plane *plane)
{
    assert(plane->registered);
    plane->registered = false;
    registered--;
    plane_cleanup++;
}
ACTUAL_FUNCTION
int main(void)
{
    struct amdgpu_display_manager dm = {0};
    for (fail_stage = 1; fail_stage <= 5; fail_stage++) {
        allocation = plane_cleanup = crtc_cleanup = ism_flush = registered = 0;
        int error = amdgpu_dm_crtc_init(&dm, NULL, 0);
        assert(error == ((fail_stage == 3 || fail_stage == 4) ? -EINVAL : -ENOMEM));
        assert(!registered);
        assert(plane_cleanup == (fail_stage >= 4));
        assert(crtc_cleanup == (fail_stage == 5));
        assert(ism_flush == (fail_stage == 5));
    }
    return 0;
}
'''

PANIC_HARNESS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32;
struct drm_scanout_buffer { int width, height; struct { int format; } *format; };
struct font_desc { int width, height; };
struct drm_rect { int unused; };
struct kmsg_dump_iter { int unused; };
struct drm_panic_line { char *txt; size_t len; };
#define DRM_RECT_INIT(...) {0}
static struct font_desc font = {1, 1};
static unsigned calls, lines, empty_record;
static const struct font_desc *get_default_font(int width, int height, void *a, void *b) { return &font; }
#define drm_draw_color_from_xrgb8888(color, format) (color)
#define drm_panic_helper_fill(sb, rect, color) ((void)(rect))
#define kmsg_dump_rewind(iter) ((void)(iter))
static bool kmsg_dump_get_buffer(struct kmsg_dump_iter *iter, bool syslog, char *buffer,
                                  size_t size, size_t *length)
{
    assert(++calls <= 3);
    assert(size >= 2048);
    *length = empty_record ? 0 : 1001;
    if (*length) {
        memset(buffer, 'x', *length);
        buffer[*length - 1] = '\n';
        buffer[*length] = 0;
    }
    return true;
}
static int draw_line_with_wrap(struct drm_scanout_buffer *sb, const struct font_desc *desc,
                               struct drm_panic_line *line, int y, u32 color)
{
    assert(line->len == 1000);
    lines++;
    return y - 1;
}
ACTUAL_FUNCTION
int main(void)
{
    struct drm_scanout_buffer sb = { .width = 2000, .height = 2 };
    typeof(*sb.format) format = {0};
    sb.format = &format;
    assert(!drm_panic_helper_draw_screen_kmsg(&sb, 0, 0));
    assert(calls == 2 && lines == 2);
    empty_record = 1;
    calls = lines = 0;
    assert(!drm_panic_helper_draw_screen_kmsg(&sb, 0, 0));
    assert(calls == 1 && !lines);
    return 0;
}
'''

MODE_HARNESS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint64_t u64;
enum { VRR_STATE_ACTIVE_FIXED, VRR_STATE_ACTIVE_VARIABLE, VRR_STATE_INACTIVE, VRR_STATE_UNSUPPORTED };
#define DRM_MODE_CONNECTOR_WRITEBACK 1
struct drm_display_mode { int rate; };
struct drm_connector { int connector_type; };
struct amdgpu_dm_connector { struct drm_connector base; bool vrr_max_from_mode; int min_vfreq, max_vfreq; };
struct dm_connector_state {
    struct { struct drm_connector *connector; } base;
    bool freesync_capable, freesync_on_desktop_capable;
};
struct dc_stream_state {
    struct { unsigned pix_clk_100hz, h_total, v_total; } timing;
    bool ignore_msa_timing_param, freesync_on_desktop;
};
struct mod_freesync_config {
    int state;
    u64 min_refresh_in_uhz, max_refresh_in_uhz, fixed_refresh_in_uhz;
    bool vsif_supported, btr;
};
struct dm_crtc_state {
    struct { struct drm_display_mode mode; bool passive_vrr_disabled, vrr_enabled; } base;
    struct dc_stream_state *stream;
    struct mod_freesync_config freesync_config;
    bool vrr_supported;
};
#define drm_mode_vrefresh(mode) ((mode)->rate)
#define to_amdgpu_dm_connector(c) ((struct amdgpu_dm_connector *)(c))
#define DIV_ROUND_UP_ULL(n, d) (((n) + (d) - 1) / (d))
#define min_t(type, a, b) ((type)(a) < (type)(b) ? (type)(a) : (type)(b))
#define div_u64(n, d) ((n) / (d))
#define drm_dbg_driver(...) ((void)0)
#define drm_dbg_kms(...) ((void)0)
ACTUAL_FUNCTIONS
int main(void)
{
    struct amdgpu_dm_connector c = { .vrr_max_from_mode = true, .min_vfreq = 48 };
    struct dm_connector_state con = { .base.connector = &c.base, .freesync_capable = true,
                                     .freesync_on_desktop_capable = true };
    struct dc_stream_state stream = { .timing.h_total = 1000, .timing.v_total = 1000 };
    struct dm_crtc_state crtc = { .base.vrr_enabled = true, .stream = &stream };
    for (unsigned rate = 1; rate <= 240; rate++) {
        crtc.base.mode.rate = rate;
        stream.timing.pix_clk_100hz = rate * 10000;
        crtc.freesync_config.state = VRR_STATE_INACTIVE;
        amdgpu_dm_get_freesync_config_for_crtc(&crtc, &con);
        assert(crtc.vrr_supported == (rate >= 58));
        if (crtc.vrr_supported) {
            assert(crtc.freesync_config.state == VRR_STATE_ACTIVE_VARIABLE);
            assert(crtc.freesync_config.max_refresh_in_uhz == (u64)rate * 1000000);
            assert(stream.freesync_on_desktop);
        }
    }
    crtc.base.mode.rate = 240;
    stream.timing.pix_clk_100hz = 2397600;
    amdgpu_dm_get_freesync_config_for_crtc(&crtc, &con);
    assert(crtc.vrr_supported);
    assert(crtc.freesync_config.max_refresh_in_uhz == 239760000);
    c.vrr_max_from_mode = false;
    c.max_vfreq = 120;
    amdgpu_dm_get_freesync_config_for_crtc(&crtc, &con);
    assert(!crtc.vrr_supported);
    crtc.base.mode.rate = 120;
    stream.timing.pix_clk_100hz = 1200000;
    amdgpu_dm_get_freesync_config_for_crtc(&crtc, &con);
    assert(crtc.vrr_supported && crtc.freesync_config.max_refresh_in_uhz == 120000000);
    return 0;
}
'''

if __name__ == '__main__':
    unittest.main()
