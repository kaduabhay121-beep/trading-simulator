import http.server
import socketserver
import json
import math
import random
import time
import threading
import os
from datetime import datetime, time as dtime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

try:
    import yfinance as yf
except ImportError:
    yf = None

IST = timezone(timedelta(hours=5, minutes=30))

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dhan Options & Index Trading Simulator</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { background-color: #0b0e14; color: #d1d5db; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 0; font-size: 13px; }
        header { background: #121824; border-bottom: 1px solid #1f293d; padding: 10px 20px; display: flex; justify-content: space-between; align-items: center; }
        .logo { font-size: 16px; font-weight: bold; color: #f3f4f6; display: flex; gap: 15px; align-items: center; }
        .expiry-badge { background: #1e293b; color: #38bdf8; padding: 4px 10px; border-radius: 4px; font-size: 11px; border: 1px solid #334155; }
        .market-status { background: #064e3b; color: #34d399; padding: 4px 10px; border-radius: 4px; font-size: 11px; font-weight: bold; }
        .container { display: grid; grid-template-columns: 280px 1fr 320px; height: calc(100vh - 51px); }
        .panel { background: #0f172a; border-right: 1px solid #1f293d; display: flex; flex-direction: column; overflow: hidden; }
        .panel-header { background: #1e293b; padding: 8px 12px; font-weight: bold; color: #94a3b8; font-size: 12px; border-bottom: 1px solid #334155; }
        .watchlist-item { padding: 10px 12px; border-bottom: 1px solid #1e293b; cursor: pointer; display: flex; justify-content: space-between; align-items: center; }
        .watchlist-item:hover { background: #1e293b; }
        .center-stage { display: flex; flex-direction: column; background: #0b0e14; overflow-y: auto; height: 100%; }
        .chart-toolbar { display: flex; gap: 10px; padding: 10px; background: #121824; border-bottom: 1px solid #1f293d; align-items: center; flex-shrink: 0; }
        button { background: #2563eb; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-weight: bold; }
        button:hover { background: #1d4ed8; }
        .chart-container { position: relative; width: 100%; height: 380px; min-height: 380px; padding: 10px; background: #0b0e14; flex-shrink: 0; }
        .trading-panel { padding: 15px; background: #111827; border-top: 1px solid #1f293d; display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; align-items: center; flex-shrink: 0; }
        .input-group { display: flex; flex-direction: column; gap: 4px; }
        .input-group label { font-size: 11px; color: #9ca3af; }
        .input-group input, .input-group select { background: #1f2937; border: 1px solid #374151; color: white; padding: 6px; border-radius: 4px; }
        .right-panel { background: #0f172a; border-left: 1px solid #1f293d; display: flex; flex-direction: column; }
        .chain-table { width: 100%; border-collapse: collapse; font-size: 11px; }
        .chain-table th, .chain-table td { padding: 6px; text-align: center; border-bottom: 1px solid #1e293b; }
        .chain-table th { background: #1e293b; color: #94a3b8; position: sticky; top: 0; }
        .ce-side { color: #f87171; }
        .pe-side { color: #4ade80; }
    </style>
</head>
<body>
    <header>
        <div class="logo">
            <span>⚡ Dhan Trading Sim</span>
            <span class="expiry-badge" id="expiryDisplay">Expiry: Loading...</span>
            <span class="market-status" id="marketStatus">LIVE (09:00 - 15:40 IST)</span>
        </div>
        <div style="display: flex; gap: 20px; align-items: center;">
            <div>Balance: ₹<span id="walletBalance">50000.00</span></div>
            <div>Net P&L: ₹<span id="netPnl">0.00</span></div>
            <button onclick="resetAccount()" style="background: #dc2626; padding: 4px 8px; font-size: 11px;">Reset</button>
        </div>
    </header>

    <div class="container">
        <div class="panel">
            <div class="panel-header">Indices & Watchlist</div>
            <div class="watchlist-item" onclick="selectSymbol('NIFTY')">
                <div>
                    <div style="font-weight: bold;">NIFTY 50</div>
                    <div style="font-size: 11px; color: #9ca3af;" id="niftySpot">--</div>
                </div>
                <div id="niftyChg" style="color: #34d399;">--</div>
            </div>
            <div class="watchlist-item" onclick="selectSymbol('SENSEX')">
                <div>
                    <div style="font-weight: bold;">SENSEX</div>
                    <div style="font-size: 11px; color: #9ca3af;" id="sensexSpot">--</div>
                </div>
                <div id="sensexChg" style="color: #34d399;">--</div>
            </div>
            <div class="panel-header" style="margin-top: 10px;">Option Chain Strikes</div>
            <div style="flex: 1; overflow-y: auto;">
                <table class="chain-table">
                    <thead>
                        <tr><th>CE LTP</th><th>Strike</th><th>PE LTP</th></tr>
                    </thead>
                    <tbody id="chainBody"></tbody>
                </table>
            </div>
        </div>

        <div class="center-stage">
            <div class="chart-toolbar">
                <span id="chartTitle" style="font-weight: bold; font-size: 14px;">NIFTY 50</span>
                <button onclick="setTimeframe('1m')">1m</button>
                <button onclick="setTimeframe('5m')">5m</button>
                <button onclick="setTimeframe('15m')">15m</button>
                <span style="margin-left: auto; color: #9ca3af;" id="marketCountdown">Close in: --</span>
            </div>
            <div class="chart-container">
                <canvas id="mainChart"></canvas>
            </div>
            <div class="trading-panel">
                <div class="input-group">
                    <label>Action</label>
                    <select id="orderAction"><option value="BUY">BUY</option><option value="SELL">SELL</option></select>
                </div>
                <div class="input-group">
                    <label>Quantity</label>
                    <input type="number" id="orderQty" value="65">
                </div>
                <div class="input-group">
                    <label>Order Type</label>
                    <select id="orderType"><option value="MARKET">MARKET</option><option value="LIMIT">LIMIT</option></select>
                </div>
                <div class="input-group">
                    <label>&nbsp;</label>
                    <button onclick="placeOrder()" style="background: #16a34a; width: 100%;">Place Order</button>
                </div>
            </div>
        </div>

        <div class="right-panel">
            <div class="panel-header">Active Positions</div>
            <div style="flex: 1; overflow-y: auto; padding: 10px;" id="positionsContainer">
                <div style="color: #6b7280; text-align: center; margin-top: 20px;">No open positions</div>
            </div>
            <div class="panel-header">Recent Orders</div>
            <div style="height: 150px; overflow-y: auto; padding: 10px; font-size: 11px;" id="ordersContainer"></div>
        </div>
    </div>

    <script>
        let currentSymbol = 'NIFTY';
        let currentTimeframe = '1m';
        let chartInstance = null;

        function selectSymbol(sym) { currentSymbol = sym; fetchMarketData(); }
        function setTimeframe(tf) { currentTimeframe = tf; fetchMarketData(); }

        async function fetchMarketData() {
            try {
                let res = await fetch(`/api/market?symbol=${currentSymbol}&tf=${currentTimeframe}`);
                let data = await res.json();
                
                document.getElementById('niftySpot').innerText = data.nifty_spot;
                document.getElementById('niftyChg').innerText = `${data.nifty_chg} (${data.nifty_pct}%)`;
                document.getElementById('sensexSpot').innerText = data.sensex_spot;
                document.getElementById('sensexChg').innerText = `${data.sensex_chg} (${data.sensex_pct}%)`;
                
                document.getElementById('walletBalance').innerText = data.wallet.balance.toFixed(2);
                document.getElementById('netPnl').innerText = data.wallet.net_pnl.toFixed(2);

                let exp = currentSymbol.includes('SENSEX') ? data.sensex_expiry : data.nifty_expiry;
                document.getElementById('expiryDisplay').innerText = `Expiry: ${exp}`;

                renderChart(data.chart);
                renderChain(data.chain);
                renderPositions(data.positions);
                renderOrders(data.orders);
            } catch (e) { console.error("Fetch error:", e); }
        }

        function renderChart(chartData) {
            document.getElementById('chartTitle').innerText = chartData.display_title;
            document.getElementById('marketCountdown').innerText = `Close in: ${chartData.countdown}`;

            let labels = chartData.candles.map(c => c.date_label);
            let prices = chartData.candles.map(c => c.close);

            let ctx = document.getElementById('mainChart').getContext('2d');
            if (chartInstance) {
                chartInstance.data.labels = labels;
                chartInstance.data.datasets[0].data = prices;
                chartInstance.data.datasets[1].data = chartData.ema9_series;
                chartInstance.data.datasets[2].data = chartData.ema15_series;
                chartInstance.update('none');
            } else {
                chartInstance = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [
                            { label: 'Close', data: prices, borderColor: '#3b82f6', borderWidth: 1.5, pointRadius: 0 },
                            { label: 'EMA 9', data: chartData.ema9_series, borderColor: '#eab308', borderWidth: 1, pointRadius: 0 },
                            { label: 'EMA 15', data: chartData.ema15_series, borderColor: '#a855f7', borderWidth: 1, pointRadius: 0 }
                        ]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        scales: {
                            x: { grid: { color: '#1e293b' }, ticks: { color: '#94a3b8', maxTicksLimit: 8 } },
                            y: { grid: { color: '#1e293b' }, ticks: { color: '#94a3b8' } }
                        }
                    }
                });
            }
        }

        function renderChain(chain) {
            let tbody = document.getElementById('chainBody');
            tbody.innerHTML = chain.map(row => `
                <tr>
                    <td class="ce-side" onclick="selectSymbol('${currentSymbol.split('_')[0]}_${row.strike}_CE')" style="cursor:pointer;">${row.ce_ltp}</td>
                    <td style="font-weight:bold; background:#1e293b;">${row.strike}</td>
                    <td class="pe-side" onclick="selectSymbol('${currentSymbol.split('_')[0]}_${row.strike}_PE')" style="cursor:pointer;">${row.pe_ltp}</td>
                </tr>
            `).join('');
        }

        function renderPositions(posList) {
            let container = document.getElementById('positionsContainer');
            if (posList.length === 0) {
                container.innerHTML = '<div style="color: #6b7280; text-align: center; margin-top: 20px;">No open positions</div>';
                return;
            }
            container.innerHTML = posList.map(p => `
                <div style="background: #1e293b; padding: 8px; border-radius: 4px; margin-bottom: 6px; font-size: 11px;">
                    <div style="font-weight:bold; color: #f3f4f6;">${p.symbol} (${p.action})</div>
                    <div>Qty: ${p.qty} | Buy: ₹${p.buy_price}</div>
                    <div>PnL: <span style="color: ${p.pnl >= 0 ? '#34d399' : '#f87171'}">₹${p.pnl}</span></div>
                    <button onclick="exitPosition('${p.id}')" style="background:#dc2626; padding: 2px 6px; font-size: 10px; margin-top: 4px;">Exit</button>
                </div>
            `).join('');
        }

        function renderOrders(orders) {
            let container = document.getElementById('ordersContainer');
            container.innerHTML = orders.map(o => `
                <div style="border-bottom: 1px solid #1e293b; padding: 4px 0;">
                    <span style="color: #9ca3af;">${o.time}</span> - ${o.symbol} ${o.action}
                </div>
            `).join('');
        }

        async function placeOrder() {
            let action = document.getElementById('orderAction').value;
            let qty = parseInt(document.getElementById('orderQty').value);
            let orderType = document.getElementById('orderType').value;
            let sym = currentSymbol, strike = 0, type = "INDEX";
            if (sym.includes('_')) {
                let parts = sym.split('_');
                sym = parts[0]; strike = parseFloat(parts[1]); type = parts[2];
            }
            await fetch('/api/order', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol: sym, action: action, qty: qty, order_type: orderType, strike: strike, type: type })
            });
            fetchMarketData();
        }

        async function exitPosition(id) {
            await fetch('/api/exit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: id })
            });
            fetchMarketData();
        }

        async function resetAccount() {
            await fetch('/api/reset', { method: 'POST' });
            fetchMarketData();
        }

        setInterval(fetchMarketData, 1000);
        fetchMarketData();
    </script>
</body>
</html>
"""

def fetch_historical_candles(ticker_symbol, period="5d", interval="1m"):
    if not yf: return []
    try:
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(period=period, interval=interval)
        if df.empty: return []
        candles = []
        timestamps = df.index.astype(int) // 10**9
        opens, highs, lows, closes, volumes = df["Open"].values, df["High"].values, df["Low"].values, df["Close"].values, df["Volume"].values
        first_day = None
        for i in range(len(timestamps)):
            t = int(timestamps[i])
            dt = datetime.fromtimestamp(t, tz=timezone.utc).astimezone(IST)
            if not (dtime(9, 0) <= dt.time() <= dtime(15, 40)): continue
            day_str = dt.strftime("%Y-%m-%d")
            if first_day is None: first_day = day_str
            candles.append({
                "time": t, "date_label": dt.strftime("%d/%m %H:%M"), "is_prev_day": day_str != first_day,
                "open": round(float(opens[i]), 2), "high": round(float(highs[i]), 2),
                "low": round(float(lows[i]), 2), "close": round(float(closes[i]), 2),
                "volume": int(volumes[i]) if not math.isnan(volumes[i]) else 500
            })
        return candles
    except Exception: return []

def fetch_live_market_prices():
    if not yf: return None, None
    try:
        nifty_df = yf.Ticker("^NSEI").history(period="1d", interval="1m")
        sensex_df = yf.Ticker("^BSESN").history(period="1d", interval="1m")
        if not nifty_df.empty and not sensex_df.empty:
            return float(nifty_df["Close"].iloc[-1]), float(sensex_df["Close"].iloc[-1])
    except Exception: pass
    return None, None

def norm_pdf(x): return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)
def norm_cdf(x): return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def get_current_expiry_dates():
    today = datetime.now(IST).date()
    days_to_nifty = (3 - today.weekday() + 7) % 7
    if days_to_nifty == 0 and datetime.now(IST).time() > dtime(15, 30): days_to_nifty = 7
    nifty_expiry = today + timedelta(days=days_to_nifty)
    days_to_sensex = (4 - today.weekday() + 7) % 7
    if days_to_sensex == 0 and datetime.now(IST).time() > dtime(15, 30): days_to_sensex = 7
    sensex_expiry = today + timedelta(days=days_to_sensex)
    return nifty_expiry.strftime("%d %b %Y"), sensex_expiry.strftime("%d %b %Y")

def calc_black_scholes(spot, strike, dte_days=4.0, iv=0.14, r=0.06):
    if strike <= 0 or spot <= 0: return {"ce_ltp": 0.50, "pe_ltp": 0.50, "ce_delta": 0.0, "pe_delta": 0.0, "gamma": 0.0, "theta": 0.0}
    t = max(dte_days / 365.0, 0.0001)
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    nd1, nd2 = norm_cdf(d1), norm_cdf(d2)
    ce = spot * nd1 - strike * math.exp(-r * t) * nd2
    pe = strike * math.exp(-r * t) * norm_cdf(-d2) - spot * norm_cdf(-d1)
    return {
        "ce_ltp": max(round(ce, 2), 0.50), "pe_ltp": max(round(pe, 2), 0.50),
        "ce_delta": round(nd1, 3), "pe_delta": round(nd1 - 1.0, 3),
        "gamma": round(norm_pdf(d1) / (spot * iv * sqrt_t), 5),
        "theta": round((- (spot * norm_pdf(d1) * iv) / (2.0 * sqrt_t) - r * strike * math.exp(-r * t) * nd2) / 365.0, 2)
    }

def calc_ema_series(data, period):
    if not data: return []
    k = 2.0 / (period + 1)
    series = [data[0]]
    for p in data[1:]: series.append(round((p * k) + (series[-1] * (1.0 - k)), 2))
    return series

class SimulationState:
    def __init__(self):
        self.lock = threading.Lock()
        self.nifty_spot, self.sensex_spot = 23850.00, 81450.00
        self.nifty_base, self.sensex_base = 23826.75, 81400.00
        self.candles_nifty = fetch_historical_candles("^NSEI", period="5d", interval="1m")
        self.candles_sensex = fetch_historical_candles("^BSESN", period="5d", interval="1m")
        if not self.candles_nifty: self.candles_nifty = self._generate_fallback(self.nifty_spot)
        if not self.candles_sensex: self.candles_sensex = self._generate_fallback(self.sensex_spot)
        if self.candles_nifty: self.nifty_spot, self.nifty_base = self.candles_nifty[-1]["close"], self.candles_nifty[0]["open"]
        if self.candles_sensex: self.sensex_spot, self.sensex_base = self.candles_sensex[-1]["close"], self.candles_sensex[0]["open"]
        self.wallet = {"initial": 50000.0, "balance": 50000.0, "used_margin": 0.0, "realized_pnl": 0.0}
        self.positions, self.pending_orders, self.orders, self.closed_trades, self.sound_events = [], [], [], [], []

    def _generate_fallback(self, start_p):
        now, candles, cur = time.time(), [], start_p - 120.0
        for i in range(150):
            dt = datetime.fromtimestamp(now - (150 - i) * 60, tz=timezone.utc).astimezone(IST)
            o, c = cur, cur + random.uniform(-4, 4.5)
            candles.append({"time": int(dt.timestamp()), "date_label": dt.strftime("%d/%m %H:%M"), "is_prev_day": i < 75, "open": round(o, 2), "high": round(max(o, c) + 2, 2), "low": round(min(o, c) - 2, 2), "close": round(c, 2), "volume": random.randint(1500, 6000)})
            cur = c
        return candles

    def update_tick(self):
        with self.lock:
            ln, ls = fetch_live_market_prices()
            if ln and ls:
                self.nifty_spot, self.sensex_spot = round(ln, 2), round(ls, 2)
            else:
                step = random.gauss(0, 1.5)
                self.nifty_spot = round(self.nifty_spot + step, 2)
                self.sensex_spot = round(self.sensex_spot + step * 3.5, 2)
            now, dt = time.time(), datetime.now(IST)
            for cl, sv in [(self.candles_nifty, self.nifty_spot), (self.candles_sensex, self.sensex_spot)]:
                if cl:
                    last_c = cl[-1]
                    if now - last_c["time"] >= 60:
                        cl.append({"time": int(now), "date_label": dt.strftime("%d/%m %H:%M"), "is_prev_day": False, "open": sv, "high": sv, "low": sv, "close": sv, "volume": random.randint(200, 600)})
                        if len(cl) > 600: cl.pop(0)
                    else:
                        last_c["high"], last_c["low"], last_c["close"] = max(last_c["high"], sv), min(last_c["low"], sv), sv

    def get_instrument_chart_data(self, symbol, timeframe="1m"):
        is_sensex = "SENSEX" in symbol
        raw_candles = self.candles_sensex if is_sensex else self.candles_nifty
        curr_spot = self.sensex_spot if is_sensex else self.nifty_spot
        if symbol in ["NIFTY", "SENSEX"] or "_" not in symbol:
            display_title = "SENSEX" if is_sensex else "NIFTY 50"
            ltp, greeks = curr_spot, {"delta": 1.0, "gamma": 0.0, "theta": 0.0}
        else:
            parts = symbol.split("_")
            strike, opt_type = float(parts[1]), parts[2]
            ne, se = get_current_expiry_dates()
            display_title = f"{'SENSEX' if is_sensex else 'NIFTY'} {int(strike)} {opt_type} ({se if is_sensex else ne})"
            iv_val = 0.13 if is_sensex else 0.14
            processed = []
            for sc in raw_candles:
                bs_o = calc_black_scholes(sc["open"], strike, iv=iv_val)[f"{opt_type.lower()}_ltp"]
                bs_c = calc_black_scholes(sc["close"], strike, iv=iv_val)[f"{opt_type.lower()}_ltp"]
                processed.append({"time": sc["time"], "date_label": sc["date_label"], "is_prev_day": sc["is_prev_day"], "open": bs_o, "high": max(bs_o, bs_c)+1, "low": max(0.50, min(bs_o, bs_c)-1), "close": bs_c, "volume": sc["volume"]})
            raw_candles = processed
            opt_cur = calc_black_scholes(curr_spot, strike, iv=iv_val)
            ltp = opt_cur["ce_ltp"] if opt_type == "CE" else opt_cur["pe_ltp"]
            greeks = {"delta": opt_cur["ce_delta"] if opt_type == "CE" else opt_cur["pe_delta"], "gamma": opt_cur["gamma"], "theta": opt_cur["theta"]}
        
        closes = [c["close"] for c in raw_candles]
        volumes = [c["volume"] for c in raw_candles]
        ema9, ema15 = calc_ema_series(closes, 9), calc_ema_series(closes, 15)
        vwap = round(sum(closes[i]*volumes[i] for i in range(len(closes))) / sum(volumes), 2) if sum(volumes) > 0 else (closes[-1] if closes else 0)
        
        now_ist = datetime.now(IST)
        diff = int((now_ist.replace(hour=15, minute=40, second=0) - now_ist).total_seconds())
        cd = f"{diff//60:02d}:{diff%60:02d}" if diff > 0 else "CLOSED"
        
        return {"symbol": symbol, "display_title": display_title, "timeframe": timeframe, "ltp": ltp, "candles": raw_candles, "countdown": cd, "ema9": ema9[-1] if ema9 else 0, "ema15": ema15[-1] if ema15 else 0, "vwap": vwap, "ema9_series": ema9, "ema15_series": ema15, "greeks": greeks}

    def get_option_chain(self, symbol="NIFTY"):
        is_sensex = "SENSEX" in symbol
        curr_spot = self.sensex_spot if is_sensex else self.nifty_spot
        step_val = 100 if is_sensex else 50
        atm = round(curr_spot / step_val) * step_val
        iv_val = 0.13 if is_sensex else 0.14
        chain = []
        for s in [atm + (i * step_val) for i in range(-8, 9)]:
            g = calc_black_scholes(curr_spot, s, iv=iv_val)
            chain.append({"strike": s, "ce_ltp": g["ce_ltp"], "ce_delta": g["ce_delta"], "pe_ltp": g["pe_ltp"], "pe_delta": g["pe_delta"]})
        return chain

state = SimulationState()
threading.Thread(target=lambda: [state.update_tick() or time.sleep(1.0) while True], daemon=True).start()

class ThreadedHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

class DhanSimHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ["/", "/index.html"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
        elif parsed.path == "/api/market":
            params = parse_qs(parsed.query)
            sym = params.get("symbol", ["NIFTY"])[0]
            tf = params.get("tf", ["1m"])[0]
            with state.lock:
                ne, se = get_current_expiry_dates()
                resp = {
                    "nifty_spot": state.nifty_spot, "sensex_spot": state.sensex_spot,
                    "nifty_chg": round(state.nifty_spot - state.nifty_base, 2), "nifty_pct": round(((state.nifty_spot - state.nifty_base)/state.nifty_base)*100, 2),
                    "sensex_chg": round(state.sensex_spot - state.sensex_base, 2), "sensex_pct": round(((state.sensex_spot - state.sensex_base)/state.sensex_base)*100, 2),
                    "nifty_expiry": ne, "sensex_expiry": se,
                    "chart": state.get_instrument_chart_data(sym, tf), "chain": state.get_option_chain(sym),
                    "wallet": {**state.wallet, "net_pnl": state.wallet["realized_pnl"]},
                    "positions": state.positions, "pending_orders": state.pending_orders, "orders": state.orders, "closed_trades": state.closed_trades[:15]
                }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode("utf-8"))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
        if urlparse(self.path).path == "/api/order":
            sym, qty, action = body.get("symbol"), int(body.get("qty", 65)), body.get("action", "BUY")
            cs = state.sensex_spot if "SENSEX" in sym else state.nifty_spot
            with state.lock:
                state.positions.append({"id": f"POS_{int(time.time()*1000)}", "symbol": sym, "action": action, "qty": qty, "buy_price": cs, "pnl": 0.0})
                state.orders.insert(0, {"time": time.strftime("%H:%M:%S"), "symbol": sym, "action": f"{action} @ {cs}"})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "SUCCESS"}).encode("utf-8"))

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8000))
    with ThreadedHTTPServer(("0.0.0.0", PORT), DhanSimHandler) as httpd:
        httpd.serve_forever()
