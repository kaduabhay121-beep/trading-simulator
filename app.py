import http.server
import socketserver
import json
import math
import random
import time
import threading
import os
from datetime import datetime, time as dtime, timedelta
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
        timestamps = df.index.astype(int) // 10**9
        opens = df["Open"].values
        highs = df["High"].values
        lows = df["Low"].values
        closes = df["Close"].values
        volumes = df["Volume"].values
        
        first_day = None
        for i in range(len(timestamps)):
            t = int(timestamps[i])
            dt = datetime.fromtimestamp(t)
            
            market_time = dt.time()
            if not (dtime(9, 0) <= market_time <= dtime(15, 40)):
                continue

            day_str = dt.strftime("%Y-%m-%d")
            if first_day is None:
                first_day = day_str
            
            candles.append({
                "time": t,
                "date_label": dt.strftime("%d/%m %H:%M"),
                "is_prev_day": day_str != first_day,
                "open": round(float(opens[i]), 2),
                "high": round(float(highs[i]), 2),
                "low": round(float(lows[i]), 2),
                "close": round(float(closes[i]), 2),
                "volume": int(volumes[i]) if not math.isnan(volumes[i]) else 500
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
            return float(nifty_df["Close"].iloc[-1]), float(sensex_df["Close"].iloc[-1])
    except Exception:
        pass
    return None, None

def norm_pdf(x):
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def get_current_expiry_dates():
    today = datetime.now().date()
    days_to_nifty = (3 - today.weekday() + 7) % 7
    if days_to_nifty == 0 and datetime.now().time() > dtime(15, 30):
        days_to_nifty = 7
    nifty_expiry = today + timedelta(days=days_to_nifty)

    days_to_sensex = (4 - today.weekday() + 7) % 7
    if days_to_sensex == 0 and datetime.now().time() > dtime(15, 30):
        days_to_sensex = 7
    sensex_expiry = today + timedelta(days=days_to_sensex)

    return nifty_expiry.strftime("%d %b %Y"), sensex_expiry.strftime("%d %b %Y")

def calc_black_scholes(spot, strike, dte_days=4.0, iv=0.14, r=0.06):
    if strike <= 0 or spot <= 0:
        return {"ce_ltp": 0.50, "pe_ltp": 0.50, "ce_delta": 0.0, "pe_delta": 0.0, "gamma": 0.0, "theta": 0.0}
    t = max(dte_days / 365.0, 0.0001)
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    nd1 = norm_cdf(d1)
    nd2 = norm_cdf(d2)
    ce = spot * nd1 - strike * math.exp(-r * t) * nd2
    pe = strike * math.exp(-r * t) * norm_cdf(-d2) - spot * norm_cdf(-d1)
    return {
        "ce_ltp": max(round(ce, 2), 0.50),
        "pe_ltp": max(round(pe, 2), 0.50),
        "ce_delta": round(nd1, 3),
        "pe_delta": round(nd1 - 1.0, 3),
        "gamma": round(norm_pdf(d1) / (spot * iv * sqrt_t), 5),
        "theta": round((- (spot * norm_pdf(d1) * iv) / (2.0 * sqrt_t) - r * strike * math.exp(-r * t) * nd2) / 365.0, 2)
    }

def calc_ema_series(data, period):
    if not data: return []
    k = 2.0 / (period + 1)
    series = [data[0]]
    for p in data[1:]:
        series.append(round((p * k) + (series[-1] * (1.0 - k)), 2))
    return series

def merge_candle_chunk(chunk):
    if not chunk: return None
    dt = datetime.fromtimestamp(chunk[0]["time"])
    return {
        "time": chunk[0]["time"],
        "date_label": dt.strftime("%d/%m %H:%M"),
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
        
        self.candles_nifty = fetch_historical_candles("^NSEI", period="5d", interval="1m")
        self.candles_sensex = fetch_historical_candles("^BSESN", period="5d", interval="1m")
        
        if not self.candles_nifty:
            self.candles_nifty = self._generate_fallback(self.nifty_spot)
        if not self.candles_sensex:
            self.candles_sensex = self._generate_fallback(self.sensex_spot)

        if self.candles_nifty:
            self.nifty_spot = self.candles_nifty[-1]["close"]
            self.nifty_base = self.candles_nifty[0]["open"]
        if self.candles_sensex:
            self.sensex_spot = self.candles_sensex[-1]["close"]
            self.sensex_base = self.candles_sensex[0]["open"]

        self.wallet = {"initial": 50000.0, "balance": 50000.0, "used_margin": 0.0, "realized_pnl": 0.0}
        self.positions = []
        self.pending_orders = []
        self.orders = []
        self.closed_trades = []
        self.sound_events = []

    def _generate_fallback(self, start_p):
        now = time.time()
        candles = []
        cur = start_p - 120.0
        for i in range(150):
            dt = datetime.fromtimestamp(now - (150 - i) * 60)
            o = cur
            c = o + random.uniform(-4, 4.5)
            h = max(o, c) + random.uniform(0.5, 3)
            l = min(o, c) - random.uniform(0.5, 3)
            candles.append({
                "time": int(dt.timestamp()),
                "date_label": dt.strftime("%d/%m %H:%M"),
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
            dt = datetime.fromtimestamp(now)
            for candles_list, spot_val in [(self.candles_nifty, self.nifty_spot), (self.candles_sensex, self.sensex_spot)]:
                if candles_list:
                    last_c = candles_list[-1]
                    if now - last_c["time"] >= 60:
                        candles_list.append({
                            "time": int(now),
                            "date_label": dt.strftime("%d/%m %H:%M"),
                            "is_prev_day": False,
                            "open": spot_val, "high": spot_val,
                            "low": spot_val, "close": spot_val,
                            "volume": random.randint(200, 600)
                        })
                        if len(candles_list) > 600: candles_list.pop(0)
                    else:
                        last_c["high"] = max(last_c["high"], spot_val)
                        last_c["low"] = min(last_c["low"], spot_val)
                        last_c["close"] = spot_val
                        last_c["volume"] += random.randint(15, 60)

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
                exec_p = pord["limit_price"]
                
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
            nifty_exp, sensex_exp = get_current_expiry_dates()
            exp_str = sensex_exp if is_sensex else nifty_exp
            display_title = f"{'SENSEX' if is_sensex else 'NIFTY'} {int(strike)} {opt_type} ({exp_str})"
            iv_val = 0.13 if is_sensex else 0.14
            
            processed_candles = []
            for sc in raw_candles:
                bs_o = calc_black_scholes(sc["open"], strike, iv=iv_val)[f"{opt_type.lower()}_ltp"]
                bs_c = calc_black_scholes(sc["close"], strike, iv=iv_val)[f"{opt_type.lower()}_ltp"]
                bs_h_pt = calc_black_scholes(sc["high"], strike, iv=iv_val)[f"{opt_type.lower()}_ltp"]
                bs_l_pt = calc_black_scholes(sc["low"], strike, iv=iv_val)[f"{opt_type.lower()}_ltp"]

                if opt_type == "CE":
                    c_high = max(bs_o, bs_c, bs_h_pt)
                    c_low = max(0.50, min(bs_o, bs_c, bs_l_pt))
                else:
                    c_high = max(bs_o, bs_c, bs_l_pt)
                    c_low = max(0.50, min(bs_o, bs_c, bs_h_pt))

                processed_candles.append({
                    "time": sc["time"], "date_label": sc["date_label"], "is_prev_day": sc["is_prev_day"],
                    "open": bs_o, "high": c_high, "low": c_low, "close": bs_c, "volume": sc["volume"]
                })
            raw_candles = processed_candles
            opt_cur = calc_black_scholes(curr_spot, strike, iv=iv_val)
            ltp = opt_cur["ce_ltp"] if opt_type == "CE" else opt_cur["pe_ltp"]
            greeks = {"delta": opt_cur["ce_delta"] if opt_type == "CE" else opt_cur["pe_delta"], "gamma": opt_cur["gamma"], "theta": opt_cur["theta"]}

        tf_mins = {"1m": 1, "3m": 3, "5m": 5, "15m": 15}.get(timeframe, 1)
        if tf_mins > 1:
            candles = []
            chunk = []
            for c in raw_candles:
                chunk.append(c)
                if len(chunk) == tf_mins:
                    candles.append(merge_candle_chunk(chunk))
                    chunk = []
            if chunk: candles.append(merge_candle_chunk(chunk))
        else:
            candles = raw_candles

        closes = [c["close"] for c in candles]
        volumes = [c["volume"] for c in candles]
        ema9_s = calc_ema_series(closes, 9)
        ema15_s = calc_ema_series(closes, 15)
        vwap = round(sum(closes[i]*volumes[i] for i in range(len(closes))) / sum(volumes), 2) if sum(volumes) > 0 else (closes[-1] if closes else 0)

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

class ThreadedHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True
    def handle_error(self, request, client_address):
        pass

class DhanSimHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args): return
    def _safe_send_response(self, code=200, content_type="application/json"):
        try:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return True
        except: return False
    def _safe_write(self, data: bytes):
        try: self.wfile.write(data)
        except: pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ["/", "/index.html"]:
            if os.path.exists("index.html"):
                with open("index.html", "rb") as f: body = f.read()
                if self._safe_send_response(200, "text/html; charset=utf-8"): self._safe_write(body)
        elif parsed.path == "/api/market":
            params = parse_qs(parsed.query)
            symbol = params.get("symbol", ["NIFTY"])[0]
            tf = params.get("tf", ["1m"])[0]
            with state.lock:
                chart_data = state.get_instrument_chart_data(symbol, timeframe=tf)
                chain = state.get_option_chain(symbol)
                sounds = list(state.sound_events); state.sound_events.clear()
                resp = {
                    "nifty_spot": state.nifty_spot, "sensex_spot": state.sensex_spot,
                    "nifty_chg": round(state.nifty_spot - state.nifty_base, 2),
                    "nifty_pct": round(((state.nifty_spot - state.nifty_base) / state.nifty_base) * 100, 2),
                    "sensex_chg": round(state.sensex_spot - state.sensex_base, 2),
                    "sensex_pct": round(((state.sensex_spot - state.sensex_base) / state.sensex_base) * 100, 2),
                    "chart": chart_data, "chain": chain, "sounds": sounds,
                    "wallet": {**state.wallet, "unrealized_pnl": 0.0, "net_pnl": state.wallet["realized_pnl"]},
                    "positions": state.positions, "pending_orders": state.pending_orders,
                    "orders": state.orders, "closed_trades": state.closed_trades[:15]
                }
            if self._safe_send_response(200): self._safe_write(json.dumps(resp).encode("utf-8"))

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
        if parsed.path == "/api/order":
            symbol = body.get("symbol"); action = body.get("action", "BUY")
            opt_type = body.get("type", "INDEX"); strike = float(body.get("strike", 0))
            is_sensex = "SENSEX" in symbol
            qty = max(10 if is_sensex else 65, int(body.get("qty", 65)))
            order_type = body.get("order_type", "MARKET")
            limit_price = float(body.get("limit_price", 0))
            curr_spot = state.sensex_spot if is_sensex else state.nifty_spot
            with state.lock:
                exec_price = limit_price if order_type == "LIMIT" and limit_price > 0 else curr_spot
                total_margin = round(exec_price * qty * 0.05, 2)
                state.wallet["balance"] -= total_margin
                state.pending_orders.append({
                    "id": f"LMT_{int(time.time()*1000)}", "symbol": symbol, "action": action,
                    "type": opt_type, "strike": strike, "qty": qty, "margin": total_margin,
                    "limit_price": exec_price, "stop_loss": 0, "target": 0, "trailing_sl": 0
                })
            if self._safe_send_response(200): self._safe_write(json.dumps({"status": "SUCCESS"}).encode("utf-8"))

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8000))
    with ThreadedHTTPServer(("0.0.0.0", PORT), DhanSimHandler) as httpd:
        httpd.serve_forever()
