import http.server
import socketserver
import json
import math
import random
import time
import threading
import os
from urllib.parse import urlparse, parse_qs

try:
    import yfinance as yf
except ImportError:
    yf = None

def fetch_historical_candles(ticker_symbol, period="5d", interval="1m"):
    if not yf:
        return []
    try:
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(period=period, interval=interval)
        if df.empty:
            return []
        
        candles = []
        # Determine if previous day boundary can be approximated or marked
        timestamps = df.index.astype(int) // 10**9
        opens = df["Open"].values
        highs = df["High"].values
        lows = df["Low"].values
        closes = df["Close"].values
        volumes = df["Volume"].values
        
        first_day = None
        for i in range(len(timestamps)):
            t = int(timestamps[i])
            o = float(opens[i])
            h = float(highs[i])
            l = float(lows[i])
            c = float(closes[i])
            v = int(volumes[i]) if not math.isnan(volumes[i]) else 500
            
            day_str = time.strftime("%Y-%m-%d", time.localtime(t))
            if first_day is None:
                first_day = day_str
            
            candles.append({
                "time": t,
                "is_prev_day": day_str != first_day,
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(l, 2),
                "close": round(c, 2),
                "volume": v
            })
        return candles
    except Exception:
        return []

def fetch_live_market_prices():
    if not yf:
        return None, None
    try:
        nifty_ticker = yf.Ticker("^NSEI")
        sensex_ticker = yf.Ticker("^BSESN")
        
        nifty_df = nifty_ticker.history(period="1d", interval="1m")
        sensex_df = sensex_ticker.history(period="1d", interval="1m")
        
        if not nifty_df.empty and not sensex_df.empty:
            live_nifty = float(nifty_df["Close"].iloc[-1])
            live_sensex = float(sensex_df["Close"].iloc[-1])
            return live_nifty, live_sensex
    except Exception:
        pass
    return None, None

def norm_pdf(x):
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def calc_black_scholes(spot, strike, dte_days=4.0, iv=0.14, r=0.06):
    if strike <= 0 or spot <= 0:
        return {
            "ce_ltp": 0.50, "pe_ltp": 0.50,
            "ce_delta": 0.0, "pe_delta": 0.0,
            "gamma": 0.0, "theta": 0.0
        }
    t = max(dte_days / 365.0, 0.0001)
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    
    nd1 = norm_cdf(d1)
    nd2 = norm_cdf(d2)
    n_neg_d1 = norm_cdf(-d1)
    n_neg_d2 = norm_cdf(-d2)
    pdf_d1 = norm_pdf(d1)
    
    ce = spot * nd1 - strike * math.exp(-r * t) * nd2
    pe = strike * math.exp(-r * t) * n_neg_d2 - spot * n_neg_d1
    
    return {
        "ce_ltp": max(round(ce, 2), 0.50),
        "pe_ltp": max(round(pe, 2), 0.50),
        "ce_delta": round(nd1, 3),
        "pe_delta": round(nd1 - 1.0, 3),
        "gamma": round(pdf_d1 / (spot * iv * sqrt_t), 5),
        "theta": round((- (spot * pdf_d1 * iv) / (2.0 * sqrt_t) - r * strike * math.exp(-r * t) * nd2) / 365.0, 2)
    }

def calc_ema_series(data, period):
    if not data:
        return []
    k = 2.0 / (period + 1)
    series = [data[0]]
    for p in data[1:]:
        series.append(round((p * k) + (series[-1] * (1.0 - k)), 2))
    return series

def merge_candle_chunk(chunk):
    if not chunk:
        return None
    return {
        "time": chunk[0]["time"],
        "is_prev_day": chunk[0].get("is_prev_day", False),
        "open": chunk[0]["open"],
        "high": max(c["high"] for c in chunk),
        "low": min(c["low"] for c in chunk),
        "close": chunk[-1]["close"],
        "volume": sum(c.get("volume", 0) for c in chunk)
    }

class SimulationState:
    def __init__(self):
        self.lock = threading.Lock()
        self.nifty_spot = 23850.00
        self.sensex_spot = 81450.00
        self.nifty_base = 23826.75
        self.sensex_base = 81400.00
        
        # Fetch real historical candles from Yahoo Finance for NSE and BSE
        self.candles_nifty = fetch_historical_candles("^NSEI", period="5d", interval="1m")
        self.candles_sensex = fetch_historical_candles("^BSESN", period="5d", interval="1m")
        
        if not self.candles_nifty:
            self.candles_nifty = self._generate_fallback_candles(self.nifty_spot)
        if not self.candles_sensex:
            self.candles_sensex = self._generate_fallback_candles(self.sensex_spot)

        if self.candles_nifty:
            self.nifty_spot = self.candles_nifty[-1]["close"]
            self.nifty_base = self.candles_nifty[0]["open"]
        if self.candles_sensex:
            self.sensex_spot = self.candles_sensex[-1]["close"]
            self.sensex_base = self.candles_sensex[0]["open"]

        self.wallet = {
            "initial": 50000.0, "balance": 50000.0,
            "used_margin": 0.0, "realized_pnl": 0.0
        }
        self.positions = []
        self.pending_orders = []
        self.orders = []
        self.closed_trades = []
        self.sound_events = []
        self.candle_start_time = time.time()

    def _generate_fallback_candles(self, start_p):
        now = time.time()
        candles = []
        cur = start_p - 120.0
        for i in range(150):
            o = cur
            c = o + random.uniform(-4, 4.5)
            h = max(o, c) + random.uniform(0.5, 3)
            l = min(o, c) - random.uniform(0.5, 3)
            candles.append({
                "time": int(now - (150 - i) * 60),
                "is_prev_day": i < 75,
                "open": round(o, 2), "high": round(h, 2),
                "low": round(l, 2), "close": round(c, 2),
                "volume": random.randint(1500, 6000)
            })
            cur = c
        return candles

    def update_tick(self):
        with self.lock:
            live_nifty, live_sensex = fetch_live_market_prices()
            if live_nifty and live_sensex:
                self.nifty_spot = round(live_nifty, 2)
                self.sensex_spot = round(live_sensex, 2)
            else:
                step = random.gauss(random.choice([-1.0, 0, 1.0]) * 0.35, 1.5)
                self.nifty_spot = round(self.nifty_spot + step, 2)
                self.sensex_spot = round(self.sensex_spot + step * 3.5, 2)

            now = time.time()
            # Append live ticks to active history buffers
            for candles_list, spot_val in [(self.candles_nifty, self.nifty_spot), (self.candles_sensex, self.sensex_spot)]:
                if candles_list:
                    last_c = candles_list[-1]
                    if now - last_c["time"] >= 60:
                        candles_list.append({
                            "time": int(now),
                            "is_prev_day": False,
                            "open": spot_val, "high": spot_val,
                            "low": spot_val, "close": spot_val,
                            "volume": random.randint(200, 600)
                        })
                        if len(candles_list) > 500:
                            candles_list.pop(0)
                    else:
                        last_c["high"] = max(last_c["high"], spot_val)
                        last_c["low"] = min(last_c["low"], spot_val)
                        last_c["close"] = spot_val
                        last_c["volume"] += random.randint(15, 60)

            # Limit Order Evaluation
            triggered = []
            for i, pord in enumerate(self.pending_orders):
                sym = pord["symbol"]
                curr_spot = self.sensex_spot if "SENSEX" in sym else self.nifty_spot
                if pord.get("type") in ["CE", "PE"] and pord.get("strike", 0) > 0:
                    g = calc_black_scholes(curr_spot, pord["strike"], iv=0.13 if "SENSEX" in sym else 0.14)
                    cur_p = g["ce_ltp"] if pord["type"] == "CE" else g["pe_ltp"]
                else:
                    cur_p = curr_spot

                if pord["action"] == "BUY" and cur_p <= pord["limit_price"]:
                    triggered.append(i)
                elif pord["action"] == "SELL" and cur_p >= pord["limit_price"]:
                    triggered.append(i)

            for idx in reversed(triggered):
                pord = self.pending_orders.pop(idx)
                sym = pord["symbol"]
                curr_spot = self.sensex_spot if "SENSEX" in sym else self.nifty_spot
                if pord.get("type") in ["CE", "PE"] and pord.get("strike", 0) > 0:
                    g = calc_black_scholes(curr_spot, pord["strike"], iv=0.13 if "SENSEX" in sym else 0.14)
                    exec_p = g["ce_ltp"] if pord["type"] == "CE" else g["pe_ltp"]
                else:
                    exec_p = curr_spot

                existing = None
                for pos in self.positions:
                    if pos["symbol"] == pord["symbol"] and pos["action"] == pord["action"]:
                        existing = pos
                        break

                if existing:
                    tot_qty = existing["qty"] + pord["qty"]
                    weighted_price = round(((existing["buy_price"] * existing["qty"]) + (exec_p * pord["qty"])) / tot_qty, 2)
                    existing["qty"] = tot_qty
                    existing["buy_price"] = weighted_price
                    existing["peak_price"] = max(existing.get("peak_price", weighted_price), exec_p)
                    existing["margin"] = round(existing.get("margin", 0.0) + pord["margin"], 2)
                else:
                    pos_id = f"POS_{int(time.time()*1000)}"
                    self.positions.append({
                        "id": pos_id, "symbol": pord["symbol"], "action": pord["action"],
                        "type": pord["type"], "strike": pord["strike"], "qty": pord["qty"],
                        "buy_price": exec_p, "peak_price": exec_p, "ltp": exec_p,
                        "margin": pord["margin"], "stop_loss": pord["stop_loss"],
                        "target": pord["target"], "trailing_sl": pord.get("trailing_sl", 0.0), "pnl": 0.0
                    })

                self.orders.insert(0, {
                    "time": time.strftime("%H:%M:%S"), "symbol": pord["symbol"],
                    "action": f"{pord['action']} LIMIT TRIGGERED @ ₹{exec_p}",
                    "qty": pord["qty"], "price": exec_p, "status": "EXECUTED"
                })
                self.sound_events.append("LIMIT_TRIGGERED")

            # Position PnL Evaluation
            total_used_margin = 0.0
            auto_exits = []
            for pos in self.positions:
                sym = pos["symbol"]
                curr_spot = self.sensex_spot if "SENSEX" in sym else self.nifty_spot
                if pos.get("type") in ["CE", "PE"] and pos.get("strike", 0) > 0:
                    opt_data = calc_black_scholes(curr_spot, pos["strike"], iv=0.13 if "SENSEX" in sym else 0.14)
                    ltp = opt_data["ce_ltp"] if pos["type"] == "CE" else opt_data["pe_ltp"]
                    pos["delta"] = opt_data["ce_delta"] if pos["type"] == "CE" else opt_data["pe_delta"]
                    pos["theta"] = opt_data["theta"]
                    pos["gamma"] = opt_data["gamma"]
                else:
                    ltp = curr_spot
                    pos["delta"] = 1.0 if pos["action"] == "BUY" else -1.0
                    pos["theta"] = 0.0; pos["gamma"] = 0.0

                pos["ltp"] = ltp
                if pos["action"] == "BUY":
                    pos["pnl"] = round((ltp - pos["buy_price"]) * pos["qty"], 2)
                    if ltp > pos.get("peak_price", pos["buy_price"]):
                        pos["peak_price"] = ltp
                        tsl = pos.get("trailing_sl", 0.0)
                        if tsl > 0:
                            new_trail_sl = round(ltp - tsl, 2)
                            if new_trail_sl > pos.get("stop_loss", 0.0):
                                pos["stop_loss"] = new_trail_sl
                    if pos.get("stop_loss") and pos["stop_loss"] > 0 and ltp <= pos["stop_loss"]:
                        auto_exits.append((pos["id"], f"SL Hit @ ₹{ltp}", "SL_HIT"))
                    elif pos.get("target") and pos["target"] > 0 and ltp >= pos["target"]:
                        auto_exits.append((pos["id"], f"TP Hit @ ₹{ltp}", "TARGET_HIT"))
                else:
                    pos["pnl"] = round((pos["buy_price"] - ltp) * pos["qty"], 2)
                    if ltp < pos.get("peak_price", pos["buy_price"]):
                        pos["peak_price"] = ltp
                        tsl = pos.get("trailing_sl", 0.0)
                        if tsl > 0:
                            new_trail_sl = round(ltp + tsl, 2)
                            if pos.get("stop_loss", 0.0) == 0.0 or new_trail_sl < pos["stop_loss"]:
                                pos["stop_loss"] = new_trail_sl
                    if pos.get("stop_loss") and pos["stop_loss"] > 0 and ltp >= pos["stop_loss"]:
                        auto_exits.append((pos["id"], f"SL Hit @ ₹{ltp}", "SL_HIT"))
                    elif pos.get("target") and pos["target"] > 0 and ltp <= pos["target"]:
                        auto_exits.append((pos["id"], f"TP Hit @ ₹{ltp}", "TARGET_HIT"))

                total_used_margin += pos.get("margin", 0.0)
            self.wallet["used_margin"] = total_used_margin
            for pid, reason, snd in auto_exits:
                self._internal_exit(pid, exit_reason=reason, sound_type=snd)

    def _internal_exit(self, pos_id, exit_reason="Exit", sound_type=None):
        for i, pos in enumerate(self.positions):
            if pos["id"] == pos_id:
                pnl = pos["pnl"]
                margin_released = pos.get("margin", 0.0)
                self.wallet["balance"] += (margin_released + pnl)
                self.wallet["realized_pnl"] = round(self.wallet["realized_pnl"] + pnl, 2)
                
                trade_record = {
                    "id": f"TRD_{int(time.time()*1000)}", "time": time.strftime("%H:%M:%S"),
                    "symbol": pos["symbol"], "action": pos["action"], "qty": pos["qty"],
                    "entry_price": pos["buy_price"], "exit_price": pos["ltp"],
                    "pnl": pnl, "reason": exit_reason
                }
                self.closed_trades.insert(0, trade_record)
                self.orders.insert(0, {
                    "time": trade_record["time"], "symbol": pos["symbol"],
                    "action": f"EXIT - {exit_reason}", "qty": pos["qty"],
                    "price": pos["ltp"], "status": "EXECUTED"
                })
                if sound_type:
                    self.sound_events.append(sound_type)
                self.positions.pop(i)
                break

    def get_analytics(self):
        trades = self.closed_trades
        total = len(trades)
        if total == 0:
            return {"total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "net_pnl": 0.0}
        wins = [t["pnl"] for t in trades if t["pnl"] > 0]
        losses = [abs(t["pnl"]) for t in trades if t["pnl"] < 0]
        gp = sum(wins); gl = sum(losses)
        return {
            "total_trades": total,
            "win_rate": round((len(wins) / total) * 100, 1),
            "profit_factor": round(gp / gl, 2) if gl > 0 else (gp if gp > 0 else 1.0),
            "net_pnl": round(self.wallet["realized_pnl"], 2)
        }

    def get_instrument_chart_data(self, symbol, timeframe="1m"):
        is_sensex = "SENSEX" in symbol
        raw_candles = self.candles_sensex if is_sensex else self.candles_nifty
        curr_spot = self.sensex_spot if is_sensex else self.nifty_spot

        if symbol in ["NIFTY", "SENSEX"] or not "_" in symbol:
            display_title = "SENSEX" if is_sensex else "NIFTY 50"
            ltp = curr_spot
            greeks = {"delta": 1.0, "gamma": 0.0, "theta": 0.0}
        else:
            parts = symbol.split("_")
            strike = float(parts[1])
            opt_type = parts[2]
            display_title = f"{'SENSEX' if is_sensex else 'NIFTY'} {int(strike)} {opt_type}"
            iv_val = 0.13 if is_sensex else 0.14
            
            # Map option candles derived from actual index candles
            processed_candles = []
            for sc in raw_candles:
                base_spot = sc["close"]
                bs_o = calc_black_scholes(sc["open"], strike, iv=iv_val)[f"{opt_type.lower()}_ltp"]
                bs_c = calc_black_scholes(base_spot, strike, iv=iv_val)[f"{opt_type.lower()}_ltp"]
                bs_h_pt = calc_black_scholes(sc["high"], strike, iv=iv_val)[f"{opt_type.lower()}_ltp"]
                bs_l_pt = calc_black_scholes(sc["low"], strike, iv=iv_val)[f"{opt_type.lower()}_ltp"]

                if opt_type == "CE":
                    c_high = max(bs_o, bs_c, bs_h_pt)
                    c_low = max(0.50, min(bs_o, bs_c, bs_l_pt))
                else:
                    c_high = max(bs_o, bs_c, bs_l_pt)
                    c_low = max(0.50, min(bs_o, bs_c, bs_h_pt))

                processed_candles.append({
                    "time": sc["time"], "is_prev_day": sc["is_prev_day"],
                    "open": bs_o, "high": c_high, "low": c_low,
                    "close": bs_c, "volume": sc["volume"]
                })
            raw_candles = processed_candles
            opt_cur = calc_black_scholes(curr_spot, strike, iv=iv_val)
            ltp = opt_cur["ce_ltp"] if opt_type == "CE" else opt_cur["pe_ltp"]
            greeks = {
                "delta": opt_cur["ce_delta"] if opt_type == "CE" else opt_cur["pe_delta"],
                "gamma": opt_cur["gamma"], "theta": opt_cur["theta"]
            }

        tf_mins = {"1m": 1, "3m": 3, "5m": 5, "15m": 15}.get(timeframe, 1)
        if tf_mins > 1:
            candles = []
            chunk = []
            for c in raw_candles:
                chunk.append(c)
                if len(chunk) == tf_mins:
                    candles.append(merge_candle_chunk(chunk))
                    chunk = []
            if chunk:
                candles.append(merge_candle_chunk(chunk))
        else:
            candles = raw_candles

        closes = [c["close"] for c in candles]
        volumes = [c["volume"] for c in candles]
        ema9_s = calc_ema_series(closes, 9)
        ema15_s = calc_ema_series(closes, 15)
        cum_pv = sum(closes[i] * volumes[i] for i in range(len(closes)))
        cum_v = sum(volumes)
        vwap = round(cum_pv / cum_v, 2) if cum_v > 0 else closes[-1] if closes else 0

        period_sec = tf_mins * 60
        seconds_remaining = period_sec - (int(time.time()) % period_sec)
        mins = seconds_remaining // 60
        secs = seconds_remaining % 60

        return {
            "symbol": symbol, "display_title": display_title, "timeframe": timeframe,
            "ltp": ltp, "candles": candles, "countdown": f"{mins:02d}:{secs:02d}",
            "ema9": ema9_s[-1] if ema9_s else 0, "ema15": ema15_s[-1] if ema15_s else 0,
            "vwap": vwap, "ema9_series": ema9_s, "ema15_series": ema15_s, "greeks": greeks
        }

    def get_option_chain(self, symbol="NIFTY"):
        is_sensex = "SENSEX" in symbol
        curr_spot = self.sensex_spot if is_sensex else self.nifty_spot
        step_val = 100 if is_sensex else 50
        atm = round(curr_spot / step_val) * step_val
        iv_val = 0.13 if is_sensex else 0.14
        strikes = [atm + (i * step_val) for i in range(-8, 9)]
        chain = []
        for s in strikes:
            g = calc_black_scholes(curr_spot, s, iv=iv_val)
            chain.append({
                "strike": s, "ce_ltp": g["ce_ltp"], "ce_delta": g["ce_delta"],
                "ce_oi": f"{random.randint(15, 65)}L", "pe_ltp": g["pe_ltp"],
                "pe_delta": g["pe_delta"], "pe_oi": f"{random.randint(18, 70)}L",
                "gamma": g["gamma"], "theta": g["theta"]
            })
        return chain

state = SimulationState()

def background_market_ticker():
    while True:
        state.update_tick()
        time.sleep(1.0)

threading.Thread(target=background_market_ticker, daemon=True).start()

class DhanSimHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _safe_send_response(self, code=200, content_type="application/json"):
        try:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False

    def _safe_write(self, data: bytes):
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            if parsed.path in ["/", "/index.html"]:
                if os.path.exists("index.html"):
                    with open("index.html", "rb") as f:
                        body = f.read()
                    if self._safe_send_response(200, "text/html; charset=utf-8"):
                        self._safe_write(body)
                else:
                    if self._safe_send_response(404, "text/plain"):
                        self._safe_write(b"index.html missing")
            elif parsed.path == "/api/market":
                params = parse_qs(parsed.query)
                symbol = params.get("symbol", ["NIFTY"])[0]
                tf = params.get("tf", ["1m"])[0]
                with state.lock:
                    chart_data = state.get_instrument_chart_data(symbol, timeframe=tf)
                    chain = state.get_option_chain(symbol)
                    sounds = list(state.sound_events)
                    state.sound_events.clear()
                    analytics = state.get_analytics()
                    unrealized = round(sum(p["pnl"] for p in state.positions), 2)
                    resp = {
                        "nifty_spot": state.nifty_spot, "sensex_spot": state.sensex_spot,
                        "nifty_chg": round(state.nifty_spot - state.nifty_base, 2),
                        "nifty_pct": round(((state.nifty_spot - state.nifty_base) / state.nifty_base) * 100, 2),
                        "sensex_chg": round(state.sensex_spot - state.sensex_base, 2),
                        "sensex_pct": round(((state.sensex_spot - state.sensex_base) / state.sensex_base) * 100, 2),
                        "chart": chart_data, "chain": chain, "sounds": sounds,
                        "analytics": analytics,
                        "wallet": {
                            **state.wallet, "unrealized_pnl": unrealized,
                            "net_pnl": round(state.wallet["realized_pnl"] + unrealized, 2)
                        },
                        "positions": state.positions, "pending_orders": state.pending_orders,
                        "orders": state.orders, "closed_trades": state.closed_trades[:15]
                    }
                if self._safe_send_response(200):
                    self._safe_write(json.dumps(resp).encode("utf-8"))
            else:
                if self._safe_send_response(404, "text/plain"):
                    self._safe_write(b"Not Found")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}

            if parsed.path == "/api/order":
                symbol = body.get("symbol")
                action = body.get("action", "BUY")
                opt_type = body.get("type", "INDEX")
                strike = float(body.get("strike", 0))
                is_sensex = "SENSEX" in symbol
                default_qty = 10 if is_sensex else 65
                qty = max(default_qty, int(body.get("qty", default_qty)))
                order_type = body.get("order_type", "MARKET")
                limit_price = float(body.get("limit_price", 0))
                stop_loss = float(body.get("stop_loss", 0))
                target = float(body.get("target", 0))
                trailing_sl = float(body.get("trailing_sl", 0))
                
                curr_spot = state.sensex_spot if is_sensex else state.nifty_spot

                with state.lock:
                    opposite_pos = None
                    for p in state.positions:
                        if p["symbol"] == symbol and p["action"] != action:
                            opposite_pos = p
                            break

                    if opposite_pos and order_type == "MARKET":
                        state._internal_exit(opposite_pos["id"], exit_reason=f"Position Exited via {action}")
                        state.sound_events.append("ORDER_PLACED")
                        if self._safe_send_response(200):
                            self._safe_write(json.dumps({"status": "CLOSED_EXISTING"}).encode("utf-8"))
                        return

                    if opt_type in ["CE", "PE"] and strike > 0:
                        greeks = calc_black_scholes(curr_spot, strike, iv=0.13 if is_sensex else 0.14)
                        current_p = greeks["ce_ltp"] if opt_type == "CE" else greeks["pe_ltp"]
                        margin_per_unit = current_p if action == "BUY" else (200.0 if is_sensex else 150.0)
                    else:
                        current_p = curr_spot
                        margin_per_unit = current_p * 0.02

                    exec_price = limit_price if (order_type == "LIMIT" and limit_price > 0) else current_p
                    total_margin = round(margin_per_unit * qty, 2)

                    if state.wallet["balance"] < total_margin:
                        if self._safe_send_response(400):
                            self._safe_write(json.dumps({"error": f"Required margin ₹{total_margin:.2f} exceeds balance ₹{state.wallet['balance']:.2f}"}).encode("utf-8"))
                        return

                    state.wallet["balance"] -= total_margin

                    is_limit_pending = False
                    if order_type == "LIMIT" and limit_price > 0:
                        if action == "BUY" and limit_price < current_p:
                            is_limit_pending = True
                        elif action == "SELL" and limit_price > current_p:
                            is_limit_pending = True

                    if is_limit_pending:
                        ord_id = f"LMT_{int(time.time()*1000)}"
                        state.pending_orders.append({
                            "id": ord_id, "symbol": symbol, "action": action,
                            "type": opt_type, "strike": strike, "qty": qty,
                            "margin": total_margin, "limit_price": limit_price,
                            "stop_loss": stop_loss, "target": target, "trailing_sl": trailing_sl
                        })
                        state.orders.insert(0, {
                            "time": time.strftime("%H:%M:%S"), "symbol": symbol,
                            "action": f"{action} LIMIT PENDING @ ₹{limit_price}",
                            "qty": qty, "price": limit_price, "status": "PENDING"
                        })
                    else:
                        existing_pos = None
                        for p in state.positions:
                            if p["symbol"] == symbol and p["action"] == action:
                                existing_pos = p
                                break

                        if existing_pos:
                            tot_qty = existing_pos["qty"] + qty
                            weighted_price = round(((existing_pos["buy_price"] * existing_pos["qty"]) + (exec_price * qty)) / tot_qty, 2)
                            existing_pos["qty"] = tot_qty
                            existing_pos["buy_price"] = weighted_price
                            existing_pos["peak_price"] = max(existing_pos.get("peak_price", weighted_price), exec_price)
                            existing_pos["margin"] = round(existing_pos.get("margin", 0.0) + total_margin, 2)
                        else:
                            pos_id = f"POS_{int(time.time()*1000)}"
                            state.positions.append({
                                "id": pos_id, "symbol": symbol, "action": action,
                                "type": opt_type, "strike": strike, "qty": qty,
                                "margin": total_margin, "buy_price": exec_price,
                                "peak_price": exec_price, "ltp": current_p,
                                "stop_loss": stop_loss, "target": target, "trailing_sl": trailing_sl,
                                "pnl": 0.0
                            })

                        state.orders.insert(0, {
                            "time": time.strftime("%H:%M:%S"), "symbol": symbol,
                            "action": f"{action} MARKET @ ₹{current_p}",
                            "qty": qty, "price": current_p, "status": "EXECUTED"
                        })

                    state.sound_events.append("ORDER_PLACED")

                if self._safe_send_response(200):
                    self._safe_write(json.dumps({"status": "SUCCESS"}).encode("utf-8"))

            elif parsed.path == "/api/cancel_order":
                ord_id = body.get("id")
                with state.lock:
                    for i, po in enumerate(state.pending_orders):
                        if po["id"] == ord_id:
                            state.wallet["balance"] += po.get("margin", 0)
                            state.pending_orders.pop(i)
                            break
                if self._safe_send_response(200):
                    self._safe_write(json.dumps({"status": "CANCELLED"}).encode("utf-8"))

            elif parsed.path == "/api/update_brackets":
                pos_id = body.get("id")
                sl = float(body.get("stop_loss", 0))
                tp = float(body.get("target", 0))
                tsl = float(body.get("trailing_sl", 0))
                with state.lock:
                    for pos in state.positions:
                        if pos["id"] == pos_id:
                            pos["stop_loss"] = sl; pos["target"] = tp; pos["trailing_sl"] = tsl
                            break
                if self._safe_send_response(200):
                    self._safe_write(json.dumps({"status": "UPDATED"}).encode("utf-8"))

            elif parsed.path == "/api/exit":
                pos_id = body.get("id")
                with state.lock:
                    state._internal_exit(pos_id, exit_reason="Manual Square Off")
                if self._safe_send_response(200):
                    self._safe_write(json.dumps({"status": "EXITED"}).encode("utf-8"))

            elif parsed.path == "/api/reset":
                with state.lock:
                    state.wallet = {"initial": 50000.0, "balance": 50000.0, "used_margin": 0.0, "realized_pnl": 0.0}
                    state.positions = []
                    state.pending_orders = []
                    state.orders = []
                    state.closed_trades = []
                    state.sound_events = []
                if self._safe_send_response(200):
                    self._safe_write(json.dumps({"status": "RESET"}.encode("utf-8")))
        except (BrokenPipeError, ConnectionResetError):
            pass

class ThreadedHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass

if __name__ == "__main__":
    import os
    PORT = int(os.environ.get("PORT", 8000))
    with ThreadedHTTPServer(("0.0.0.0", PORT), DhanSimHandler) as httpd:
        print(f"Server running on port {PORT}")
        httpd.serve_forever()
