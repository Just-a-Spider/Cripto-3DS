# Cripto-3DS Bot Engine (`cripto-bot-engine`)

A lightweight, high-performance Binance trading bot daemon and Nintendo 3DS real-time telemetry server built with **Python 3.11+**, **FastAPI**, and **AsyncIO**.

---

## Quick Start (Native `uv` Workflow)

We use `uv` for dependency management and execution.

### 1. Environment Setup
```bash
cd cripto-bot-engine

# Copy template configuration
cp .env.example .env

# Or launch the interactive CLI setup wizard
uv run python setup_env.py
```

### 2. Run the Bot Daemon & Web UI
```bash
uv run main.py
```
- **Web UI Companion**: [http://localhost:7344/web](http://localhost:7344/web)
- **3DS Socket Telemetry**: Listening on `0.0.0.0:7343`

### 3. Run Automated Tests & Linting
```bash
# Run full test suite (85 unit and integration tests)
uv run pytest

# Check code formatting & linting
uv run ruff check .
```

---

## Environment Configuration

The engine reads credentials from `.env` in `cripto-bot-engine/`. See `.env.example` for full variable documentation.

### Core Variables
- `BINANCE_TESTNET`: `true` for paper trading, `false` for live trading.
- `BINANCE_API_KEY`: Binance account API key.
- `BINANCE_SECRET_KEY`: Binance account secret key.
- `AUTH_PIN`: 4-digit PIN for client-side encryption and sensitive API authorization.
- `SERVER_3DS_PORT`: Port for raw 3DS TCP telemetry (default: `7343`).
- `WEB_PORT`: Port for FastAPI REST API and WebSocket broadcaster (default: `7344`).
- `AI_PROVIDER`: Multi-provider LLM support (`google`, `groq`, `openai`, `anthropic`, `ollama`, `deepseek`).
- `AI_MODEL`: Primary quantitative risk model (default: `gemini-3.1-flash`).

---

## Ports & Network Protocols

| Port | Protocol | Usage | Description |
| :--- | :--- | :--- | :--- |
| **7344** | HTTP / WS | Web UI & REST API | Serves glassmorphism dashboard, live portfolio stream (`/ws`), and trade approvals. |
| **7343** | TCP JSON | 3DS Telemetry | Emits rotating crypto price stream & receives hardware commands (`APPROVE`, `REJECT`, `EMERGENCY_STOP`). |
| **8022** | SSH | Termux Remote Access | OpenSSH port for low-power Android phone deployments. |

---

## Safety Features & Watchdogs

1. **Human Trade Approval**: Algorithmic trade signals queue into pending state until explicitly confirmed via 3DS hardware button (`A`), Web Companion (`[Approve]`), or Discord (`[Approve]`).
2. **Auto-Cancel Watchdog**: If a pending proposal is not confirmed within 600 seconds (10 minutes), it cancels automatically.
3. **Emergency Kill Switch**: Instantly pauses bot execution and cancels active orders via 3DS (`Y` button) or Web UI.
4. **Binance CEX Isolation**: Operates strictly within spot account balances—no access to private keys or external on-chain wallets.

---

## Running as a Linux Systemd Service

To keep `cripto-bot-engine` running as a background service on a Linux server or desktop:

Create `/etc/systemd/system/cripto-bot.service`:
```ini
[Unit]
Description=Cripto-3DS Bot Engine Daemon
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/path/to/Cripto-3DS/cripto-bot-engine
ExecStart=/usr/local/bin/uv run main.py
Restart=always
RestartSec=5
Environment=HEADLESS=true

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cripto-bot
sudo systemctl status cripto-bot
```
