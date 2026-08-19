#pragma once
#include <citro2d.h>

// Theme structures
typedef struct {
    char name[16];
    u8 bg_r,  bg_g,  bg_b;
    u8 pan_r, pan_g, pan_b;
    u8 dim_r, dim_g, dim_b;
    u8 ac1_r, ac1_g, ac1_b;
    u8 ac2_r, ac2_g, ac2_b;
    u8 dng_r, dng_g, dng_b;
} ThemePreset;

typedef struct {
    u32 bg, panel, panelDim;
    u32 text, textDim;
    u32 amber, cyan, danger;
} ThemeColors;

extern ThemeColors s_colors;
extern int g_selected_asset_idx;

// Modal States
typedef enum {
    MODAL_NONE = 0,
    MODAL_CONFIRM_EMERGENCY_STOP,
    MODAL_CONFIRM_APPROVE
} ModalState;

extern ModalState g_modal_state;

// UI Hit Actions
typedef enum {
    HIT_NONE = 0,
    HIT_EMERGENCY_STOP,
    HIT_PAUSE,
    HIT_TOGGLE_DCA,
    HIT_FORCE_EVALUATE,
    HIT_APPROVE,
    HIT_REJECT,
    HIT_SETTINGS_THEME,
    HIT_MODAL_CONFIRM,
    HIT_MODAL_CANCEL
} UiHitAction;

// Button Coordinates (Shared Single Source of Truth)
#define BTN_EMERGENCY_X 20.0f
#define BTN_EMERGENCY_Y 40.0f
#define BTN_EMERGENCY_W 280.0f
#define BTN_EMERGENCY_H 35.0f

#define BTN_PAUSE_X 20.0f
#define BTN_PAUSE_Y 85.0f
#define BTN_PAUSE_W 280.0f
#define BTN_PAUSE_H 35.0f

#define BTN_DCA_X 20.0f
#define BTN_DCA_Y 130.0f
#define BTN_DCA_W 280.0f
#define BTN_DCA_H 35.0f

#define BTN_FORCE_EVAL_X 20.0f
#define BTN_FORCE_EVAL_Y 175.0f
#define BTN_FORCE_EVAL_W 280.0f
#define BTN_FORCE_EVAL_H 35.0f

#define BTN_APPROVE_X 10.0f
#define BTN_APPROVE_Y 180.0f
#define BTN_APPROVE_W 140.0f
#define BTN_APPROVE_H 40.0f

#define BTN_REJECT_X 160.0f
#define BTN_REJECT_Y 180.0f
#define BTN_REJECT_W 140.0f
#define BTN_REJECT_H 40.0f

#define MODAL_BOX_X 15.0f
#define MODAL_BOX_Y 35.0f
#define MODAL_BOX_W 290.0f
#define MODAL_BOX_H 170.0f

#define BTN_MODAL_CONFIRM_X 25.0f
#define BTN_MODAL_CONFIRM_Y 150.0f
#define BTN_MODAL_CONFIRM_W 130.0f
#define BTN_MODAL_CONFIRM_H 40.0f

#define BTN_MODAL_CANCEL_X 165.0f
#define BTN_MODAL_CANCEL_Y 150.0f
#define BTN_MODAL_CANCEL_W 130.0f
#define BTN_MODAL_CANCEL_H 40.0f

#define BTN_SETTINGS_THEME_X 20.0f
#define BTN_SETTINGS_THEME_Y 155.0f
#define BTN_SETTINGS_THEME_W 270.0f
#define BTN_SETTINGS_THEME_H 35.0f

void graphics_init(int initial_theme);
void graphics_exit(void);
void graphics_draw_frame(const char* ip_buffer, int port);

// Theme management
void graphics_set_theme(int theme_idx);
void graphics_next_theme(void);
int graphics_get_theme_count(void);
int graphics_get_current_theme_idx(void);
const char* graphics_get_theme_name(int theme_idx);

// Hit testing
UiHitAction graphics_hit_test(float px, float py, int screen_mode);
int graphics_tab_touch_hit(float px, float py);

// HUD drawing helper
void graphics_draw_hud_panel(float x, float y, float w, float h, u32 fill, u32 accent, float chamfer);
void graphics_draw_tab_drawer(int active_mode);
