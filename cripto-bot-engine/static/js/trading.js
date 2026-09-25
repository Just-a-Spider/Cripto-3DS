// ==========================================
// trading.js - Trading Actions & Modals
// ==========================================

async function toggleBot(active) {
    await fetch(getApiBase() + "/api/bot/toggle?active=" + active, { method: "POST", headers: { "X-Auth-PIN": authPin } });
}

async function toggleDca(enabled = null) {
    let url = getApiBase() + "/api/strategy/dca/toggle";
    if (enabled !== null) {
        url += "?enabled=" + enabled;
    }
    await fetch(url, { method: "POST", headers: { "X-Auth-PIN": authPin } });
}

async function toggleDcaViaBadge() {
    await toggleDca();
}

async function decideTrade(approved) {
    const overrideInput = document.getElementById("override-trade-amount");
    let url = getApiBase() + "/api/trade/decide?approved=" + approved;
    if (overrideInput && approved) {
        url += "&override_usdt=" + overrideInput.value;
    }
    try {
        await fetch(url, { method: "POST", headers: { "X-Auth-PIN": authPin } });
        setTimeout(fetchTrades, 400);
    } catch(e) {
        console.error(e);
    }
}

async function triggerSimulatedTrade() {
    try {
        await fetch(getApiBase() + "/api/trade/simulate", { method: "POST", headers: { "X-Auth-PIN": authPin } });
    } catch(e) {
        console.error(e);
    }
}

async function forceEvaluate() {
    try {
        await fetch(getApiBase() + "/api/trade/force", { method: "POST", headers: { "X-Auth-PIN": authPin } });
    } catch(e) {
        console.error(e);
    }
}

async function deduplicateTrades() {
    try {
        const res = await fetch(getApiBase() + "/api/trades/deduplicate", {
            method: "POST",
            headers: { "X-Auth-PIN": authPin }
        });
        const data = await res.json();
        alert("Deduplication complete. Pruned " + (data.pruned_duplicates || 0) + " duplicate records.");
        await fetchTrades();
    } catch(e) {
        alert("[ERROR] Error deduplicating trades: " + e.message);
    }
}

async function clearTrades(onlyRejected = true) {
    const promptText = onlyRejected 
        ? "Are you sure you want to clean all REJECTED and TIMEOUT test entries from the database?"
        : "Are you sure you want to completely WIPE all trade records and reset your PnL ledger?";
    if (!confirm(promptText)) return;
    try {
        const res = await fetch(getApiBase() + "/api/trades/clear?only_rejected=" + onlyRejected, {
            method: "DELETE",
            headers: { "X-Auth-PIN": authPin }
        });
        const data = await res.json();
        alert("Cleaned " + data.deleted + " trade records.");
        await fetchTrades();
    } catch(e) {
        alert("[ERROR] Error cleaning trades: " + e.message);
    }
}

// --- Manual Sell Modal Logic ---
let activeSellAsset = null;
let activeSellAmount = 0;
let activeSellVal = 0;
let activeSellPrice = 0;

function openManualSellModal(asset, amount, val, price) {
    activeSellAsset = asset;
    activeSellAmount = amount;
    activeSellVal = val;
    activeSellPrice = price;

    document.getElementById("sell-modal-asset").innerText = `(${asset})`;
    document.getElementById("sell-modal-price").innerText = price ? `$${price.toLocaleString()}` : "Live Ticker";
    document.getElementById("sell-modal-total-qty").innerText = `${amount} ${asset}`;
    document.getElementById("sell-modal-total-val").innerText = `$${val.toFixed(2)} USDT`;
    document.getElementById("sell-pin-input").value = authPin || "";

    setSellPercent(100);
    document.getElementById("manual-sell-modal").style.display = "flex";
}

function closeManualSellModal() {
    document.getElementById("manual-sell-modal").style.display = "none";
}

function setSellPercent(pct) {
    document.getElementById("sell-percent-range").value = pct;
    onSellPercentChange(pct);
}

function onSellPercentChange(pct) {
    pct = parseFloat(pct);
    document.getElementById("sell-percent-label").innerText = `${pct}%`;
    const estVal = activeSellVal * (pct / 100.0);
    const estFee = estVal * 0.001; // 0.10% Binance Spot fee
    document.getElementById("sell-est-value").innerText = `$${estVal.toFixed(2)} USDT`;
    document.getElementById("sell-est-fee").innerText = `$${estFee.toFixed(4)} USDT`;

    const warningEl = document.getElementById("sell-min-warning");
    const submitBtn = document.getElementById("btn-execute-sell");
    if (estVal < 5.0) {
        warningEl.style.display = "block";
        submitBtn.disabled = true;
        submitBtn.style.opacity = "0.5";
    } else {
        warningEl.style.display = "none";
        submitBtn.disabled = false;
        submitBtn.style.opacity = "1.0";
    }
}

async function submitManualSell() {
    const pct = parseFloat(document.getElementById("sell-percent-range").value);
    const pin = document.getElementById("sell-pin-input").value;
    if (!pin) {
        alert("[WARNING] Please enter your Auth PIN.");
        return;
    }

    const estVal = activeSellVal * (pct / 100.0);
    if (estVal < 5.0) {
        alert("[WARNING] Minimum Binance order value is $5.00 USDT. Please increase sell percentage.");
        return;
    }

    if (!confirm(`Are you sure you want to SELL ${pct}% of ${activeSellAsset} (~$${estVal.toFixed(2)} USDT)?`)) {
        return;
    }

    try {
        const res = await fetch(getApiBase() + "/api/manual_sell", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Auth-PIN": pin
            },
            body: JSON.stringify({
                asset: activeSellAsset,
                percent: pct,
                pin: pin
            })
        });

        const data = await res.json();
        if (res.ok && data.status === "success") {
            alert(`[SUCCESS] Successfully sold ${data.sold_qty} ${data.pair}! Realized PnL: $${data.realized_pnl_usdt >= 0 ? "+" : ""}${data.realized_pnl_usdt} (${data.realized_pnl_percent}%)`);
            closeManualSellModal();
            await fetchTrades();
        } else {
            alert(`[FAILED] Sell Failed: ${data.message || "Unknown error"}`);
        }
    } catch(e) {
        alert(`[ERROR] Network/Server Error: ${e.message}`);
    }
}

// --- Manual Buy Modal Logic ---
let activeBuyPair = null;
let activeBuyPrice = 0.0;

function openManualBuyModal(pair, price) {
    activeBuyPair = pair;
    activeBuyPrice = price || 0.0;
    const availUsdtText = document.getElementById("usdt-available").innerText.replace("$", "").replace(",", "").trim();
    const availUsdt = parseFloat(availUsdtText) || 0.0;
    
    document.getElementById("buy-asset-name").innerText = pair;
    document.getElementById("buy-current-price").innerText = `$${activeBuyPrice.toFixed(4)}`;
    document.getElementById("buy-avail-usdt").innerText = `$${availUsdt.toFixed(2)}`;
    document.getElementById("buy-pin-input").value = "";

    const defaultUsdt = Math.min(10.0, Math.max(5.0, availUsdt));
    document.getElementById("buy-usdt-input").value = defaultUsdt.toFixed(2);
    onBuyUsdtChange(defaultUsdt);

    document.getElementById("manual-buy-modal").style.display = "flex";
}

function closeManualBuyModal() {
    document.getElementById("manual-buy-modal").style.display = "none";
}

function onBuyUsdtChange(usdt) {
    usdt = parseFloat(usdt) || 0.0;
    const estQty = activeBuyPrice > 0 ? (usdt / activeBuyPrice) : 0.0;
    const estFee = usdt * 0.001;
    document.getElementById("buy-est-qty").innerText = estQty.toFixed(6);
    document.getElementById("buy-est-fee").innerText = `$${estFee.toFixed(4)}`;

    const warningEl = document.getElementById("buy-min-warning");
    const submitBtn = document.getElementById("btn-execute-buy");
    if (usdt < 5.0) {
        warningEl.style.display = "block";
        submitBtn.disabled = true;
        submitBtn.style.opacity = "0.5";
    } else {
        warningEl.style.display = "none";
        submitBtn.disabled = false;
        submitBtn.style.opacity = "1.0";
    }
}

async function submitManualBuy() {
    const usdt = parseFloat(document.getElementById("buy-usdt-input").value);
    const pin = document.getElementById("buy-pin-input").value;
    if (!pin) {
        alert("[WARNING] Please enter your Auth PIN.");
        return;
    }
    if (usdt < 5.0) {
        alert("[WARNING] Minimum buy order value is $5.00 USDT.");
        return;
    }
    if (!confirm(`Are you sure you want to BUY $${usdt.toFixed(2)} USDT of ${activeBuyPair}?`)) {
        return;
    }

    try {
        const res = await fetch(getApiBase() + "/api/manual_buy", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Auth-PIN": pin
            },
            body: JSON.stringify({
                asset: activeBuyPair,
                usdt_amount: usdt,
                pin: pin
            })
        });

        const data = await res.json();
        if (res.ok && data.status === "success") {
            alert(`[SUCCESS] Successfully bought $${data.bought_usdt} USDT of ${data.pair} (${data.bought_qty} units at avg price $${data.price})!`);
            closeManualBuyModal();
            await syncBalances();
        } else {
            alert(`[FAILED] Buy Failed: ${data.message || "Unknown error"}`);
        }
    } catch(e) {
        alert(`[ERROR] Network/Server Error: ${e.message}`);
    }
}

async function syncBalances() {
    try {
        const res = await fetch(getApiBase() + "/api/balance/sync", {
            method: "POST",
            headers: { "X-Auth-PIN": authPin }
        });
        const data = await res.json();
        alert(`[SUCCESS] Synced Binance Balances! USDT: $${data.usdt_balance.toFixed(2)}`);
    } catch(e) {
        alert("[ERROR] Error syncing balances: " + e.message);
    }
}
