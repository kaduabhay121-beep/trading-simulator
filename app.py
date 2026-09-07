import urllib.request
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
    target_weekday = 4 if is_sensex else 1  # Tuesday for Nifty, Friday for Sensex
    expiries = []
    curr = now
    while len(expiries) < 5:
        days_ahead = (target_weekday - curr.weekday()) % 7
        if days_ahead == 0 and curr.time() > dtime(15, 30):
            days_ahead = 7
        exp_date = curr + timedelta(days=days_ahead)
        s = exp_date.strftime("%d%b%y").upper()
        if s not in expiries:
            expiries.append(s)
        curr = exp_date + timedelta(days=1)
    return expiries

def get_dte_from_expiry(expiry_str):
    try:
        now = datetime.now(IST)
        exp_date = datetime.strptime(expiry_str, "%d%b%y")
        exp_target = IST.localize(exp_date.replace(hour=15, minute=30, second=0))
        diff_sec = (exp_target - now).total_seconds()
        return max(round(diff_sec / 86400.0, 3), 0.08)
    except Exception:
        return 1.1

def norm_pdf(x):
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def calc_black_scholes(spot, strike, dte_days=1.15, iv=0.143, r=0.065, is_sensex=False):
    if strike <= 0 or spot <= 0:
        return {"ce_ltp": 0.50, "pe_ltp": 0.50, "ce_delta": 0.0, "pe_delta": 0.0, "gamma": 0.0, "theta": 0.0}
    
    # Forward basis alignment: +46.5 pts basis on Nifty, +140 pts on Sensex
    basis = (140.0 if is_sensex else 46.5) * max(min(dte_days / 1.15, 2.5), 0.2)
    F = spot + basis
    t = max(dte_days / 365.0, 0.0001)
    sqrt_t = math.sqrt(t)
    vol = iv if iv else 0.143

    d1 = (math.log(F / strike) + 0.5 * vol * vol * t) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    nd1 = norm_cdf(d1)
    nd2 = norm_cdf(d2)
    pdf_d1 = norm_pdf(d1)
    df = math.exp(-r * t)

    ce = df * (F * nd1 - strike * nd2)
    pe = df * (strike * norm_cdf(-d2) - F * norm_cdf(-d1))

    # OTM Put volatility skew calibration
    if strike < F:
        pe *= (1.0 + min(0.06, ((F - strike) / F) * 1.2))

    return {
        "ce_ltp": max(round(ce, 2), 0.50),
        "pe_ltp": max(round(pe, 2), 0.50),
        "ce_delta": round(nd1, 3),
        "pe_delta": round(nd1 - 1.0, 3),
        "gamma": round(pdf_d1 / (spot * vol * sqrt_t), 5),
        "theta": round((- (spot * pdf_d1 * vol) / (2.0 * sqrt_t) - r * strike * df * nd2) / 365.0, 2)
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
        self.nifty_spot = 23768.0
        self.sensex_spot = 81200.0
        self.prev_close = 23897.70
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
        self.candles_1m = []
        self.order_counter = 100
        self._running = True
        self._init_history()
        self.ticker_thread = threading.Thread(target=self._tick_loop, daemon=True)
        self.ticker_thread.start()

    def _fetch_yahoo_candles(self):
        try:
            url = "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI?interval=1m&range=1d"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode())
                res = data["chart"]["result"][0]
                meta = res.get("meta", {})
                timestamps = res.get("timestamp", [])
                quote = res["indicators"]["quote"][0]
                o, h, l, c, v = quote.get("open", []), quote.get("high", []), quote.get("low", []), quote.get("close", []), quote.get("volume", [])
                candles = []
                for i in range(len(timestamps)):
                    if None not in (o[i], h[i], l[i], c[i]):
                        candles.append({
                            "time": int(timestamps[i]),
                            "is_prev_day": False,
                            "open": round(float(o[i]), 2),
                            "high": round(float(h[i]), 2),
                            "low": round(float(l[i]), 2),
                            "close": round(float(c[i]), 2),
                            "volume": int(v[i] or 1000)
                        })
                if candles:
                    pc = meta.get("chartPreviousClose") or meta.get("previousClose") or candles[0]["open"]
                    return candles, round(float(candles[-1]["close"]), 2), round(float(pc), 2)
        except Exception:
            pass
        return None, None, None

    def _init_history(self):
        real_candles, spot, pc = self._fetch_yahoo_candles()
        if real_candles and len(real_candles) >= 15:
            self.candles_1m = real_candles
            self.nifty_spot = spot
            self.prev_close = pc
            self.sensex_spot = round(self.nifty_spot * 3.41, 2)
            return

        now_ts = int(time.time())
        cur_min_ts = (now_ts // 60) * 60
        cur_price = self.nifty_spot
        generated = []
        for i in range(90):
            t = cur_min_ts - ((89 - i) * 60)
            change = random.uniform(-2.5, 2.5)
            o = round(cur_price - change, 2)
            c = round(cur_price, 2)
            h = round(max(o, c) + random.uniform(0.2, 1.8), 2)
            l = round(min(o, c) - random.uniform(0.2, 1.8), 2)
            v = random.randint(1200, 6500)
            generated.append({
                "time": t, "is_prev_day": False,
                "open": o, "high": h, "low": l, "close": c,
                "volume": v
            })
            cur_price = o
        generated.sort(key=lambda x: x["time"])
        self.candles_1m = generated

    def _get_live_instrument_ltp(self, symbol):
        if symbol in ["NIFTY", "NIFTY 50", "INDEX"]:
            return self.nifty_spot, 1.0, 0.0
        if symbol in ["SENSEX"]:
            return self.sensex_spot, 1.0, 0.0

        is_sensex = "SENSEX" in symbol
        curr_spot = self.sensex_spot if is_sensex else self.nifty_spot
        iv_val = 0.125
        expiries = get_available_expiries(is_sensex)

        # Parse symbol variants: e.g. "NIFTY 08SEP26 23750 CE" or "NIFTY_08SEP26_23750_CE"
        clean = symbol.replace("_", " ").split()
        strike = curr_spot
        opt_type = "CE"
        dte = 1.1

        for part in clean:
            if part.upper() in ["CE", "PE"]:
                opt_type = part.upper()
            elif part.isdigit() and len(part) >= 4:
                strike = float(part)
            elif len(part) == 7 and any(m in part.upper() for m in ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]):
                dte = get_dte_from_expiry(part.upper())

        bs = calc_black_scholes(curr_spot, strike, dte_days=dte, iv=iv_val)
        ltp = bs["ce_ltp"] if opt_type == "CE" else bs["pe_ltp"]
        delta = bs["ce_delta"] if opt_type == "CE" else bs["pe_delta"]
        return ltp, delta, bs["theta"]

    def _tick_loop(self):
        last_sync = 0
        while self._running:
            try:
                time.sleep(1.0)
                now_ts = int(time.time())

                if now_ts - last_sync > 20:
                    last_sync = now_ts
                    _, spot, pc = self._fetch_yahoo_candles()
                    if spot:
                        with self.lock:
                            self.nifty_spot = spot
                            if pc: self.prev_close = pc
                            self.sensex_spot = round(spot * 3.41, 2)

                with self.lock:
                    jitter = random.choice([-1.2, -0.6, 0.0, 0.6, 1.2]) * random.uniform(0.4, 1.1)
                    self.nifty_spot = round(self.nifty_spot + jitter, 2)
                    self.sensex_spot = round(self.nifty_spot * 3.41, 2)

                    cur_interval = (now_ts // 60) * 60
                    if not self.candles_1m or cur_interval > self.candles_1m[-1]["time"]:
                        prev_c = self.candles_1m[-1]["close"] if self.candles_1m else self.nifty_spot
                        self.candles_1m.append({
                            "time": cur_interval,
                            "is_prev_day": False,
                            "open": prev_c,
                            "high": max(prev_c, self.nifty_spot),
                            "low": min(prev_c, self.nifty_spot),
                            "close": self.nifty_spot,
                            "volume": random.randint(150, 400)
                        })
                        if len(self.candles_1m) > 400:
                            self.candles_1m.pop(0)
                    else:
                        c = self.candles_1m[-1]
                        c["close"] = self.nifty_spot
                        if self.nifty_spot > c["high"]: c["high"] = self.nifty_spot
                        if self.nifty_spot < c["low"]: c["low"] = self.nifty_spot
                        c["volume"] += random.randint(20, 60)

                    # Update position prices & PnL
                    unrealized_total = 0.0
                    to_auto_exit = []
                    for pos in self.positions:
                        ltp, delta, theta = self._get_live_instrument_ltp(pos["symbol"])
                        pos["ltp"] = ltp
                        pos["delta"] = delta
                        pos["theta"] = theta
                        if pos["action"] == "BUY":
                            pos["pnl"] = round((pos["ltp"] - pos["buy_price"]) * pos["qty"], 2)
                            if pos.get("stop_loss", 0) > 0 and pos["ltp"] <= pos["stop_loss"]:
                                to_auto_exit.append(pos["id"])
                            elif pos.get("target", 0) > 0 and pos["ltp"] >= pos["target"]:
                                to_auto_exit.append(pos["id"])
                        else:
                            pos["pnl"] = round((pos["buy_price"] - pos["ltp"]) * pos["qty"], 2)
                            if pos.get("stop_loss", 0) > 0 and pos["ltp"] >= pos["stop_loss"]:
                                to_auto_exit.append(pos["id"])
                            elif pos.get("target", 0) > 0 and pos["ltp"] <= pos["target"]:
                                to_auto_exit.append(pos["id"])
                        unrealized_total += pos["pnl"]

                    for pid in to_auto_exit:
                        self._internal_exit(pid)

                    # Check pending limit orders
                    remaining_pending = []
                    for po in self.pending_orders:
                        pltp, _, _ = self._get_live_instrument_ltp(po["symbol"])
                        filled = False
                        if po["action"] == "BUY" and pltp <= po["limit_price"]:
                            filled = True
                        elif po["action"] == "SELL" and pltp >= po["limit_price"]:
                            filled = True

                        if filled:
                            self._execute_fill(po["symbol"], po["action"], po["qty"], po["limit_price"],
                                               po.get("stop_loss", 0), po.get("target", 0), po.get("trailing_sl", 0))
                        else:
                            remaining_pending.append(po)
                    self.pending_orders = remaining_pending

            except Exception:
                pass

    def _execute_fill(self, symbol, action, qty, fill_price, sl=0, target=0, tsl=0):
        pos_id = f"pos_{self.order_counter}"
        self.order_counter += 1
        ltp, delta, theta = self._get_live_instrument_ltp(symbol)
        cost = round(fill_price * qty, 2)

        self.wallet["balance"] = round(self.wallet["balance"] - cost, 2)
        self.wallet["used_margin"] = round(self.wallet["used_margin"] + cost, 2)

        new_pos = {
            "id": pos_id,
            "symbol": symbol,
            "action": action,
            "qty": qty,
            "buy_price": fill_price,
            "ltp": ltp,
            "pnl": 0.0,
            "stop_loss": sl,
            "target": target,
            "trailing_sl": tsl,
            "delta": delta,
            "theta": theta
        }
        self.positions.append(new_pos)
        self.orders.insert(0, {
            "id": pos_id,
            "time": datetime.now(IST).strftime("%H:%M:%S"),
            "symbol": symbol,
            "action": action,
            "qty": qty,
            "price": fill_price,
            "status": "FILLED"
        })

    def _internal_exit(self, pos_id):
        for i, p in enumerate(self.positions):
            if p["id"] == pos_id:
                pos = self.positions.pop(i)
                cost = round(pos["buy_price"] * pos["qty"], 2)
                pnl = pos["pnl"]
                self.wallet["used_margin"] = max(0.0, round(self.wallet["used_margin"] - cost, 2))
                self.wallet["balance"] = round(self.wallet["balance"] + cost + pnl, 2)
                self.wallet["realized_pnl"] = round(self.wallet["realized_pnl"] + pnl, 2)
                self.closed_trades.insert(0, {
                    "id": pos["id"],
                    "symbol": pos["symbol"],
                    "action": pos["action"],
                    "qty": pos["qty"],
                    "buy_price": pos["buy_price"],
                    "exit_price": pos["ltp"],
                    "pnl": pnl,
                    "time": datetime.now(IST).strftime("%H:%M:%S")
                })
                break

    def _resample(self, candles, tf_sec):
        if tf_sec <= 60 or not candles:
            return candles
        buckets = {}
        for c in candles:
            b_time = (c["time"] // tf_sec) * tf_sec
            if b_time not in buckets:
                buckets[b_time] = {
                    "time": b_time, "is_prev_day": c.get("is_prev_day", False),
                    "open": c["open"], "high": c["high"],
                    "low": c["low"], "close": c["close"],
                    "volume": c["volume"]
                }
            else:
                b = buckets[b_time]
                b["high"] = max(b["high"], c["high"])
                b["low"] = min(b["low"], c["low"])
                b["close"] = c["close"]
                b["volume"] += c["volume"]
        return list(buckets.values())

    def get_instrument_chart_data(self, symbol, timeframe="1m"):
        is_sensex = "SENSEX" in symbol
        curr_spot = self.sensex_spot if is_sensex else self.nifty_spot

        tf_seconds_map = {"1m": 60, "3m": 180, "5m": 300, "15m": 900}
        tf_sec = tf_seconds_map.get(timeframe, 60)
        now_epoch = int(time.time())
        rem_sec = tf_sec - (now_epoch % tf_sec)
        countdown = f"{rem_sec // 60:02d}:{rem_sec % 60:02d}"

        base_resampled = self._resample(self.candles_1m, tf_sec)

        if symbol in ["NIFTY", "SENSEX"] or "_" not in symbol:
            raw_candles = []
            multiplier = 3.41 if is_sensex else 1.0
            for sc in base_resampled:
                raw_candles.append({
                    "time": sc["time"], "is_prev_day": sc.get("is_prev_day", False),
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
                expiry_str, strike, opt_type = parts[1], float(parts[2]), parts[3]
            elif len(parts) == 3:
                expiry_str, strike, opt_type = get_available_expiries(is_sensex)[0], float(parts[1]), parts[2]
            else:
                strike, opt_type = curr_spot, "CE"
                expiry_str = get_available_expiries(is_sensex)[0]

            dte = get_dte_from_expiry(expiry_str)
            display_title = f"{'SENSEX' if is_sensex else 'NIFTY'} {expiry_str} {int(strike)} {opt_type}"
            raw_candles = []
            iv_val = 0.125
            for sc in base_resampled:
                s_open = sc["open"] / (3.41 if is_sensex else 1.0)
                s_close = sc["close"] / (3.41 if is_sensex else 1.0)
                bs_o = calc_black_scholes(s_open, strike, dte_days=dte, iv=iv_val)[f"{opt_type.lower()}_ltp"]
                bs_c = calc_black_scholes(s_close, strike, dte_days=dte, iv=iv_val)[f"{opt_type.lower()}_ltp"]
                raw_candles.append({
                    "time": sc["time"], "is_prev_day": sc.get("is_prev_day", False),
                    "open": bs_o, "high": max(bs_o, bs_c), "low": min(bs_o, bs_c),
                    "close": bs_c, "volume": sc["volume"]
                })
            opt_cur = calc_black_scholes(curr_spot, strike, dte_days=dte, iv=0.143, is_sensex=is_sensex)
            ltp = opt_cur["ce_ltp"] if opt_type == "CE" else opt_cur["pe_ltp"]
            greeks = {
                "delta": opt_cur["ce_delta"] if opt_type == "CE" else opt_cur["pe_delta"],
                "gamma": opt_cur["gamma"], "theta": opt_cur["theta"]
            }

        closes = [c["close"] for c in raw_candles]
        volumes = [c["volume"] for c in raw_candles]
        ema9_s = calc_ema_series(closes, 9)
        ema15_s = calc_ema_series(closes, 15)
        vwap = round(sum(closes[i] * volumes[i] for i in range(len(closes))) / sum(volumes), 2) if sum(volumes) > 0 else (closes[-1] if closes else 0)

        return {
            "symbol": symbol, "display_title": display_title, "timeframe": timeframe,
            "ltp": ltp, "candles": raw_candles, "countdown": countdown,
            "ema9": ema9_s[-1] if ema9_s else 0, "ema15": ema15_s[-1] if ema15_s else 0,
            "vwap": vwap, "ema9_series": ema9_s, "ema15_series": ema15_s, "greeks": greeks
        }

    def get_option_chain(self, symbol="NIFTY", expiry=None):
        is_sensex = "SENSEX" in symbol
        curr_spot = self.sensex_spot if is_sensex else self.nifty_spot
        step_val = 100 if is_sensex else 50
        atm = round(curr_spot / step_val) * step_val
        expiries = get_available_expiries(is_sensex)
        selected_expiry = expiry if (expiry and expiry in expiries) else expiries[0]
        dte = get_dte_from_expiry(selected_expiry)
        iv_val = 0.125

        chain = []
        for s in [atm + (i * step_val) for i in range(-8, 9)]:
            g = calc_black_scholes(curr_spot, s, dte_days=dte, iv=0.143, is_sensex=is_sensex)
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

    def _send_json(self, data_dict, code=200):
        try:
            body = json.dumps(data_dict).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ["/", "/index.html"]:
            if os.path.exists("index.html"):
                with open("index.html", "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
        elif parsed.path == "/api/market":
            params = parse_qs(parsed.query)
            symbol = params.get("symbol", ["NIFTY"])[0]
            tf = params.get("tf", ["1m"])[0]
            expiry = params.get("expiry", [None])[0]
            with state.lock:
                chart_data = state.get_instrument_chart_data(symbol, timeframe=tf)
                chain_data = state.get_option_chain(symbol, expiry=expiry)
                unrealized = sum(p.get("pnl", 0) for p in state.positions)
                trades = state.closed_trades
                wins = sum(1 for t in trades if t["pnl"] > 0)
                win_rate = round((wins / len(trades) * 100), 1) if trades else 0.0
                gross_profit = sum(t["pnl"] for t in trades if t["pnl"] > 0)
                gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
                pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

                resp = {
                    "nifty_spot": state.nifty_spot,
                    "sensex_spot": state.sensex_spot,
                    "nifty_chg": round(state.nifty_spot - state.prev_close, 2),
                    "nifty_pct": round(((state.nifty_spot - state.prev_close) / state.prev_close) * 100, 2),
                    "chart": chart_data,
                    "chain": chain_data["chain"],
                    "expiries": chain_data["expiries"],
                    "selected_expiry": chain_data["selected_expiry"],
                    "wallet": {
                        **state.wallet,
                        "unrealized_pnl": round(unrealized, 2),
                        "net_pnl": round(state.wallet["realized_pnl"] + unrealized, 2)
                    },
                    "analytics": {
                        "win_rate": win_rate,
                        "profit_factor": pf,
                        "net_pnl": round(state.wallet["realized_pnl"] + unrealized, 2),
                        "total_trades": len(trades)
                    },
                    "positions": state.positions,
                    "pending_orders": state.pending_orders,
                    "orders": state.orders,
                    "closed_trades": state.closed_trades[:15]
                }
            self._send_json(resp)

    def do_POST(self):
        parsed = urlparse(self.path)
        content_len = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
        try:
            payload = json.loads(post_data)
        except Exception:
            payload = {}

        with state.lock:
            if parsed.path == "/api/order":
                symbol = payload.get("symbol", "NIFTY")
                action = payload.get("action", "BUY")
                qty = int(payload.get("qty", 65))
                order_type = payload.get("order_type", "MARKET")
                limit_price = float(payload.get("limit_price", 0))
                sl = float(payload.get("stop_loss", 0))
                target = float(payload.get("target", 0))
                tsl = float(payload.get("trailing_sl", 0))

                ltp, _, _ = state._get_live_instrument_ltp(symbol)
                fill_price = ltp if order_type == "MARKET" else limit_price

                if order_type == "LIMIT":
                    # Check if immediate execution is possible
                    can_fill = (action == "BUY" and ltp <= limit_price) or (action == "SELL" and ltp >= limit_price)
                    if can_fill:
                        state._execute_fill(symbol, action, qty, fill_price, sl, target, tsl)
                    else:
                        p_id = f"ord_{state.order_counter}"
                        state.order_counter += 1
                        state.pending_orders.append({
                            "id": p_id,
                            "symbol": symbol,
                            "action": action,
                            "qty": qty,
                            "limit_price": limit_price,
                            "order_type": order_type,
                            "stop_loss": sl,
                            "target": target,
                            "trailing_sl": tsl,
                            "time": datetime.now(IST).strftime("%H:%M:%S")
                        })
                else:
                    state._execute_fill(symbol, action, qty, fill_price, sl, target, tsl)

                self._send_json({"status": "ok"})

            elif parsed.path == "/api/exit":
                pid = payload.get("id")
                state._internal_exit(pid)
                self._send_json({"status": "ok"})

            elif parsed.path == "/api/cancel_order":
                oid = payload.get("id")
                state.pending_orders = [po for po in state.pending_orders if po["id"] != oid]
                self._send_json({"status": "ok"})

            elif parsed.path == "/api/reset":
                state.wallet = {"initial": 50000.0, "balance": 50000.0, "used_margin": 0.0, "realized_pnl": 0.0}
                state.positions = []
                state.pending_orders = []
                state.orders = []
                state.closed_trades = []
                self._send_json({"status": "ok"})

            elif parsed.path == "/api/update_brackets":
                pid = payload.get("id")
                for p in state.positions:
                    if p["id"] == pid:
                        if "stop_loss" in payload: p["stop_loss"] = float(payload["stop_loss"])
                        if "target" in payload: p["target"] = float(payload["target"])
                        if "trailing_sl" in payload: p["trailing_sl"] = float(payload["trailing_sl"])
                        break
                self._send_json({"status": "ok"})

            else:
                self._send_json({"status": "ok"})

class ThreadedHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True
    def handle_error(self, request, client_address): pass

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8000))
    with ThreadedHTTPServer(("0.0.0.0", PORT), DhanSimHandler) as httpd:
        print(f"Server running on port {PORT}")
        httpd.serve_forever()
