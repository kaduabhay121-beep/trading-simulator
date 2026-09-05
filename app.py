#!/usr/bin/env python3
"""
PAPER TRADER V2
NIFTY + SENSEX + optional option-chain support
TERMUX / ANDROID

IMPORTANT:
- PAPER TRADING ONLY.
- NO Dhan.
- NO broker connection.
- NO real orders.
- Option prices are NEVER fabricated.
- Free Yahoo Finance data can be delayed/unofficial.
- Indian index option chains may be unavailable through Yahoo. If unavailable,
  the program displays OPTION DATA UNAVAILABLE and blocks option trades.

Install:
    pip install --upgrade yfinance pandas tzdata

Run:
    python game.py
"""

import os
import sys
import time
import math
import csv
from datetime import datetime, time as dtime
from pathlib import Path

try:
    import pandas as pd
    import yfinance as yf
except ImportError:
    print("Run: pip install --upgrade yfinance pandas tzdata")
    sys.exit(1)

# ========================= CONFIG =========================
STARTING_CAPITAL = 50_000.00
POLL_SECONDS = 20
INDEX_PERIOD = "5d"
INDEX_INTERVAL = "1m"

SYMBOLS = {
    "NIFTY": "^NSEI",
    "SENSEX": "^BSESN",
}

# These are only displayed as reference lot sizes. They are NOT used to
# fabricate option contracts or prices.
REFERENCE_LOT_SIZE = {
    "NIFTY": 65,
    "SENSEX": 20,
}

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)

DATA_DIR = Path.home() / "trading_simulator_data"
TRADE_FILE = DATA_DIR / "trades.csv"
CANDLE_FILE = DATA_DIR / "candles.csv"

# ========================= STATE =========================
capital = STARTING_CAPITAL
positions = {}       # key -> position dict
running = True
last_refresh = None

market = {
    "NIFTY": {"price": None, "prev_close": None, "candles": pd.DataFrame(), "timestamp": None},
    "SENSEX": {"price": None, "prev_close": None, "candles": pd.DataFrame(), "timestamp": None},
}

# option_chain[symbol] = {
#   "status": "...",
#   "expiry": "...",
#   "calls": dataframe,
#   "puts": dataframe,
#   "timestamp": datetime
# }
option_chain = {
    "NIFTY": {"status": "NOT LOADED", "expiry": None, "calls": pd.DataFrame(), "puts": pd.DataFrame(), "timestamp": None},
    "SENSEX": {"status": "NOT LOADED", "expiry": None, "calls": pd.DataFrame(), "puts": pd.DataFrame(), "timestamp": None},
}


# ========================= BASIC HELPERS =========================
def clear_screen():
    os.system("clear" if os.name != "nt" else "cls")


def now():
    return datetime.now()


def market_is_open():
    n = now()
    return n.weekday() < 5 and MARKET_OPEN <= n.time() <= MARKET_CLOSE


def money(x):
    try:
        if x is None or math.isnan(float(x)):
            return "--"
        return f"₹{float(x):,.2f}"
    except Exception:
        return "--"


def num(x, digits=2):
    try:
        if x is None or math.isnan(float(x)):
            return "--"
        return f"{float(x):,.{digits}f}"
    except Exception:
        return "--"


def ensure_files():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not TRADE_FILE.exists():
        with open(TRADE_FILE, "w", newline="") as f:
            csv.writer(f).writerow([
                "timestamp", "instrument", "side", "quantity", "entry_or_exit_price",
                "value", "realized_pnl", "note"
            ])

    if not CANDLE_FILE.exists():
        with open(CANDLE_FILE, "w", newline="") as f:
            csv.writer(f).writerow([
                "timestamp", "symbol", "open", "high", "low", "close",
                "volume", "vwap", "ema9", "ema21", "rsi"
            ])


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def add_indicators(df):
    if df is None or df.empty:
        return df

    out = df.copy()

    if "Volume" not in out.columns:
        out["Volume"] = 0

    typical = (out["High"] + out["Low"] + out["Close"]) / 3
    volume = out["Volume"].fillna(0)

    # Index feeds may have zero/missing volume. In that case VWAP is unavailable.
    cum_volume = volume.cumsum()
    cum_tpv = (typical * volume).cumsum()

    out["VWAP"] = cum_tpv / cum_volume.replace(0, float("nan"))
    out["EMA9"] = out["Close"].ewm(span=9, adjust=False).mean()
    out["EMA21"] = out["Close"].ewm(span=21, adjust=False).mean()
    out["RSI"] = rsi(out["Close"])

    return out


# ========================= INDEX DATA =========================
def fetch_index(symbol):
    ticker = SYMBOLS[symbol]

    try:
        df = yf.download(
            ticker,
            period=INDEX_PERIOD,
            interval=INDEX_INTERVAL,
            auto_adjust=False,
            progress=False,
            threads=False,
        )

        if df is None or df.empty:
            return None, "No data returned"

        if isinstance(df.columns, pd.MultiIndex):
            # Usually the first level contains Open/High/Low/Close/Volume.
            df.columns = df.columns.get_level_values(0)

        required = ["Open", "High", "Low", "Close"]
        if not all(c in df.columns for c in required):
            return None, "Unexpected Yahoo columns"

        if "Volume" not in df.columns:
            df["Volume"] = 0

        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.dropna(subset=["Open", "High", "Low", "Close"], inplace=True)

        if df.empty:
            return None, "No usable candles"

        # Convert timezone if Yahoo supplied one.
        try:
            if getattr(df.index, "tz", None) is not None:
                df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)
        except Exception:
            pass

        candles = df.resample("5min", label="left", closed="left").agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        })

        candles.dropna(subset=["Open", "High", "Low", "Close"], inplace=True)
        candles = add_indicators(candles)

        price = float(df["Close"].iloc[-1])
        stamp = df.index[-1]

        # Previous close is obtained separately when Yahoo exposes it.
        prev_close = None
        try:
            fi = yf.Ticker(ticker).fast_info
            pc = fi.get("previous_close")
            if pc is not None:
                prev_close = float(pc)
        except Exception:
            pass

        return {
            "price": price,
            "prev_close": prev_close,
            "candles": candles,
            "timestamp": stamp,
        }, None

    except Exception as e:
        return None, str(e)


def refresh_indices():
    global last_refresh

    errors = {}

    for symbol in SYMBOLS:
        data, error = fetch_index(symbol)

        if data is not None:
            market[symbol].update(data)
        else:
            errors[symbol] = error

    last_refresh = now()
    return errors


# ========================= OPTION DATA =========================
def clean_option_df(df):
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()

    wanted = [
        "contractSymbol", "strike", "lastPrice", "bid", "ask",
        "volume", "openInterest", "impliedVolatility"
    ]

    for col in wanted:
        if col not in out.columns:
            out[col] = None

    out = out[wanted].copy()

    for col in ["strike", "lastPrice", "bid", "ask", "volume", "openInterest", "impliedVolatility"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out.dropna(subset=["strike"], inplace=True)
    return out


def fetch_option_chain(symbol):
    """
    Try Yahoo's option interface using the index ticker.

    Yahoo does not necessarily list Indian index options for ^NSEI/^BSESN.
    If unavailable, return a clear unavailable state. Never synthesize premiums.
    """
    ticker = SYMBOLS[symbol]

    try:
        t = yf.Ticker(ticker)
        expirations = list(t.options)

        if not expirations:
            return {
                "status": "OPTION DATA UNAVAILABLE FROM FREE SOURCE",
                "expiry": None,
                "calls": pd.DataFrame(),
                "puts": pd.DataFrame(),
                "timestamp": now(),
            }

        expiry = expirations[0]
        chain = t.option_chain(expiry)

        calls = clean_option_df(chain.calls)
        puts = clean_option_df(chain.puts)

        if calls.empty and puts.empty:
            return {
                "status": "OPTION DATA UNAVAILABLE FROM FREE SOURCE",
                "expiry": expiry,
                "calls": calls,
                "puts": puts,
                "timestamp": now(),
            }

        return {
            "status": "AVAILABLE",
            "expiry": expiry,
            "calls": calls,
            "puts": puts,
            "timestamp": now(),
        }

    except Exception as e:
        return {
            "status": "OPTION DATA UNAVAILABLE: " + str(e)[:90],
            "expiry": None,
            "calls": pd.DataFrame(),
            "puts": pd.DataFrame(),
            "timestamp": now(),
        }


def refresh_options(symbol):
    option_chain[symbol] = fetch_option_chain(symbol)


def nearest_strikes(symbol, side, count=9):
    data = option_chain[symbol]
    df = data["calls"] if side == "CE" else data["puts"]

    if df is None or df.empty:
        return pd.DataFrame()

    spot = market[symbol]["price"]
    if spot is None:
        return df.head(count)

    temp = df.copy()
    temp["distance"] = (temp["strike"] - spot).abs()
    return temp.sort_values("distance").head(count).sort_values("strike")


def display_option_chain(symbol):
    data = option_chain[symbol]

    print(f"\n{symbol} OPTION CHAIN")
    print("-" * 100)
    print("Status :", data["status"])
    print("Expiry :", data["expiry"] or "--")
    print("Fetched:", data["timestamp"] or "--")

    if data["status"] != "AVAILABLE":
        print("\nOPTION DATA IS NOT AVAILABLE FROM THE FREE DATA SOURCE.")
        print("No option premium will be invented and option trading is disabled.")
        return

    calls = nearest_strikes(symbol, "CE", 11)
    puts = nearest_strikes(symbol, "PE", 11)

    print("\n" + f"{'CALL LTP':>12}{'CALL OI':>12}{'STRIKE':>12}{'PUT LTP':>12}{'PUT OI':>12}")
    print("-" * 60)

    strikes = sorted(set(calls["strike"].tolist()) | set(puts["strike"].tolist()))

    for strike in strikes:
        c = calls[calls["strike"] == strike]
        p = puts[puts["strike"] == strike]

        c_ltp = c.iloc[0]["lastPrice"] if not c.empty else None
        c_oi = c.iloc[0]["openInterest"] if not c.empty else None
        p_ltp = p.iloc[0]["lastPrice"] if not p.empty else None
        p_oi = p.iloc[0]["openInterest"] if not p.empty else None

        print(
            f"{num(c_ltp,2):>12}"
            f"{num(c_oi,0):>12}"
            f"{num(strike,0):>12}"
            f"{num(p_ltp,2):>12}"
            f"{num(p_oi,0):>12}"
        )


def get_option_contract(symbol, side, strike):
    data = option_chain[symbol]

    if data["status"] != "AVAILABLE":
        return None

    df = data["calls"] if side == "CE" else data["puts"]

    if df is None or df.empty:
        return None

    matches = df[abs(df["strike"] - strike) < 0.0001]
    if matches.empty:
        return None

    return matches.iloc[0].to_dict()


# ========================= PAPER OPTION TRADING =========================
def option_key(symbol, side, strike, expiry):
    return f"{symbol}_{expiry}_{strike:.2f}_{side}"


def option_price(contract):
    """
    Use lastPrice only when it is a real, non-NaN value.
    We do not estimate or calculate a synthetic premium.
    """
    value = contract.get("lastPrice")

    try:
        value = float(value)
        if math.isnan(value) or value <= 0:
            return None
        return value
    except Exception:
        return None


def paper_buy_option(symbol, side, strike, quantity):
    global capital

    data = option_chain[symbol]

    if data["status"] != "AVAILABLE":
        print("Option data unavailable. Paper option trade blocked.")
        return

    contract = get_option_contract(symbol, side, strike)
    if contract is None:
        print("Strike not found in current option chain.")
        return

    price = option_price(contract)
    if price is None:
        print("Real LTP unavailable for this contract. Trade blocked.")
        return

    if quantity <= 0:
        print("Quantity must be positive.")
        return

    cost = quantity * price

    if cost > capital:
        print(f"Insufficient virtual cash. Need {money(cost)}, have {money(capital)}.")
        return

    expiry = data["expiry"]
    key = option_key(symbol, side, strike, expiry)

    if key in positions:
        p = positions[key]
        old_qty = p["quantity"]
        new_qty = old_qty + quantity
        p["avg_price"] = ((old_qty * p["avg_price"]) + (quantity * price)) / new_qty
        p["quantity"] = new_qty
        p["last_price"] = price
    else:
        positions[key] = {
            "kind": "OPTION",
            "underlying": symbol,
            "side": side,
            "strike": strike,
            "expiry": expiry,
            "quantity": quantity,
            "avg_price": price,
            "last_price": price,
            "stop_loss": None,
            "target": None,
        }

    capital -= cost
    log_trade(key, "BUY", quantity, price, 0.0, "PAPER OPTION")

    print(
        f"Paper BUY {symbol} {strike:.0f} {side} "
        f"x{quantity} @ {money(price)}"
    )


def paper_sell_option(key, quantity):
    global capital

    p = positions.get(key)

    if not p:
        print("Position not found.")
        return

    data = option_chain[p["underlying"]]

    if data["status"] != "AVAILABLE":
        print("Current option data unavailable. Exit blocked to avoid fake pricing.")
        return

    contract = get_option_contract(p["underlying"], p["side"], p["strike"])

    if contract is None:
        print("Current contract not available. Exit blocked.")
        return

    price = option_price(contract)

    if price is None:
        print("Real current LTP unavailable. Exit blocked.")
        return

    if quantity <= 0 or quantity > p["quantity"]:
        print("Invalid quantity.")
        return

    realized = (price - p["avg_price"]) * quantity
    capital += quantity * price

    p["quantity"] -= quantity
    p["last_price"] = price

    log_trade(key, "SELL", quantity, price, realized, "PAPER OPTION")

    if p["quantity"] == 0:
        del positions[key]

    print(
        f"Paper SELL {p['underlying']} {p['strike']:.0f} {p['side']} "
        f"x{quantity} @ {money(price)} | P&L {money(realized)}"
    )


def buy_option_menu():
    symbol = input("NIFTY or SENSEX: ").strip().upper()
    if symbol not in SYMBOLS:
        print("Invalid symbol.")
        return

    refresh_options(symbol)

    if option_chain[symbol]["status"] != "AVAILABLE":
        print("\nOPTION DATA UNAVAILABLE.")
        print("No trade will be created.")
        return

    display_option_chain(symbol)

    side = input("\nCE or PE: ").strip().upper()
    if side not in ("CE", "PE"):
        print("Invalid option type.")
        return

    try:
        strike = float(input("Strike: ").strip())
        qty = int(input("Quantity: ").strip())
    except ValueError:
        print("Invalid strike or quantity.")
        return

    paper_buy_option(symbol, side, strike, qty)


def sell_option_menu():
    opts = [(k, p) for k, p in positions.items() if p.get("kind") == "OPTION"]

    if not opts:
        print("No open option positions.")
        return

    print("\nOPEN OPTION POSITIONS")
    for i, (key, p) in enumerate(opts, 1):
        print(
            f"{i}. {p['underlying']} {p['strike']:.0f} {p['side']} "
            f"EXP {p['expiry']} QTY {p['quantity']} AVG {money(p['avg_price'])}"
        )

    try:
        choice = int(input("Select position: ").strip())
        qty = int(input("Quantity to sell: ").strip())
        key = opts[choice - 1][0]
    except (ValueError, IndexError):
        print("Invalid selection.")
        return

    paper_sell_option(key, qty)


# ========================= INDEX PAPER TRADING =========================
def paper_buy_index(symbol, quantity):
    global capital

    price = market[symbol]["price"]
    if price is None:
        print("No index price.")
        return

    if quantity <= 0:
        print("Quantity must be positive.")
        return

    cost = quantity * price
    if cost > capital:
        print(f"Insufficient virtual cash. Need {money(cost)}.")
        return

    key = symbol

    if key in positions:
        p = positions[key]
        old = p["quantity"]
        new = old + quantity
        p["avg_price"] = ((old * p["avg_price"]) + quantity * price) / new
        p["quantity"] = new
        p["last_price"] = price
    else:
        positions[key] = {
            "kind": "INDEX",
            "underlying": symbol,
            "quantity": quantity,
            "avg_price": price,
            "last_price": price,
            "stop_loss": None,
            "target": None,
        }

    capital -= cost
    log_trade(symbol, "BUY", quantity, price, 0.0, "PAPER INDEX")
    print(f"Paper BUY {symbol} x{quantity} @ {money(price)}")


def paper_sell_index(symbol, quantity):
    global capital

    p = positions.get(symbol)
    price = market[symbol]["price"]

    if not p or p.get("kind") != "INDEX":
        print("No index position.")
        return

    if price is None:
        print("No current price.")
        return

    if quantity <= 0 or quantity > p["quantity"]:
        print("Invalid quantity.")
        return

    realized = (price - p["avg_price"]) * quantity
    capital += quantity * price

    p["quantity"] -= quantity
    p["last_price"] = price

    log_trade(symbol, "SELL", quantity, price, realized, "PAPER INDEX")

    if p["quantity"] == 0:
        del positions[symbol]

    print(f"Paper SELL {symbol} x{quantity} @ {money(price)} | P&L {money(realized)}")


def buy_index_menu():
    symbol = input("NIFTY or SENSEX: ").strip().upper()
    if symbol not in SYMBOLS:
        print("Invalid symbol.")
        return

    print(f"Current price: {money(market[symbol]['price'])}")

    try:
        qty = int(input("Quantity: ").strip())
    except ValueError:
        print("Invalid quantity.")
        return

    paper_buy_index(symbol, qty)


def sell_index_menu():
    symbol = input("NIFTY or SENSEX: ").strip().upper()
    if symbol not in SYMBOLS:
        print("Invalid symbol.")
        return

    try:
        qty = int(input("Quantity: ").strip())
    except ValueError:
        print("Invalid quantity.")
        return

    paper_sell_index(symbol, qty)


# ========================= P&L / RISK =========================
def refresh_position_prices():
    # Index positions.
    for key, p in positions.items():
        if p.get("kind") == "INDEX":
            price = market[p["underlying"]]["price"]
            if price is not None:
                p["last_price"] = price

    # Option positions: refresh chain and use actual lastPrice only.
    option_symbols = set(
        p["underlying"] for p in positions.values()
        if p.get("kind") == "OPTION"
    )

    for symbol in option_symbols:
        refresh_options(symbol)

        for key, p in list(positions.items()):
            if p.get("kind") != "OPTION" or p["underlying"] != symbol:
                continue

            contract = get_option_contract(symbol, p["side"], p["strike"])
            if contract:
                price = option_price(contract)
                if price is not None:
                    p["last_price"] = price


def unrealized_pnl():
    total = 0.0

    for p in positions.values():
        total += (p["last_price"] - p["avg_price"]) * p["quantity"]

    return total


def equity():
    return capital + sum(p["last_price"] * p["quantity"] for p in positions.values())


def check_risk():
    # For a long paper position, trigger only when current real data exists.
    for key in list(positions.keys()):
        p = positions.get(key)
        if not p:
            continue

        price = p["last_price"]

        if p["stop_loss"] is not None and price <= p["stop_loss"]:
            if p["kind"] == "INDEX":
                paper_sell_index(p["underlying"], p["quantity"])
            else:
                paper_sell_option(key, p["quantity"])

        elif p["target"] is not None and price >= p["target"]:
            if p["kind"] == "INDEX":
                paper_sell_index(p["underlying"], p["quantity"])
            else:
                paper_sell_option(key, p["quantity"])


def set_risk():
    if not positions:
        print("No open positions.")
        return

    print("\nPOSITIONS")
    keys = list(positions.keys())

    for i, key in enumerate(keys, 1):
        p = positions[key]
        label = key
        if p["kind"] == "OPTION":
            label = f"{p['underlying']} {p['strike']:.0f} {p['side']} {p['expiry']}"
        print(f"{i}. {label}")

    try:
        idx = int(input("Select: ")) - 1
        p = positions[keys[idx]]

        sl = input("Stop-loss price (blank = none): ").strip()
        target = input("Target price (blank = none): ").strip()

        p["stop_loss"] = float(sl) if sl else None
        p["target"] = float(target) if target else None

        print("Risk levels saved.")
    except (ValueError, IndexError):
        print("Invalid input.")


# ========================= LOGGING =========================
def log_trade(instrument, side, quantity, price, realized_pnl, note):
    with open(TRADE_FILE, "a", newline="") as f:
        csv.writer(f).writerow([
            now().strftime("%Y-%m-%d %H:%M:%S"),
            instrument,
            side,
            quantity,
            f"{price:.4f}",
            f"{quantity * price:.2f}",
            f"{realized_pnl:.2f}",
            note,
        ])


# ========================= DISPLAY =========================
def latest(symbol, column):
    df = market[symbol]["candles"]

    if df is None or df.empty or column not in df.columns:
        return None

    try:
        return float(df[column].iloc[-1])
    except Exception:
        return None


def display_dashboard():
    clear_screen()

    print("=" * 100)
    print("                         LIVE MARKET PAPER TRADER V2")
    print("=" * 100)
    print("DATA : Yahoo Finance / yfinance (free; may be delayed/unofficial)")
    print("MODE : PAPER ONLY — NO BROKER — NO REAL ORDERS")
    print(f"TIME : {now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"MARKET: {'OPEN' if market_is_open() else 'CLOSED'}")
    print("=" * 100)

    print(
        f"{'INDEX':<10}"
        f"{'LTP':>14}"
        f"{'VWAP':>14}"
        f"{'EMA9':>14}"
        f"{'EMA21':>14}"
        f"{'RSI':>10}"
    )
    print("-" * 100)

    for symbol in SYMBOLS:
        print(
            f"{symbol:<10}"
            f"{num(market[symbol]['price']):>14}"
            f"{num(latest(symbol,'VWAP')):>14}"
            f"{num(latest(symbol,'EMA9')):>14}"
            f"{num(latest(symbol,'EMA21')):>14}"
            f"{num(latest(symbol,'RSI')):>10}"
        )

    print("-" * 100)
    print("OPTION DATA STATUS")
    for symbol in SYMBOLS:
        print(f"  {symbol:<8}: {option_chain[symbol]['status']}")

    print("=" * 100)
    print(f"Starting Capital : {money(STARTING_CAPITAL)}")
    print(f"Cash Available   : {money(capital)}")
    print(f"Unrealized P&L   : {money(unrealized_pnl())}")
    print(f"Virtual Equity   : {money(equity())}")

    print("\nOPEN POSITIONS")
    print("-" * 100)

    if not positions:
        print("No open positions.")
    else:
        for key, p in positions.items():
            pnl = (p["last_price"] - p["avg_price"]) * p["quantity"]

            if p["kind"] == "OPTION":
                label = f"{p['underlying']} {p['strike']:.0f} {p['side']} EXP {p['expiry']}"
            else:
                label = p["underlying"]

            print(
                f"{label:<34}"
                f"QTY={p['quantity']:<6}"
                f"AVG={money(p['avg_price']):<14}"
                f"LTP={money(p['last_price']):<14}"
                f"P&L={money(pnl):<14}"
                f"SL={num(p['stop_loss']):<10}"
                f"TGT={num(p['target']):<10}"
            )

    print("=" * 100)
    print("1 Index BUY   2 Index SELL   3 Option BUY   4 Option SELL")
    print("5 Positions   6 Option Chain   7 Refresh      8 Candles")
    print("9 Trades       A SL/Target     R Reset         0 Exit")
    print("=" * 100)


def show_positions():
    print("\nOPEN POSITIONS")
    if not positions:
        print("None.")
        return

    for key, p in positions.items():
        pnl = (p["last_price"] - p["avg_price"]) * p["quantity"]

        if p["kind"] == "OPTION":
            label = f"{p['underlying']} {p['strike']:.0f} {p['side']} EXP {p['expiry']}"
        else:
            label = p["underlying"]

        print(
            f"{label} | Qty {p['quantity']} | "
            f"Avg {money(p['avg_price'])} | LTP {money(p['last_price'])} | "
            f"P&L {money(pnl)}"
        )


def show_candles():
    symbol = input("NIFTY or SENSEX: ").strip().upper()

    if symbol not in SYMBOLS:
        print("Invalid symbol.")
        return

    df = market[symbol]["candles"]

    if df is None or df.empty:
        print("No candle data.")
        return

    print(f"\n{symbol} — LAST 20 FIVE-MINUTE CANDLES")
    print("-" * 110)
    print(
        f"{'TIME':<20}"
        f"{'OPEN':>12}"
        f"{'HIGH':>12}"
        f"{'LOW':>12}"
        f"{'CLOSE':>12}"
        f"{'VOLUME':>12}"
        f"{'VWAP':>12}"
    )
    print("-" * 110)

    for idx, row in df.tail(20).iterrows():
        print(
            f"{str(idx):<20}"
            f"{num(row['Open']):>12}"
            f"{num(row['High']):>12}"
            f"{num(row['Low']):>12}"
            f"{num(row['Close']):>12}"
            f"{num(row['Volume'],0):>12}"
            f"{num(row['VWAP']):>12}"
        )


def show_trades():
    try:
        df = pd.read_csv(TRADE_FILE)
        if df.empty:
            print("No trades.")
        else:
            print("\nLAST 30 PAPER TRADES")
            print(df.tail(30).to_string(index=False))
    except Exception as e:
        print("Trade log error:", e)


def reset_account():
    global capital, positions

    confirm = input("Type RESET to erase virtual account: ").strip()

    if confirm != "RESET":
        print("Cancelled.")
        return

    capital = STARTING_CAPITAL
    positions = {}

    if TRADE_FILE.exists():
        TRADE_FILE.unlink()

    if CANDLE_FILE.exists():
        CANDLE_FILE.unlink()

    ensure_files()
    print("Virtual account reset.")


# ========================= MAIN =========================
def main():
    global running

    ensure_files()

    print("Starting paper-trading engine...")
    print("Downloading NIFTY + SENSEX data...")

    errors = refresh_indices()

    for symbol, error in errors.items():
        print(symbol, ":", error)

    if not any(market[s]["price"] is not None for s in SYMBOLS):
        print("\nNo index data received.")
        print("Check internet connection.")
        return

    # Load option status once at startup. It may legitimately be unavailable.
    print("Checking free option-chain availability...")
    for symbol in SYMBOLS:
        refresh_options(symbol)

    while running:
        try:
            refresh_indices()
            refresh_position_prices()
            check_risk()

            display_dashboard()

            choice = input("Select: ").strip().upper()

            if choice == "1":
                buy_index_menu()
                input("\nPress Enter...")
            elif choice == "2":
                sell_index_menu()
                input("\nPress Enter...")
            elif choice == "3":
                buy_option_menu()
                input("\nPress Enter...")
            elif choice == "4":
                sell_option_menu()
                input("\nPress Enter...")
            elif choice == "5":
                show_positions()
                input("\nPress Enter...")
            elif choice == "6":
                symbol = input("NIFTY or SENSEX: ").strip().upper()
                if symbol in SYMBOLS:
                    refresh_options(symbol)
                    display_option_chain(symbol)
                else:
                    print("Invalid symbol.")
                input("\nPress Enter...")
            elif choice == "7":
                errors = refresh_indices()
                for symbol, error in errors.items():
                    print(symbol, ":", error)
                input("\nPress Enter...")
            elif choice == "8":
                show_candles()
                input("\nPress Enter...")
            elif choice == "9":
                show_trades()
                input("\nPress Enter...")
            elif choice == "A":
                set_risk()
                input("\nPress Enter...")
            elif choice == "R":
                reset_account()
                input("\nPress Enter...")
            elif choice == "0":
                running = False
            else:
                print("Invalid choice.")
                time.sleep(1)

        except KeyboardInterrupt:
            running = False
        except Exception as e:
            print("\nProgram error:", e)
            print("The simulator is still paper-only.")
            time.sleep(3600)

    print("\nPaper trader stopped.")


if __name__ == "__main__":
    main()
