#ifndef NETWORK_H
#define NETWORK_H

#include <3ds.h>

typedef struct {
    char symbol[16];
    float balance;
    float price;
    float rsi;
    float value_usdt;
} ParsedAsset;

typedef struct {
    ParsedAsset items[20];
    int count;
    float total_crypto_value;
} AssetList;

extern AssetList g_parsed_assets;

extern char g_telemetry_status[16];
extern char g_telemetry_pair[16];
extern float g_telemetry_price;
extern float g_telemetry_usdt;
extern float g_telemetry_rsi;
extern char g_telemetry_assets[512];
extern int g_play_alert_sound;
extern int g_telemetry_has_trade;
extern int g_dca_enabled;

extern char g_trade_action[16];
extern char g_trade_pair[16];
extern char g_trade_reason[64];
extern float g_trade_price;
extern float g_trade_amount_usdt;
extern char g_ai_risk[32];
extern char g_ai_verdict[16];

extern float g_price_history[10];
extern int g_history_count;

extern int g_http_status; // 0=connecting, 1=connected, -1=error
extern int g_fetching_enabled;
extern char g_auth_key[16];

Result network_init(const char* ip, int port);
void network_exit(void);
void network_send_str(const char* str);
void network_send_action(const char* action, const char* param);
void parse_telemetry_assets(const char* raw_str, float total_wealth);

#endif
