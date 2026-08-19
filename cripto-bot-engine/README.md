# Cripto-3DS Bot Engine (`cripto-bot-engine`)

A lightweight, high-performance Binance trading bot and Nintendo 3DS real-time telemetry server built with **Python**, **FastAPI**, and **AsyncIO**.

---

## 🚀 Quick Start (Native `uv` Workflow)

No `pip` or virtualenv activation needed! `uv` handles project sync, dependencies, and execution automatically.

### 1. Run the Bot Daemon & Web UI
```bash
cd cripto-bot-engine
uv run main.py
```
- **Web UI Dashboard**: [`http://localhost:7344`](http://localhost:7344)
- **3DS Socket Telemetry**: Listening on `0.0.0.0:7343`

### 2. Managing Dependencies
```bash
# Add main project dependency
uv add <package-name>

# Add dev dependency (e.g. testing/linting)
uv add --group dev pytest pytest-asyncio httpx
```

### 3. Run Automated Tests
```bash
uv run --group dev pytest
```

---

## ⚙️ Environment Configuration

The engine reads credentials from environment files in the following priority order:
1. `testnet.env` (Binance Testnet - Default for safe paper trading)
2. `config.env` / `binance.env` (Live Production Binance Account)

### `testnet.env` Example
```env
BINANCE_TESTNET=true
BINANCE_API_KEY=your_testnet_api_key
BINANCE_SECRET_KEY=your_testnet_secret_key
SERVER_3DS_PORT=7343
WEB_PORT=7344
FAVORITE_PAIRS=BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT
```

---

## 📡 Ports & Network Protocols

| Port | Protocol | Usage | Description |
|---|---|---|---|
| **7344** | HTTP / WS | Web UI & REST API | Serves interactive web config, live balance, and trade decision buttons. |
| **7343** | TCP JSON | 3DS Telemetry | Emits rotating crypto price stream & receives touch commands (`APPROVE`, `REJECT`, `EMERGENCY_STOP`). |

---

## 🛡️ Safety Features & Timeout Watchdog

1. **Human Trade Approval**: Strategy signals generate a pending trade proposal.
2. **10-Minute Auto-Cancel Watchdog**: If a trade proposal is not approved within **600 seconds** (10 minutes) via 3DS or Web UI, it automatically cancels.
3. **Emergency Kill Switch**: Instantly pauses bot execution and cancels active orders via 3DS touch button or Web UI.
4. **Binance CEX Only**: Operates strictly within Binance account balances—no connection to personal external wallets.

---

## 🐧 Installing as a Linux Systemd Service

To keep `cripto-bot-engine` running in the background on system boot:

```bash
chmod +x install_service.sh
sudo ./install_service.sh
```

### Control & View Logs
```bash
# Check status
sudo systemctl status cripto-bot

# View live logs
journalctl -u cripto-bot -f
```
