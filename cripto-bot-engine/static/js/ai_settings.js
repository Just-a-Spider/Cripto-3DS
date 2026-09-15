/**
 * ai_settings.js - Modular LangChain Universal AI Provider Management
 * Handles provider selection, dynamic model presets, custom base URLs,
 * connection diagnostic testing, and conversation session clearing.
 */

let aiProvidersData = null;

async function loadAiProviders() {
    if (aiProvidersData) return aiProvidersData;
    try {
        const res = await fetch(getApiBase() + "/api/ai/providers", {
            headers: { "X-Auth-PIN": authPin }
        });
        if (res.ok) {
            const json = await res.json();
            aiProvidersData = json.providers;
            return aiProvidersData;
        }
    } catch (e) {
        console.warn("Could not fetch AI providers metadata:", e);
    }
    return null;
}

function onAiProviderChanged() {
    const provSelect = document.getElementById("ai-provider");
    if (!provSelect) return;
    const prov = provSelect.value;
    const modelSelect = document.getElementById("ai-model");
    const baseUrlContainer = document.getElementById("ai-base-url-container");
    const baseUrlInput = document.getElementById("ai-base-url");
    const keyInput = document.getElementById("ai-api-key");
    const keyLabel = document.getElementById("ai-key-label");

    if (!aiProvidersData) return;
    const info = aiProvidersData[prov] || aiProvidersData["custom"] || {};

    // Update models dropdown
    if (modelSelect && info.models) {
        const currentModel = modelSelect.value;
        let optionsHtml = info.models.map(m => 
            `<option value="${m}" ${m === currentModel ? "selected" : ""}>${m}</option>`
        ).join("");
        optionsHtml += `<option value="custom">-- Enter Custom Model Name --</option>`;
        modelSelect.innerHTML = optionsHtml;
        if (!info.models.includes(currentModel)) {
            modelSelect.value = info.default_model || (info.models[0] || "");
        }
    }

    // Toggle Base URL input
    if (baseUrlContainer && baseUrlInput) {
        if (info.requires_base_url || prov === "deepseek" || prov === "ollama" || prov === "openrouter" || prov === "custom") {
            baseUrlContainer.style.display = "block";
            if (!baseUrlInput.value && info.default_base_url) {
                baseUrlInput.placeholder = info.default_base_url;
            }
        } else {
            baseUrlContainer.style.display = "none";
        }
    }

    // Update API Key placeholder / requirements
    if (keyInput && keyLabel) {
        if (prov === "ollama") {
            keyLabel.innerText = "API Key (Optional for Ollama):";
            keyInput.placeholder = "Not required for local Ollama";
        } else {
            keyLabel.innerText = `${info.name || prov.toUpperCase()} API Key:`;
            if (!keyInput.value) {
                keyInput.placeholder = `Enter ${info.name || prov} API Key...`;
            }
        }
    }
}

function onAiModelChanged() {
    const modelSelect = document.getElementById("ai-model");
    const customModelInput = document.getElementById("ai-custom-model-input");
    if (!modelSelect || !customModelInput) return;

    if (modelSelect.value === "custom") {
        customModelInput.style.display = "block";
        customModelInput.focus();
    } else {
        customModelInput.style.display = "none";
    }
}

async function populateAiSettingsUI(data) {
    await loadAiProviders();

    const provSelect = document.getElementById("ai-provider");
    const modelSelect = document.getElementById("ai-model");
    const baseUrlInput = document.getElementById("ai-base-url");
    const keyInput = document.getElementById("ai-api-key");
    const fallbackProv = document.getElementById("ai-fallback-provider");
    const fallbackModel = document.getElementById("ai-fallback-model");
    const fallbackKey = document.getElementById("ai-fallback-key");

    if (provSelect) {
        provSelect.value = data.ai_provider || "google";
    }

    onAiProviderChanged();

    if (modelSelect) {
        const targetModel = data.ai_model || data.gemini_model || "gemini-3.1-flash";
        const exists = Array.from(modelSelect.options).some(o => o.value === targetModel);
        if (exists) {
            modelSelect.value = targetModel;
        } else {
            modelSelect.value = "custom";
            const customInput = document.getElementById("ai-custom-model-input");
            if (customInput) {
                customInput.style.display = "block";
                customInput.value = targetModel;
            }
        }
    }

    if (baseUrlInput && data.ai_base_url) {
        baseUrlInput.value = data.ai_base_url;
    }

    if (keyInput && (data.has_ai || data.has_gemini) && !keyInput.value) {
        keyInput.placeholder = "•••••••••••••••• (Saved)";
    }

    if (fallbackProv && data.ai_fallback_provider) {
        fallbackProv.value = data.ai_fallback_provider;
    }

    if (fallbackModel && (data.ai_fallback_model || data.groq_model)) {
        fallbackModel.value = data.ai_fallback_model || data.groq_model;
    }

    if (fallbackKey && (data.has_ai_fallback || data.has_groq) && !fallbackKey.value) {
        fallbackKey.placeholder = "•••••••••••••••• (Saved)";
    }
}

function collectAiConfigPayload() {
    const provSelect = document.getElementById("ai-provider");
    const modelSelect = document.getElementById("ai-model");
    const customModelInput = document.getElementById("ai-custom-model-input");
    const baseUrlInput = document.getElementById("ai-base-url");
    const keyInput = document.getElementById("ai-api-key");
    const fallbackProv = document.getElementById("ai-fallback-provider");
    const fallbackModel = document.getElementById("ai-fallback-model");
    const fallbackKey = document.getElementById("ai-fallback-key");

    let chosenModel = modelSelect ? modelSelect.value : "gemini-3.1-flash";
    if (chosenModel === "custom" && customModelInput && customModelInput.value.trim()) {
        chosenModel = customModelInput.value.trim();
    }

    const provider = provSelect ? provSelect.value : "google";
    const apiKey = keyInput ? keyInput.value : "";
    const baseUrl = baseUrlInput ? baseUrlInput.value.trim() : "";
    const fbProv = fallbackProv ? fallbackProv.value : "groq";
    const fbModel = fallbackModel ? fallbackModel.value.trim() : "llama-3.3-70b-versatile";
    const fbKey = fallbackKey ? fallbackKey.value : "";

    return {
        ai_provider: provider,
        ai_model: chosenModel,
        ai_api_key: apiKey,
        ai_base_url: baseUrl,
        ai_fallback_provider: fbProv,
        ai_fallback_model: fbModel,
        ai_fallback_api_key: fbKey,
        // Sync legacy variables
        gemini_api_key: provider === "google" && apiKey ? apiKey : (document.getElementById("gemini-api-key")?.value || ""),
        gemini_model: chosenModel,
        groq_api_key: fbProv === "groq" && fbKey ? fbKey : (document.getElementById("groq-api-key")?.value || ""),
        groq_model: fbModel
    };
}

async function testAiConnection() {
    const btn = document.getElementById("btn-test-ai");
    const statusBadge = document.getElementById("ai-test-status");
    if (!statusBadge) return;

    statusBadge.innerHTML = `<span style="color: var(--yellow);">Testing provider connection...</span>`;
    if (btn) btn.disabled = true;

    const payload = collectAiConfigPayload();

    try {
        const res = await fetch(getApiBase() + "/api/ai/test", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Auth-PIN": authPin
            },
            body: JSON.stringify({
                provider: payload.ai_provider,
                model: payload.ai_model,
                api_key: payload.ai_api_key,
                base_url: payload.ai_base_url
            })
        });

        const data = await res.json();
        if (res.ok && data.status === "ok") {
            statusBadge.innerHTML = `<span style="color: var(--green); font-weight: bold;">Connected (${data.latency_ms}ms) • ${data.model}</span>`;
        } else {
            const err = data.message || data.detail || "Connection failed";
            statusBadge.innerHTML = `<span style="color: var(--red); font-size: 0.8rem;">Failed: ${err.substring(0, 100)}</span>`;
        }
    } catch (e) {
        statusBadge.innerHTML = `<span style="color: var(--red); font-size: 0.8rem;">Network error: ${e.message}</span>`;
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function clearAiSessionMemory() {
    if (!confirm("Clear active conversational memory and reset assistant chat context?")) {
        return;
    }
    try {
        const res = await fetch(getApiBase() + "/api/ai/chat/history?session_id=default", {
            method: "DELETE",
            headers: { "X-Auth-PIN": authPin }
        });
        const data = await res.json();
        if (res.ok) {
            alert("AI conversational memory cleared.");
        } else {
            alert("Failed to clear memory: " + (data.detail || "Error"));
        }
    } catch (e) {
        alert("Network error: " + e.message);
    }
}
