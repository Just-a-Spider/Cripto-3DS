#include <string.h>
#include <stdio.h>
#include <time.h>
#include <3ds.h>
#include <3ds/services/ptmu.h>

#include "network.h"
#include "graphics.h"
#include "input.h"
#include "audio.h"

int g_screen_mode = 0; // 0=TRADE, 1=PORTFOLIO, 2=SET

static void save_config(const char* ip, int port, const char* key, int theme) {
    FILE* f_out = fopen("sdmc:/cripto_cfg.txt", "w");
    if (f_out) {
        fprintf(f_out, "%s %d %s %d\n", ip, port, key, theme);
        fclose(f_out);
    }
}

int main()
{
    gfxInitDefault();
    romfsInit();
    ptmuInit();
    audio_init();

    char ip_buffer[60] = "192.168.1.100";
    int port = 7343;
    int theme_idx = 0;
    
    int need_prompt = 1;
    FILE* f = fopen("sdmc:/cripto_cfg.txt", "r");
    if (f) {
        int read_fields = fscanf(f, "%59s %d %15s %d", ip_buffer, &port, g_auth_key, &theme_idx);
        if (read_fields >= 2) {
            need_prompt = 0;
            if (read_fields < 4) theme_idx = 0;
        }
        fclose(f);
    }

    if (need_prompt) {
        prompt_for_ip(ip_buffer, sizeof(ip_buffer));
        prompt_for_port(&port);
        prompt_for_key(g_auth_key, sizeof(g_auth_key));
        save_config(ip_buffer, port, g_auth_key, theme_idx);
    }

    network_init(ip_buffer, port);
    graphics_init(theme_idx);

    while (aptMainLoop())
    {
        hidScanInput();
        u32 kDown = hidKeysDown();
        if (kDown & KEY_START) break;

        touchPosition touch;
        hidTouchRead(&touch);

        // Modal Input Handling (Blocks regular input when modal dialog is active)
        if (g_modal_state != MODAL_NONE) {
            UiHitAction modal_hit = (kDown & KEY_TOUCH) ? graphics_hit_test(touch.px, touch.py, g_screen_mode) : HIT_NONE;

            if ((kDown & KEY_A) || modal_hit == HIT_MODAL_CONFIRM) {
                if (g_modal_state == MODAL_CONFIRM_EMERGENCY_STOP) {
                    network_send_str("EMERGENCY_STOP");
                } else if (g_modal_state == MODAL_CONFIRM_APPROVE) {
                    network_send_str("APPROVE");
                    g_telemetry_has_trade = 0;
                }
                g_modal_state = MODAL_NONE;
            } else if ((kDown & KEY_B) || modal_hit == HIT_MODAL_CANCEL) {
                g_modal_state = MODAL_NONE;
            }

            graphics_draw_frame(ip_buffer, port);
            continue;
        }

        // Configuration prompt trigger
        if (kDown & KEY_SELECT) {
            prompt_for_ip(ip_buffer, sizeof(ip_buffer));
            prompt_for_port(&port);
            prompt_for_key(g_auth_key, sizeof(g_auth_key));
            save_config(ip_buffer, port, g_auth_key, graphics_get_current_theme_idx());
            
            network_exit();
            network_init(ip_buffer, port);
        }
        
        // Tab navigation
        if (kDown & KEY_L) {
            g_screen_mode = (g_screen_mode > 0) ? g_screen_mode - 1 : 2;
        }
        if (kDown & KEY_R) {
            g_screen_mode = (g_screen_mode < 2) ? g_screen_mode + 1 : 0;
        }
        
        // Mode 0: TRADING DESK
        if (g_screen_mode == 0) {
            if (kDown & KEY_X) {
                network_send_str("TOGGLE_DCA");
            }
            
            if (g_telemetry_has_trade) {
                if (kDown & KEY_A) {
                    g_modal_state = MODAL_CONFIRM_APPROVE;
                }
                if (kDown & KEY_B) {
                    network_send_str("REJECT");
                    g_telemetry_has_trade = 0;
                }
                if (kDown & KEY_Y) {
                    char override_buf[16] = "";
                    prompt_for_amount(override_buf, sizeof(override_buf));
                    if (strlen(override_buf) > 0) {
                        char cmd_buf[32];
                        snprintf(cmd_buf, sizeof(cmd_buf), "SET_OVERRIDE|%s", override_buf);
                        network_send_str(cmd_buf);
                    }
                }
            }
        }
        
        // Mode 1: PORTFOLIO
        if (g_screen_mode == 1) {
            int count = g_parsed_assets.count;
            if (count > 0) {
                if (kDown & KEY_DOWN) {
                    g_selected_asset_idx = (g_selected_asset_idx + 1) % count;
                }
                if (kDown & KEY_UP) {
                    g_selected_asset_idx = (g_selected_asset_idx - 1 + count) % count;
                }
                
                if ((kDown & KEY_LEFT) || (kDown & KEY_RIGHT)) {
                    if (g_selected_asset_idx >= 0 && g_selected_asset_idx < count) {
                        char cmd_buf[64];
                        const char* sym = g_parsed_assets.items[g_selected_asset_idx].symbol;
                        if (kDown & KEY_LEFT) {
                            snprintf(cmd_buf, sizeof(cmd_buf), "CLEAR_SELL|%sUSDT", sym);
                        } else {
                            snprintf(cmd_buf, sizeof(cmd_buf), "CLEAR_BUY|%sUSDT", sym);
                        }
                        network_send_str(cmd_buf);
                    }
                }
            }
        }

        // Mode 2: SETTINGS
        if (g_screen_mode == 2) {
            if (kDown & KEY_X) {
                graphics_next_theme();
                save_config(ip_buffer, port, g_auth_key, graphics_get_current_theme_idx());
            }
        }

        // High-Priority Trade Notification Sound
        if (g_play_alert_sound) {
            g_play_alert_sound = 0;
            audio_play_confirm();
        }

        // Touch Input Router (Unified calibrated hit detection)
        if (kDown & KEY_TOUCH) {
            UiHitAction hit = graphics_hit_test(touch.px, touch.py, g_screen_mode);
            switch (hit) {
                case HIT_EMERGENCY_STOP:
                    g_modal_state = MODAL_CONFIRM_EMERGENCY_STOP;
                    break;
                case HIT_PAUSE:
                    network_send_str("PAUSE");
                    break;
                case HIT_TOGGLE_DCA:
                    network_send_str("TOGGLE_DCA");
                    break;
                case HIT_FORCE_EVALUATE:
                    network_send_str("FORCE_EVALUATE");
                    break;
                case HIT_APPROVE:
                    g_modal_state = MODAL_CONFIRM_APPROVE;
                    break;
                case HIT_REJECT:
                    network_send_str("REJECT");
                    g_telemetry_has_trade = 0;
                    break;
                case HIT_SETTINGS_THEME:
                    graphics_next_theme();
                    save_config(ip_buffer, port, g_auth_key, graphics_get_current_theme_idx());
                    break;
                default:
                    break;
            }
        }

        graphics_draw_frame(ip_buffer, port);
    }

    graphics_exit();
    audio_exit();
    ptmuExit();
    network_exit();
    romfsExit();
    gfxExit();
    return 0;
}
