#!/usr/bin/env python3
"""
setup_env.py - Headless configuration wizard for Cripto-3DS Engine.
Generates or updates .env with proper permissions (0600) for edge deployment (e.g. Termux on Moto E20).
"""

import argparse
import os
import sys
from pathlib import Path


def parse_args(args=None):
    parser = argparse.ArgumentParser(
        description="Configure Cripto-3DS .env file securely for edge and local deployments."
    )
    parser.add_argument("--discord-id", dest="discord_id", help="Operator Discord user ID(s), comma-separated")
    parser.add_argument("--pin", dest="pin", default=None, help="Auth PIN for encryption and web API access")
    parser.add_argument("--binance-key", dest="binance_key", default=None, help="Binance API Key")
    parser.add_argument("--binance-secret", dest="binance_secret", default=None, help="Binance Secret Key")
    parser.add_argument("--testnet", dest="testnet", choices=["true", "false", "True", "False"], default=None, help="Binance Testnet mode (true/false)")
    parser.add_argument("--discord-token", dest="discord_token", default=None, help="Discord Bot Token")
    parser.add_argument("--discord-channel", dest="discord_channel", default=None, help="Discord Channel ID")
    parser.add_argument("--ai-provider", dest="ai_provider", default="google", help="AI Provider (google, openai, anthropic, groq, deepseek, ollama, openrouter, custom)")
    parser.add_argument("--ai-model", dest="ai_model", default="gemini-3.1-flash", help="Primary AI Model")
    parser.add_argument("--ai-key", dest="ai_key", default=None, help="Primary AI Provider API Key")
    parser.add_argument("--ai-base-url", dest="ai_base_url", default="", help="Custom Base URL for Ollama / Custom endpoints")
    parser.add_argument("--ai-fallback-provider", dest="ai_fallback_provider", default="groq", help="Fallback AI Provider")
    parser.add_argument("--ai-fallback-model", dest="ai_fallback_model", default="llama-3.3-70b-versatile", help="Fallback AI Model")
    parser.add_argument("--ai-fallback-key", dest="ai_fallback_key", default=None, help="Fallback AI Provider API Key")
    parser.add_argument("--gemini-key", dest="gemini_key", default=None, help="Google Gemini API Key (legacy alias)")
    parser.add_argument("--gemini-model", dest="gemini_model", default=None, help="Gemini Model (legacy alias)")
    parser.add_argument("--groq-key", dest="groq_key", default=None, help="Groq Cloud API Key (legacy alias)")
    parser.add_argument("--env-path", dest="env_path", default=None, help="Custom destination path for .env file")
    parser.add_argument("--non-interactive", dest="non_interactive", action="store_true", help="Do not prompt interactively; use defaults or flags")
    return parser.parse_args(args)

def prompt_val(prompt_text, default=""):
    if default:
        res = input(f"{prompt_text} [{default}]: ").strip()
        return res if res else default
    return input(f"{prompt_text}: ").strip()

def setup_environment(cli_args=None):
    if cli_args is None:
        cli_args = parse_args()

    project_dir = Path(__file__).resolve().parent
    env_path = Path(cli_args.env_path) if cli_args.env_path else project_dir / ".env"

    existing_values = {}
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                existing_values[k.strip()] = v.strip()

    is_interactive = not cli_args.non_interactive and sys.stdin.isatty()

    # Discord user IDs
    if cli_args.discord_id is not None:
        discord_ids = cli_args.discord_id.strip()
    elif is_interactive:
        current = existing_values.get("ALLOWED_DISCORD_USER_IDS", existing_values.get("DISCORD_USER_ID", ""))
        discord_ids = prompt_val("Allowed Discord User ID(s) (comma-separated)", current)
    else:
        discord_ids = existing_values.get("ALLOWED_DISCORD_USER_IDS", existing_values.get("DISCORD_USER_ID", ""))

    # Auth PIN
    if cli_args.pin is not None:
        pin = cli_args.pin.strip()
    elif is_interactive:
        current = existing_values.get("AUTH_PIN", "1234")
        pin = prompt_val("Security PIN (encryption and companion UI)", current)
    else:
        pin = existing_values.get("AUTH_PIN", "1234")

    # Binance Key
    if cli_args.binance_key is not None:
        binance_key = cli_args.binance_key.strip()
    elif is_interactive:
        current = existing_values.get("BINANCE_API_KEY", "")
        binance_key = prompt_val("Binance API Key (optional, can save via UI)", current)
    else:
        binance_key = existing_values.get("BINANCE_API_KEY", "")

    # Binance Secret
    if cli_args.binance_secret is not None:
        binance_secret = cli_args.binance_secret.strip()
    elif is_interactive:
        current = existing_values.get("BINANCE_SECRET_KEY", "")
        binance_secret = prompt_val("Binance Secret Key (optional, can save via UI)", current)
    else:
        binance_secret = existing_values.get("BINANCE_SECRET_KEY", "")

    # Testnet
    if cli_args.testnet is not None:
        testnet = str(cli_args.testnet).lower()
    elif is_interactive:
        current = existing_values.get("BINANCE_TESTNET", "true")
        testnet = prompt_val("Testnet mode (true/false)", current).lower()
    else:
        testnet = existing_values.get("BINANCE_TESTNET", "true").lower()

    # Discord Bot Token
    if cli_args.discord_token is not None:
        discord_token = cli_args.discord_token.strip()
    elif is_interactive:
        current = existing_values.get("DISCORD_BOT_TOKEN", "")
        discord_token = prompt_val("Discord Bot Token (optional)", current)
    else:
        discord_token = existing_values.get("DISCORD_BOT_TOKEN", "")

    # Discord Channel ID
    if cli_args.discord_channel is not None:
        discord_channel = cli_args.discord_channel.strip()
    elif is_interactive:
        current = existing_values.get("DISCORD_CHANNEL_ID", "")
        discord_channel = prompt_val("Discord Channel ID (optional)", current)
    else:
        discord_channel = existing_values.get("DISCORD_CHANNEL_ID", "")

    # AI Provider & Models
    ai_provider = cli_args.ai_provider.strip() if cli_args.ai_provider else existing_values.get("AI_PROVIDER", "google")

    # Primary Key
    if cli_args.ai_key is not None:
        ai_key = cli_args.ai_key.strip()
    elif cli_args.gemini_key is not None and ai_provider == "google":
        ai_key = cli_args.gemini_key.strip()
    elif is_interactive:
        current = existing_values.get("AI_API_KEY", existing_values.get("GEMINI_API_KEY", ""))
        ai_key = prompt_val(f"AI Provider ({ai_provider}) API Key", current)
    else:
        ai_key = existing_values.get("AI_API_KEY", existing_values.get("GEMINI_API_KEY", ""))

    # Primary Model
    if cli_args.gemini_model is not None and ai_provider == "google":
        ai_model = cli_args.gemini_model.strip()
    elif cli_args.ai_model is not None:
        ai_model = cli_args.ai_model.strip()
    else:
        ai_model = existing_values.get("AI_MODEL", existing_values.get("GEMINI_MODEL", "gemini-3.1-flash"))

    ai_base_url = cli_args.ai_base_url.strip() if cli_args.ai_base_url is not None else existing_values.get("AI_BASE_URL", "")

    # Fallback Provider & Key
    ai_fallback_provider = cli_args.ai_fallback_provider.strip() if cli_args.ai_fallback_provider else existing_values.get("AI_FALLBACK_PROVIDER", "groq")
    ai_fallback_model = cli_args.ai_fallback_model.strip() if cli_args.ai_fallback_model else existing_values.get("AI_FALLBACK_MODEL", existing_values.get("GROQ_MODEL", "llama-3.3-70b-versatile"))
    if cli_args.ai_fallback_key is not None:
        ai_fallback_key = cli_args.ai_fallback_key.strip()
    elif cli_args.groq_key is not None:
        ai_fallback_key = cli_args.groq_key.strip()
    else:
        ai_fallback_key = existing_values.get("AI_FALLBACK_API_KEY", existing_values.get("GROQ_API_KEY", ""))

    # Legacy variables for compatibility
    gemini_key = ai_key if ai_provider == "google" else existing_values.get("GEMINI_API_KEY", "")
    gemini_model = ai_model if ai_provider == "google" else existing_values.get("GEMINI_MODEL", "gemini-3.1-flash")
    groq_key = ai_fallback_key if ai_fallback_provider == "groq" else existing_values.get("GROQ_API_KEY", "")
    groq_model = ai_fallback_model if ai_fallback_provider == "groq" else existing_values.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    enable_grounding = existing_values.get("ENABLE_SEARCH_GROUNDING", "false")

    server_port = existing_values.get("SERVER_3DS_PORT", "7343")
    web_port = existing_values.get("WEB_PORT", "7344")
    headless = existing_values.get("HEADLESS", "true")

    content = f"""# ==========================================
# Cripto-3DS Engine Configuration
# Managed securely by setup_env.py
# ==========================================

# --- Operator Authorization ---
ALLOWED_DISCORD_USER_IDS={discord_ids}

# --- Binance API Credentials ---
BINANCE_API_KEY={binance_key}
BINANCE_SECRET_KEY={binance_secret}
BINANCE_TESTNET={testnet}

# --- Discord Gateway Bot Integration ---
DISCORD_BOT_TOKEN={discord_token}
DISCORD_CHANNEL_ID={discord_channel}
DISCORD_WEBHOOK_URL={existing_values.get('DISCORD_WEBHOOK_URL', '')}

# --- Universal AI Provider (LangChain Engine) ---
AI_PROVIDER={ai_provider}
AI_MODEL={ai_model}
AI_API_KEY={ai_key}
AI_BASE_URL={ai_base_url}
AI_FALLBACK_PROVIDER={ai_fallback_provider}
AI_FALLBACK_MODEL={ai_fallback_model}
AI_FALLBACK_API_KEY={ai_fallback_key}

# --- Backward Compatible AI Variables ---
GEMINI_API_KEY={gemini_key}
GEMINI_MODEL={gemini_model}
ENABLE_SEARCH_GROUNDING={enable_grounding}
GROQ_API_KEY={groq_key}
GROQ_MODEL={groq_model}

# --- Server Ports & Security ---
AUTH_PIN={pin}
SERVER_3DS_PORT={server_port}
WEB_PORT={web_port}
HEADLESS={headless}

# --- Note: Trading Strategies & Risk Limits ---
# Quantitative trading settings (RSI, DCA, Stop Loss, Trailing Stops, Max Spend, etc.)
# are dynamically managed from the Web Companion UI (http://localhost:{web_port}/web)
# and safely persisted in the SQLite database (bot_data.db).
"""

    env_path.parent.mkdir(parents=True, exist_ok=True)
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(content)

    # Secure permissions: owner read/write only (0600)
    try:
        os.chmod(env_path, 0o600)
    except Exception as e:
        sys.stderr.write(f"Warning: Could not set 0600 permissions on {env_path}: {e}\\n")

    print(f"Configuration successfully written to: {env_path}")
    print("File permissions set to 0600 (owner read/write only).")
    if not discord_ids:
        print("NOTICE: No ALLOWED_DISCORD_USER_IDS specified. Slash commands and approval buttons will reject incoming requests until configured.")
    return env_path

if __name__ == "__main__":
    setup_environment()
