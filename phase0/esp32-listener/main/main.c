// ESP32-C6 listener for the Phase 0 Wi-Fi capacity test.
//
// Joins the hub's Wi-Fi, registers with hub/loadgen.py, receives one audio-like UDP
// stream and reports packet loss, lateness and jitter back every report interval.
// Mirrors listener/listener.py exactly; see that file for the reasoning behind the
// statistics.
//
// Configure the network with `idf.py menuconfig` -> "Boca a Oreja listener".
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "lwip/sockets.h"
#include "nvs_flash.h"

#include "protocol.h"

static const char *TAG = "bao";

#define CONNECTED_BIT BIT0
#define BUCKET_US     5000000LL   // delay baseline window half, as in listener.py

static EventGroupHandle_t s_wifi_events;

typedef struct {
    uint32_t stream_id;
    uint32_t highest;
    uint32_t received;
    uint32_t lost;
    uint32_t reordered;
    uint32_t late;
    int64_t  jitter16;          // RFC 3550 jitter scaled by 16; the C6 has no FPU
    int64_t  max_excess;
    int64_t  prev_transit;
    bool     have_prev;
    int64_t  bucket_min[2];
    bool     bucket_set[2];
    int      bucket;
    int64_t  bucket_started;
} stats_t;

static void stats_reset(stats_t *s, uint32_t stream_id)
{
    memset(s, 0, sizeof(*s));
    s->stream_id = stream_id;
    s->bucket_started = esp_timer_get_time();
}

static void stats_on_packet(stats_t *s, const bao_data_t *d, int64_t recv_us)
{
    if (d->stream_id != s->stream_id) {
        stats_reset(s, d->stream_id);
    }
    int64_t transit = recv_us - (int64_t)d->send_us;

    if (!s->have_prev) {
        s->highest = d->seq;
    } else if (d->seq > s->highest) {
        s->lost += d->seq - s->highest - 1;
        s->highest = d->seq;
    } else if (d->seq == s->highest) {
        return;  // duplicate
    } else if (s->lost > 0) {
        s->lost--;  // an earlier gap filled in late
        s->reordered++;
    }
    s->received++;

    if (s->have_prev) {
        int64_t diff = llabs(transit - s->prev_transit);
        s->jitter16 += diff - ((s->jitter16 + 8) >> 4);
    }
    s->prev_transit = transit;
    s->have_prev = true;

    if (recv_us - s->bucket_started >= BUCKET_US) {
        s->bucket ^= 1;
        s->bucket_set[s->bucket] = false;
        s->bucket_started = recv_us;
    }
    if (!s->bucket_set[s->bucket] || transit < s->bucket_min[s->bucket]) {
        s->bucket_min[s->bucket] = transit;
        s->bucket_set[s->bucket] = true;
    }
    int64_t baseline = s->bucket_min[s->bucket];
    int other = s->bucket ^ 1;
    if (s->bucket_set[other] && s->bucket_min[other] < baseline) {
        baseline = s->bucket_min[other];
    }
    int64_t excess = transit - baseline;
    if (excess > (int64_t)CONFIG_BAO_LATE_MS * 1000) {
        s->late++;
    }
    if (excess > s->max_excess) {
        s->max_excess = excess;
    }
}

static void wifi_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(s_wifi_events, CONNECTED_BIT);
        ESP_LOGW(TAG, "disconnected, reconnecting");
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = data;
        ESP_LOGI(TAG, "got IP " IPSTR, IP2STR(&event->ip_info.ip));
        xEventGroupSetBits(s_wifi_events, CONNECTED_BIT);
    }
}

static void wifi_start(void)
{
    s_wifi_events = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event_handler, NULL));

    wifi_config_t cfg = {0};
    strlcpy((char *)cfg.sta.ssid, CONFIG_BAO_WIFI_SSID, sizeof(cfg.sta.ssid));
    strlcpy((char *)cfg.sta.password, CONFIG_BAO_WIFI_PASSWORD, sizeof(cfg.sta.password));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &cfg));
    ESP_ERROR_CHECK(esp_wifi_start());

    // Phones listening with the screen off use power save too, and the AP buffering
    // frames for dozing clients is part of what this test should see. Turn it off
    // only to isolate the radio itself.
#if CONFIG_BAO_WIFI_POWER_SAVE
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_MIN_MODEM));
#else
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
#endif
}

static void send_report(int sock, const struct sockaddr_in *hub, const uint8_t mac[6], stats_t *s)
{
    bao_report_t r = {0};
    memcpy(r.magic, BAO_REPORT_MAGIC, 4);
    memcpy(r.mac, mac, 6);
    r.stream_id = s->stream_id;
    r.uptime_ms = (uint32_t)(esp_timer_get_time() / 1000);
    r.highest_seq = s->highest;
    r.received = s->received;
    r.lost = s->lost;
    r.reordered = s->reordered;
    r.late = s->late;
    r.jitter_us = (uint32_t)(s->jitter16 >> 4);
    r.max_excess_us = (uint32_t)s->max_excess;

    wifi_ap_record_t ap;
    if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
        r.rssi = ap.rssi;
        r.channel = ap.primary;
    }

    // Fails while reconnecting; the next report will try again.
    sendto(sock, &r, sizeof(r), 0, (const struct sockaddr *)hub, sizeof(*hub));
    ESP_LOGI(TAG, "rx %lu lost %lu late %lu jitter %lu us maxdly %lu us rssi %d",
             (unsigned long)r.received, (unsigned long)r.lost, (unsigned long)r.late,
             (unsigned long)r.jitter_us, (unsigned long)r.max_excess_us, r.rssi);
    s->max_excess = 0;
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    uint8_t mac[6];
    ESP_ERROR_CHECK(esp_read_mac(mac, ESP_MAC_WIFI_STA));
    ESP_LOGI(TAG, "listener " MACSTR " -> %s:%d", MAC2STR(mac), CONFIG_BAO_SERVER_IP, CONFIG_BAO_SERVER_PORT);

    wifi_start();
    xEventGroupWaitBits(s_wifi_events, CONNECTED_BIT, pdFALSE, pdTRUE, portMAX_DELAY);

    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock < 0) {
        ESP_LOGE(TAG, "socket: %d", errno);
        abort();
    }
    struct timeval tv = {.tv_sec = 0, .tv_usec = 50000};
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    struct sockaddr_in hub = {
        .sin_family = AF_INET,
        .sin_port = htons(CONFIG_BAO_SERVER_PORT),
        .sin_addr.s_addr = inet_addr(CONFIG_BAO_SERVER_IP),
    };

    static stats_t stats;
    stats_reset(&stats, 0);
    static uint8_t buf[1500];
    int64_t next_report = 0;

    while (true) {
        int len = recv(sock, buf, sizeof(buf), 0);
        int64_t now = esp_timer_get_time();
        if (len >= (int)sizeof(bao_data_t) && memcmp(buf, BAO_DATA_MAGIC, 4) == 0) {
            bao_data_t d;
            memcpy(&d, buf, sizeof(d));
            stats_on_packet(&stats, &d, now);
        }
        if (now >= next_report) {
            send_report(sock, &hub, mac, &stats);
            next_report = now + (int64_t)CONFIG_BAO_REPORT_INTERVAL_MS * 1000;
        }
    }
}
