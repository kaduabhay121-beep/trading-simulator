import types

from app import SimulationState, _self_test


def test_offline_self_test_is_deterministic():
    first = _self_test()
    second = _self_test()
    assert first == second
    assert first["broker_calls"] == 0
    assert first["orders_sent"] == 0


def test_paper_buy_accounting_round_trip():
    state = SimulationState.__new__(SimulationState)
    state.wallet = {"initial": 1_000_000.0, "balance": 1_000_000.0, "used_margin": 0.0, "realized_pnl": 0.0}
    state.positions = []
    state.orders = []
    state.closed_trades = []
    state.order_counter = 100
    state.bot = {"daily_pnl": 0.0}

    state._get_live_instrument_ltp = types.MethodType(lambda self, symbol: (105.0, 0.0, 0.0), state)
    state._journal_paper_trade = types.MethodType(lambda self, trade: None, state)
    state._save_state = types.MethodType(lambda self: None, state)

    state._execute_fill("NIFTY", "BUY", 10, 100.0)
    assert state.wallet["balance"] == 999_000.0
    assert state.wallet["used_margin"] == 1_000.0
    state.positions[0]["ltp"] = 105.0
    state._internal_exit(state.positions[0]["id"], "TEST")

    assert state.wallet["balance"] == 1_000_050.0
    assert state.wallet["used_margin"] == 0.0
    assert state.wallet["realized_pnl"] == 50.0
    assert state.closed_trades[0]["pnl"] == 50.0


def test_paper_short_accounting_round_trip():
    state = SimulationState.__new__(SimulationState)
    state.wallet = {"initial": 1_000_000.0, "balance": 1_000_000.0, "used_margin": 0.0, "realized_pnl": 0.0}
    state.positions = []
    state.orders = []
    state.closed_trades = []
    state.order_counter = 200
    state.bot = {"daily_pnl": 0.0}

    state._get_live_instrument_ltp = types.MethodType(lambda self, symbol: (95.0, 0.0, 0.0), state)
    state._journal_paper_trade = types.MethodType(lambda self, trade: None, state)
    state._save_state = types.MethodType(lambda self: None, state)

    state._execute_fill("NIFTY", "SELL", 10, 100.0)
    assert state.wallet["balance"] == 1_001_000.0
    assert state.wallet["used_margin"] == 1_000.0
    state.positions[0]["ltp"] = 95.0
    state._internal_exit(state.positions[0]["id"], "TEST")

    assert state.wallet["balance"] == 1_000_050.0
    assert state.wallet["used_margin"] == 0.0
    assert state.wallet["realized_pnl"] == 50.0
