import http.server
import socketserver
import json
import math
import random
import time
import threading
import os
from datetime import datetime, time as dtime, timedelta
import pytz
from urllib.parse import urlparse, parse_qs

IST = pytz.timezone('Asia/Kolkata')

def is_market_open():
    now = datetime.now(IST)
    if now.weekday() >= 5: 
        return False
    return dtime(9, 15) <= now.time() <= dtime(15, 30)

def get_available_expiries(is_sensex=False):
    now = datetime.now(IST)
    target_weekday = 4 if is_sensex else 3
    expiries = []
    curr = now
    while len(expiries) < 3:
        days_ahead = (target_weekday - curr.weekday()) % 7
        if days_ahead == 0 and curr.time() > dtime(15, 30):
            days_ahead = 7
        exp_date = curr + timedelta(days=days_ahead)
        exp_str = exp_date.strftime("%d%b%y").upper()
        if exp_str not in expiries:
            expiries.append(exp_str)
        curr = exp_date + timedelta(days=1)
    return expiries

def norm_pdf(x):
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def calc_black_scholes(spot, strike, dte_days=4.0, iv=0.14, r=0.06):
    if strike <= 0 or spot <= 0:
        return {"ce_ltp": 0.50, "pe_ltp": 0.50, "ce_delta": 0.0, "pe_delta": 0.0, "gamma": 0.0, "theta": 0.0}
    t = max(dte_days / 365.0, 0.0001)
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    nd1 = norm_cdf(d1)
    nd2 = norm_cdf(d2)
    pdf_d1 = norm_pdf(d1)
    ce = spot * nd1 - strike * math.exp(-r * t) * nd2
    pe = strike * math.exp(-r * t) * norm_cdf(-d2) - spot * norm_cdf(-d1)
    return {
        "ce_ltp": max(round(ce, 2), 0.50), "pe_ltp": max(round(pe, 2), 0.50),
        "ce_delta": round(nd1, 3), "pe_delta": round(nd1 - 1.0, 3),
        "gamma": round(pdf_d1 / (spot * iv * sqrt_t), 5),
        "theta": round((- (spot * pdf_d1 * iv) / (2.0 * sqrt_t) - r * strike * math.exp(-r * t) * nd2) / 365.0, 2)
    }

def calc_ema_series(data, period):
    if not data: return []
    k = 2.0 / (period + 1)
    series = [data[0]]
    for p in data[1:]:
        series.append(round((p * k) + (series[-1] * (1.0 - k)), 2))
    return series

class SimulationState:
    def __init__(self):
        self.lock = threading.Lock()
        self.nifty_spot = 23897.70
        self.sensex_spot = 81450.00
        self.wallet = {"initial": 50000.0, "balance": 50000.0, "used_margin": 0.0, "realized_pnl": 0.0}
        self.positions = []
        self.pending_orders = []
        self.orders = []
        self.closed_trades = []
        self.sound_events = []
        self.candles_5m = []
        self._init_history()

    def _init_history(self):
        now_ist = datetime.now(IST)
        start_dt = now_ist.replace(hour=9, minute=15, second=0, microsecond=0) - timedelta(days=2)
        start_ts = int(start_dt.timestamp())
        cur = self.nifty_spot - 40.0
        for i in range(150):
            c_time = start_ts + (i * 300)
            o = cur
            c = o + random.uniform(-4, 4.5)
            h = max(o, c) + random.uniform(0.5, 2.5)
            l = min(o, c) - random.uniform(0.5, 2.5)
            v = random.randint(1500, 6000)
            self.candles_5m.append({
                "time": c_time, "is_prev_day": False,
                "open": round(o, 2), "high": round(h, 2),
                "low": round(l, 2), "close": round(c, 2),
                "volume": v
            })
            cur = c

    def get_instrument_chart_data(self, symbol, timeframe="5m"):
        is_sensex = "SENSEX" in symbol
        curr_spot = self.sensex_spot if is_sensex else self.nifty_spot

        if symbol in ["NIFTY", "SENSEX"] or not "_" in symbol:
            raw_candles = []
            multiplier = 3.4 if is_sensex else 1.0
            for sc in self.candles_5m:
                raw_candles.append({
                    "time": sc["time"], "is_prev_day": sc["is_prev_day"],
                    "open": round(sc["open"] * multiplier, 2),
                    "high": round(sc["high"] * multiplier, 2),
                    "low": round(sc["low"] * multiplier, 2),
                    "close": round(sc["close"] * multiplier, 2),
                    "volume": sc["volume"]
                })
            ltp = curr_spot
            display_title = "SENSEX" if is_sensex else "NIFTY 50"
            greeks = {"delta": 1.0, "gamma": 0.0, "theta": 0.0}
        else:
            parts = symbol.split("_")
            if len(parts) >= 4:
                expiry_str = parts[1]
                strike = float(parts[2])
                opt_type = parts[3]
            elif len(parts) == 3:
                expiry_str = get_available_expiries(is_sensex)[0]
                strike = float(parts[1])
                opt_type = parts[2]
            else:
                strike = curr_spot
                opt_type = "CE"
                expiry_str = get_available_expiries(is_sensex)[0]

            display_title = f"{'SENSEX' if is_sensex else 'NIFTY'} {expiry_str} {int(strike)} {opt_type}"
            raw_candles = []
            iv_val = 0.13 if is_sensex else 0.14
            for sc in self.candles_5m:
                base_spot = sc["close"] / (3.4 if is_sensex else 1.0)
                bs_o = calc_black_scholes(sc["open"] / (3.4 if is_sensex else 1.0), strike, iv=iv_val)[f"{opt_type.lower()}_ltp"]
                bs_c = calc_black_scholes(base_spot, strike, iv=iv_val)[f"{opt_type.lower()}_ltp"]
                raw_candles.append({
                    "time": sc["time"], "is_prev_day": sc["is_prev_day"],
                    "open": bs_o, "high": max(bs_o, bs_c), "low": min(bs_o, bs_c),
                    "close": bs_c, "volume": sc["volume"]
                })
            opt_cur = calc_black_scholes(curr_spot, strike, iv=iv_val)
            ltp = opt_cur["ce_ltp"] if opt_type == "CE" else opt_cur["pe_ltp"]
            greeks = {"delta": opt_cur["ce_delta"] if opt_type == "CE" else opt_cur["pe_delta"], "gamma": opt_cur["gamma"], "theta": opt_cur["theta"]}

        closes = [c["close"] for c in raw_candles]
        volumes = [c["volume"] for c in raw_candles]
        ema9_s = calc_ema_series(closes, 9)
        ema15_s = calc_ema_series(closes, 15)
        vwap = round(sum(closes[i] * volumes[i] for i in range(len(closes))) / sum(volumes), 2) if sum(volumes) > 0 else closes[-1]

        return {
            "symbol": symbol, "display_title": display_title, "timeframe": timeframe,
            "ltp": ltp, "candles": raw_candles, "countdown": "05:00",
            "ema9": ema9_s[-1] if ema9_s else 0, "ema15": ema15_s[-1] if ema15_s else 0,
            "vwap": vwap, "ema9_series": ema9_s, "ema15_series": ema15_s, "greeks": greeks
        }

    def get_option_chain(self, symbol="NIFTY", expiry=None):
        is_sensex = "SENSEX" in symbol
        curr_spot = self.sensex_spot if is_sensex else self.nifty_spot
        step_val = 100 if is_sensex else 50
        atm = round(curr_spot / step_val) * step_val
        iv_val = 0.13 if is_sensex else 0.14
        expiries = get_available_expiries(is_sensex)
        selected_expiry = expiry if expiry in expiries else expiries[0]
        
        chain = []
        for s in [atm + (i * step_val) for i in range(-8, 9)]:
            g = calc_black_scholes(curr_spot, s, iv=iv_val)
            chain.append({
                "strike": s, "expiry": selected_expiry,
                "ce_ltp": g["ce_ltp"], "ce_delta": g["ce_delta"],
                "ce_oi": f"{random.randint(15, 65)}L",
                "pe_ltp": g["pe_ltp"], "pe_delta": g["pe_delta"],
                "pe_oi": f"{random.randint(18, 70)}L",
                "gamma": g["gamma"], "theta": g["theta"]
            })
        return {"expiries": expiries, "selected_expiry": selected_expiry, "chain": chain}

state = SimulationState()

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
                with open("index.html", "rb") as f:
                    body = f.read()
                if self._safe_send_response(200, "text/html; charset=utf-8"):
                    self._safe_write(body)
        elif parsed.path == "/api/market":
            params = parse_qs(parsed.query)
            symbol = params.get("symbol", ["NIFTY"])[0]
            tf = params.get("tf", ["5m"])[0]
            expiry = params.get("expiry", [None])[0]
            with state.lock:
                chart_data = state.get_instrument_chart_data(symbol, timeframe=tf)
                chain_data = state.get_option_chain(symbol, expiry=expiry)
                resp = {
                    "nifty_spot": state.nifty_spot, "sensex_spot": state.sensex_spot,
                    "nifty_chg": 24.25, "nifty_pct": 0.10,
                    "chart": chart_data, "chain": chain_data["chain"],
                    "expiries": chain_data["expiries"], "selected_expiry": chain_data["selected_expiry"],
                    "wallet": {**state.wallet, "unrealized_pnl": 0.0, "net_pnl": state.wallet["realized_pnl"]},
                    "positions": state.positions, "pending_orders": state.pending_orders,
                    "orders": state.orders, "closed_trades": state.closed_trades[:15]
                }
            if self._safe_send_response(200):
                self._safe_write(json.dumps(resp).encode("utf-8"))

    def do_POST(self):
        pass

class ThreadedHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True
    def handle_error(self, request, client_address): pass

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8000))
    with ThreadedHTTPServer(("0.0.0.0", PORT), DhanSimHandler) as httpd:
        print(f"Server running on port {PORT}")
        httpd.serve_forever()
