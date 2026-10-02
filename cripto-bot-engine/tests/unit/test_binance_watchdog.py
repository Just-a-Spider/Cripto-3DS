import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from engine.binance_client import restart_binance_websocket
from engine.state import state
from engine.watchdogs import binance_connection_watchdog


@pytest.mark.asyncio
async def test_watchdog_detects_silence_and_reconnects():
    state.api_key = "test_key"
    state.is_reconnecting_ws = False
    state.favorite_pairs = ["BTCUSDT"]
    state.last_ws_message_time = time.time() - 150.0  # Silent for 150s (> 120s threshold)
    state.ws_connect_time = time.time() - 500.0

    dummy_task = asyncio.create_task(asyncio.sleep(100))
    state.ws_tasks = [dummy_task]

    restarted = False

    async def fake_restart():
        nonlocal restarted
        restarted = True

    try:
        with patch("engine.binance_client.restart_binance_websocket", side_effect=fake_restart):
            # Run one iteration of watchdog
            wd_task = asyncio.create_task(binance_connection_watchdog(initial_delay=0))
            # Fast-forward initial sleep
            await asyncio.sleep(0.05)
            # Give watchdog a brief moment to evaluate
            for _ in range(5):
                if restarted:
                    break
                await asyncio.sleep(0.05)
            wd_task.cancel()
            try:
                await wd_task
            except asyncio.CancelledError:
                pass

        assert restarted is True
    finally:
        dummy_task.cancel()
        state.ws_tasks = []


@pytest.mark.asyncio
async def test_watchdog_detects_crashed_task():
    state.api_key = "test_key"
    state.is_reconnecting_ws = False
    state.favorite_pairs = ["BTCUSDT"]
    state.last_ws_message_time = time.time()  # Fresh
    state.ws_connect_time = time.time()

    # Create a task that immediately finishes (done() == True)
    finished_task = asyncio.create_task(asyncio.sleep(0))
    await finished_task
    state.ws_tasks = [finished_task]

    restarted = False

    async def fake_restart():
        nonlocal restarted
        restarted = True

    with patch("engine.binance_client.restart_binance_websocket", side_effect=fake_restart):
        wd_task = asyncio.create_task(binance_connection_watchdog(initial_delay=0))
        for _ in range(5):
            if restarted:
                break
            await asyncio.sleep(0.05)
        wd_task.cancel()
        try:
            await wd_task
        except asyncio.CancelledError:
            pass

    state.ws_tasks = []
    assert restarted is True


@pytest.mark.asyncio
async def test_watchdog_proactive_rotation_20h():
    state.api_key = "test_key"
    state.is_reconnecting_ws = False
    state.favorite_pairs = ["BTCUSDT"]
    state.last_ws_message_time = time.time()
    state.ws_connect_time = time.time() - 75000.0  # > 72000s (20 hours)

    dummy_task = asyncio.create_task(asyncio.sleep(100))
    state.ws_tasks = [dummy_task]

    restarted = False

    async def fake_restart():
        nonlocal restarted
        restarted = True

    try:
        with patch("engine.binance_client.restart_binance_websocket", side_effect=fake_restart):
            wd_task = asyncio.create_task(binance_connection_watchdog(initial_delay=0))
            for _ in range(5):
                if restarted:
                    break
                await asyncio.sleep(0.05)
            wd_task.cancel()
            try:
                await wd_task
            except asyncio.CancelledError:
                pass

        assert restarted is True
    finally:
        dummy_task.cancel()
        state.ws_tasks = []


@pytest.mark.asyncio
async def test_restart_binance_websocket_concurrency_guard():
    state.is_reconnecting_ws = True
    # Should exit immediately without error when already reconnecting
    with patch("engine.binance_client.start_binance_websocket") as mock_start:
        await restart_binance_websocket()
        mock_start.assert_not_called()
    state.is_reconnecting_ws = False
