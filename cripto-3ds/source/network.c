#include "network.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <fcntl.h>
#include <malloc.h>
#include <3ds/thread.h>
#include <3ds/svc.h>
#include <3ds/synchronization.h>
#include <3ds/services/soc.h>
#include <netinet/tcp.h>
#include <errno.h>

char g_telemetry_status[16] = "PAUSED";
char g_telemetry_pair[16] = "BTCUSDT";
float g_telemetry_price = 0.0f;
float g_telemetry_usdt = 0.0f;
float g_telemetry_rsi = 0;
char g_telemetry_assets[512] = "";
int g_play_alert_sound = 0;
int g_telemetry_has_trade = 0;
int g_dca_enabled = 1;
char g_trade_action[16] = "";
char g_trade_pair[16] = "";
char g_trade_reason[64] = "";
float g_trade_price = 0.0f;
float g_trade_amount_usdt = 0.0f;
char g_ai_risk[32] = "";
char g_ai_verdict[16] = "";

float g_price_history[10] = {0};
int g_history_count = 0;

int g_http_status = 0; // 0=connecting, 1=connected, -1=error
int g_fetching_enabled = 1;
char g_auth_key[16] = "1234";
AssetList g_parsed_assets = {0};

void parse_telemetry_assets(const char* raw_str, float total_wealth) {
    g_parsed_assets.count = 0;
    g_parsed_assets.total_crypto_value = 0.0f;
    if (!raw_str || strlen(raw_str) == 0) return;

    char copy[512];
    strncpy(copy, raw_str, sizeof(copy) - 1);
    copy[sizeof(copy) - 1] = '\0';

    char* saveptr = NULL;
    char* token = strtok_r(copy, ",", &saveptr);
    while (token != NULL && g_parsed_assets.count < 20) {
        char* asset = token;
        char* bal_str = strchr(asset, ':');
        char* price_str = NULL;
        char* rsi_str = NULL;
        if (bal_str) {
            *bal_str = '\0';
            bal_str++;
            price_str = strchr(bal_str, ':');
            if (price_str) {
                *price_str = '\0';
                price_str++;
                rsi_str = strchr(price_str, ':');
                if (rsi_str) {
                    *rsi_str = '\0';
                    rsi_str++;
                }
            }
        }

        ParsedAsset* item = &g_parsed_assets.items[g_parsed_assets.count];
        strncpy(item->symbol, asset, sizeof(item->symbol) - 1);
        item->symbol[sizeof(item->symbol) - 1] = '\0';
        item->balance = bal_str ? (float)atof(bal_str) : 0.0f;
        item->price = price_str ? (float)atof(price_str) : 0.0f;
        item->rsi = rsi_str ? (float)atof(rsi_str) : 0.0f;
        item->value_usdt = item->balance * item->price;

        g_parsed_assets.total_crypto_value += item->value_usdt;
        g_parsed_assets.count++;

        token = strtok_r(NULL, ",", &saveptr);
    }
}

static u32 *soc_buffer = NULL;
static int g_socket = -1;
static Thread net_thread = NULL;
static volatile int g_net_running = 0;
static LightLock send_lock;

static char srv_ip[64];
static int srv_port = 7343;

static void parse_telemetry(const char* json) {
    char *status_ptr = strstr(json, "\"status\": \"");
    if (status_ptr) sscanf(status_ptr, "\"status\": \"%15[^\"]\"", g_telemetry_status);

    char *pair_ptr = strstr(json, "\"pair\": \"");
    if (pair_ptr) sscanf(pair_ptr, "\"pair\": \"%15[^\"]\"", g_telemetry_pair);

    char *price_ptr = strstr(json, "\"price\": ");
    if (price_ptr) sscanf(price_ptr, "\"price\": %f", &g_telemetry_price);

    char *usdt_ptr = strstr(json, "\"usdt\": ");
    if (usdt_ptr) sscanf(usdt_ptr, "\"usdt\": %f", &g_telemetry_usdt);

    char *rsi_ptr = strstr(json, "\"rsi\": ");
    if (rsi_ptr) sscanf(rsi_ptr, "\"rsi\": %f", &g_telemetry_rsi);
    
    char *assets_ptr = strstr(json, "\"top_assets\": \"");
    if (assets_ptr) {
        sscanf(assets_ptr, "\"top_assets\": \"%511[^\"]\"", g_telemetry_assets);
        parse_telemetry_assets(g_telemetry_assets, g_telemetry_usdt);
    }

    char *trade_ptr = strstr(json, "\"has_trade\": ");
    if (trade_ptr) {
        trade_ptr += strlen("\"has_trade\": ");
        
        int was_trade = g_telemetry_has_trade;
        if (*trade_ptr == 't') g_telemetry_has_trade = 1;
        else g_telemetry_has_trade = 0;
        
        if (g_telemetry_has_trade && !was_trade) {
            g_play_alert_sound = 1;
        }
        
        char *dca_ptr = strstr(json, "\"dca_enabled\": ");
        if (dca_ptr) {
            dca_ptr += strlen("\"dca_enabled\": ");
            g_dca_enabled = (*dca_ptr == 't') ? 1 : 0;
        }
        
        char *t_action = strstr(json, "\"trade_action\": \"");
        if (t_action) sscanf(t_action, "\"trade_action\": \"%15[^\"]\"", g_trade_action);
        
        char *t_pair = strstr(json, "\"trade_pair\": \"");
        if (t_pair) sscanf(t_pair, "\"trade_pair\": \"%15[^\"]\"", g_trade_pair);
        
        char *t_reason = strstr(json, "\"trade_reason\": \"");
        if (t_reason) sscanf(t_reason, "\"trade_reason\": \"%63[^\"]\"", g_trade_reason);

        char *t_price = strstr(json, "\"trade_price\": ");
        if (t_price) sscanf(t_price, "\"trade_price\": %f", &g_trade_price);

        char *t_amt = strstr(json, "\"trade_amount_usdt\": ");
        if (t_amt) sscanf(t_amt, "\"trade_amount_usdt\": %f", &g_trade_amount_usdt);

        char *t_risk = strstr(json, "\"ai_risk\": \"");
        if (t_risk) sscanf(t_risk, "\"ai_risk\": \"%31[^\"]\"", g_ai_risk);
        else strcpy(g_ai_risk, "");

        char *t_verdict = strstr(json, "\"ai_verdict\": \"");
        if (t_verdict) sscanf(t_verdict, "\"ai_verdict\": \"%15[^\"]\"", g_ai_verdict);
        else strcpy(g_ai_verdict, "");
    } else {
        strcpy(g_ai_risk, "");
        strcpy(g_ai_verdict, "");
    }

    static int call_counter = 0;
    call_counter++;
    if (call_counter >= 10 || g_history_count == 0) {
        if (g_history_count < 10) {
            g_price_history[g_history_count] = g_telemetry_price;
            g_history_count++;
        } else {
            for (int i = 0; i < 9; i++) {
                g_price_history[i] = g_price_history[i+1];
            }
            g_price_history[9] = g_telemetry_price;
        }
        call_counter = 0;
    }
}

static void net_thread_func(void *arg) {
    while (g_net_running) {
        if (!g_fetching_enabled) {
            svcSleepThread(500 * 1000000LL);
            continue;
        }

        g_http_status = 0; // connecting
        g_socket = socket(AF_INET, SOCK_STREAM, 0);
        if (g_socket < 0) {
            g_http_status = -1;
            svcSleepThread(1000 * 1000000LL);
            continue;
        }

        struct sockaddr_in srv_addr;
        memset(&srv_addr, 0, sizeof(srv_addr));
        srv_addr.sin_family = AF_INET;
        srv_addr.sin_port = htons(srv_port);
        inet_pton(AF_INET, srv_ip, &srv_addr.sin_addr);


        if (connect(g_socket, (struct sockaddr*)&srv_addr, sizeof(srv_addr)) < 0) {
            close(g_socket);
            g_socket = -1;
            g_http_status = -1;
            svcSleepThread(1000 * 1000000LL);
            continue;
        }

        
        // AUTH handshake
        char auth_cmd[64];
        snprintf(auth_cmd, sizeof(auth_cmd), "AUTH %s\n", g_auth_key);
        if (send(g_socket, auth_cmd, strlen(auth_cmd), 0) < 0) {
            close(g_socket);
            g_socket = -1;
            g_http_status = -1;
            svcSleepThread(1000 * 1000000LL);
            continue;
        }

        int flag = 1;
        setsockopt(g_socket, IPPROTO_TCP, TCP_NODELAY, &flag, sizeof(int));
        
        int flags = fcntl(g_socket, F_GETFL, 0);
        fcntl(g_socket, F_SETFL, flags | O_NONBLOCK);

        g_http_status = 1; // connected
        char buf[8192];
        memset(buf, 0, sizeof(buf));
        int total_len = 0;

        while (g_net_running && g_fetching_enabled) {
            int recvd = recv(g_socket, buf + total_len, sizeof(buf) - total_len - 1, 0);
            
            if (recvd < 0) {
                if (errno == EWOULDBLOCK || errno == EAGAIN) {
                    svcSleepThread(50 * 1000000LL); // 50ms sleep
                    continue;
                }
                break; // Socket error
            } else if (recvd == 0) {
                break; // Connection closed
            }
            
            total_len += recvd;
            buf[total_len] = '\0';
            
            // Check for newline which separates packets
            char *nl = strchr(buf, '\n');
            while (nl) {
                *nl = '\0';
                
                // AUTH_FAIL drops connection
                if (strstr(buf, "AUTH_FAIL")) {
                    g_http_status = -1;
                    close(g_socket);
                    g_socket = -1;
                    goto reconnect;
                } else {
                    parse_telemetry(buf);
                }
                
                int remaining = total_len - ((nl + 1) - buf);
                memmove(buf, nl + 1, remaining);
                total_len = remaining;
                buf[total_len] = '\0';
                
                nl = strchr(buf, '\n');
            }
        }

reconnect:
        if (g_socket >= 0) {
            close(g_socket);
            g_socket = -1;
        }
        g_http_status = -1;
    }
}

Result network_init(const char* ip, int port) {
    soc_buffer = (u32*)memalign(0x1000, 0x20000);
    if (!soc_buffer) return -1;
    socInit(soc_buffer, 0x20000);

    strncpy(srv_ip, ip, sizeof(srv_ip)-1);
    srv_port = port;

    LightLock_Init(&send_lock);
    
    g_net_running = 1;
    net_thread = threadCreate(net_thread_func, NULL, 32768, 0x3f, -2, false);
    return 0;
}

void network_exit() {
    g_net_running = 0;
    if (g_socket >= 0) close(g_socket);
    if (net_thread) {
        threadJoin(net_thread, U64_MAX);
        threadFree(net_thread);
    }
    socExit();
    if (soc_buffer) free(soc_buffer);
}

void network_send_action(const char* action, const char* param) {
    if (g_socket < 0 || !action) return;
    
    char buf[256];
    if (param && strlen(param) > 0) {
        snprintf(buf, sizeof(buf), "%s|%s|%s\n", action, param, g_auth_key);
    } else {
        snprintf(buf, sizeof(buf), "%s|%s\n", action, g_auth_key);
    }
    
    LightLock_Lock(&send_lock);
    send(g_socket, buf, strlen(buf), 0);
    LightLock_Unlock(&send_lock);
}

void network_send_str(const char* str) {
    if (g_socket < 0 || !str) return;
    
    // If string already contains '|', split into action and param
    const char* sep = strchr(str, '|');
    if (sep) {
        char action[64] = {0};
        char param[64] = {0};
        size_t act_len = sep - str;
        if (act_len < sizeof(action)) {
            memcpy(action, str, act_len);
            action[act_len] = '\0';
            strncpy(param, sep + 1, sizeof(param) - 1);
            network_send_action(action, param);
            return;
        }
    }
    network_send_action(str, NULL);
}
