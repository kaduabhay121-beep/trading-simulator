import ssl
import http.cookiejar
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

def calc_vwap_series(candles):
    if not candles: return []
    series = []
    cum_pv = 0.0
    cum_vol = 0
    for c in candles:
        tp = (c["high"] + c["low"] + c["close"]) / 3.0
        v = max(int(c.get("volume", 100)), 1)
        cum_pv += tp * v
        cum_vol += v
        series.append(round(cum_pv / cum_vol, 2))
    return series

def calc_ema_series(data, period):
    if not data: return []
    k = 2.0 / (period + 1)
    series = [data[0]]
    for p in data[1:]:
        series.append(round((p * k) + (series[-1] * (1.0 - k)), 2))
    return series

class NSEDataFetcher:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/"
        }
        self.last_cookie_time = 0

    def refresh_session(self):
        try:
            req = urllib.request.Request("https://www.nseindia.com", headers=self.headers)
            self.opener.open(req, timeout=3.5)
            self.last_cookie_time = time.time()
            return True
        except Exception:
            return False

    def get_option_chain_raw(self, symbol="NIFTY"):
        try:
            now = time.time()
            if now - self.last_cookie_time > 180:
                self.refresh_session()

            url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
            req = urllib.request.Request(url, headers={
                **self.headers,
                "Accept": "application/json, text/plain, */*",
                "Referer": f"https://www.nseindia.com/option-chain"
            })
            with self.opener.open(req, timeout=4.0) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode())
        except Exception:
            pass
        return None

def calc_deep_greeks(spot, strike, dte_days, iv=14.3, r=0.065, is_sensex=False):
    t = max(dte_days / 365.0, 0.0001)
    sqrt_t = math.sqrt(t)
    vol = max(float(iv) / 100.0 if iv > 1.0 else float(iv), 0.05)

    # Dhan / NSE forward basis calibration
    basis = (140.0 if is_sensex else 46.5) * max(min(dte_days / 1.15, 2.5), 0.2)
    F = spot + basis

    d1 = (math.log(F / strike) + 0.5 * vol * vol * t) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t
    nd1 = norm_cdf(d1)
    nd2 = norm_cdf(d2)
    pdf_d1 = norm_pdf(d1)
    df = math.exp(-r * t)

    ce_ltp = max(round(df * (F * nd1 - strike * nd2), 2), 0.05)
    pe_ltp = max(round(df * (strike * norm_cdf(-d2) - F * norm_cdf(-d1)), 2), 0.05)

    gamma = round(pdf_d1 / (spot * vol * sqrt_t), 6)
    vega = round((spot * sqrt_t * pdf_d1) / 100.0, 2)
    theta = round((-(spot * pdf_d1 * vol) / (2.0 * sqrt_t) - r * strike * df * nd2) / 365.0, 2)

    return {
        "ce_ltp": ce_ltp, "pe_ltp": pe_ltp,
        "ce_delta": round(nd1, 3), "pe_delta": round(nd1 - 1.0, 3),
        "gamma": gamma, "theta": theta, "vega": vega,
        "iv": round(vol * 100.0, 1)
    }

class SimulationState:
    def __init__(self):
        self.lock = threading.Lock()
        self.nifty_spot = 23765.0
        self.sensex_spot = 81200.0
        self.prev_close = 23897.70
        self.wallet = {"initial": 50000.0, "balance": 50000.0, "used_margin": 0.0, "realized_pnl": 0.0}
        self.positions = []
        self.pending_orders = []
        self.orders = []
        self.closed_trades = []
        self.candles_1m = []
        self.order_counter = 100
        self.live_chain_cache = {}
        self.nse_fetcher = NSEDataFetcher()
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
                        o_val, h_val = round(float(o[i]), 2), round(float(h[i]), 2)
                        l_val, c_val = round(float(l[i]), 2), round(float(c[i]), 2)
                        raw_v = v[i] if (v[i] and int(v[i]) > 0) else None
                        if raw_v is None:
                            rng = max(abs(h_val - l_val), 0.5)
                            sim_vol = int(rng * random.randint(14000, 32000) + random.randint(18000, 65000))
                        else:
                            sim_vol = int(raw_v)
                        candles.append({
                            "time": int(timestamps[i]), "is_prev_day": False,
                            "open": o_val, "high": h_val, "low": l_val, "close": c_val,
                            "volume": sim_vol
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

    def _poll_nse_feed(self):
        raw = self.nse_fetcher.get_option_chain_raw("NIFTY")
        if not raw or "records" not in raw:
            return False

        records = raw["records"]
        underlying = records.get("underlyingValue")
        if underlying:
            self.nifty_spot = round(float(underlying), 2)
            self.sensex_spot = round(self.nifty_spot * 3.41, 2)

        expiries = records.get("expiryDates", [])
        data_rows = records.get("data", [])
        
        parsed_chain = {}
        for row in data_rows:
            strike = row.get("strikePrice")
            expiry = row.get("expiryDate")
            if not strike or not expiry: continue

            key = (expiry, strike)
            ce = row.get("CE", {})
            pe = row.get("PE", {})
            parsed_chain[key] = {
                "strike": strike,
                "expiry": expiry,
                "ce_ltp": float(ce.get("lastPrice", 0)),
                "ce_oi": ce.get("openInterest", 0),
                "ce_chg_oi": ce.get("changeinOpenInterest", 0),
                "ce_volume": ce.get("totalTradedVolume", 0),
                "ce_iv": float(ce.get("impliedVolatility", 14.3)),
                "pe_ltp": float(pe.get("lastPrice", 0)),
                "pe_oi": pe.get("openInterest", 0),
                "pe_chg_oi": pe.get("changeinOpenInterest", 0),
                "pe_volume": pe.get("totalTradedVolume", 0),
                "pe_iv": float(pe.get("impliedVolatility", 14.3))
            }

        self.live_chain_cache = {"expiries": expiries, "data": parsed_chain}
        return True

    def _get_live_instrument_ltp(self, symbol):
        if symbol in ["NIFTY", "NIFTY 50", "INDEX"]:
            return self.nifty_spot, 1.0, 0.0
        if symbol in ["SENSEX"]:
            return self.sensex_spot, 1.0, 0.0

        is_sensex = "SENSEX" in symbol
        curr_spot = self.sensex_spot if is_sensex else self.nifty_spot
        clean = symbol.replace("_", " ").split()
        strike = curr_spot
        opt_type = "CE"
        dte = 1.15
        for part in clean:
            if part.upper() in ["CE", "PE"]: opt_type = part.upper()
            elif part.isdigit() and len(part) >= 4: strike = float(part)
            elif len(part) == 7 and any(m in part.upper() for m in ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]):
                dte = get_dte_from_expiry(part.upper())

        g = calc_deep_greeks(curr_spot, strike, dte, iv=14.3, is_sensex=is_sensex)
        ltp = g["ce_ltp"] if opt_type == "CE" else g["pe_ltp"]
        delta = g["ce_delta"] if opt_type == "CE" else g["pe_delta"]
        return ltp, delta, g["theta"]

    def _tick_loop(self):
        last_sync = 0
        while self._running:
            try:
                time.sleep(1.0)
                now_ts = int(time.time())

                if now_ts - last_sync > 12:
                    last_sync = now_ts
                    success = self._poll_nse_feed()
                    if not success:
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
                            "time": cur_interval, "is_prev_day": False,
                            "open": prev_c, "high": max(prev_c, self.nifty_spot),
                            "low": min(prev_c, self.nifty_spot), "close": self.nifty_spot,
                            "volume": random.randint(150, 400)
                        })
                        if len(self.candles_1m) > 400: self.candles_1m.pop(0)
                    else:
                        c = self.candles_1m[-1]
                        c["close"] = self.nifty_spot
                        if self.nifty_spot > c["high"]: c["high"] = self.nifty_spot
                        if self.nifty_spot < c["low"]: c["low"] = self.nifty_spot
                        c["volume"] += random.randint(20, 60)

                    # Update open positions
                    for pos in self.positions:
                        ltp, delta, theta = self._get_live_instrument_ltp(pos["symbol"])
                        pos["ltp"] = ltp
                        pos["delta"] = delta
                        pos["theta"] = theta
                        if pos["action"] == "BUY":
                            pos["pnl"] = round((pos["ltp"] - pos["buy_price"]) * pos["qty"], 2)
                        else:
                            pos["pnl"] = round((pos["buy_price"] - pos["ltp"]) * pos["qty"], 2)

                    # Trigger pending limit orders
                    remaining_pending = []
                    for po in self.pending_orders:
                        pltp, _, _ = self._get_live_instrument_ltp(po["symbol"])
                        filled = (po["action"] == "BUY" and pltp <= po["limit_price"]) or                                  (po["action"] == "SELL" and pltp >= po["limit_price"])
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
        self.positions.append({
            "id": pos_id, "symbol": symbol, "action": action, "qty": qty,
            "buy_price": fill_price, "ltp": ltp, "pnl": 0.0,
            "stop_loss": sl, "target": target, "trailing_sl": tsl,
            "delta": delta, "theta": theta
        })
        self.orders.insert(0, {
            "id": pos_id, "time": datetime.now(IST).strftime("%H:%M:%S"),
            "symbol": symbol, "action": action, "qty": qty, "price": fill_price, "status": "FILLED"
        })

    def _internal_exit(self, pos_id, reason='MANUAL'):
        for i, p in enumerate(self.positions):
            if p["id"] == pos_id:
                pos = self.positions.pop(i)
                cost = round(pos["buy_price"] * pos["qty"], 2)
                pnl = pos["pnl"]
                self.wallet["used_margin"] = max(0.0, round(self.wallet["used_margin"] - cost, 2))
                self.wallet["balance"] = round(self.wallet["balance"] + cost + pnl, 2)
                self.wallet["realized_pnl"] = round(self.wallet["realized_pnl"] + pnl, 2)
                self.closed_trades.insert(0, {
                    "id": pos["id"], "symbol": pos["symbol"], "action": pos["action"],
                    "qty": pos["qty"], "buy_price": pos["buy_price"], "exit_price": pos["ltp"],
                    "pnl": pnl, "time": datetime.now(IST).strftime("%H:%M:%S")
                })
                break

    def _resample(self, candles, tf_sec):
        if tf_sec <= 60 or not candles: return candles
        buckets = {}
        for c in candles:
            b_time = (c["time"] // tf_sec) * tf_sec
            if b_time not in buckets:
                buckets[b_time] = {
                    "time": b_time, "is_prev_day": c.get("is_prev_day", False),
                    "open": c["open"], "high": c["high"],
                    "low": c["low"], "close": c["close"], "volume": c["volume"]
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
        tf_sec = {"1m": 60, "3m": 180, "5m": 300, "15m": 900}.get(timeframe, 60)
        rem_sec = tf_sec - (int(time.time()) % tf_sec)
        countdown = f"{rem_sec // 60:02d}:{rem_sec % 60:02d}"
        base_resampled = self._resample(self.candles_1m, tf_sec)

        if symbol in ["NIFTY", "SENSEX"] or "_" not in symbol:
            mult = 3.41 if is_sensex else 1.0
            raw_candles = [{
                "time": sc["time"], "is_prev_day": sc.get("is_prev_day", False),
                "open": round(sc["open"] * mult, 2), "high": round(sc["high"] * mult, 2),
                "low": round(sc["low"] * mult, 2), "close": round(sc["close"] * mult, 2),
                "volume": sc["volume"]
            } for sc in base_resampled]
            ltp = curr_spot
            display_title = "SENSEX" if is_sensex else "NIFTY 50"
            greeks = {"delta": 1.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "iv": 14.3}
        else:
            parts = symbol.split("_")
            expiry_str = parts[1] if len(parts) >= 4 else get_available_expiries(is_sensex)[0]
            strike = float(parts[2]) if len(parts) >= 4 else (float(parts[1]) if len(parts) == 3 else curr_spot)
            opt_type = parts[3] if len(parts) >= 4 else (parts[2] if len(parts) == 3 else "CE")
            dte = get_dte_from_expiry(expiry_str)
            display_title = f"{'SENSEX' if is_sensex else 'NIFTY'} {expiry_str} {int(strike)} {opt_type}"

            raw_candles = []
            for sc in base_resampled:
                div = 3.41 if is_sensex else 1.0
                s_o = sc["open"] / div
                s_c = sc["close"] / div
                s_h = sc["high"] / div
                s_l = sc["low"] / div

                op = opt_type.lower()
                bs_o = calc_deep_greeks(s_o, strike, dte, iv=14.3, is_sensex=is_sensex)[f"{op}_ltp"]
                bs_c = calc_deep_greeks(s_c, strike, dte, iv=14.3, is_sensex=is_sensex)[f"{op}_ltp"]

                # Project spot shadows into option wicks
                if opt_type == "CE":
                    bs_h = calc_deep_greeks(s_h, strike, dte, iv=14.3, is_sensex=is_sensex)["ce_ltp"]
                    bs_l = calc_deep_greeks(s_l, strike, dte, iv=14.3, is_sensex=is_sensex)["ce_ltp"]
                else:
                    bs_h = calc_deep_greeks(s_l, strike, dte, iv=14.3, is_sensex=is_sensex)["pe_ltp"]
                    bs_l = calc_deep_greeks(s_h, strike, dte, iv=14.3, is_sensex=is_sensex)["pe_ltp"]

                c_high = max(bs_o, bs_c, bs_h)
                c_low = max(0.05, min(bs_o, bs_c, bs_l))

                # Ensure minimum natural wick visibility
                body = abs(bs_c - bs_o)
                if c_high == max(bs_o, bs_c):
                    c_high = round(c_high + max(0.3, body * 0.15), 2)
                if c_low == min(bs_o, bs_c):
                    c_low = round(max(0.05, c_low - max(0.3, body * 0.15)), 2)

                raw_candles.append({
                    "time": sc["time"], "is_prev_day": sc.get("is_prev_day", False),
                    "open": bs_o, "high": c_high, "low": c_low,
                    "close": bs_c, "volume": int(max(abs(c_high - c_low), 0.4) * random.randint(900, 2600) + random.randint(1200, 4800))
                })
            greeks = calc_deep_greeks(curr_spot, strike, dte, iv=14.3, is_sensex=is_sensex)
            greeks["delta"] = greeks["ce_delta"] if opt_type == "CE" else greeks["pe_delta"]
            ltp = greeks["ce_ltp"] if opt_type == "CE" else greeks["pe_ltp"]

        closes = [c["close"] for c in raw_candles]
        volumes = [c["volume"] for c in raw_candles]
        ema9_s = calc_ema_series(closes, 9)
        ema15_s = calc_ema_series(closes, 15)
        vwap_s = calc_vwap_series(raw_candles)

        return {
            "symbol": symbol, "display_title": display_title, "timeframe": timeframe,
            "ltp": ltp, "candles": raw_candles, "countdown": countdown,
            "ema9": ema9_s[-1] if ema9_s else 0, "ema15": ema15_s[-1] if ema15_s else 0,
            "vwap": vwap_s[-1] if vwap_s else 0, "vwap_series": vwap_s,
            "ema9_series": ema9_s, "ema15_series": ema15_s, "greeks": greeks
        }

    def get_option_chain(self, symbol="NIFTY", expiry=None):
        is_sensex = "SENSEX" in symbol
        curr_spot = self.sensex_spot if is_sensex else self.nifty_spot
        step_val = 100 if is_sensex else 50
        atm = round(curr_spot / step_val) * step_val
        expiries = get_available_expiries(is_sensex)
        selected_expiry = expiry if (expiry and expiry in expiries) else expiries[0]
        dte = get_dte_from_expiry(selected_expiry)

        chain = []
        for s in [atm + (i * step_val) for i in range(-8, 9)]:
            # Check if live cached row from NSE exists
            cached_row = self.live_chain_cache.get("data", {}).get((selected_expiry, s))
            g = calc_deep_greeks(curr_spot, s, dte_days=dte, iv=cached_row.get("ce_iv", 14.3) if cached_row else 14.3)
            
            # Format numbers in Dhan convention (Lakhs/Crores)
            def fmt(n):
                if n >= 10000000: return f"{n/10000000:.2f} Cr"
                if n >= 100000: return f"{n/100000:.2f} L"
                return str(n)

            # Use live exchange fields if available, otherwise calibrate via market distribution
            ce_oi = fmt(cached_row["ce_oi"]) if cached_row and cached_row["ce_oi"] else f"{random.randint(15, 85)}.{random.randint(10,99)} L"
            pe_oi = fmt(cached_row["pe_oi"]) if cached_row and cached_row["pe_oi"] else f"{random.randint(20, 95)}.{random.randint(10,99)} L"
            ce_vol = fmt(cached_row["ce_volume"]) if cached_row and cached_row["ce_volume"] else f"{random.randint(2, 25)}.{random.randint(10,99)} L"
            pe_vol = fmt(cached_row["pe_volume"]) if cached_row and cached_row["pe_volume"] else f"{random.randint(2, 25)}.{random.randint(10,99)} L"
            ce_chg = fmt(cached_row["ce_chg_oi"]) if cached_row and cached_row["ce_chg_oi"] else f"+{random.randint(1, 15)}.{random.randint(10,99)} L"
            pe_chg = fmt(cached_row["pe_chg_oi"]) if cached_row and cached_row["pe_chg_oi"] else f"+{random.randint(1, 15)}.{random.randint(10,99)} L"

            ce_ltp = cached_row["ce_ltp"] if cached_row and cached_row["ce_ltp"] > 0 else g["ce_ltp"]
            pe_ltp = cached_row["pe_ltp"] if cached_row and cached_row["pe_ltp"] > 0 else g["pe_ltp"]

            chain.append({
                "strike": s, "expiry": selected_expiry,
                "ce_ltp": ce_ltp, "ce_delta": g["ce_delta"], "ce_oi": ce_oi,
                "ce_chg_oi": ce_chg, "ce_volume": ce_vol, "ce_iv": g["iv"],
                "pe_ltp": pe_ltp, "pe_delta": g["pe_delta"], "pe_oi": pe_oi,
                "pe_chg_oi": pe_chg, "pe_volume": pe_vol, "pe_iv": g["iv"],
                "gamma": g["gamma"], "theta": g["theta"], "vega": g["vega"]
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
