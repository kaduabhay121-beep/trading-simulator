import http.server
import socketserver
import json
import math
import random
import time
import threading
import os
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs

try:
    import yfinance as yf
except ImportError:
    yf = None

IST = timezone(timedelta(hours=5, minutes=30))

def is_indian_market_open():
    now = datetime.now(IST)
    if now.weekday() >= 5: # Weekend
        return False, False
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    trade_window_end = now.replace(hour=14, minute=30, second=0, microsecond=0)
    trade_window_start = now.replace(hour=9, minute=20, second=0, microsecond=0)
    
    is_open = market_open <= now <= market_close
    is_in_trade_window = trade_window_start <= now <= trade_window_end
    return is_open, is_in_trade_window

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
            "ce_ltp": 0.50, "pe_ltp": 0.50, "ce_delta": 0.0, "pe_delta": 0.0, "gamma": 0.0, "theta": 0.0
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

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    if avg_loss == 0:
        return 100.0
        
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
    if avg_loss == 0:
        return 100.0
        
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)

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
        self.banknifty_spot = 54820.00
        self.sensex_spot = 81450.00
        self.nifty_base = 23826.75
        self.sensex_base = 81400.00
        self.wallet = {
            "initial": 50000.0,
            "balance": 50000.0,
            "used_margin": 0.0,
            "realized_pnl": 0.0
        }
        self.positions = []
        self.pending_orders = []
        self.orders = []
        self.closed_trades = []
        self.sound_events = []
        self.candles_1m = []
        self.current_1m_candle = None
        self.candle_start_time = time.time()
        self._init_history()

    def _init_history(self):
        now = time.time()
        cur = self.nifty_spot - 120.0
        for i in range(300):
            o = cur
            c = o + random.uniform(-4, 4.5)
            h = max(o, c) + random.uniform(0.5, 3)
            l = min(o, c) - random.uniform(0.5, 3)
            v = random.randint(1500, 6000)
            self.candles_1m.append({
                "time": int(now - (300 - i) * 60),
                "is_prev_day": i < 150,
                "open": round(o, 2), "high": round(h, 2), "low": round(l, 2), "close": round(c, 2), "volume": v
            })
            cur = c

        self.current_1m_candle = {
            "time": int(now), "is_prev_day": False, "open": round(cur, 2), "high": round(cur, 2), "low": round(cur, 2), "close": round(cur, 2), "volume": 800
        }

    def evaluate_strategy(self):
        if len(self.candles_1m) < 40:
            return
            
        # Aggregate 1m into 5m candles for strategy evaluation
        candles_5m = []
        chunk = []
        for c in self.candles_1m + [self.current_1m_candle]:
            chunk.append(c)
            if len(chunk) == 5:
                candles_5m.append(merge_candle_chunk(chunk))
                chunk = []
                
        if len(candles_5m) < 25:
            return
            
        closes = [c["close"] for c in candles_5m]
        vols = [c["volume"] for c in candles_5m]
        
        rsi = calc_rsi(closes, 14)
        ema9 = calc_ema_series(closes, 9)
        ema15 = calc_ema_series(closes, 15)
        avg_vol = sum(vols[-20:]) / 20.0
        
        atm_strike = round(self.nifty_spot / 50.0) * 50
        
        # Check if already in a position
        if len(self.positions) > 0 or len(self.pending_orders) > 0:
            return
            
        # Strategy Checklist Logic
        # CALL Setup
        if (ema9[-1] > ema15[-1] and closes[-1] > ema9[-1] and closes[-1] > ema15[-1] and rsi > 55 and vols[-1] > avg_vol and closes[-1] > closes[-2]):
            strike = atm_strike
            g = calc_black_scholes(self.nifty_spot, strike)
            entry_p = g["ce_ltp"]
            sl_p = round(entry_p * 0.80, 2) # 20% Max Risk SL rule
            tp_p = round(entry_p + (entry_p - sl_p) * 2, 2) # 1:2 RR Target
            
            margin = round(entry_p * 65, 2)
            if self.wallet["balance"] >= margin:
                self.wallet["balance"] -= margin
                self.positions.append({
                    "id": f"POS_{int(time.time()*1000)}",
                    "symbol": f"NIFTY {int(strike)} CE",
                    "action": "BUY", "type": "CE", "strike": strike, "qty": 65,
                    "buy_price": entry_p, "peak_price": entry_p, "ltp": entry_p,
                    "margin": margin, "stop_loss": sl_p, "target": tp_p, "trailing_sl": 0.0, "pnl": 0.0
                })
                self.orders.insert(0, {"time": time.strftime("%H:%M:%S"), "symbol": f"NIFTY {int(strike)} CE", "action": "AUTO BUY CE (Strategy)", "qty": 65, "price": entry_p, "status": "EXECUTED"})
                self.sound_events.append("ORDER_PLACED")

        # PUT Setup
        elif (ema9[-1] < ema15[-1] and closes[-1] < ema9[-1] and closes[-1] < ema15[-1] and rsi < 45 and vols[-1] > avg_vol and closes[-1] < closes[-2]):
            strike = atm_strike
            g = calc_black_scholes(self.nifty_spot, strike)
            entry_p = g["pe_ltp"]
            sl_p = round(entry_p * 0.80, 2)
            tp_p = round(entry_p + (entry_p - sl_p) * 2, 2)
            
            margin = round(entry_p * 65, 2)
            if self.wallet["balance"] >= margin:
                self.wallet["balance"] -= margin
                self.positions.append({
                    "id": f"POS_{int(time.time()*1000)}",
                    "symbol": f"NIFTY {int(strike)} PE",
                    "action": "BUY", "type": "PE", "strike": strike, "qty": 65,
                    "buy_price": entry_p, "peak_price": entry_p, "ltp": entry_p,
                    "margin": margin, "stop_loss": sl_p, "target": tp_p, "trailing_sl": 0.0, "pnl": 0.0
                })
                self.orders.insert(0, {"time": time.strftime("%H:%M:%S"), "symbol": f"NIFTY {int(strike)} PE", "action": "AUTO BUY PE (Strategy)", "qty": 65, "price": entry_p, "status": "EXECUTED"})
                self.sound_events.append("ORDER_PLACED")

    def update_tick(self):
        with self.lock:
            is_open, in_trade_window = is_indian_market_open()
            
            if is_open:
                live_nifty, live_sensex = fetch_live_market_prices()
                if live_nifty and live_sensex:
                    self.nifty_spot = round(live_nifty, 2)
                    self.sensex_spot = round(live_sensex, 2)
            else:
                # Outside market hours or weekend: freeze prices
                pass

            now = time.time()
            if now - self.candle_start_time >= 60 and is_open:
                self.candles_1m.append(self.current_1m_candle)
                if len(self.candles_1m) > 400:
                    self.candles_1m.pop(0)
                self.candle_start_time = now
                self.current_1m_candle = {
                    "time": int(now), "is_prev_day": False,
                    "open": self.nifty_spot, "high": self.nifty_spot, "low": self.nifty_spot, "close": self.nifty_spot,
                    "volume": random.randint(200, 600)
                }
                # Run strategy check upon every new 1m candle closure when inside the 9:20 - 2:30 window
                if in_trade_window:
                    self.evaluate_strategy()
            else:
                c = self.current_1m_candle
                c["high"] = max(c["high"], self.nifty_spot)
                c["low"] = min(c["low"], self.nifty_spot)
                c["close"] = self.nifty_spot

            # Position PnL, SL, and TP Evaluation
            total_used_margin = 0.0
            auto_exits = []

            for pos in self.positions:
                sym = pos["symbol"]
                curr_spot = self.sensex_spot if "SENSEX" in sym else self.nifty_spot
                if pos.get("type") in ["CE", "PE"] and pos.get("strike", 0) > 0:
                    opt_data = calc_black_scholes(curr_spot, pos["strike"])
                    ltp = opt_data["ce_ltp"] if pos["type"] == "CE" else opt_data["pe_ltp"]
                else:
                    ltp = curr_spot

                pos["ltp"] = ltp
                pos["pnl"] = round((ltp - pos["buy_price"]) * pos["qty"], 2)

                if pos.get("stop_loss") and pos["stop_loss"] > 0 and ltp <= pos["stop_loss"]:
                    auto_exits.append((pos["id"], f"SL Hit @ ₹{ltp}", "SL_HIT"))
                elif pos.get("target") and pos["target"] > 0 and ltp >= pos["target"]:
                    auto_exits.append((pos["id"], f"Target Hit @ ₹{ltp}", "TARGET_HIT"))

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
                
                self.closed_trades.insert(0, {
                    "id": f"TRD_{int(time.time()*1000)}", "time": time.strftime("%H:%M:%S"),
                    "symbol": pos["symbol"], "action": pos["action"], "qty": pos["qty"],
                    "entry_price": pos["buy_price"], "exit_price": pos["ltp"], "pnl": pnl, "reason": exit_reason
                })
                self.orders.insert(0, {
                    "time": time.strftime("%H:%M:%S"), "symbol": pos["symbol"],
                    "action": f"EXIT - {exit_reason}", "qty": pos["qty"], "price": pos["ltp"], "status": "EXECUTED"
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
        gp = sum(wins)
        gl = sum(losses)
        return {
            "total_trades": total,
            "win_rate": round((len(wins) / total) * 100, 1),
            "profit_factor": round(gp / gl, 2) if gl > 0 else (gp if gp > 0 else 1.0),
            "net_pnl": round(self.wallet["realized_pnl"], 2)
        }

    def get_instrument_chart_data(self, symbol, timeframe="1m"):
        all_1m = self.candles_1m + [self.current_1m_candle]
        is_sensex = "SENSEX" in symbol
        curr_spot = self.sensex_spot if is_sensex else self.nifty_spot

        if symbol in ["NIFTY", "SENSEX"] or not "_" in symbol:
            raw_candles = [{
                "time": sc["time"], "is_prev_day": sc["is_prev_day"],
                "open": sc["open"], "high": sc["high"], "low": sc["low"], "close": sc["close"], "volume": sc["volume"]
            } for sc in all_1m]
            ltp = curr_spot
            display_title = "SENSEX" if is_sensex else "NIFTY 50"
            greeks = {"delta": 1.0, "gamma": 0.0, "theta": 0.0}
        else:
            parts = symbol.split("_")
            strike = float(parts[1])
            opt_type = parts[2]
            display_title = f"{'SENSEX' if is_sensex else 'NIFTY'} {int(strike)} {opt_type}"
            raw_candles = []
            for sc in all_1m:
                bs_o = calc_black_scholes(sc["open"], strike)[f"{opt_type.lower()}_ltp"]
                bs_c = calc_black_scholes(sc["close"], strike)[f"{opt_type.lower()}_ltp"]
                raw_candles.append({
                    "time": sc["time"], "is_prev_day": sc["is_prev_day"],
                    "open": bs_o, "high": max(bs_o, bs_c), "low": min(bs_o, bs_c), "close": bs_c, "volume": sc["volume"]
                })
            opt_cur = calc_black_scholes(curr_spot, strike)
            ltp = opt_cur["ce_ltp"] if opt_type == "CE" else opt_cur["pe_ltp"]
            greeks = {"delta": opt_cur["ce_delta"] if opt_type == "CE" else opt_cur["pe_delta"], "gamma": opt_cur["gamma"], "theta": opt_cur["theta"]}

        tf_mins = {"1m": 1, "3m": 3, "5m": 5, "15m": 15}.get(timeframe, 1)
        if tf_mins > 1:
            candles, chunk = [], []
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
        vwap = round(sum(closes[i] * volumes[i] for i in range(len(closes))) / sum(volumes), 2) if sum(volumes) > 0 else closes[-1]

        return {
            "symbol": symbol, "display_title": display_title, "timeframe": timeframe,
            "ltp": ltp, "candles": candles, "countdown": "00:00",
            "ema9": ema9_s[-1] if ema9_s else 0, "ema15": ema15_s[-1] if ema15_s else 0,
            "vwap": vwap, "ema9_series": ema9_s, "ema15_series": ema15_s, "greeks": greeks
        }

    def get_option_chain(self, symbol="NIFTY"):
        is_sensex = "SENSEX" in symbol
        curr_spot = self.sensex_spot if is_sensex else self.nifty_spot
        step_val = 100 if is_sensex else 50
        atm = round(curr_spot / step_val) * step_val
        strikes = [atm + (i * step_val) for i in range(-8, 9)]
        chain = []
        for s in strikes:
            g = calc_black_scholes(curr_spot, s)
            chain.append({
                "strike": s, "ce_ltp": g["ce_ltp"], "ce_delta": g["ce_delta"], "ce_oi": f"{random.randint(15, 65)}L",
                "pe_ltp": g["pe_ltp"], "pe_delta": g["pe_delta"], "pe_oi": f"{random.randint(18, 70)}L",
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
        except:
            return False

    def _safe_write(self, data: bytes):
        try:
            self.wfile.write(data)
        except:
            pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ["/", "/index.html"]:
            if os.path.exists("index.html"):
                with open("index.html", "rb") as f:
                    body = f.read()
                if self._safe_send_response(200, "text/html; charset=utf-8"):
                    self._safe_write(body)
        elif parsed.path == "/api/market":
            params = parse_qs(parsed.query)
            symbol = params.get("symbol", ["NIFTY"])[0]
            tf = params.get("tf", ["1m"])[0]
            with state.lock:
                chart_data = state.get_instrument_chart_data(symbol, timeframe=tf)
                chain = state.get_option_chain(symbol)
                sounds = list(state.sound_events)
                state.sound_events.clear()
                resp = {
                    "nifty_spot": state.nifty_spot, "sensex_spot": state.sensex_spot,
                    "nifty_chg": round(state.nifty_spot - state.nifty_base, 2),
                    "nifty_pct": round(((state.nifty_spot - state.nifty_base) / state.nifty_base) * 100, 2),
                    "sensex_chg": round(state.sensex_spot - state.sensex_base, 2),
                    "sensex_pct": round(((state.sensex_spot - state.sensex_base) / state.sensex_base) * 100, 2),
                    "chart": chart_data, "chain": chain, "sounds": sounds,
                    "analytics": state.get_analytics(),
                    "wallet": {
                        **state.wallet,
                        "unrealized_pnl": round(sum(p["pnl"] for p in state.positions), 2),
                        "net_pnl": round(state.wallet["realized_pnl"] + sum(p["pnl"] for p in state.positions), 2)
                    },
                    "positions": state.positions, "pending_orders": state.pending_orders,
                    "orders": state.orders, "closed_trades": state.closed_trades[:15]
                }
            if self._safe_send_response(200):
                self._safe_write(json.dumps(resp).encode("utf-8"))

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
        if parsed.path == "/api/reset":
            with state.lock:
                state.wallet = {"initial": 50000.0, "balance": 50000.0, "used_margin": 0.0, "realized_pnl": 0.0}
                state.positions = []
                state.pending_orders = []
                state.orders = []
                state.closed_trades = []
                state.sound_events = []
            if self._safe_send_response(200):
                self._safe_write(json.dumps({"status": "RESET"}).encode("utf-8"))

class ThreadedHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8000))
    with ThreadedHTTPServer(("0.0.0.0", PORT), DhanSimHandler) as httpd:
        print(f"Server running on port {PORT}")
        httpd.serve_forever()
