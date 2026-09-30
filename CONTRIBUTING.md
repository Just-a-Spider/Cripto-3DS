# Contributing to Cripto-3DS

Thank you for your interest in contributing to Cripto-3DS. This project consists of two core subprojects:
1. `cripto-bot-engine/`: Asynchronous Python trading daemon, FastAPI REST/WebSocket server, Discord gateway, and AI risk analyst.
2. `cripto-3ds/`: Native Nintendo 3DS C homebrew client using `libctru` and `citro2d`.

---

## Code of Conduct & Safety First

- **Financial Safety**: This software interacts with cryptocurrency trading accounts. Never commit real API keys, secret keys, Discord tokens, or personal wallet addresses.
- **Testnet Default**: Always test trading logic against Binance Testnet (`BINANCE_TESTNET=true`) or mocked fixtures before submitting changes.

---

## Development Environment Setup

### 1. Python Bot Engine (`cripto-bot-engine`)

We use [`uv`](https://github.com/astral-sh/uv) for fast, deterministic Python package management.

```bash
cd cripto-bot-engine

# Sync dependencies and create virtual environment
uv sync --all-groups

# Copy environment template
cp .env.example .env
# Or run the configuration wizard
uv run python setup_env.py
```

#### Running Tests
Always verify the full test suite passes before making changes:

```bash
cd cripto-bot-engine
uv run pytest
```

Run specific test modules:
```bash
# Unit tests
uv run pytest tests/unit/

# Integration tests
uv run pytest tests/integration/
```

#### Code Style & Linting
We use Ruff for linting and code formatting:

```bash
cd cripto-bot-engine

# Check for lint errors
uv run ruff check .

# Automatically apply safe fixes
uv run ruff check . --fix

# Format code
uv run ruff format .
```

---

### 2. Nintendo 3DS Homebrew (`cripto-3ds`)

Requires [devkitPro](https://devkitpro.org/) with the 3DS toolchain.

```bash
cd cripto-3ds

# Ensure DEVKITARM is in your path
export DEVKITPRO=/opt/devkitpro
export DEVKITARM=${DEVKITPRO}/devkitARM

# Build .3dsx and .cia binaries
make clean
make
```

---

## Pull Request Guidelines

1. **Create a Feature Branch**:
   ```bash
   git checkout -b feat/your-feature-name
   # or
   git checkout -b fix/issue-description
   ```
2. **Keep Changes Focused**: Avoid combining unrelated refactors with functional changes.
3. **Write Tests**: When adding new strategy indicators, API endpoints, or risk watchdogs, add corresponding unit or integration tests under `tests/unit/` or `tests/integration/`.
4. **Pass All Quality Checks**:
   - `uv run pytest` must pass 100%.
   - `uv run ruff check` must report 0 errors.
5. **Describe Your Changes**: Detail what problem was solved, what was tested, and how reviewers can reproduce the behavior.
