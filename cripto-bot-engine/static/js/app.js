// ==========================================
// app.js - Core Dashboard Engine & WebSocket
// ==========================================

var lastStateData = null;
var lastPendingTradeState = false;

const urlParams = new URLSearchParams(window.location.search);
var authPin = urlParams.get("pin") || localStorage.getItem("cripto_auth_pin") || "";

function getHost() {
    return window.location.host ? window.location.host : "127.0.0.1:7344";
}

function getApiBase() {
    return window.location.protocol === "file:" ? "http://127.0.0.1:7344" : "";
}

function getWsUrl() {
    const host = getHost();
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    let url = window.location.protocol === "file:" ? "ws://127.0.0.1:7344/ws" : protocol + "//" + host + "/ws";
    if (authPin) {
        url += `?pin=${encodeURIComponent(authPin)}`;
    }
    return url;
}

async function submitPin(autoPin = null) {
    const pinVal = autoPin || document.getElementById("pin-input").value;
    if (!pinVal) return;
    authPin = pinVal;
    try { localStorage.setItem("cripto_auth_pin", authPin); } catch(e) {}
    
    try {
        const res = await fetch(getApiBase() + "/api/state", { headers: { "X-Auth-PIN": authPin } });
        if (res.ok) {
            document.getElementById("pin-modal").style.display = "none";
            document.getElementById("main-app").style.display = "block";
            initApp();
        } else {
            document.getElementById("pin-error").style.display = "block";
            try { localStorage.removeItem("cripto_auth_pin"); } catch(e) {}
            authPin = "";
        }
    } catch (e) {
        console.error("Login Error:", e);
        alert("Connection failed: " + e.message);
    }
}

function initApp() {
    if (document.getElementById("api-key").value === "") {
        document.getElementById("api-key").placeholder = "•••••••••••••••• (Saved)";
        document.getElementById("secret-key").placeholder = "•••••••••••••••• (Saved)";
    }

    // Connect WebSocket
    const wsUrl = getWsUrl();
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        document.getElementById("ws-status").style.color = "var(--green)";
        document.getElementById("ws-status").innerText = "[LIVE WS]";
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        updateUI(data);
    };

    ws.onclose = () => {
        document.getElementById("ws-status").style.color = "var(--red)";
        document.getElementById("ws-status").innerText = "[RECONNECTING...]";
        setTimeout(() => location.reload(), 3000);
    };
    
    fetchSymbols();
    fetchTrades();
}

function updateUI(data) {
    lastStateData = data;
    document.getElementById("mode-text").innerText = data.testnet ? "TESTNET" : "REAL";
    
    const statusEl = document.getElementById("bot-status");
    statusEl.innerText = data.is_active ? "ACTIVE" : "PAUSED";
    statusEl.className = "badge " + (data.is_active ? "badge-active" : "badge-paused");

    const dcaBadge = document.getElementById("dca-status-badge");
    if (dcaBadge && data.strategies) {
        const dcaOn = Boolean(data.strategies.dca_enabled);
        dcaBadge.innerText = dcaOn ? "DCA: ON" : "DCA: OFF";
        dcaBadge.className = "badge " + (dcaOn ? "badge-active" : "badge-paused");
    }
    
    let listHtml = "";
    for (const pair of data.favorite_pairs) {
        const price = data.prices[pair];
        const ind = (data.indicators && data.indicators[pair]) || { rsi: 50.0, pct_b: 0.5 };
        
        let rsiClass = "rsi-neutral";
        if (ind.rsi <= (data.strategies.rsi_threshold || 30)) rsiClass = "rsi-oversold";
        else if (ind.rsi >= 70) rsiClass = "rsi-overbought";

        const buyBtn = `<button class="btn btn-green" style="padding: 2px 8px; font-size: 0.72rem; margin-left: 8px; flex:0 0 auto; min-width:auto;" onclick="openManualBuyModal('${pair}', ${price})">BUY</button>`;

        listHtml += `
            <div class="price-item">
                <div style="display:flex; align-items:center;">
                    <span style="font-weight:bold;">${pair}</span>
                    <span class="rsi-pill ${rsiClass}" style="margin-left:10px;">RSI ${ind.rsi}</span>
                    <span class="bb-pill">%B ${ind.pct_b}</span>
                    ${buyBtn}
                </div>
                <strong>$${price ? price.toLocaleString() : "---"}</strong>
            </div>
        `;
    }
    document.getElementById("watchlist").innerHTML = listHtml || "<div>No favorites set.</div>";

    // Update Portfolio
    if (data.portfolio) {
        let totalValue = data.usdt_balance || 0;
        let portHtml = "";
        
        if (data.usdt_balance > 0) {
            portHtml += `<div class="price-item"><span>USDT</span><strong>$${data.usdt_balance.toFixed(2)}</strong></div>`;
        }

        let assets = [];
        for (const [asset, amount] of Object.entries(data.portfolio)) {
            if (asset === "USDT" || amount <= 0) continue;
            
            let price = data.prices[asset + "USDT"] || 0;
            let val = amount * price;
            assets.push({asset, amount, val, price});
            totalValue += val;
        }
        
        assets.sort((a, b) => b.val - a.val);
        
        assets.slice(0, 10).forEach(a => {
            const sellBtn = a.val >= 0.50 ? `<button class="btn btn-red" style="padding: 2px 8px; font-size: 0.72rem; margin-left: 8px;" onclick="openManualSellModal('${a.asset}', ${a.amount}, ${a.val}, ${a.price})">SELL</button>` : "";
            portHtml += `
                <div class="price-item" style="display:flex; justify-content:space-between; align-items:center;">
                    <span>${a.asset} <small>(${a.amount})</small></span>
                    <div style="display:flex; align-items:center;">
                        <strong>$${a.val.toFixed(2)}</strong>
                        ${sellBtn}
                    </div>
                </div>`;
        });
        
        if (assets.length > 10) {
            portHtml += `<div style="text-align:center; font-size: 0.8rem; margin-top:10px; color:var(--accent)">+ ${assets.length - 10} other assets</div>`;
        }

        let usdt = data.usdt_balance || 0;
        let assetsValue = totalValue - usdt;
        
        document.getElementById("total-portfolio-value").innerText = `$${totalValue.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2})}`;
        document.getElementById("assets-value").innerText = `$${assetsValue.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2})}`;
        document.getElementById("usdt-available").innerText = `$${usdt.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2})}`;
        document.getElementById("portfolio-list").innerHTML = portHtml || "<div>No assets found.</div>";
    }

    const tradeContainer = document.getElementById("trade-container");
    const hasPending = !!data.pending_trade;
    if (lastPendingTradeState && !hasPending) {
        fetchTrades();
    }
    lastPendingTradeState = hasPending;

    if (data.pending_trade) {
        const t = data.pending_trade;
        const existingBanner = document.getElementById("trade-banner-" + t.id);
        if (existingBanner) {
            const timerEl = document.getElementById("trade-timer-" + t.id);
            if (timerEl) timerEl.innerText = t.timeout_sec;
        } else {
            const proj = t.projection;
            let projBadge = "";
            if (proj && proj.summary_str) {
                const isProfit = proj.outcome_type === "PROFIT" || (proj.action === "BUY" && (proj.risk_reward_ratio || 0) >= 1.5);
                const isLoss = proj.outcome_type === "LOSS";
                const borderClr = isLoss ? "#ff5555" : (isProfit ? "#50fa7b" : "#f1fa8c");
                const label = proj.action === "SELL" ? "Projected Exit Return" : "Projected Trade Parameters";
                projBadge = `
                    <div style="background: rgba(0,0,0,0.06); border-left: 3px solid ${borderClr}; border-radius: 6px; padding: 8px 12px; margin-bottom: 12px; font-size: 0.88rem;">
                        <span style="font-weight: 700; font-size: 0.75rem; text-transform: uppercase; color: #555; letter-spacing: 0.5px;">${label}</span>
                        <div style="font-weight: 600; color: #111; margin-top: 2px;">${proj.summary_str}</div>
                    </div>`;
            }

            const aiCard = t.ai_verdict ? `
                <div style="background: rgba(0,0,0,0.06); border: 1px solid rgba(0,0,0,0.15); border-radius: 8px; padding: 10px; margin-bottom: 15px; font-size: 0.88rem;">
                    <div style="display: flex; gap: 15px; align-items: center; margin-bottom: 4px;">
                        <span style="font-weight: 800; color: ${t.ai_verdict === "APPROVE" ? "green" : "red"};">[AI VERDICT] ${t.ai_verdict}</span>
                        <span><strong>Risk Score:</strong> ${t.ai_risk}/10</span>
                        <span><strong>Suggested SL:</strong> ${t.ai_sl}%</span>
                    </div>
                    <div style="color: #222; font-style: italic;">"${t.ai_summary}"</div>
                </div>` : "";

            tradeContainer.innerHTML = `
                <div class="card" id="trade-banner-${t.id}" style="border-color: var(--accent); box-shadow: 0 0 15px rgba(255,121,198,0.2);">
                    <h3 style="color: var(--accent);">[ACTION REQUIRED] ${t.action} ${t.pair}</h3>
                    <p style="font-size: 1.1rem; margin: 10px 0;">
                        <strong>Reason:</strong> ${t.reason || t.strategy}<br>
                        <strong>Price:</strong> $${t.price.toFixed(4)}<br>
                        <strong>Amount:</strong> $<input type="number" id="override-trade-amount" value="${t.amount_usdt.toFixed(2)}" style="width: 80px; padding: 2px; font-weight: bold; background: #fff; color: #000; border: none; border-radius: 4px; text-align: center;"> USDT
                    </p>
                    ${projBadge}
                    ${aiCard}
                    <p style="font-weight: bold; color: var(--yellow);">Expires in <span id="trade-timer-${t.id}">${t.timeout_sec}</span>s...</p>
                    <div style="margin-top: 15px; display: flex; gap: 10px; justify-content: center;">
                        <button class="btn btn-green" onclick="decideTrade(true)">APPROVE</button>
                        <button class="btn btn-red" onclick="decideTrade(false)">REJECT</button>
                    </div>
                </div>
            `;
        }
    } else {
        tradeContainer.innerHTML = "";
    }

    if (document.getElementById("settings-modal").style.display !== "flex" && typeof populateSettingsInputs === "function") {
        populateSettingsInputs(data);
    }

    if (data.asset_performance) {
        renderAssetPerformance(data.asset_performance);
    }

    if (data.logs) {
        const term = document.getElementById("terminal-logs");
        if (term) {
            let html = "";
            data.logs.forEach(log => {
                let cls = "";
                if (log.includes("[ERROR]")) cls = "log-error";
                if (log.includes("[WARNING]")) cls = "log-warn";
                html += `<div class="${cls}">${log}</div>`;
            });
            
            const isScrolledToBottom = term.scrollHeight - term.clientHeight <= term.scrollTop + 20;
            term.innerHTML = html;
            if (isScrolledToBottom) {
                term.scrollTop = term.scrollHeight;
            }
        }
    }
}

function renderAssetPerformance(assetPerf) {
    if (!assetPerf) return;
    const tbody = document.getElementById("asset-performance-table");
    if (!tbody) return;

    const assets = assetPerf.assets || {};
    const glob = assetPerf.global || {};
    const badgeEl = document.getElementById("best-asset-badge");
    if (badgeEl && glob.best_performing_asset && glob.best_performing_asset !== "NONE") {
        badgeEl.innerText = `Top Alpha: ${glob.best_performing_asset}`;
    }

    const assetKeys = Object.keys(assets);
    if (assetKeys.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding: 10px; color: var(--comment);">No asset history recorded yet.</td></tr>`;
        return;
    }

    tbody.innerHTML = assetKeys.map(k => {
        const m = assets[k];
        const pnl = m.net_realized_pnl_usdt || 0.0;
        const pnlColor = pnl >= 0 ? "var(--green)" : "var(--red)";
        const statusClass = m.performance_status === "DRAWDOWN" ? "badge-paused" : (m.performance_status.includes("PROFIT") ? "badge-active" : "badge-neutral");
        const streakStr = (m.consecutive_losses >= 2) ? `<span style="color:var(--red); font-size:0.75rem;">(${m.consecutive_losses}L streak)</span>` : "";

        return `
            <tr style="border-bottom: 1px solid rgba(255,255,255,0.05);">
                <td style="padding: 6px; font-weight: bold;">${m.asset || k}</td>
                <td style="padding: 6px;">${m.closed_trades || 0}</td>
                <td style="padding: 6px;">${m.wins || 0}W / ${m.losses || 0}L</td>
                <td style="padding: 6px; font-weight: bold;">${m.win_rate || 0.0}%</td>
                <td style="padding: 6px; color: ${pnlColor}; font-weight: bold;">${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}</td>
                <td style="padding: 6px;">${m.current_cost_basis > 0 ? '$' + m.current_cost_basis.toFixed(2) : '---'}</td>
                <td style="padding: 6px;">${m.current_position_qty > 0 ? m.current_position_qty.toFixed(4) : '0.0'}</td>
                <td style="padding: 6px;"><span class="badge ${statusClass}" style="font-size:0.75rem; padding: 2px 6px;">${m.performance_status || 'NEUTRAL'}</span> ${streakStr}</td>
            </tr>
        `;
    }).join("");
}

async function fetchTrades() {
    try {
        const res = await fetch(getApiBase() + "/api/trades", { headers: { "X-Auth-PIN": authPin } });
        if (!res.ok) return;
        const data = await res.json();

        if (data.asset_performance) {
            renderAssetPerformance(data.asset_performance);
        }
        
        if (data.summary) {
            const pnl = data.summary.total_pnl_usdt || 0.0;
            const pnlEl = document.getElementById("pnl-total");
            if (pnlEl) {
                pnlEl.innerText = `${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}`;
                pnlEl.style.color = pnl >= 0 ? "var(--green)" : "var(--red)";
            }
            
            const winEl = document.getElementById("pnl-winrate");
            if (winEl) winEl.innerText = `${data.summary.win_rate || 0.0}% (${data.summary.wins}W / ${data.summary.losses}L)`;

            const closedEl = document.getElementById("pnl-closed");
            if (closedEl) closedEl.innerText = data.summary.closed_trades || 0;
        }

        const tbody = document.getElementById("trade-history-table");
        if (!tbody) return;

        const trades = (data.trades || []).slice().sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
        if (trades.length === 0) {
            tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding: 15px; color: var(--comment);">No trades recorded yet.</td></tr>`;
            return;
        }

        tbody.innerHTML = trades.map(t => {
            const d = new Date((t.timestamp || 0) * 1000);
            const dateStr = d.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" }) + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
            const isoStr = d.toISOString();
            const isBuy = t.action === "BUY";
            const sideClass = isBuy ? "badge-active" : "badge-paused";
            
            let pnlHtml = "---";
            if (t.action === "SELL" && t.status === "EXECUTED") {
                const pnlVal = t.realized_pnl_usdt || 0.0;
                const pnlPct = t.realized_pnl_percent || 0.0;
                const color = pnlVal >= 0 ? "var(--green)" : "var(--red)";
                pnlHtml = `<span style="color:${color}; font-weight:bold;">${pnlVal >= 0 ? "+" : ""}$${pnlVal.toFixed(2)} (${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(1)}%)</span>`;
            }

            return `
                <tr style="border-bottom: 1px solid rgba(255,255,255,0.05);">
                    <td style="padding: 8px; color: var(--comment);" title="${isoStr}">${dateStr}</td>
                    <td style="padding: 8px; font-weight: bold;">${t.pair}</td>
                    <td style="padding: 8px;"><span class="badge ${sideClass}" style="font-size:0.75rem; padding: 2px 6px;">${t.action}</span></td>
                    <td style="padding: 8px;">$${t.price ? t.price.toLocaleString() : "---"}</td>
                    <td style="padding: 8px;">$${t.amount_usdt.toFixed(2)}</td>
                    <td style="padding: 8px;">${pnlHtml}</td>
                    <td style="padding: 8px; font-size:0.8rem; color:${t.status === "EXECUTED" ? "var(--green)" : "var(--yellow)"};">${t.status}</td>
                </tr>
            `;
        }).join("");
    } catch(e) {
        console.error("Error fetching trades:", e);
    }
}

async function fetchSymbols() {
    try {
        const res = await fetch(getApiBase() + "/api/symbols", { headers: { "X-Auth-PIN": authPin } });
        const data = await res.json();
        const dl = document.getElementById("symbols-list");
        if (dl) dl.innerHTML = data.symbols.map(s => `<option value="${s}">`).join("");
    } catch(e) {}
}

async function fetchNewsInsights() {
    const el = document.getElementById("news-bullets-container");
    if (!el) return;
    try {
        const res = await fetch(getApiBase() + "/api/news", { headers: { "X-Auth-PIN": authPin } });
        const data = await res.json();
        const bullets = data.bullets || [];
        const cat = data.overall_catalyst || "NEUTRAL";
        const catColor = cat === "BULLISH" ? "var(--green)" : (cat === "BEARISH" ? "var(--red)" : "var(--yellow)");

        let html = `<div style="font-weight:bold; margin-bottom:8px; color:${catColor}; font-size:1.05rem;">Overall Catalyst: ${cat}</div>`;
        html += `<ul style="margin:0; padding-left:20px; line-height:1.6;">` + bullets.map(b => `<li>${b}</li>`).join("") + `</ul>`;
        el.innerHTML = html;
    } catch(e) {
        el.innerHTML = `<span style="color:var(--comment);">Could not load news feed.</span>`;
    }
}

async function runTestSuite() {
    try {
        alert("[TEST] Running full test suite on server... Click OK to execute.");
        const res = await fetch(getApiBase() + "/api/test/run", {
            method: "POST",
            headers: { "X-Auth-PIN": authPin }
        });
        const data = await res.json();
        if (data.status === "success") {
            alert(`[SUCCESS] TEST SUITE PASSED!\nExecuted ${data.passed} tests cleanly in ${data.duration}s!`);
        } else {
            alert(`[FAILED] TEST SUITE FAILED (Exit Code ${data.exit_code}):\n${data.log.slice(-300)}`);
        }
    } catch(e) {
        alert("[ERROR] Error running test suite: " + e.message);
    }
}

function autoLoginIfPin() {
    if (authPin) {
        submitPin(authPin);
    }
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoLoginIfPin);
} else {
    autoLoginIfPin();
}

setTimeout(fetchNewsInsights, 1500);
