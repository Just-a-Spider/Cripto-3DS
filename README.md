# Cripto-3DS: Binance Crypto Bot & Nintendo 3DS Real-Time Monitor

An algorithmic cryptocurrency trading bot engine (Python / FastAPI) and Nintendo 3DS homebrew application (`Cripto-3DS`) with 24/7 low-power Android phone deployment, interactive Discord Gateway bot, and Google Gemini AI market intelligence.

> 📖 **Full System Architecture, Algorithmic Trading Math, and Hardware Setup**: See [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md).

---

## 🏗️ Quick Architecture Overview

- **`cripto-3ds/`**: Nintendo 3DS client application (`libctru` / Berkeley TCP sockets) for hardware-button trade confirmations and telemetry.
- **`cripto-bot-engine/`**: 24/7 asynchronous trading engine running on low-power Moto E20 Android Server via Termux.
  - **Port `7343`**: 3DS TCP Telemetry server.
  - **Port `7344`**: Glassmorphism Web Companion & REST/WebSocket broadcaster (`/ws`).
  - **Discord Bot**: Interactive action buttons (`[Approve]`/`[Reject]`), candlestick `/chart`, and `/ask` Gemini AI analyst.
  - **Google AI Studio**: Powered by `gemini-3.1-flash-lite` for live contextual quantitative risk assessments.

---

## 🚀 Quick Start (Local PC / Laptop)

```bash
cd cripto-bot-engine
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
uv run main.py
```

- Web Companion: `http://localhost:7344/web`
- Run Test Suite: `uv run pytest -v tests/test_engine.py` (17/17 tests passing)

---

## ⚠️ Disclaimer
This software is an experimental open-source tool created for educational and personal research purposes. Cryptocurrency trading involves substantial financial risk. The authors assume no liability for financial losses.

