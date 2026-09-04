// ==========================================
// settings.js - Configuration Modal & Pair Management
// ==========================================

var currentPairs = [];

function populateSettingsInputs(data) {
    if (!data) return;
    if (data.risk_config) {
        if (data.risk_config.max_trade_usdt !== undefined) document.getElementById("max-trade").value = data.risk_config.max_trade_usdt;
        if (data.risk_config.max_daily_spend_usdt !== undefined) document.getElementById("max-daily").value = data.risk_config.max_daily_spend_usdt;
        if (data.risk_config.min_usdt_reserve !== undefined) document.getElementById("min-reserve").value = data.risk_config.min_usdt_reserve;
        document.getElementById("auth-pin").value = data.risk_config.auth_pin || authPin || "1234";
        if (data.risk_config.require_human_approval !== undefined) document.getElementById("require-approval").checked = data.risk_config.require_human_approval;
    }
    if (data.testnet !== undefined) {
        document.getElementById("testnet-mode").checked = data.testnet;
    }
    if (data.strategies) {
        if (data.strategies.dca_interval !== undefined) document.getElementById("dca-interval").value = data.strategies.dca_interval;
        if (data.strategies.rsi_threshold !== undefined) document.getElementById("rsi-threshold").value = data.strategies.rsi_threshold;
        if (data.strategies.tp_percent !== undefined) document.getElementById("tp-percent").value = data.strategies.tp_percent;
        if (data.strategies.sl_percent !== undefined) document.getElementById("sl-percent").value = data.strategies.sl_percent;
        document.getElementById("trailing-enabled").checked = data.strategies.trailing_enabled !== false;
        document.getElementById("trailing-activation").value = data.strategies.trailing_activation_percent || 3.0;
        document.getElementById("trailing-delta").value = data.strategies.trailing_delta_percent || 1.5;
        document.getElementById("bull-dip-enabled").checked = data.strategies.bull_regime_dip_enabled !== false;
        document.getElementById("bull-rsi-threshold").value = data.strategies.bull_rsi_threshold || 42.0;
        document.getElementById("partial-tp-enabled").checked = data.strategies.partial_tp_enabled !== false;
        document.getElementById("partial-tp-percent").value = data.strategies.partial_tp_percent || 4.0;
        document.getElementById("partial-tp-ratio").value = data.strategies.partial_tp_ratio || 0.5;
        if (data.strategies.rsi_timeframe_minutes !== undefined) document.getElementById("rsi-timeframe").value = data.strategies.rsi_timeframe_minutes;
        if (data.strategies.rsi_history_length !== undefined) document.getElementById("rsi-history").value = data.strategies.rsi_history_length;
        if (data.strategies.signal_cooldown_hours !== undefined) document.getElementById("signal-cooldown").value = data.strategies.signal_cooldown_hours;
    }
    if (data.ai_scout) {
        document.getElementById("ai-scout-enabled").checked = data.ai_scout.enabled !== false;
        document.getElementById("ai-scout-interval").value = data.ai_scout.interval_hours || 2.0;
        document.getElementById("ai-scout-confidence").value = data.ai_scout.min_confidence || 0.85;
    }
    document.getElementById("discord-webhook").value = data.discord_webhook_url || "";
    document.getElementById("discord-channel-id").value = data.discord_channel_id || "";

    const rawIds = (data.risk_config && data.risk_config.allowed_discord_user_ids !== undefined) 
        ? data.risk_config.allowed_discord_user_ids 
        : data.allowed_discord_user_ids;
    if (rawIds !== undefined && rawIds !== null) {
        const idsVal = Array.isArray(rawIds) ? rawIds.join(", ") : String(rawIds);
        document.getElementById("allowed-discord-user-ids").value = idsVal;
    }

    if (data.favorite_pairs) {
        currentPairs = [...data.favorite_pairs];
        const display = document.getElementById("favorite-pairs-display");
        display.innerHTML = currentPairs.map(p => 
            `<div class="pair-tag">${p} <span onclick="removePair('${p}')">x</span></div>`
        ).join("");
    }
    
    if (data.has_keys && !document.getElementById("api-key").value) {
        document.getElementById("api-key").placeholder = "•••••••••••••••• (Saved)";
        document.getElementById("secret-key").placeholder = "•••••••••••••••• (Saved)";
    }
    if (data.has_discord_bot && !document.getElementById("discord-bot-token").value) {
        document.getElementById("discord-bot-token").placeholder = "•••••••••••••••• (Saved)";
    }
    if (data.has_gemini && !document.getElementById("gemini-api-key").value) {
        document.getElementById("gemini-api-key").placeholder = "•••••••••••••••• (Saved)";
    }
    if (data.has_groq && document.getElementById("groq-api-key") && !document.getElementById("groq-api-key").value) {
        document.getElementById("groq-api-key").placeholder = "•••••••••••••••• (Saved)";
    }
    if (data.groq_model && document.getElementById("groq-model")) {
        document.getElementById("groq-model").value = data.groq_model;
    }
    if (data.enable_search_grounding !== undefined && document.getElementById("enable-search-grounding")) {
        document.getElementById("enable-search-grounding").checked = Boolean(data.enable_search_grounding);
    }
    if (data.available_gemini_models && data.available_gemini_models.length > 0) {
        const sel = document.getElementById("gemini-model");
        const curVal = data.gemini_model || sel.value || data.available_gemini_models[0];
        sel.innerHTML = data.available_gemini_models.map(m => 
            `<option value="${m}" ${m === curVal ? "selected" : ""}>${m}</option>`
        ).join("");
    } else if (data.gemini_model) {
        document.getElementById("gemini-model").value = data.gemini_model;
    }
    if (data.gemini_search_model) {
        const searchSel = document.getElementById("gemini-search-model");
        if (searchSel) searchSel.value = data.gemini_search_model;
    }
}

async function showSettings() {
    if (!lastStateData) {
        try {
            const res = await fetch(getApiBase() + "/api/state", { headers: { "X-Auth-PIN": authPin } });
            if (res.ok) {
                lastStateData = await res.json();
            }
        } catch(e) {}
    }
    if (lastStateData) {
        populateSettingsInputs(lastStateData);
    }
    document.getElementById("settings-modal").style.display = "flex";
}

function addPair() {
    const val = document.getElementById("pair-search").value.trim().toUpperCase();
    if (val && !currentPairs.includes(val)) {
        currentPairs.push(val);
        document.getElementById("favorite-pairs-display").innerHTML = currentPairs.map(p => 
            `<div class="pair-tag">${p} <span onclick="removePair('${p}')">x</span></div>`
        ).join("");
        document.getElementById("pair-search").value = "";
    }
}

function clearPairs() { 
    currentPairs = []; 
    document.getElementById("favorite-pairs-display").innerHTML = ""; 
}

function removePair(pair) {
    currentPairs = currentPairs.filter(p => p !== pair);
    document.getElementById("favorite-pairs-display").innerHTML = currentPairs.map(p => 
        `<div class="pair-tag">${p} <span onclick="removePair('${p}')">x</span></div>`
    ).join("");
}

async function saveConfig() {
    const body = {
        max_trade_usdt: parseFloat(document.getElementById("max-trade").value),
        max_daily_spend_usdt: parseFloat(document.getElementById("max-daily").value),
        min_usdt_reserve: parseFloat(document.getElementById("min-reserve").value),
        require_human_approval: document.getElementById("require-approval").checked,
        auth_pin: document.getElementById("auth-pin").value,
        api_key: document.getElementById("api-key").value,
        secret_key: document.getElementById("secret-key").value,
        favorite_pairs: currentPairs.join(","),
        testnet: document.getElementById("testnet-mode").checked,
        dca_interval: parseInt(document.getElementById("dca-interval").value) || 3600,
        rsi_threshold: parseFloat(document.getElementById("rsi-threshold").value) || 30.0,
        tp_percent: parseFloat(document.getElementById("tp-percent").value) || 5.0,
        sl_percent: parseFloat(document.getElementById("sl-percent").value) || 3.0,
        trailing_enabled: document.getElementById("trailing-enabled").checked,
        trailing_activation_percent: parseFloat(document.getElementById("trailing-activation").value) || 3.0,
        trailing_delta_percent: parseFloat(document.getElementById("trailing-delta").value) || 1.5,
        bull_regime_dip_enabled: document.getElementById("bull-dip-enabled").checked,
        bull_rsi_threshold: parseFloat(document.getElementById("bull-rsi-threshold").value) || 42.0,
        partial_tp_enabled: document.getElementById("partial-tp-enabled").checked,
        partial_tp_percent: parseFloat(document.getElementById("partial-tp-percent").value) || 4.0,
        partial_tp_ratio: parseFloat(document.getElementById("partial-tp-ratio").value) || 0.5,
        ai_scout_enabled: document.getElementById("ai-scout-enabled").checked,
        ai_scout_interval_hours: parseFloat(document.getElementById("ai-scout-interval").value) || 2.0,
        ai_scout_min_confidence: parseFloat(document.getElementById("ai-scout-confidence").value) || 0.85,
        rsi_timeframe_minutes: parseInt(document.getElementById("rsi-timeframe").value) || 60,
        rsi_history_length: parseInt(document.getElementById("rsi-history").value) || 250,
        signal_cooldown_hours: parseFloat(document.getElementById("signal-cooldown").value) || 24.0,
        discord_webhook_url: document.getElementById("discord-webhook").value,
        discord_bot_token: document.getElementById("discord-bot-token").value,
        discord_channel_id: document.getElementById("discord-channel-id").value,
        allowed_discord_user_ids: document.getElementById("allowed-discord-user-ids").value,
        gemini_api_key: document.getElementById("gemini-api-key").value,
        gemini_model: document.getElementById("gemini-model").value,
        gemini_search_model: document.getElementById("gemini-search-model").value,
        enable_search_grounding: document.getElementById("enable-search-grounding") ? document.getElementById("enable-search-grounding").checked : false,
        groq_api_key: document.getElementById("groq-api-key") ? document.getElementById("groq-api-key").value : "",
        groq_model: document.getElementById("groq-model") ? document.getElementById("groq-model").value : "llama-3.3-70b-versatile"
    };
    try {
        const res = await fetch(getApiBase() + "/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-Auth-PIN": authPin },
            body: JSON.stringify(body)
        });
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            alert("Error saving settings (" + res.status + "): " + (errData.detail || errData.message || "Request failed"));
            return;
        }
        const resState = await fetch(getApiBase() + "/api/state", { headers: { "X-Auth-PIN": authPin } });
        if (resState.ok) {
            lastStateData = await resState.json();
            populateSettingsInputs(lastStateData);
        }
        document.getElementById("settings-modal").style.display = "none";
        alert("Settings Saved Successfully!");
    } catch (e) {
        alert("Network error saving configuration: " + e.message);
    }
}

async function testDiscordBot() {
    const token = document.getElementById("discord-bot-token").value;
    const channel = document.getElementById("discord-channel-id").value;
    const body = {
        max_trade_usdt: parseFloat(document.getElementById("max-trade").value),
        max_daily_spend_usdt: parseFloat(document.getElementById("max-daily").value),
        min_usdt_reserve: parseFloat(document.getElementById("min-reserve").value),
        require_human_approval: document.getElementById("require-approval").checked,
        auth_pin: document.getElementById("auth-pin").value,
        api_key: document.getElementById("api-key").value,
        secret_key: document.getElementById("secret-key").value,
        favorite_pairs: currentPairs.join(","),
        testnet: document.getElementById("testnet-mode").checked,
        dca_interval: parseInt(document.getElementById("dca-interval").value) || 3600,
        rsi_threshold: parseFloat(document.getElementById("rsi-threshold").value) || 30.0,
        tp_percent: parseFloat(document.getElementById("tp-percent").value) || 5.0,
        sl_percent: parseFloat(document.getElementById("sl-percent").value) || 3.0,
        trailing_enabled: document.getElementById("trailing-enabled").checked,
        trailing_activation_percent: parseFloat(document.getElementById("trailing-activation").value) || 3.0,
        trailing_delta_percent: parseFloat(document.getElementById("trailing-delta").value) || 1.5,
        bull_regime_dip_enabled: document.getElementById("bull-dip-enabled").checked,
        bull_rsi_threshold: parseFloat(document.getElementById("bull-rsi-threshold").value) || 42.0,
        partial_tp_enabled: document.getElementById("partial-tp-enabled").checked,
        partial_tp_percent: parseFloat(document.getElementById("partial-tp-percent").value) || 4.0,
        partial_tp_ratio: parseFloat(document.getElementById("partial-tp-ratio").value) || 0.5,
        ai_scout_enabled: document.getElementById("ai-scout-enabled").checked,
        ai_scout_interval_hours: parseFloat(document.getElementById("ai-scout-interval").value) || 2.0,
        ai_scout_min_confidence: parseFloat(document.getElementById("ai-scout-confidence").value) || 0.85,
        rsi_timeframe_minutes: parseInt(document.getElementById("rsi-timeframe").value) || 60,
        rsi_history_length: parseInt(document.getElementById("rsi-history").value) || 250,
        signal_cooldown_hours: parseFloat(document.getElementById("signal-cooldown").value) || 24.0,
        discord_webhook_url: document.getElementById("discord-webhook").value,
        discord_bot_token: token,
        discord_channel_id: channel,
        allowed_discord_user_ids: document.getElementById("allowed-discord-user-ids").value,
        gemini_api_key: document.getElementById("gemini-api-key").value,
        gemini_model: document.getElementById("gemini-model").value,
        gemini_search_model: document.getElementById("gemini-search-model").value,
        enable_search_grounding: document.getElementById("enable-search-grounding") ? document.getElementById("enable-search-grounding").checked : false,
        groq_api_key: document.getElementById("groq-api-key") ? document.getElementById("groq-api-key").value : "",
        groq_model: document.getElementById("groq-model") ? document.getElementById("groq-model").value : "llama-3.3-70b-versatile"
    };

    try {
        const resSave = await fetch(getApiBase() + "/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-Auth-PIN": authPin },
            body: JSON.stringify(body)
        });
        if (!resSave.ok) {
            alert("[FAILED] Could not save settings prior to bot test.");
            return;
        }

        const resTest = await fetch(getApiBase() + "/api/discord/test", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-Auth-PIN": authPin },
            body: JSON.stringify({ token: token, channel_id: channel })
        });
        const dataTest = await resTest.json();
        if (dataTest.status === "ok") {
            alert("[SUCCESS] " + dataTest.message);
        } else {
            alert("[FAILED] TEST FAILED:\n" + dataTest.message);
        }
    } catch (e) {
        alert("[ERROR] Error sending test message: " + e.message);
    }
}

async function fetchGeminiModels() {
    const key = document.getElementById("gemini-api-key").value;
    if (key) {
        await saveConfig();
    }
    try {
        const res = await fetch(getApiBase() + "/api/gemini/models", { headers: { "X-Auth-PIN": authPin } });
        const data = await res.json();
        if (data.models && data.models.length > 0) {
            const sel = document.getElementById("gemini-model");
            const cur = sel.value;
            sel.innerHTML = data.models.map(m => `<option value="${m}" ${m === cur ? "selected" : ""}>${m}</option>`).join("");
            if (!data.models.includes(cur)) {
                sel.value = data.models[0];
            }
            alert(`[SUCCESS] Found ${data.models.length} active Gemini models! Selected: ${sel.value}`);
        } else {
            alert("[WARNING] No models returned. Check if your API key is valid at aistudio.google.com.");
        }
    } catch (e) {
        alert("[ERROR] Error fetching models: " + e.message);
    }
}
