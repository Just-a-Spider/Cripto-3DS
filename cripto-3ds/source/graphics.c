#include "graphics.h"
#include "network.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <3ds/services/ptmu.h>

extern int g_screen_mode;
int g_selected_asset_idx = 0;
ModalState g_modal_state = MODAL_NONE;

static C3D_RenderTarget* top;
static C3D_RenderTarget* bottom;
static C2D_TextBuf dynamicBuf;
static C2D_TextBuf staticBuf;
static C2D_Font customFont;

ThemeColors s_colors;

static const ThemePreset s_presets[5] = {
    // 0: NMS (No Man's Sky amber/cyan/charcoal)
    {
        "NMS",
        8, 12, 18,     // bg
        18, 24, 32,    // panel
        28, 34, 40,    // panelDim
        255, 160, 40,  // amber
        60, 220, 220,  // cyan
        220, 60, 40    // danger
    },
    // 1: CYBERPUNK (Neon Yellow / Magenta / Deep Indigo)
    {
        "CYBERPUNK",
        12, 10, 24,    // bg
        24, 18, 42,    // panel
        38, 28, 60,    // panelDim
        254, 220, 0,   // amber (neon yellow)
        255, 0, 128,   // cyan (hot magenta)
        255, 40, 60    // danger
    },
    // 2: CLASSIC 3DS (Nintendo Crimson / System Blue / Dark Slate)
    {
        "CLASSIC 3DS",
        20, 22, 26,    // bg
        35, 38, 45,    // panel
        50, 54, 62,    // panelDim
        230, 40, 40,   // amber (crimson red)
        0, 140, 255,   // cyan (system blue)
        255, 60, 60    // danger
    },
    // 3: MATRIX (Terminal Green / Phosphor Mint / Void Black)
    {
        "MATRIX",
        6, 12, 8,      // bg
        12, 26, 16,    // panel
        20, 40, 24,    // panelDim
        0, 230, 110,   // amber (bright green)
        120, 255, 180, // cyan (mint)
        240, 70, 70    // danger
    },
    // 4: SOLARIZED (Solarized Amber / Cyan / Solarized Dark)
    {
        "SOLARIZED",
        7, 34, 43,     // bg
        12, 48, 60,    // panel
        20, 64, 78,    // panelDim
        181, 137, 0,   // amber (solarized yellow)
        42, 161, 152,  // cyan (solarized cyan)
        220, 50, 47    // danger (solarized red)
    }
};

static int s_current_theme = 0;

void graphics_set_theme(int theme_idx) {
    if (theme_idx < 0 || theme_idx >= 5) theme_idx = 0;
    s_current_theme = theme_idx;

    const ThemePreset* p = &s_presets[s_current_theme];
    s_colors.bg = C2D_Color32(p->bg_r, p->bg_g, p->bg_b, 255);
    s_colors.panel = C2D_Color32(p->pan_r, p->pan_g, p->pan_b, 255);
    s_colors.panelDim = C2D_Color32(p->dim_r, p->dim_g, p->dim_b, 255);
    s_colors.text = C2D_Color32(230, 230, 220, 255);
    s_colors.textDim = C2D_Color32(140, 150, 150, 255);
    s_colors.amber = C2D_Color32(p->ac1_r, p->ac1_g, p->ac1_b, 255);
    s_colors.cyan = C2D_Color32(p->ac2_r, p->ac2_g, p->ac2_b, 255);
    s_colors.danger = C2D_Color32(p->dng_r, p->dng_g, p->dng_b, 255);
}

void graphics_next_theme(void) {
    int next = (s_current_theme + 1) % 5;
    graphics_set_theme(next);
}

int graphics_get_theme_count(void) {
    return 5;
}

int graphics_get_current_theme_idx(void) {
    return s_current_theme;
}

const char* graphics_get_theme_name(int theme_idx) {
    if (theme_idx < 0 || theme_idx >= 5) theme_idx = 0;
    return s_presets[theme_idx].name;
}

static void fill_polygon(const float *vx, const float *vy, int n, u32 color) {
    float cx = 0.0f, cy = 0.0f;
    for (int i = 0; i < n; i++) { cx += vx[i]; cy += vy[i]; }
    cx /= n; cy /= n;
    for (int i = 0; i < n; i++) {
        int j = (i + 1) % n;
        C2D_DrawTriangle(cx, cy, color, vx[i], vy[i], color, vx[j], vy[j], color, 0.0f);
    }
}

void graphics_draw_hud_panel(float x, float y, float w, float h, u32 fill, u32 accent, float chamfer) {
    float vx[6] = {x+chamfer, x+w, x+w, x+w-chamfer, x, x};
    float vy[6] = {y, y, y+h-chamfer, y+h, y+h, y+chamfer};
    fill_polygon(vx, vy, 6, fill);
    C2D_DrawLine(vx[5],vy[5],accent,vx[0],vy[0],accent,1.5f,0);
    C2D_DrawLine(vx[2],vy[2],accent,vx[3],vy[3],accent,1.5f,0);
    float bl = (w < 60.0f || h < 60.0f) ? 6.0f : 10.0f;
    C2D_DrawLine(x+w-bl,y,    accent,x+w,y,    accent,1.5f,0);
    C2D_DrawLine(x+w,   y,    accent,x+w,y+bl, accent,1.5f,0);
    C2D_DrawLine(x,     y+h-bl,accent,x,  y+h, accent,1.5f,0);
    C2D_DrawLine(x,     y+h,  accent,x+bl,y+h, accent,1.5f,0);
}

static void draw_tab(float x, float y, float w, float h, u32 fill, u32 accent, int active) {
    float c = h * 0.28f;
    float vx[5] = {x+c, x+w, x+w, x+c, x};
    float vy[5] = {y, y, y+h, y+h, y+h/2.0f};
    fill_polygon(vx, vy, 5, fill);
    if (active) {
        C2D_DrawLine(vx[4],vy[4],accent,vx[0],vy[0],accent,2.0f,0);
        C2D_DrawLine(vx[4],vy[4],accent,vx[3],vy[3],accent,2.0f,0);
        float px[3]={x-6.0f,x,x}, py[3]={y+h/2.0f,y+h/2.0f-5.0f,y+h/2.0f+5.0f};
        fill_polygon(px, py, 3, accent);
    }
}

int graphics_tab_touch_hit(float px, float py) {
    if (px < 270.0f || px > 320.0f || py < 0.0f || py > 240.0f) return -1;
    float row_h = 240.0f / 3.0f;
    int index = (int)(py / row_h);
    if (index < 0 || index >= 3) return -1;
    return index;
}

void graphics_draw_tab_drawer(int active_mode) {
    graphics_draw_hud_panel(270, 0, 50, 240, s_colors.panel, s_colors.amber, 10);
    float row_h = 240.0f / 3.0f;
    const char* labels[] = {"TRADE", "PORT", "SET"};
    u32 accents[] = {s_colors.amber, s_colors.cyan, s_colors.danger};
    C2D_Text textObj;
    
    for (int i = 0; i < 3; i++) {
        float y = i * row_h;
        int active = (i == active_mode);
        draw_tab(270, y, 50, row_h, active ? s_colors.panelDim : s_colors.panel, accents[i], active);
        if (customFont) {
            C2D_TextFontParse(&textObj, customFont, dynamicBuf, labels[i]);
        } else {
            C2D_TextParse(&textObj, dynamicBuf, labels[i]);
        }
        C2D_TextOptimize(&textObj);
        C2D_DrawText(&textObj, C2D_WithColor, 275, y + (row_h * 0.5f) - 6.0f, 0.5f, 0.35f, 0.35f, s_colors.text);
    }
}

void graphics_init(int initial_theme) {
    C3D_Init(C3D_DEFAULT_CMDBUF_SIZE);
    C2D_Init(C2D_DEFAULT_MAX_OBJECTS);
    C2D_Prepare();

    top = C2D_CreateScreenTarget(GFX_TOP, GFX_LEFT);
    bottom = C2D_CreateScreenTarget(GFX_BOTTOM, GFX_LEFT);
    dynamicBuf = C2D_TextBufNew(4096);
    staticBuf = C2D_TextBufNew(4096);

    customFont = C2D_FontLoad("romfs:/minecraft.bcfnt");

    graphics_set_theme(initial_theme);
}

void graphics_exit(void) {
    if (customFont) C2D_FontFree(customFont);
    C2D_TextBufDelete(dynamicBuf);
    C2D_TextBufDelete(staticBuf);
    C2D_Fini();
    C3D_Fini();
}

static void draw_dynamic_text(C2D_Text* textObj, const char* str, float x, float y, float scale, u32 color) {
    if (customFont) {
        C2D_TextFontParse(textObj, customFont, dynamicBuf, str);
    } else {
        C2D_TextParse(textObj, dynamicBuf, str);
    }
    C2D_TextOptimize(textObj);
    C2D_DrawText(textObj, C2D_WithColor, x, y, 0.5f, scale, scale, color);
}

static void draw_chunky_button(float x, float y, float w, float h, u32 color, const char* text) {
    graphics_draw_hud_panel(x, y, w, h, s_colors.panel, color, 8);
    C2D_Text textObj;
    draw_dynamic_text(&textObj, text, x + 10, y + (h/2) - 8, 0.6f, s_colors.text);
}

static inline int hit_rect(float px, float py, float x, float y, float w, float h) {
    return (px >= x && px <= (x + w) && py >= y && py <= (y + h));
}

UiHitAction graphics_hit_test(float px, float py, int screen_mode) {
    if (g_modal_state != MODAL_NONE) {
        if (hit_rect(px, py, BTN_MODAL_CONFIRM_X, BTN_MODAL_CONFIRM_Y, BTN_MODAL_CONFIRM_W, BTN_MODAL_CONFIRM_H)) {
            return HIT_MODAL_CONFIRM;
        }
        if (hit_rect(px, py, BTN_MODAL_CANCEL_X, BTN_MODAL_CANCEL_Y, BTN_MODAL_CANCEL_W, BTN_MODAL_CANCEL_H)) {
            return HIT_MODAL_CANCEL;
        }
        return HIT_NONE;
    }

    if (screen_mode == 0) {
        if (hit_rect(px, py, BTN_EMERGENCY_X, BTN_EMERGENCY_Y, BTN_EMERGENCY_W, BTN_EMERGENCY_H)) {
            return HIT_EMERGENCY_STOP;
        }
        if (hit_rect(px, py, BTN_PAUSE_X, BTN_PAUSE_Y, BTN_PAUSE_W, BTN_PAUSE_H)) {
            return HIT_PAUSE;
        }
        if (hit_rect(px, py, BTN_DCA_X, BTN_DCA_Y, BTN_DCA_W, BTN_DCA_H)) {
            return HIT_TOGGLE_DCA;
        }
        if (g_telemetry_has_trade) {
            if (hit_rect(px, py, BTN_APPROVE_X, BTN_APPROVE_Y, BTN_APPROVE_W, BTN_APPROVE_H)) {
                return HIT_APPROVE;
            }
            if (hit_rect(px, py, BTN_REJECT_X, BTN_REJECT_Y, BTN_REJECT_W, BTN_REJECT_H)) {
                return HIT_REJECT;
            }
        } else {
            if (hit_rect(px, py, BTN_FORCE_EVAL_X, BTN_FORCE_EVAL_Y, BTN_FORCE_EVAL_W, BTN_FORCE_EVAL_H)) {
                return HIT_FORCE_EVALUATE;
            }
        }
    } else if (screen_mode == 2) {
        if (hit_rect(px, py, BTN_SETTINGS_THEME_X, BTN_SETTINGS_THEME_Y, BTN_SETTINGS_THEME_W, BTN_SETTINGS_THEME_H)) {
            return HIT_SETTINGS_THEME;
        }
    }

    return HIT_NONE;
}

static void draw_price_sparkline(float px, float py, float pw, float ph) {
    if (g_history_count < 2) return;

    float min_p = g_price_history[0];
    float max_p = g_price_history[0];
    for (int i = 1; i < g_history_count; i++) {
        if (g_price_history[i] < min_p) min_p = g_price_history[i];
        if (g_price_history[i] > max_p) max_p = g_price_history[i];
    }

    float p_range = max_p - min_p;
    if (p_range <= 0.0001f) p_range = 1.0f;

    u32 trend_color = (g_price_history[g_history_count - 1] >= g_price_history[0]) ? s_colors.cyan : s_colors.danger;

    // Draw reference box outline
    C2D_DrawRectSolid(px, py, 0.4f, pw, ph, s_colors.panelDim);

    float step_x = pw / (float)(g_history_count - 1);
    float last_x = px;
    float last_y = py + ph / 2.0f;

    for (int i = 0; i < g_history_count - 1; i++) {
        float norm1 = (g_price_history[i] - min_p) / p_range;
        float norm2 = (g_price_history[i + 1] - min_p) / p_range;
        float x1 = px + (i * step_x);
        float y1 = (py + ph - 4.0f) - (norm1 * (ph - 8.0f));
        float x2 = px + ((i + 1) * step_x);
        float y2 = (py + ph - 4.0f) - (norm2 * (ph - 8.0f));

        C2D_DrawLine(x1, y1, trend_color, x2, y2, trend_color, 2.0f, 0.5f);
        last_x = x2;
        last_y = y2;
    }

    // Endpoint dot
    C2D_DrawCircleSolid(last_x, last_y, 0.6f, 3.0f, trend_color);
}

static void draw_top_screen(void) {
    C2D_TargetClear(top, s_colors.bg);
    C2D_SceneBegin(top);

    // Compact Total Value Card
    graphics_draw_hud_panel(10, 10, 380, 60, s_colors.panel, s_colors.amber, 10);
    
    char textStr[256];
    C2D_Text textObj;
    
    draw_dynamic_text(&textObj, "ACCOUNT VALUE (USDT)", 20, 15, 0.42f, s_colors.textDim);
    snprintf(textStr, sizeof(textStr), "$%.2f", g_telemetry_usdt);
    draw_dynamic_text(&textObj, textStr, 20, 30, 0.85f, s_colors.amber);
    
    // Sparkline Graph on Right of Account Card
    draw_dynamic_text(&textObj, g_telemetry_pair, 215, 14, 0.42f, s_colors.cyan);

    u32 status_color = s_colors.text;
    if (strstr(g_telemetry_status, "ACTIVE")) status_color = s_colors.cyan;
    else if (strstr(g_telemetry_status, "PAUSE")) status_color = s_colors.amber;
    else status_color = s_colors.danger;
    draw_dynamic_text(&textObj, g_telemetry_status, 310, 14, 0.42f, status_color);

    draw_price_sparkline(215, 30, 165, 30);

    // Portfolio Distribution
    if (!g_telemetry_has_trade) {
        draw_dynamic_text(&textObj, "PORTFOLIO DISTRIBUTION", 10, 78, 0.5f, s_colors.text);
        
        float total_crypto = g_parsed_assets.total_crypto_value;
        float cash = g_telemetry_usdt - total_crypto;
        if (cash < 0.0f) cash = 0.0f;
        
        float current_x = 10.0f;
        float bar_w = 380.0f;
        float total_wealth = g_telemetry_usdt;
        if (total_wealth <= 0.0f) total_wealth = total_crypto + cash;
        
        u32 palette[] = {
            s_colors.amber,
            s_colors.cyan,
            C2D_Color32(189, 147, 249, 255), // Purple
            C2D_Color32(255, 121, 198, 255), // Pink
            C2D_Color32(80, 250, 123, 255)   // Green
        };

        if (cash > 0.0f && total_wealth > 0.0f) {
            float w = (cash / total_wealth) * bar_w;
            C2D_DrawRectSolid(current_x, 98, 0, w, 22, s_colors.panelDim); // Grey/Dim for USDT
            current_x += w;
        }
        
        for (int i = 0; i < g_parsed_assets.count; i++) {
            if (total_wealth > 0.0f && g_parsed_assets.items[i].value_usdt > 0.0f) {
                float w = (g_parsed_assets.items[i].value_usdt / total_wealth) * bar_w;
                C2D_DrawRectSolid(current_x, 98, 0, w, 22, palette[i % 5]);
                current_x += w;
            }
        }
        
        int leg_x = 10;
        int leg_y = 126;
        draw_dynamic_text(&textObj, "USDT", leg_x + 14, leg_y, 0.42f, s_colors.text);
        C2D_DrawRectSolid(leg_x, leg_y + 2, 0, 10, 10, s_colors.panelDim);
        leg_x += 65;
        
        for (int i = 0; i < g_parsed_assets.count; i++) {
            if (leg_x > 330) {
                leg_x = 10;
                leg_y += 18;
            }
            draw_dynamic_text(&textObj, g_parsed_assets.items[i].symbol, leg_x + 14, leg_y, 0.42f, s_colors.text);
            C2D_DrawRectSolid(leg_x, leg_y + 2, 0, 10, 10, palette[i % 5]);
            leg_x += 75;
        }
    }

    // Connection Status
    if (g_http_status == 1) {
        draw_dynamic_text(&textObj, "TCP: Connected", 10, 215, 0.45f, s_colors.cyan);
    } else {
        draw_dynamic_text(&textObj, "TCP: Connecting...", 10, 215, 0.45f, s_colors.danger);
    }
    
    // Trade Overlay Banner!
    if (g_telemetry_has_trade) {
        u32 banner_bg = s_colors.amber;
        u32 banner_border = s_colors.danger;
        u32 badge_color = C2D_Color32(180, 0, 0, 255);

        if (strstr(g_ai_verdict, "HIGH_RISK") || strstr(g_ai_risk, "HIGH")) {
            banner_bg = C2D_Color32(255, 110, 110, 240); // Red alert tint
            banner_border = s_colors.danger;
            badge_color = C2D_Color32(160, 0, 0, 255);
        } else if (strstr(g_ai_verdict, "CAUTION") || strstr(g_ai_risk, "MEDIUM")) {
            banner_bg = s_colors.amber;
            banner_border = C2D_Color32(255, 184, 108, 255);
            badge_color = C2D_Color32(150, 80, 0, 255);
        } else if (strstr(g_ai_verdict, "APPROVE") || strstr(g_ai_risk, "LOW")) {
            banner_bg = C2D_Color32(100, 250, 140, 240); // Green safe tint
            banner_border = s_colors.cyan;
            badge_color = C2D_Color32(0, 110, 30, 255);
        }

        graphics_draw_hud_panel(0, 80, 400, 80, banner_bg, banner_border, 0);
        
        char tradeStr[128];
        snprintf(tradeStr, sizeof(tradeStr), "PENDING %s: %s", g_trade_action, g_trade_pair);
        draw_dynamic_text(&textObj, tradeStr, 15, 88, 0.65f, C2D_Color32(0,0,0,255));
        
        // AI Risk Badge
        if (g_ai_risk[0] != '\0' || g_ai_verdict[0] != '\0') {
            char aiBadge[64];
            if (g_ai_risk[0] != '\0') {
                snprintf(aiBadge, sizeof(aiBadge), "[AI: %s]", g_ai_risk);
            } else {
                snprintf(aiBadge, sizeof(aiBadge), "[AI: %s]", g_ai_verdict);
            }
            draw_dynamic_text(&textObj, aiBadge, 250, 90, 0.44f, badge_color);
        }

        snprintf(tradeStr, sizeof(tradeStr), "$%.2f USDT @ $%.2f", g_trade_amount_usdt, g_trade_price);
        draw_dynamic_text(&textObj, tradeStr, 15, 110, 0.52f, C2D_Color32(0,0,0,255));
        
        snprintf(tradeStr, sizeof(tradeStr), "Reason: %s", g_trade_reason);
        draw_dynamic_text(&textObj, tradeStr, 15, 130, 0.38f, C2D_Color32(30,30,30,255));
    }
}

static void draw_bottom_base(void) {
    C2D_TargetClear(bottom, s_colors.bg);
    C2D_SceneBegin(bottom);
    C2D_Text textObj;

    graphics_draw_hud_panel(0, 0, 320, 25, s_colors.panel, s_colors.cyan, 5);
    draw_dynamic_text(&textObj, "[L]", 10, 5, 0.5f, s_colors.amber);
    draw_dynamic_text(&textObj, "[R]", 290, 5, 0.5f, s_colors.amber);
    
    if (g_screen_mode == 0) draw_dynamic_text(&textObj, "TRADING DESK", 100, 5, 0.6f, s_colors.text);
    else if (g_screen_mode == 1) draw_dynamic_text(&textObj, "PORTFOLIO", 110, 5, 0.6f, s_colors.text);
    else draw_dynamic_text(&textObj, "SETTINGS", 120, 5, 0.6f, s_colors.text);
}

static void draw_bottom_trade(void) {
    draw_chunky_button(BTN_EMERGENCY_X, BTN_EMERGENCY_Y, BTN_EMERGENCY_W, BTN_EMERGENCY_H, s_colors.danger, "EMERGENCY STOP");
    
    if (strstr(g_telemetry_status, "ACTIVE")) {
        draw_chunky_button(BTN_PAUSE_X, BTN_PAUSE_Y, BTN_PAUSE_W, BTN_PAUSE_H, s_colors.amber, "PAUSE BOT");
    } else {
        draw_chunky_button(BTN_PAUSE_X, BTN_PAUSE_Y, BTN_PAUSE_W, BTN_PAUSE_H, s_colors.cyan, "START BOT");
    }
    
    if (g_dca_enabled) {
        draw_chunky_button(BTN_DCA_X, BTN_DCA_Y, BTN_DCA_W, BTN_DCA_H, s_colors.cyan, "(X) DCA STRATEGY: ON");
    } else {
        draw_chunky_button(BTN_DCA_X, BTN_DCA_Y, BTN_DCA_W, BTN_DCA_H, s_colors.panelDim, "(X) DCA STRATEGY: OFF");
    }
    
    if (g_telemetry_has_trade) {
        draw_chunky_button(BTN_APPROVE_X, BTN_APPROVE_Y, BTN_APPROVE_W, BTN_APPROVE_H, s_colors.cyan, "(A) APPROVE");
        draw_chunky_button(BTN_REJECT_X, BTN_REJECT_Y, BTN_REJECT_W, BTN_REJECT_H, s_colors.danger, "(B) REJECT");
    } else {
        draw_chunky_button(BTN_FORCE_EVAL_X, BTN_FORCE_EVAL_Y, BTN_FORCE_EVAL_W, BTN_FORCE_EVAL_H, s_colors.cyan, "FORCE EVALUATE ALL");
    }
}

static void draw_bottom_portfolio(void) {
    C2D_Text textObj;
    draw_dynamic_text(&textObj, "Favorite Assets", 10, 32, 0.45f, s_colors.textDim);
    
    int total = g_parsed_assets.count;
    if (total == 0) {
        draw_dynamic_text(&textObj, "No telemetry assets available.", 20, 80, 0.5f, s_colors.textDim);
    } else {
        if (g_selected_asset_idx >= total) g_selected_asset_idx = total - 1;
        if (g_selected_asset_idx < 0) g_selected_asset_idx = 0;

        int scroll_offset = 0;
        if (total > 3) {
            scroll_offset = g_selected_asset_idx - 1;
            if (scroll_offset < 0) scroll_offset = 0;
            if (scroll_offset > total - 3) scroll_offset = total - 3;
        }

        int start_y = 48;
        int card_h = 38;
        int gap = 6;

        for (int i = 0; i < 3 && (scroll_offset + i) < total; i++) {
            int idx = scroll_offset + i;
            int y = start_y + i * (card_h + gap);
            int is_sel = (idx == g_selected_asset_idx);

            u32 card_fill = is_sel ? s_colors.cyan : s_colors.panel;
            u32 card_acc  = is_sel ? s_colors.panel : s_colors.amber;
            u32 text_col1 = is_sel ? C2D_Color32(0, 0, 0, 255) : s_colors.text;
            u32 text_col2 = is_sel ? C2D_Color32(40, 40, 40, 255) : s_colors.textDim;

            graphics_draw_hud_panel(10, y, 300, card_h, card_fill, card_acc, 6);

            char top_str[64];
            snprintf(top_str, sizeof(top_str), "%s  [$%.2f]  RSI: %.1f", 
                     g_parsed_assets.items[idx].symbol, 
                     g_parsed_assets.items[idx].price,
                     g_parsed_assets.items[idx].rsi);
            draw_dynamic_text(&textObj, top_str, 20, y + 4, 0.45f, text_col1);

            char bot_str[64];
            snprintf(bot_str, sizeof(bot_str), "Bal: %.4f  (Val: $%.2f)", 
                     g_parsed_assets.items[idx].balance,
                     g_parsed_assets.items[idx].value_usdt);
            draw_dynamic_text(&textObj, bot_str, 20, y + 20, 0.40f, text_col2);
        }

        char page_str[64];
        snprintf(page_str, sizeof(page_str), "[%d / %d] (UP/DOWN to select)", g_selected_asset_idx + 1, total);
        draw_dynamic_text(&textObj, page_str, 10, 185, 0.38f, s_colors.textDim);
    }

    draw_dynamic_text(&textObj, "D-PAD L: Sell Check | D-PAD R: Buy Check", 10, 222, 0.40f, s_colors.amber);
}

static void draw_bottom_settings(const char* ip_buffer, int port) {
    C2D_Text textObj;
    graphics_draw_hud_panel(10, 35, 300, 85, s_colors.panel, s_colors.cyan, 10);
    
    draw_dynamic_text(&textObj, "NETWORK CONFIGURATION (SELECT)", 20, 45, 0.45f, s_colors.cyan);
    
    char textStr[256];
    snprintf(textStr, sizeof(textStr), "Target IP:   %s", ip_buffer);
    draw_dynamic_text(&textObj, textStr, 20, 68, 0.45f, s_colors.text);
    
    snprintf(textStr, sizeof(textStr), "Target Port: %d", port);
    draw_dynamic_text(&textObj, textStr, 20, 90, 0.45f, s_colors.text);

    // Theme Preset Selector Card
    graphics_draw_hud_panel(10, 130, 300, 85, s_colors.panel, s_colors.amber, 10);
    draw_dynamic_text(&textObj, "THEME PRESET (Tap or [X] to cycle)", 20, 140, 0.45f, s_colors.amber);

    char theme_btn_str[64];
    snprintf(theme_btn_str, sizeof(theme_btn_str), "Theme: [%.16s]", graphics_get_theme_name(s_current_theme));
    draw_chunky_button(BTN_SETTINGS_THEME_X, BTN_SETTINGS_THEME_Y, BTN_SETTINGS_THEME_W, BTN_SETTINGS_THEME_H, s_colors.cyan, theme_btn_str);
}

static void draw_modal(void) {
    C2D_Text textObj;

    // Darken background overlay
    C2D_DrawRectSolid(0, 0, 0.7f, 320, 240, C2D_Color32(0, 0, 0, 180));

    if (g_modal_state == MODAL_CONFIRM_EMERGENCY_STOP) {
        graphics_draw_hud_panel(MODAL_BOX_X, MODAL_BOX_Y, MODAL_BOX_W, MODAL_BOX_H, s_colors.panel, s_colors.danger, 10);
        
        draw_dynamic_text(&textObj, "CONFIRM EMERGENCY STOP", 25, 50, 0.55f, s_colors.danger);
        draw_dynamic_text(&textObj, "Halt bot and cancel pending trades?", 25, 80, 0.45f, s_colors.text);
        draw_dynamic_text(&textObj, "This will stop all automatic trading.", 25, 102, 0.40f, s_colors.textDim);

        draw_chunky_button(BTN_MODAL_CONFIRM_X, BTN_MODAL_CONFIRM_Y, BTN_MODAL_CONFIRM_W, BTN_MODAL_CONFIRM_H, s_colors.danger, "(A) HALT");
        draw_chunky_button(BTN_MODAL_CANCEL_X, BTN_MODAL_CANCEL_Y, BTN_MODAL_CANCEL_W, BTN_MODAL_CANCEL_H, s_colors.panelDim, "(B) CANCEL");
    } 
    else if (g_modal_state == MODAL_CONFIRM_APPROVE) {
        u32 border_col = s_colors.cyan;
        if (strstr(g_ai_verdict, "HIGH_RISK") || strstr(g_ai_risk, "HIGH")) {
            border_col = s_colors.danger;
        } else if (strstr(g_ai_verdict, "CAUTION") || strstr(g_ai_risk, "MEDIUM")) {
            border_col = s_colors.amber;
        }

        graphics_draw_hud_panel(MODAL_BOX_X, MODAL_BOX_Y, MODAL_BOX_W, MODAL_BOX_H, s_colors.panel, border_col, 10);

        draw_dynamic_text(&textObj, "CONFIRM TRADE APPROVAL", 25, 45, 0.52f, border_col);

        char line1[128];
        snprintf(line1, sizeof(line1), "Execute %s %s?", g_trade_action, g_trade_pair);
        draw_dynamic_text(&textObj, line1, 25, 68, 0.46f, s_colors.text);

        char line2[128];
        snprintf(line2, sizeof(line2), "$%.2f USDT @ $%.2f", g_trade_amount_usdt, g_trade_price);
        draw_dynamic_text(&textObj, line2, 25, 86, 0.42f, s_colors.amber);

        char line3[128];
        snprintf(line3, sizeof(line3), "Reason: %s", g_trade_reason);
        draw_dynamic_text(&textObj, line3, 25, 104, 0.38f, s_colors.textDim);

        if (g_ai_risk[0] != '\0' || g_ai_verdict[0] != '\0') {
            char aiLine[128];
            u32 aiCol = s_colors.cyan;
            if (strstr(g_ai_verdict, "HIGH_RISK") || strstr(g_ai_risk, "HIGH")) {
                aiCol = s_colors.danger;
                snprintf(aiLine, sizeof(aiLine), "AI Risk: %s (HIGH RISK)", g_ai_risk);
            } else if (strstr(g_ai_verdict, "CAUTION")) {
                aiCol = s_colors.amber;
                snprintf(aiLine, sizeof(aiLine), "AI Risk: %s (CAUTION)", g_ai_risk);
            } else {
                aiCol = s_colors.cyan;
                snprintf(aiLine, sizeof(aiLine), "AI Risk: %s (OK)", g_ai_risk);
            }
            draw_dynamic_text(&textObj, aiLine, 25, 122, 0.38f, aiCol);
        }

        draw_chunky_button(BTN_MODAL_CONFIRM_X, BTN_MODAL_CONFIRM_Y, BTN_MODAL_CONFIRM_W, BTN_MODAL_CONFIRM_H, border_col, "(A) APPROVE");
        draw_chunky_button(BTN_MODAL_CANCEL_X, BTN_MODAL_CANCEL_Y, BTN_MODAL_CANCEL_W, BTN_MODAL_CANCEL_H, s_colors.panelDim, "(B) CANCEL");
    }
}

void graphics_draw_frame(const char* ip_buffer, int port) {
    C3D_FrameBegin(C3D_FRAME_SYNCDRAW);
    C2D_TextBufClear(dynamicBuf);

    draw_top_screen();
    
    draw_bottom_base();
    if (g_screen_mode == 0) draw_bottom_trade();
    else if (g_screen_mode == 1) draw_bottom_portfolio();
    else draw_bottom_settings(ip_buffer, port);
    
    if (g_modal_state != MODAL_NONE) {
        draw_modal();
    }

    C3D_FrameEnd(0);
}
