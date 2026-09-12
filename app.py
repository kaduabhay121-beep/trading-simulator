import ssl
import urllib.request
import urllib.error
import http.server
import socketserver
import json
import math
import random
import time
import threading
import os
import base64
import struct
import hmac
import hashlib
import sqlite3
from datetime import datetime, time as dtime, timedelta
import pytz
from urllib.parse import urlparse, parse_qs

IST = pytz.timezone('Asia/Kolkata')
ANGEL_ROOT = 'https://apiconnect.angelone.in'
MASTER_URL = 'https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json'
QUOTE_URL = ANGEL_ROOT + '/rest/secure/angelbroking/market/v1/quote/'
LOGIN_URL = ANGEL_ROOT + '/rest/auth/angelbroking/user/v1/loginByPassword'
CANDLE_URL = ANGEL_ROOT + '/rest/secure/angelbroking/historical/v1/getCandleData'
GREEK_URL = ANGEL_ROOT + '/rest/secure/angelbroking/marketData/v1/optionGreek'
WS_URL = 'wss://smartapisocket.angelone.in/smart-stream'


def is_market_open():
    now = datetime.now(IST)
    return now.weekday() < 5 and dtime(9, 15) <= now.time() <= dtime(15, 30)


def get_available_expiries(is_sensex=False):
    now = datetime.now(IST)
    target_weekday = 4 if is_sensex else 1
    expiries = []
    curr = now
    while len(expiries) < 5:
        days_ahead = (target_weekday - curr.weekday()) % 7
        if days_ahead == 0 and curr.time() > dtime(15, 30): days_ahead = 7
        exp_date = curr + timedelta(days=days_ahead)
        s = exp_date.strftime('%d%b%y').upper()
        if s not in expiries: expiries.append(s)
        curr = exp_date + timedelta(days=1)
    return expiries


def get_dte_from_expiry(expiry_str):
    try:
        exp_date = datetime.strptime(expiry_str, '%d%b%y')
        target = IST.localize(exp_date.replace(hour=15, minute=30))
        return max((target - datetime.now(IST)).total_seconds() / 86400.0, 0.08)
    except Exception:
        return 1.1


def norm_pdf(x): return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)
def norm_cdf(x): return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def calc_deep_greeks(spot, strike, dte_days, iv=14.3, r=0.065, is_sensex=False):
    t = max(dte_days / 365.0, 0.0001); sqrt_t = math.sqrt(t)
    vol = max(float(iv) / 100.0 if float(iv) > 1 else float(iv), 0.05)
    basis = (140.0 if is_sensex else 46.5) * max(min(dte_days / 1.15, 2.5), 0.2)
    F = spot + basis
    d1 = (math.log(max(F, 0.01) / max(strike, 0.01)) + 0.5 * vol * vol * t) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t; nd1 = norm_cdf(d1); nd2 = norm_cdf(d2); pdf = norm_pdf(d1); df = math.exp(-r*t)
    ce = max(df * (F*nd1 - strike*nd2), 0.05)
    pe = max(df * (strike*norm_cdf(-d2) - F*norm_cdf(-d1)), 0.05)
    return {'ce_ltp':round(ce,2),'pe_ltp':round(pe,2),'ce_delta':round(nd1,3),'pe_delta':round(nd1-1,3),
            'gamma':round(pdf/(spot*vol*sqrt_t),6),'theta':round((-(spot*pdf*vol)/(2*sqrt_t)-r*strike*df*nd2)/365,2),
            'vega':round((spot*sqrt_t*pdf)/100,2),'iv':round(vol*100,1)}


def calc_vwap_series(candles):
    if not candles: return []
    out=[]; pv=0.0; vol=0
    for c in candles:
        v=max(int(c.get('volume',0)),1); pv += ((c['high']+c['low']+c['close'])/3)*v; vol += v; out.append(round(pv/vol,2))
    return out


def calc_ema_series(data, period):
    if not data: return []
    k=2/(period+1); out=[data[0]]
    for p in data[1:]: out.append(round(p*k + out[-1]*(1-k),2))
    return out


def _totp(secret, digits=6, period=30):
    secret = ''.join(str(secret).split()).upper()
    key = base64.b32decode(secret + '='*((8-len(secret)%8)%8), casefold=True)
    counter = int(time.time()) // period
    msg = struct.pack('>Q', counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    off = digest[-1] & 15
    code = (struct.unpack('>I', digest[off:off+4])[0] & 0x7fffffff) % (10**digits)
    return str(code).zfill(digits)


class AngelOneData:
    def __init__(self):
        self.enabled = os.getenv('ANGELONE_ENABLED','0').lower() in ('1','true','yes','on')
        self.api_key = os.getenv('ANGELONE_API_KEY','').strip()
        self.client_code = os.getenv('ANGELONE_CLIENT_CODE','').strip()
        self.pin = os.getenv('ANGELONE_PIN','').strip()
        self.totp_secret = os.getenv('ANGELONE_TOTP_SECRET','').strip()
        self.jwt = os.getenv('ANGELONE_JWT_TOKEN','').strip()
        self.feed_token = os.getenv('ANGELONE_FEED_TOKEN','').strip()
        self.local_ip = os.getenv('ANGELONE_CLIENT_LOCAL_IP','127.0.0.1')
        self.public_ip = os.getenv('ANGELONE_CLIENT_PUBLIC_IP','0.0.0.0')
        self.mac = os.getenv('ANGELONE_MAC','00:00:00:00:00:00')
        self.instruments = []
        self.instrument_by_key = {}
        self.quote_cache = {}
        self.quote_cache_ts = {}
        self.greek_cache = {}
        self.greek_cache_ts = {}
        self.last_quote = 0
        self.last_candle = 0
        self.candle_cache = {}
        self.candle_cache_ttl = 300.0
        self.last_master = 0
        self.last_error = ''
        self.lock = threading.Lock()
        self.ws = None
        self.ws_thread = None
        self.ws_lock = threading.Lock()
        self.ws_tokens = set()
        self.ws_token_inst = {}
        self.ws_ready = False
        self.ws_error = ''
        self.option_lookup_cache = {}
        self._load_master()
        if self.enabled:
            if self.login():
                self._start_websocket()

    def _request(self, url, payload=None, headers=None, timeout=12):
        data = None if payload is None else json.dumps(payload).encode()
        h = {'Content-Type':'application/json','Accept':'application/json', **(headers or {})}
        req=urllib.request.Request(url,data=data,headers=h,method='POST' if payload is not None else 'GET')
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw=r.read().decode(errors='replace')
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            body=e.read().decode(errors='replace')
            try:
                detail=json.loads(body)
                msg=detail.get('message') or detail.get('errorcode') or body[:300]
            except Exception:
                msg=body[:300] or str(e)
            raise RuntimeError(f'Angel One HTTP {e.code}: {msg}')
        except urllib.error.URLError as e:
            raise RuntimeError(f'Angel One network error: {e.reason}')

    def _auth_headers(self):
        return {'Authorization': self.jwt if self.jwt.startswith('Bearer ') else ('Bearer '+self.jwt),
                'X-PrivateKey':self.api_key,'X-UserType':'USER','X-SourceID':'WEB',
                'X-ClientLocalIP':self.local_ip,'X-ClientPublicIP':self.public_ip,'X-MACAddress':self.mac}

    def login(self):
        if not all([self.api_key,self.client_code,self.pin,self.totp_secret]):
            self.last_error='Angel One credentials are incomplete'
            return False
        try:
            body={'clientcode':self.client_code,'password':self.pin,'totp':_totp(self.totp_secret)}
            h={'X-PrivateKey':self.api_key,'X-UserType':'USER','X-SourceID':'WEB','X-ClientLocalIP':self.local_ip,'X-ClientPublicIP':self.public_ip,'X-MACAddress':self.mac}
            res=self._request(LOGIN_URL,body,h)
            if res.get('status') and res.get('data'):
                self.jwt=res['data'].get('jwtToken',''); self.feed_token=res['data'].get('feedToken','')
                self.last_error=''; return True
            self.last_error=res.get('message') or res.get('errorcode') or 'Angel login failed'
        except Exception as e: self.last_error=str(e)
        return False

    def _load_master(self):
        path=os.getenv('ANGELONE_MASTER_CACHE','angel_master.json')
        try:
            if os.path.exists(path) and time.time()-os.path.getmtime(path)<86400:
                with open(path,'r',encoding='utf-8') as f: self.instruments=json.load(f)
            else:
                req=urllib.request.Request(MASTER_URL,headers={'User-Agent':'Mozilla/5.0'})
                with urllib.request.urlopen(req,timeout=30) as r: raw=r.read()
                self.instruments=json.loads(raw.decode('utf-8'))
                with open(path,'w',encoding='utf-8') as f: json.dump(self.instruments,f)
            for x in self.instruments:
                key=(str(x.get('exch_seg','')).upper(),str(x.get('symbol','')).upper())
                self.instrument_by_key[key]=x
            self.last_master=time.time()
        except Exception as e:
            self.last_error='Instrument master: '+str(e)

    def find_index(self, name):
        name=name.upper().replace(' 50','').strip()
        # Instrument master uses NSE/BSE for cash indices; older builds incorrectly
        # searched for nse_cm/bse_cm, which can make historical lookups fail.
        exch = 'BSE' if name == 'SENSEX' else 'NSE'
        candidates=[x for x in self.instruments
                    if str(x.get('exch_seg','')).upper()==exch
                    and str(x.get('instrumenttype','')).upper() in ('INDEX','AMXIDX','')
                    and (str(x.get('symbol','')).upper()==name or str(x.get('name','')).upper()==name or
                         str(x.get('symbol','')).upper().startswith(name))]
        if candidates:
            return candidates[0]
        fallback={'NIFTY':os.getenv('ANGELONE_NIFTY_TOKEN','99926000'),'SENSEX':os.getenv('ANGELONE_SENSEX_TOKEN','99919000')}
        tok=fallback.get(name)
        return {'token':tok,'symbol':name,'name':name,'exch_seg':exch,'instrumenttype':'INDEX','lotsize':'1'} if tok else None

    def quote(self, items, mode='FULL'):
        if not self.enabled or not self.jwt: return {}
        now=time.time(); result={}; missing=[]
        for x in items:
            token=str(x['token'])
            cached=self.quote_cache.get(token)
            if cached is not None and now-self.quote_cache_ts.get(token,0) < 2.0:
                result[token]=cached
            else:
                missing.append(x)
        groups={}
        for x in missing:
            groups.setdefault(x['exch_seg'].upper(),[]).append(str(x['token']))
        for exch,tokens in groups.items():
            for i in range(0,len(tokens),50):
                payload={'mode':mode,'exchangeTokens':{exch:tokens[i:i+50]}}
                try:
                    res=self._request(QUOTE_URL,payload,self._auth_headers())
                    for q in (res.get('data') or {}).get('fetched',[]) if isinstance(res.get('data'),dict) else []:
                        token=str(q.get('symbolToken')); result[token]=q; self.quote_cache[token]=q; self.quote_cache_ts[token]=now
                except Exception as e: self.last_error=str(e)
        return result

    def _last_market_close(self):
        now=datetime.now(IST)
        # Historical API is more reliable when the end time is a completed
        # market session instead of a weekend/future timestamp.
        if now.weekday() >= 5 or now.time() < dtime(9,15):
            d=now-timedelta(days=1)
            while d.weekday() >= 5: d -= timedelta(days=1)
            return d.replace(hour=15,minute=30,second=0,microsecond=0)
        if now.time() > dtime(15,30):
            return now.replace(hour=15,minute=30,second=0,microsecond=0)
        return now.replace(second=0,microsecond=0)

    def candles(self, inst, interval='ONE_MINUTE', days=2):
        if not self.enabled or not self.jwt or not inst: return []
        max_days={'ONE_MINUTE':30,'THREE_MINUTE':60,'FIVE_MINUTE':100,'TEN_MINUTE':100,'FIFTEEN_MINUTE':200,'THIRTY_MINUTE':200,'ONE_HOUR':400,'ONE_DAY':2000}.get(interval,30)
        days=max(1,min(int(days),max_days)); end=self._last_market_close(); start=end-timedelta(days=days)
        key=(str(inst.get('exch_seg','NSE')).upper(),str(inst.get('token')),interval,days,end.strftime('%Y-%m-%d %H:%M'))
        now=time.time(); cached=self.candle_cache.get(key)
        if cached and now-cached.get('ts',0)<self.candle_cache_ttl: return [dict(x) for x in cached['rows']]
        body={'exchange':str(inst.get('exch_seg','NSE')).upper(),'symboltoken':str(inst['token']),'interval':interval,'fromdate':start.strftime('%Y-%m-%d %H:%M'),'todate':end.strftime('%Y-%m-%d %H:%M')}
        try:
            with self.lock:
                wait=max(0.0,0.45-(time.time()-self.last_candle))
                if wait: time.sleep(wait)
                res=self._request(CANDLE_URL,body,self._auth_headers()); self.last_candle=time.time()
            if not res.get('status',True): raise RuntimeError(res.get('message') or res.get('errorcode') or 'Historical candle request failed')
            out=[]
            for r in (res.get('data') or []):
                if len(r)<6: continue
                ts=r[0]
                if isinstance(ts,str):
                    dt=datetime.fromisoformat(ts.replace('Z','+00:00'))
                    if dt.tzinfo is None: dt=IST.localize(dt)
                    ts=int(dt.timestamp())
                else: ts=int(ts)
                out.append({'time':ts,'is_prev_day':False,'open':float(r[1]),'high':float(r[2]),'low':float(r[3]),'close':float(r[4]),'volume':int(float(r[5] or 0))})
            if not out: raise RuntimeError(f'No historical candles returned for {body["exchange"]} token {body["symboltoken"]} ({body["fromdate"]} → {body["todate"]})')
            self.candle_cache[key]={'ts':time.time(),'rows':[dict(x) for x in out]}; self.last_error=''; return out
        except Exception as e:
            self.last_error=str(e)
            if cached and cached.get('rows'): return [dict(x) for x in cached['rows']]
            return []

    def option_instruments(self, underlying, expiry):
        u=underlying.upper(); segment='BFO' if u=='SENSEX' else 'NFO'
        key=(u, str(expiry or '').upper())
        cached=self.option_lookup_cache.get(key)
        if cached is not None:
            return cached
        out=[x for x in self.instruments if str(x.get('exch_seg','')).upper()==segment and str(x.get('instrumenttype','')).upper()=='OPTIDX' and str(x.get('name','')).upper()==u and str(x.get('expiry','')).upper()==key[1] and str(x.get('symbol','')).upper().endswith(('CE','PE'))]
        self.option_lookup_cache[key]=out
        return out

    def greek(self, underlying, expiry):
        if not self.enabled or not self.jwt: return {}
        key=(underlying.upper(),expiry.upper()); now=time.time()
        if key in self.greek_cache and now-self.greek_cache_ts.get(key,0) < 10.0:
            return self.greek_cache[key]
        try:
            res=self._request(GREEK_URL,{'name':underlying,'expirydate':expiry},self._auth_headers())
            out={}
            for x in res.get('data') or []: out[(float(x['strikePrice']),x['optionType'])]=x
            self.greek_cache[key]=out; self.greek_cache_ts[key]=now; return out
        except Exception as e: self.last_error=str(e); return self.greek_cache.get(key,{})

    def _decode_ws_tick(self, message):
        if not isinstance(message, (bytes, bytearray)) or len(message) < 51:
            return None
        try:
            mode = message[0]
            exch = message[1]
            token = message[2:27].decode('utf-8', errors='ignore').strip('\x00').strip()
            ltp_raw = struct.unpack('<q', message[43:51])[0]
            ltp = ltp_raw / 100.0
            return {'mode': mode, 'exchange_type': exch, 'token': token, 'ltp': ltp, 'raw': message}
        except Exception:
            return None

    def _start_websocket(self):
        if not self.enabled or not self.jwt or not self.feed_token:
            return
        try:
            import websocket
        except Exception as e:
            self.ws_error = 'websocket-client unavailable: ' + str(e)
            return

        def run():
            while self.enabled:
                try:
                    url = f"{WS_URL}?clientCode={self.client_code}&feedToken={self.feed_token}&apiKey={self.api_key}"
                    ws = websocket.WebSocket()
                    ws.connect(url, timeout=10, origin='https://smartapi.angelone.in')
                    ws.settimeout(1.0)
                    last_heartbeat = time.time()
                    with self.ws_lock:
                        self.ws = ws
                        self.ws_ready = True
                        self.ws_error = ''
                        self.ws_tokens.clear()
                    # Core indices are always streamed. LTP mode = 1.
                    self._ws_subscribe([('1', str(x['token'])) for x in [self.find_index('NIFTY'), self.find_index('SENSEX')] if x])
                    while self.enabled:
                        try:
                            try:
                                msg = ws.recv()
                            except websocket.WebSocketTimeoutException:
                                if time.time() - last_heartbeat >= 20:
                                    ws.send('ping')
                                    last_heartbeat = time.time()
                                continue
                            if msg is None:
                                break
                            if isinstance(msg, str):
                                if msg.lower() == 'pong':
                                    continue
                                continue
                            tick = self._decode_ws_tick(msg)
                            if tick:
                                token = tick['token']; ltp = tick['ltp']
                                with self.lock:
                                    cached = self.quote_cache.get(token, {})
                                    cached = dict(cached) if isinstance(cached, dict) else {}
                                    cached['symbolToken'] = token
                                    cached['ltp'] = ltp
                                    self.quote_cache[token] = cached
                                    self.quote_cache_ts[token] = time.time()
                                    for name, key in [('NIFTY','nifty'),('SENSEX','sensex')]:
                                        inst = self.find_index(name)
                                        if inst and str(inst.get('token')) == token:
                                            cached['close'] = cached.get('close', 0)
                        except Exception:
                            break
                except Exception as e:
                    self.ws_error = str(e)
                finally:
                    with self.ws_lock:
                        self.ws_ready = False
                        self.ws = None
                    time.sleep(2)
        self.ws_thread = threading.Thread(target=run, daemon=True, name='angel-ws')
        self.ws_thread.start()

    def _ws_subscribe(self, token_pairs):
        if not token_pairs:
            return
        with self.ws_lock:
            ws = self.ws
            ready = self.ws_ready
        if not ws or not ready:
            return
        grouped = {}
        for exch, token in token_pairs:
            if not token or (exch, token) in self.ws_tokens:
                continue
            grouped.setdefault(exch, []).append(token)
        if not grouped:
            return
        payload = {
            'correlationID': 'paperfeed',
            'action': 1,
            'params': {
                'mode': 1,
                'tokenList': [{'exchangeType': int(ex), 'tokens': toks} for ex, toks in grouped.items()]
            }
        }
        try:
            ws.send(json.dumps(payload))
            for ex, toks in grouped.items():
                for tok in toks:
                    self.ws_tokens.add((ex, tok))
                    self.ws_token_inst[(ex, tok)] = tok
        except Exception as e:
            self.ws_error = str(e)

    def subscribe_instruments(self, insts):
        pairs=[]
        exch_map={'NSE':'1','NSE_CM':'1','NFO':'2','BSE':'3','BSE_CM':'3','BFO':'4','MCX':'5'}
        for inst in insts or []:
            if not inst: continue
            ex=exch_map.get(str(inst.get('exch_seg','')).upper())
            if ex and inst.get('token') is not None:
                pairs.append((ex,str(inst.get('token'))))
        self._ws_subscribe(pairs)

    def subscribe_instrument(self, inst):
        if inst:
            exch_map={'NSE': '1', 'NSE_CM': '1', 'NFO': '2', 'BSE': '3', 'BSE_CM': '3', 'BFO': '4'}
            ex=exch_map.get(str(inst.get('exch_seg','')).upper())
            if ex:
                self._ws_subscribe([(ex, str(inst.get('token')))])

    def websocket_ltp(self, inst):
        if not inst:
            return None
        token=str(inst.get('token'))
        q=self.quote_cache.get(token)
        ts=self.quote_cache_ts.get(token,0)
        if q and time.time()-ts < 5:
            try:
                return float(q.get('ltp'))
            except Exception:
                pass
        self.subscribe_instrument(inst)
        return None

    def refresh_quotes(self, instruments):
        q=self.quote(instruments,'FULL')
        with self.lock: self.quote_cache.update(q)
        return q



def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    gains=[]; losses=[]
    for i in range(1,len(closes)):
        d=closes[i]-closes[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains[-period:])/period; al=sum(losses[-period:])/period
    if al == 0: return 100.0
    return round(100 - (100/(1 + ag/al)),2)

def strategy_signal(candles, strategy):
    if len(candles) < 25: return 'HOLD', 0.0, {}
    closes=[float(c['close']) for c in candles]
    ema9=calc_ema_series(closes,9)[-1]; ema15=calc_ema_series(closes,15)[-1]
    rsi=calc_rsi(closes,14)
    recent=candles[-20:]; vwap=calc_vwap_series(recent)[-1] if recent else closes[-1]
    high20=max(float(c['high']) for c in candles[-21:-1]); low20=min(float(c['low']) for c in candles[-21:-1])
    last=closes[-1]
    if strategy=='EMA_CROSS':
        prev9=calc_ema_series(closes[:-1],9)[-1]; prev15=calc_ema_series(closes[:-1],15)[-1]
        if prev9<=prev15 and ema9>ema15: return 'BUY',0.80,{'ema9':ema9,'ema15':ema15}
        if prev9>=prev15 and ema9<ema15: return 'SELL',0.80,{'ema9':ema9,'ema15':ema15}
    elif strategy=='RSI_MEAN_REVERT':
        if rsi<=30 and last>closes[-2]: return 'BUY',0.75,{'rsi':rsi}
        if rsi>=70 and last<closes[-2]: return 'SELL',0.75,{'rsi':rsi}
    elif strategy=='VWAP_REVERT':
        if last < vwap*0.998 and last>closes[-2]: return 'BUY',0.72,{'vwap':vwap}
        if last > vwap*1.002 and last<closes[-2]: return 'SELL',0.72,{'vwap':vwap}
    elif strategy=='BREAKOUT':
        if last>high20: return 'BUY',0.82,{'breakout':high20}
        if last<low20: return 'SELL',0.82,{'breakout':low20}
    elif strategy=='RBS':
        # Resistance -> Support: the previous candle broke the prior 20-bar resistance;
        # the current candle retests that level and closes back above it.
        level=max(float(c['high']) for c in candles[-21:-1])
        # Do not include the breakout candle itself in the resistance calculation.
        if len(candles)>=23:
            level=max(float(c['high']) for c in candles[-22:-2])
        prev=float(candles[-2]['close']); cur=candles[-1]
        if prev>level and float(cur['low'])<=level*1.001 and float(cur['close'])>level:
            return 'BUY',0.78,{'level':level,'setup':'RBS'}
    elif strategy=='SBR':
        # Support -> Resistance: the previous candle broke prior 20-bar support;
        # the current candle retests that level and closes back below it.
        level=min(float(c['low']) for c in candles[-21:-1])
        if len(candles)>=23:
            level=min(float(c['low']) for c in candles[-22:-2])
        prev=float(candles[-2]['close']); cur=candles[-1]
        if prev<level and float(cur['high'])>=level*0.999 and float(cur['close'])<level:
            return 'SELL',0.78,{'level':level,'setup':'SBR'}
    return 'HOLD',0.0,{'rsi':rsi,'ema9':ema9,'ema15':ema15,'vwap':vwap}


STRATEGIES=('EMA_CROSS','RSI_MEAN_REVERT','VWAP_REVERT','BREAKOUT','RBS','SBR')
TF_INTERVALS={60:'ONE_MINUTE',180:'THREE_MINUTE',300:'FIVE_MINUTE',600:'TEN_MINUTE',900:'FIFTEEN_MINUTE',1800:'THIRTY_MINUTE',3600:'ONE_HOUR'}

def _rr_ratio(sl_pct,tp_pct):
    try: return round(float(tp_pct)/max(float(sl_pct),0.0001),2)
    except Exception: return 0.0

def _session_date(ts):
    return datetime.fromtimestamp(int(ts),IST).date()

def _is_cas_window(ts):
    dt=datetime.fromtimestamp(int(ts),IST)
    return dt.weekday()<5 and dtime(15,15)<=dt.time()<dtime(15,35)

def _is_preopen_window(ts):
    dt=datetime.fromtimestamp(int(ts),IST)
    return dt.weekday()<5 and dtime(9,0)<=dt.time()<dtime(9,15)


def backtest_strategy(candles, strategy, sl_pct=0.6, tp_pct=1.2, starting_balance=1000000.0, qty=1, trade_type='INTRADAY'):
    """Deterministic long-only candle backtest. Intraday never carries overnight; BTST does."""
    candles=_align_candles(candles)
    if len(candles) < 40:
        return {'ok':False,'error':'Need at least 40 candles'}
    balance=float(starting_balance); peak=balance; max_dd=0.0; trades=[]; position=None; wins=losses=0
    for i in range(30, len(candles)-1):
        c=candles[i]; nxt=candles[i+1]
        if position:
            if _session_date(int(nxt['time']))!=position['session']:
                if trade_type=='INTRADAY':
                    exitc=c; exitp=float(exitc['close']); reason='EOD'
                else:
                    exitc=c; exitp=float(c['open']); reason='BTST/NEXT_OPEN'
                pnl=(exitp-position['entry'])*position['qty']; balance+=pnl
                trades.append({'entry_time':position['time'],'exit_time':exitc['time'],'entry':round(position['entry'],2),'exit':round(exitp,2),'qty':position['qty'],'pnl':round(pnl,2),'reason':reason,'rr':_rr_ratio(sl_pct,tp_pct),'session':trade_type})
                wins += pnl>0; losses += pnl<=0; position=None; peak=max(peak,balance); max_dd=max(max_dd,peak-balance)
                continue
            hit_sl=float(nxt['low']) <= position['sl']; hit_tp=float(nxt['high']) >= position['tp']
            if hit_sl or hit_tp:
                exit_price=position['sl'] if hit_sl else position['tp']; pnl=(exit_price-position['entry'])*position['qty']
                balance += pnl; trades.append({'entry_time':position['time'],'exit_time':nxt['time'],'entry':round(position['entry'],2),'exit':round(exit_price,2),'qty':position['qty'],'pnl':round(pnl,2),'reason':'SL' if hit_sl else 'TARGET','rr':_rr_ratio(sl_pct,tp_pct),'session':trade_type})
                wins += pnl>0; losses += pnl<=0; position=None; peak=max(peak,balance); max_dd=max(max_dd,peak-balance); continue
        signal,conf,_=strategy_signal(candles[:i+1],strategy)
        if position is None and signal=='BUY' and conf>=0.70:
            if trade_type=='INTRADAY' and _session_date(int(nxt['time']))!=_session_date(int(c['time'])): continue
            if trade_type=='BTST' and datetime.fromtimestamp(int(c['time']),IST).time() < dtime(15,0): continue
            entry=float(nxt['open']); position={'time':nxt['time'],'entry':entry,'sl':entry*(1-sl_pct/100),'tp':entry*(1+tp_pct/100),'qty':qty,'session':_session_date(int(nxt['time']))}
    if position:
        exitc=candles[-1]; exit_price=float(exitc['close']); pnl=(exit_price-position['entry'])*position['qty']; balance+=pnl
        trades.append({'entry_time':position['time'],'exit_time':exitc['time'],'entry':round(position['entry'],2),'exit':round(exit_price,2),'qty':position['qty'],'pnl':round(pnl,2),'reason':'EOD' if trade_type=='INTRADAY' else 'BTST/EOD','rr':_rr_ratio(sl_pct,tp_pct),'session':trade_type})
        wins += pnl>0; losses += pnl<=0; peak=max(peak,balance); max_dd=max(max_dd,peak-balance)
    net=balance-starting_balance; gross_profit=sum(max(0,t['pnl']) for t in trades); gross_loss=sum(-min(0,t['pnl']) for t in trades)
    pf=(gross_profit/gross_loss) if gross_loss else (999.0 if gross_profit else 0.0)
    avg_r=sum((t['pnl']/(max(sl_pct/100*max(t['entry']*t['qty'],1),0.01))) for t in trades)/len(trades) if trades else 0.0
    return {'ok':True,'strategy':strategy,'trade_type':trade_type,'candles':len(candles),'trades':len(trades),'wins':int(wins),'losses':int(losses),'win_rate':round((wins/len(trades)*100),1) if trades else 0.0,'net_pnl':round(net,2),'return_pct':round(net/starting_balance*100,2),'profit_factor':round(pf,2),'max_drawdown':round(max_dd,2),'rr_ratio':_rr_ratio(sl_pct,tp_pct),'avg_r':round(avg_r,3),'qty':qty,'trades_detail':trades}

def _align_candles(rows):
    return sorted([dict(x) for x in (rows or [])], key=lambda x:int(x.get('time',0)))

def _option_backtest(strategy, underlying, option_mode, days, sl_pct, tp_pct, starting_balance, angel, timeframe=60, lot_multiplier=1, trade_type='INTRADAY', shared_cache=None, base_override=None):
    inst=angel.find_index(underlying); interval=TF_INTERVALS.get(int(timeframe),'ONE_MINUTE')
    base=_align_candles(base_override if base_override is not None else (angel.candles(inst,interval,days) if inst else []))
    if len(base)<40: return {'ok':False,'error':'Not enough historical underlying candles for replay'}
    balance=float(starting_balance); peak=balance; max_dd=0.0; trades=[]; position=None; wins=losses=0; cache=shared_cache if shared_cache is not None else {}
    def contract_for(spot, ts):
        exps=[]; seg='BFO' if underlying=='SENSEX' else 'NFO'; day=_session_date(ts)
        for x in angel.instruments:
            if str(x.get('exch_seg','')).upper()==seg and str(x.get('name','')).upper()==underlying and str(x.get('instrumenttype','')).upper()=='OPTIDX' and x.get('expiry'):
                e=str(x.get('expiry')).upper()
                try:
                    dt=datetime.strptime(e,'%d%b%Y') if len(e)==9 else datetime.strptime(e,'%d%b%y')
                    if dt.date()>=day: exps.append((dt,e))
                except: pass
        if not exps: return None
        exps.sort(); exp=exps[0][1]; step=100 if underlying=='SENSEX' else 50; atm=round(float(spot)/step)*step
        import re
        mm=re.match(r'^(ITM|OTM)_(\d+)$',str(option_mode))
        if mm:
            n=max(1,min(int(mm.group(2)),5)); atm += step*n if mm.group(1)=='OTM' else -step*n
        arr=angel.option_instruments(underlying,exp)
        candidates=[i for i in arr if str(i.get('symbol','')).upper().endswith('CE')]
        return min(candidates,key=lambda i:abs(float(i.get('strike',0))/100-atm),default=None)
    def option_series(inst2):
        tok=str(inst2.get('token')); key=(tok,int(timeframe))
        if key not in cache: cache[key]=_align_candles(angel.candles(inst2,interval,days))
        return cache[key]
    def at_or_after(series, ts):
        lo,hi=0,len(series)-1; ans=None
        while lo<=hi:
            mid=(lo+hi)//2
            if int(series[mid]['time'])>=ts: ans=series[mid]; hi=mid-1
            else: lo=mid+1
        return ans
    for i in range(30,len(base)-1):
        c=base[i]; nxt=base[i+1]
        if position:
            if _session_date(int(c['time']))!=position['session']:
                if trade_type=='INTRADAY':
                    prev=at_or_after(position['series'],int(position['eod_ts']))
                    if not prev: prev=position['series'][-1] if position['series'] else None
                    reason='EOD'; exitp=float(prev['close']) if prev else position['entry']; exit_ts=prev['time'] if prev else c['time']
                else:
                    prev=at_or_after(position['series'],int(c['time']))
                    reason='BTST/NEXT_OPEN'; exitp=float(prev['open']) if prev else position['entry']; exit_ts=prev['time'] if prev else c['time']
                pnl=(exitp-position['entry'])*position['qty']; balance+=pnl
                trades.append({'signal_time':position['signal_time'],'entry_time':position['time'],'exit_time':exit_ts,'entry':round(position['entry'],2),'exit':round(exitp,2),'qty':position['qty'],'lots':lot_multiplier,'pnl':round(pnl,2),'reason':reason,'symbol':position['symbol'],'rr':_rr_ratio(sl_pct,tp_pct),'session':trade_type,'underlying_spot':position['spot'],'atm_strike':position['atm']})
                wins += pnl>0; losses += pnl<=0; position=None; peak=max(peak,balance); max_dd=max(max_dd,peak-balance)
                continue
            elif int(c['time'])>=position['eod_ts'] and trade_type=='INTRADAY':
                oc=at_or_after(position['series'],int(c['time'])) or (position['series'][-1] if position['series'] else None)
                if oc:
                    exitp=float(oc['close']); pnl=(exitp-position['entry'])*position['qty']; balance+=pnl
                    trades.append({'signal_time':position['signal_time'],'entry_time':position['time'],'exit_time':oc['time'],'entry':round(position['entry'],2),'exit':round(exitp,2),'qty':position['qty'],'lots':lot_multiplier,'pnl':round(pnl,2),'reason':'EOD','symbol':position['symbol'],'rr':_rr_ratio(sl_pct,tp_pct),'session':'INTRADAY','underlying_spot':position['spot'],'atm_strike':position['atm']})
                    wins += pnl>0; losses += pnl<=0; position=None; peak=max(peak,balance); max_dd=max(max_dd,peak-balance); continue
            if position:
                oc=at_or_after(position['series'],int(nxt['time']))
                if oc:
                    hit_sl=float(oc['low'])<=position['sl']; hit_tp=float(oc['high'])>=position['tp']
                    if hit_sl or hit_tp:
                        exitp=position['sl'] if hit_sl else position['tp']; pnl=(exitp-position['entry'])*position['qty']; balance+=pnl
                        trades.append({'signal_time':position['signal_time'],'entry_time':position['time'],'exit_time':oc['time'],'entry':round(position['entry'],2),'exit':round(exitp,2),'qty':position['qty'],'lots':lot_multiplier,'pnl':round(pnl,2),'reason':'SL' if hit_sl else 'TARGET','symbol':position['symbol'],'rr':_rr_ratio(sl_pct,tp_pct),'session':trade_type,'underlying_spot':position['spot'],'atm_strike':position['atm']})
                        wins += pnl>0; losses += pnl<=0; position=None; peak=max(peak,balance); max_dd=max(max_dd,peak-balance); continue
        signal,conf,_=strategy_signal(base[:i+1],strategy)
        if position is None and signal=='BUY' and conf>=0.70:
            if trade_type=='INTRADAY' and _session_date(int(nxt['time']))!=_session_date(int(c['time'])): continue
            if trade_type=='BTST' and datetime.fromtimestamp(int(c['time']),IST).time() < dtime(15,0): continue
            inst2=contract_for(float(c['close']),int(c['time']));
            if not inst2: continue
            series=option_series(inst2); oc=at_or_after(series,int(nxt['time']))
            if not oc: continue
            entry=float(oc['open']);
            if entry<=0: continue
            base_qty=max(1,int(lot_multiplier))*int(float(inst2.get('lotsize') or inst2.get('lot_size') or 1))
            eod_dt=IST.localize(datetime.combine(_session_date(int(nxt['time'])),dtime(15,29)))
            position={'signal_time':c['time'],'time':oc['time'],'entry':entry,'sl':entry*(1-sl_pct/100),'tp':entry*(1+tp_pct/100),'series':series,'symbol':str(inst2.get('symbol','')),'qty':base_qty,'session':_session_date(int(nxt['time'])),'eod_ts':int(eod_dt.timestamp()),'spot':float(c['close']),'atm':round(float(c['close'])/(100 if underlying=='SENSEX' else 50))*(100 if underlying=='SENSEX' else 50)}
    if position:
        oc=position['series'][-1] if position['series'] else None
        if oc:
            exitp=float(oc['close']); pnl=(exitp-position['entry'])*position['qty']; balance+=pnl
            trades.append({'signal_time':position['signal_time'],'entry_time':position['time'],'exit_time':oc['time'],'entry':round(position['entry'],2),'exit':round(exitp,2),'qty':position['qty'],'lots':lot_multiplier,'pnl':round(pnl,2),'reason':'BTST/EOD' if trade_type=='BTST' else 'EOD','symbol':position['symbol'],'rr':_rr_ratio(sl_pct,tp_pct),'session':trade_type,'underlying_spot':position['spot'],'atm_strike':position['atm']}); wins += pnl>0; losses += pnl<=0
        peak=max(peak,balance); max_dd=max(max_dd,peak-balance)
    net=balance-starting_balance; gp=sum(max(0,t['pnl']) for t in trades); gl=sum(-min(0,t['pnl']) for t in trades); pf=gp/gl if gl else (999.0 if gp else 0.0)
    return {'ok':True,'mode':option_mode,'underlying':underlying,'strategy':strategy,'timeframe':timeframe,'trade_type':trade_type,'lot_multiplier':lot_multiplier,'candles':len(base),'trades':len(trades),'wins':int(wins),'losses':int(losses),'win_rate':round(wins/len(trades)*100,1) if trades else 0.0,'net_pnl':round(net,2),'return_pct':round(net/starting_balance*100,2),'profit_factor':round(pf,2),'max_drawdown':round(max_dd,2),'rr_ratio':_rr_ratio(sl_pct,tp_pct),'trades_detail':trades,'data_note':'Historical option candles; research only; no Angel One orders are sent.'}

def build_strategy_markers(candles):
    """Generate deterministic BUY/SELL markers for every strategy without trading."""
    rows=_align_candles(candles)
    if len(rows)<25: return []
    closes=[float(c['close']) for c in rows]
    ema9=calc_ema_series(closes,9); ema15=calc_ema_series(closes,15)
    out=[]
    for i in range(24,len(rows)):
        c=rows[i]; last=closes[i]
        # EMA cross
        if ema9[i-1] <= ema15[i-1] and ema9[i] > ema15[i]: out.append({'time':c['time'],'price':last,'signal':'BUY','strategy':'EMA_CROSS','confidence':0.80})
        elif ema9[i-1] >= ema15[i-1] and ema9[i] < ema15[i]: out.append({'time':c['time'],'price':last,'signal':'SELL','strategy':'EMA_CROSS','confidence':0.80})
        # RSI mean reversion
        rsi=calc_rsi(closes[:i+1],14)
        if rsi<=30 and last>closes[i-1]: out.append({'time':c['time'],'price':last,'signal':'BUY','strategy':'RSI_MEAN_REVERT','confidence':0.75})
        elif rsi>=70 and last<closes[i-1]: out.append({'time':c['time'],'price':last,'signal':'SELL','strategy':'RSI_MEAN_REVERT','confidence':0.75})
        # VWAP mean reversion
        vw=calc_vwap_series(rows[max(0,i-19):i+1])[-1]
        if last < vw*0.998 and last>closes[i-1]: out.append({'time':c['time'],'price':last,'signal':'BUY','strategy':'VWAP_REVERT','confidence':0.72})
        elif last > vw*1.002 and last<closes[i-1]: out.append({'time':c['time'],'price':last,'signal':'SELL','strategy':'VWAP_REVERT','confidence':0.72})
        # 20-candle breakout
        high20=max(float(x['high']) for x in rows[i-20:i]); low20=min(float(x['low']) for x in rows[i-20:i])
        if last>high20: out.append({'time':c['time'],'price':last,'signal':'BUY','strategy':'BREAKOUT','confidence':0.82})
        elif last<low20: out.append({'time':c['time'],'price':last,'signal':'SELL','strategy':'BREAKOUT','confidence':0.82})
        # RBS / SBR: prior candle breaks a prior 20-bar level; current candle retests it.
        if i>=22:
            level=max(float(x['high']) for x in rows[i-21:i-1]); prev=float(rows[i-1]['close'])
            if prev>level and float(c['low'])<=level*1.001 and last>level: out.append({'time':c['time'],'price':last,'signal':'BUY','strategy':'RBS','confidence':0.78,'level':level})
            level=min(float(x['low']) for x in rows[i-21:i-1]);
            if prev<level and float(c['high'])>=level*0.999 and last<level: out.append({'time':c['time'],'price':last,'signal':'SELL','strategy':'SBR','confidence':0.78,'level':level})
    return out


def _scale_research_result(base_result, lot_multiplier):
    lm=max(1,int(lot_multiplier)); r=dict(base_result); r['lot_multiplier']=lm
    if lm==1: return r
    for k in ('net_pnl','max_drawdown'):
        if k in r: r[k]=round(float(r[k])*lm,2)
    td=[]
    for t in r.get('trades_detail',[]) or []:
        x=dict(t)
        if 'qty' in x: x['qty']=int(x['qty'])*lm
        if 'pnl' in x: x['pnl']=round(float(x['pnl'])*lm,2)
        x['lots']=lm; td.append(x)
    r['trades_detail']=td
    return r

MATRIX_JOBS = {}
MATRIX_JOBS_LOCK = threading.Lock()


class SimulationState:
    def __init__(self):
        self.lock=threading.Lock(); self.nifty_spot=23765.0; self.sensex_spot=81200.0; self.prev_close=23897.70; self.sensex_prev_close=0.0
        self.wallet={'initial':1000000.0,'balance':1000000.0,'used_margin':0.0,'realized_pnl':0.0}
        self.positions=[]; self.pending_orders=[]; self.orders=[]; self.closed_trades=[]; self.candles_1m=[]; self.order_counter=100
        self.live_chain_cache={}; self.chart_cache={}; self.market_cache={}; self.market_cache_ts=0; self.angel=AngelOneData(); self.bot={'enabled':False,'strategy':'EMA_CROSS','underlying':'NIFTY','instrument_mode':'INDEX','qty':1,'risk_per_trade':1.0,'max_daily_loss':2.0,'stop_loss_pct':0.6,'target_pct':1.2,'last_signal':'HOLD','last_confidence':0,'last_reason':'Waiting for signal…','trades_today':0,'daily_pnl':0.0,'last_trade_ts':0,'last_eval_ts':0,'risk_lock':False,'risk_lock_reason':'','last_entry_price':0.0,'max_trades_per_day':5,'max_open_positions':1,'cooldown_sec':60,'trade_count_today':0,'session_date':datetime.now(IST).strftime('%Y-%m-%d'),'last_signal_change_ts':0,'strategy_stats':{k:{'trades':0,'wins':0,'loss':0,'pnl':0.0,'status':'UNVALIDATED'} for k in STRATEGIES},'initial_balance':1000000.0}; self._load_state(); self._init_history(); self.bot['enabled']=False; self._running=True
        threading.Thread(target=self._tick_loop,daemon=True).start()

    DB_PATH=os.environ.get('SIM_DB_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'simulator_state.db'))

    def _db(self):
        db_dir=os.path.dirname(os.path.abspath(self.DB_PATH))
        os.makedirs(db_dir, exist_ok=True)
        db=sqlite3.connect(self.DB_PATH, timeout=5)
        db.execute("CREATE TABLE IF NOT EXISTS simulator_state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS research_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at INTEGER NOT NULL, kind TEXT, strategy TEXT, underlying TEXT, mode TEXT, timeframe INTEGER, trade_type TEXT, days INTEGER, sl REAL, tp REAL, rr REAL, lot_multiplier INTEGER, result_json TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS research_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, created_at INTEGER NOT NULL, trade_json TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS paper_trade_journal (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at INTEGER NOT NULL, trade_json TEXT NOT NULL)")
        cutoff=int(time.time())-30*86400
        for table in ('research_runs','research_trades','paper_trade_journal'):
            db.execute(f"DELETE FROM {table} WHERE created_at < ?",(cutoff,))
        return db

    def _store_research(self, kind, result, days=0, sl=0.6, tp=1.2, lot_multiplier=1):
        try:
            db=self._db(); now=int(time.time());
            cur=db.execute("INSERT INTO research_runs(created_at,kind,strategy,underlying,mode,timeframe,trade_type,days,sl,tp,rr,lot_multiplier,result_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(now,kind,result.get('strategy'),result.get('underlying'),result.get('mode'),result.get('timeframe'),result.get('trade_type','INTRADAY'),days,sl,tp,_rr_ratio(sl,tp),lot_multiplier,json.dumps(result,separators=(',',':'))))
            rid=cur.lastrowid
            for t in result.get('trades_detail',[]): db.execute("INSERT INTO research_trades(run_id,created_at,trade_json) VALUES(?,?,?)",(rid,now,json.dumps(t,separators=(',',':'))))
            db.commit(); db.close(); return rid
        except Exception: return None

    def _journal_paper_trade(self, trade):
        try:
            db=self._db(); db.execute("INSERT INTO paper_trade_journal(created_at,trade_json) VALUES(?,?)",(int(time.time()),json.dumps(trade,separators=(',',':')))); db.commit(); db.close()
        except Exception: pass

    def _save_state(self):
        try:
            payload={'wallet':self.wallet,'positions':self.positions,'pending_orders':self.pending_orders,'orders':self.orders,'closed_trades':self.closed_trades,'order_counter':self.order_counter,'bot':{k:v for k,v in self.bot.items() if k not in ('enabled','last_signal','last_confidence','last_reason','last_eval_ts')}}
            db=self._db(); db.execute("INSERT INTO simulator_state(id,data) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (json.dumps(payload,separators=(',',':')),)); db.commit(); db.close()
        except Exception:
            pass

    def _load_state(self):
        try:
            db=self._db(); row=db.execute("SELECT data FROM simulator_state WHERE id=1").fetchone(); db.close()
            if not row: return
            d=json.loads(row[0]); self.wallet=d.get('wallet',self.wallet); self.positions=d.get('positions',[]); self.pending_orders=d.get('pending_orders',[]); self.orders=d.get('orders',[]); self.closed_trades=d.get('closed_trades',[]); self.order_counter=int(d.get('order_counter',self.order_counter))
            saved=d.get('bot',{})
            for k in ('strategy','underlying','instrument_mode','qty','risk_per_trade','max_daily_loss','stop_loss_pct','target_pct','trades_today','daily_pnl','last_trade_ts','risk_lock','risk_lock_reason','last_entry_price','max_trades_per_day','max_open_positions','cooldown_sec','trade_count_today','session_date'):
                if k in saved: self.bot[k]=saved[k]
            # Capital migration: v2.x used ₹1,00,000. New paper wallet is ₹10,00,000.
            if float(self.wallet.get('initial',1000000.0) or 0) < 1000000.0:
                pnl=float(self.wallet.get('realized_pnl',0.0) or 0.0)
                self.wallet['initial']=1000000.0
                self.wallet['balance']=round(1000000.0 + pnl,2)
            self.bot['initial_balance']=1000000.0
            self.bot['enabled']=False
        except Exception:
            pass

    def _market_open_now(self):
        now=datetime.now(IST)
        return now.weekday() < 5 and dtime(9,15) <= now.time() <= dtime(15,30)

    def _accept_index_ltp(self, name, ltp, reference=None):
        try:
            value=float(ltp)
        except Exception:
            return False
        if value <= 0:
            return False
        # Guard against malformed/stale index packets. Angel's index LTPs are
        # normal rupee prices (e.g. NIFTY ~25k, SENSEX ~80k), not 2-digit values.
        floor=1000.0 if name=='NIFTY' else 10000.0
        if value < floor:
            return False
        if reference and reference > 0 and abs(value-reference)/reference > 0.20:
            return False
        return True

    def _init_history(self):
        if self.angel.enabled:
            inst=self.angel.find_index('NIFTY'); c=self.angel.candles(inst,'ONE_MINUTE',2)
            if c:
                self.candles_1m=c
                # Historical close is the authoritative seed outside market hours.
                # This prevents a malformed/stale WebSocket/quote value from
                # turning NIFTY into something like ₹260 and selecting bad strikes.
                hist_nifty=float(c[-1].get('close') or self.nifty_spot)
                if self._accept_index_ltp('NIFTY', hist_nifty):
                    self.nifty_spot=hist_nifty
                self.prev_close=float(c[-1].get('close') or self.prev_close)
                if self._market_open_now():
                    q=self.angel.quote([inst]); z=q.get(str(inst['token']))
                    if z and self._accept_index_ltp('NIFTY', z.get('ltp'), self.nifty_spot):
                        self.nifty_spot=float(z['ltp'])
                    if z and z.get('close') is not None:
                        self.prev_close=float(z['close'])
                self.sensex_spot=self._sensex_ltp()
                self.sensex_prev_close=self.sensex_spot
                return
        # Existing development fallback remains isolated to ANGELONE_ENABLED=0.
        now_ts=int(time.time()); cur=(now_ts//60)*60; p=self.nifty_spot; out=[]
        for i in range(90):
            t=cur-(89-i)*60; c=round(p+random.uniform(-2,2),2); o=p; h=max(o,c)+random.random(); l=min(o,c)-random.random(); out.append({'time':t,'is_prev_day':False,'open':o,'high':h,'low':l,'close':c,'volume':random.randint(1000,5000)}); p=c
        self.candles_1m=out

    def _sensex_ltp(self):
        if not self.angel.enabled: return round(self.nifty_spot*3.41,2)
        inst=self.angel.find_index('SENSEX')
        # Use recent historical candles as the seed when the market is closed.
        hist=0.0
        if inst:
            try:
                hc=self.angel.candles(inst,'ONE_MINUTE',2)
                if hc: hist=float(hc[-1].get('close') or 0)
            except Exception:
                hist=0.0
        base=hist if self._accept_index_ltp('SENSEX',hist) else self.sensex_spot
        if self._market_open_now() and inst:
            q=self.angel.quote([inst]); z=q.get(str(inst['token']))
            if z and self._accept_index_ltp('SENSEX', z.get('ltp'), base):
                return float(z['ltp'])
            if base: return base
        return base or self.sensex_spot

    def _live_indices(self):
        ni=self.angel.find_index('NIFTY'); si=self.angel.find_index('SENSEX')
        for inst, attr in [(ni,'nifty_spot'),(si,'sensex_spot')]:
            if not inst: continue
            self.angel.subscribe_instrument(inst)
            ltp=self.angel.websocket_ltp(inst)
            if ltp is None:
                q=self.angel.quote([inst]); z=q.get(str(inst['token']))
                ltp=float(z.get('ltp')) if z and z.get('ltp') is not None else None
                if z:
                    if attr=='nifty_spot': self.prev_close=float(z.get('close',self.prev_close))
                    if attr=='sensex_spot': self.sensex_prev_close=float(z.get('close',self.sensex_prev_close or self.sensex_spot))
            reference=float(getattr(self,attr) or 0)
            name='SENSEX' if attr=='sensex_spot' else 'NIFTY'
            if ltp is not None and self._accept_index_ltp(name, ltp, reference):
                setattr(self,attr,float(ltp))
            elif not self._market_open_now() and inst:
                # Outside market hours, keep the last valid/historical close and
                # never replace it with a malformed reference/stale packet.
                try:
                    hc=self.angel.candles(inst,'ONE_MINUTE',1)
                    if hc:
                        close=float(hc[-1].get('close') or 0)
                        if self._accept_index_ltp(name, close, reference if reference else None):
                            setattr(self,attr,close)
                except Exception:
                    pass

    def _bot_underlying_candles(self, underlying):
        if underlying=='NIFTY':
            return list(self.candles_1m)
        # Build SENSEX history from its own instrument when available; otherwise use a small rolling cache.
        key=('SENSEX','1m'); cached=self.chart_cache.get(key)
        if cached and cached.get('candles'): return [dict(c) for c in cached['candles']]
        if self.angel.enabled:
            inst=self.angel.find_index('SENSEX'); fresh=self.angel.candles(inst,'ONE_MINUTE',2) if inst else []
            if fresh: self.chart_cache[key]={'fetched':time.time(),'candles':fresh}; return fresh
        return []

    def _bot_strike(self, underlying, mode, spot=None):
        is_s=underlying=='SENSEX'; step=100 if is_s else 50; spot=float(spot if spot is not None else (self.sensex_spot if is_s else self.nifty_spot))
        atm=round(spot/step)*step
        m=str(mode or 'ATM_OPTIONS').upper()
        if m=='ATM_OPTIONS' or m=='ATM': return atm
        import re
        mm=re.match(r'^(ITM|OTM)_(\d+)$',m)
        if not mm: return atm
        side,n=mm.group(1),int(mm.group(2)); n=max(1,min(n,5))
        # Bot entries are long calls on BUY signals. For calls, lower strike is ITM and higher is OTM.
        return atm + (step*n if side=='OTM' else -step*n)

    def _bot_option_symbol(self, underlying, action):
        is_s=underlying=='SENSEX'; spot=self.sensex_spot if is_s else self.nifty_spot; expiry=self.get_option_chain(underlying).get('selected_expiry')
        if not expiry or not self.angel.enabled: return None
        strike=self._bot_strike(underlying,self.bot.get('instrument_mode','ATM_OPTIONS'),spot); typ='CE' if action=='BUY' else 'PE'
        arr=self.angel.option_instruments(underlying,expiry)
        x=next((i for i in arr if abs(float(i.get('strike',0))/100-strike)<0.01 and str(i.get('symbol','')).upper().endswith(typ)),None)
        return f"{underlying}_{expiry}_{int(strike)}_{typ}" if x else None

    def _reset_bot_session_if_needed(self):
        today=datetime.now(IST).strftime('%Y-%m-%d')
        if self.bot.get('session_date') != today:
            self.bot['session_date']=today
            self.bot['daily_pnl']=0.0
            self.bot['trades_today']=0
            self.bot['trade_count_today']=0
            self.bot['risk_lock']=False
            self.bot['risk_lock_reason']=''
            self._save_state()

    def _bot_positions(self):
        return [p for p in self.positions if p.get('bot_tag')]

    def _run_bot_once(self):
        b=self.bot
        self._reset_bot_session_if_needed()
        if not b['enabled'] or not self.angel.enabled: return
        now=time.time()
        if now-b.get('last_eval_ts',0)<0.5: return
        b['last_eval_ts']=now
        candles=self._bot_underlying_candles(b['underlying'])
        if len(candles)>1: candles=candles[:-1]
        if len(candles)<30:
            b['last_signal']='HOLD'; b['last_confidence']=0; b['last_reason']='Waiting for 30+ one-minute candles…'; return
        signal,conf,meta=strategy_signal(candles,b['strategy'])
        b['last_signal']=signal; b['last_confidence']=conf
        b['last_signal_change_ts']=now
        # Human-readable reason plus indicator snapshot; the UI can render the fields without raw debug JSON.
        b['signal_meta']=meta
        b['last_reason']=self._bot_reason(signal, meta)

        loss_limit=abs(float(b['max_daily_loss'])/100.0)*float(b['initial_balance'])
        if b['daily_pnl'] <= -loss_limit:
            b['risk_lock']=True; b['risk_lock_reason']=f'Daily loss limit ₹{loss_limit:.0f} reached'; b['enabled']=False; self._save_state(); return
        if not is_market_open():
            b['last_reason']='Market closed — bot is armed but waiting for market hours.'
            return
        if signal=='HOLD' or conf<0.70:
            if not self._bot_positions(): b['last_reason']=self._bot_reason(signal,meta)
            return

        bot_positions=self._bot_positions()
        if signal=='SELL':
            for pos in list(bot_positions): self._internal_exit(pos['id'],'BOT_SIGNAL')
            return
        if bot_positions: return
        if int(b.get('trade_count_today',0)) >= int(b.get('max_trades_per_day',5)):
            b['last_reason']='Daily trade limit reached.'; return
        if now-float(b.get('last_trade_ts',0)) < float(b.get('cooldown_sec',60)):
            return
        if len(self.positions) >= int(b.get('max_open_positions',1)):
            b['last_reason']='Maximum open-position limit reached.'; return

        symbol=b['underlying'] if b['instrument_mode']=='INDEX' else self._bot_option_symbol(b['underlying'],'BUY')
        if not symbol:
            b['last_reason']=f"{b.get('instrument_mode','ATM_OPTIONS').replace('_',' ')} option unavailable."; return
        ltp,_,_=self._get_live_instrument_ltp(symbol)
        if not ltp or ltp<=0: b['last_reason']='Live entry price unavailable.'; return
        qty=max(1,int(b['qty']))
        sl=round(ltp*(1-float(b['stop_loss_pct'])/100),2); target=round(ltp*(1+float(b['target_pct'])/100),2)
        risk_per_unit=max(ltp-sl,0.01)
        risk_budget=max(float(b.get('risk_per_trade',1.0))/100.0*float(self.wallet.get('initial',1000000)), risk_per_unit)
        risk_qty=max(1,int(risk_budget/risk_per_unit))
        qty=min(qty,risk_qty)
        cost=round(ltp*qty,2)
        if cost>self.wallet['balance']:
            b['last_reason']='Insufficient paper funds for configured quantity.'; return
        self._execute_fill(symbol,'BUY',qty,ltp,sl,target,0,bot_tag=True)
        b['last_entry_price']=ltp
        b['trades_today']=int(b.get('trades_today',0))+1
        b['trade_count_today']=int(b.get('trade_count_today',0))+1
        b['last_trade_ts']=now
        b['last_reason']=f'BUY {qty} × {symbol} @ ₹{ltp:.2f} · SL ₹{sl:.2f} · Target ₹{target:.2f}'
        self._save_state()

    def _bot_reason(self, signal, meta):
        if signal=='BUY': prefix='Buy setup detected.'
        elif signal=='SELL': prefix='Exit signal detected.'
        else: prefix='No qualified entry.'
        bits=[]
        if isinstance(meta,dict):
            if 'rsi' in meta: bits.append(f"RSI {float(meta['rsi']):.1f}")
            if 'ema9' in meta and 'ema15' in meta: bits.append(f"EMA9 {float(meta['ema9']):.2f} / EMA15 {float(meta['ema15']):.2f}")
            if 'vwap' in meta: bits.append(f"VWAP {float(meta['vwap']):.2f}")
            if 'breakout' in meta: bits.append(f"Level {float(meta['breakout']):.2f}")
        return prefix + (" · " + " · ".join(bits) if bits else '')

    def _tick_loop(self):
        last_indices=0.0
        while self._running:
            time.sleep(0.05)
            try:
                now=time.time()
                if self.angel.enabled:
                    # WebSocket is the primary feed. REST is used only as a fallback when a tick is not yet available.
                    if now-last_indices >= 0.5:
                        last_indices=now
                        self._live_indices()
                    with self.lock:
                        ts=int(now//60)*60
                        if not self.candles_1m or ts>self.candles_1m[-1]['time']:
                            p=self.nifty_spot; self.candles_1m.append({'time':ts,'is_prev_day':False,'open':p,'high':p,'low':p,'close':p,'volume':0})
                        c=self.candles_1m[-1]; c['close']=self.nifty_spot; c['high']=max(c['high'],self.nifty_spot); c['low']=min(c['low'],self.nifty_spot)
                else:
                    with self.lock:
                        self.nifty_spot=round(self.nifty_spot+random.choice([-0.8,-0.4,0,0.4,0.8]),2); self.sensex_spot=round(self.nifty_spot*3.41,2)
                with self.lock:
                    for p in self.positions:
                        ltp,delta,theta=self._get_live_instrument_ltp(p['symbol']); p['ltp']=ltp; p['delta']=delta; p['theta']=theta
                        p['pnl']=round((ltp-p['buy_price'])*p['qty'] if p['action']=='BUY' else (p['buy_price']-ltp)*p['qty'],2)
                    remaining=[]
                    for po in self.pending_orders:
                        ltp,_,_=self._get_live_instrument_ltp(po['symbol']); filled=(po['action']=='BUY' and ltp<=po['limit_price']) or (po['action']=='SELL' and ltp>=po['limit_price'])
                        if filled: self._execute_fill(po['symbol'],po['action'],po['qty'],po['limit_price'],po.get('stop_loss',0),po.get('target',0),po.get('trailing_sl',0))
                        else: remaining.append(po)
                    self.pending_orders=remaining
                    for p in list(self.positions):
                        if p.get('stop_loss') and ((p['action']=='BUY' and p['ltp']<=p['stop_loss']) or (p['action']=='SELL' and p['ltp']>=p['stop_loss'])): self._internal_exit(p['id'],'SL')
                        elif p.get('target') and ((p['action']=='BUY' and p['ltp']>=p['target']) or (p['action']=='SELL' and p['ltp']<=p['target'])): self._internal_exit(p['id'],'TARGET')
                    if datetime.now(IST).time() >= dtime(15,29,30):
                        for bp in list(self._bot_positions()): self._internal_exit(bp['id'],'EOD')
                        self.bot['enabled']=False
                    self._run_bot_once()
            except Exception as e: self.angel.last_error=str(e)

    def _get_live_instrument_ltp(self,symbol):
        if symbol in ('NIFTY','NIFTY 50','INDEX'): return self.nifty_spot,1.0,0.0
        if symbol=='SENSEX': return self.sensex_spot,1.0,0.0
        if self.angel.enabled:
            inst=self._find_option_from_ui(symbol)
            if inst:
                self.angel.subscribe_instrument(inst)
                ltp=self.angel.websocket_ltp(inst)
                if ltp is None:
                    q=self.angel.quote([inst]); z=q.get(str(inst['token']))
                    ltp=float(z.get('ltp') or 0) if z else 0
                return ltp,0.0,0.0
        # Development-only synthetic fallback.
        is_s='SENSEX' in symbol; spot=self.sensex_spot if is_s else self.nifty_spot; parts=symbol.split('_'); strike=spot; typ='CE'
        for x in parts:
            if x in ('CE','PE'): typ=x
            elif x.isdigit() and len(x)>=4: strike=float(x)
        g=calc_deep_greeks(spot,strike,get_dte_from_expiry(parts[1]) if len(parts)>1 else 1.1,is_sensex=is_s); return g['ce_ltp'] if typ=='CE' else g['pe_ltp'], g['ce_delta'] if typ=='CE' else g['pe_delta'], g['theta']

    def _find_option_from_ui(self,symbol):
        # Accept both the compact UI key (NIFTY_25000_CE / NIFTY_EXP_25000_CE)
        # and the chart title sent by the existing frontend (NIFTY EXP 25000 CE).
        raw=str(symbol or '').strip()
        p=raw.split('_')
        if len(p)>=4:
            u,exp,strike,typ=p[0],p[1],float(p[2]),p[3].upper()
        elif len(p)==3:
            u, strike, typ=p[0], float(p[1]), p[2].upper()
            exps=sorted({str(x.get('expiry','')).upper() for x in self.angel.instruments if str(x.get('name','')).upper()==u and str(x.get('instrumenttype','')).upper()=='OPTIDX' and str(x.get('exch_seg','')).upper()==('BFO' if u=='SENSEX' else 'NFO')})
            exp=exps[0] if exps else ''
        else:
            parts=raw.upper().replace('  ',' ').split()
            if len(parts)>=4 and parts[-1] in ('CE','PE'):
                u,exp,typ=parts[0],parts[1],parts[-1]; strike=float(parts[2])
            else:
                return None
        arr=[x for x in self.angel.option_instruments(u,exp) if str(x.get('symbol','')).upper().endswith(typ) and abs(float(x.get('strike',-1))/100-strike)<0.01]
        return arr[0] if arr else None

    def _execute_fill(self,symbol,action,qty,price,sl=0,target=0,tsl=0,bot_tag=False):
        pos_id=f'pos_{self.order_counter}'; self.order_counter+=1; ltp,delta,theta=self._get_live_instrument_ltp(symbol); cost=round(price*qty,2)
        self.wallet['balance']=round(self.wallet['balance']-cost,2); self.wallet['used_margin']=round(self.wallet['used_margin']+cost,2)
        self.positions.append({'id':pos_id,'symbol':symbol,'action':action,'qty':qty,'buy_price':price,'ltp':ltp,'pnl':0.0,'stop_loss':sl,'target':target,'trailing_sl':tsl,'delta':delta,'theta':theta,'bot_tag':bool(bot_tag)})
        self.orders.insert(0,{'id':pos_id,'time':datetime.now(IST).strftime('%H:%M:%S'),'symbol':symbol,'action':action,'qty':qty,'price':price,'status':'FILLED','bot_tag':bool(bot_tag)}); self._save_state()

    def _internal_exit(self,pos_id,reason='MANUAL'):
        for i,p in enumerate(self.positions):
            if p['id']==pos_id:
                p=self.positions.pop(i); cost=round(p['buy_price']*p['qty'],2); pnl=p['pnl']; self.wallet['used_margin']=max(0,round(self.wallet['used_margin']-cost,2)); self.wallet['balance']=round(self.wallet['balance']+cost+pnl,2); self.wallet['realized_pnl']=round(self.wallet['realized_pnl']+pnl,2); self.bot['daily_pnl']=round(self.bot.get('daily_pnl',0)+pnl,2) if p.get('bot_tag') else self.bot.get('daily_pnl',0)
                trade={'id':p['id'],'symbol':p['symbol'],'action':p['action'],'qty':p['qty'],'buy_price':p['buy_price'],'exit_price':p['ltp'],'pnl':pnl,'reason':reason,'time':datetime.now(IST).isoformat(),'bot_tag':bool(p.get('bot_tag'))}; self.closed_trades.insert(0,trade); self._journal_paper_trade(trade); self._save_state(); return

    def _resample(self,candles,tf):
        if tf<=60:return candles
        buckets={}
        for c in candles:
            bt=(c['time']//tf)*tf
            if bt not in buckets: buckets[bt]={'time':bt,'is_prev_day':False,'open':c['open'],'high':c['high'],'low':c['low'],'close':c['close'],'volume':c['volume']}
            else:
                b=buckets[bt]; b['high']=max(b['high'],c['high']); b['low']=min(b['low'],c['low']); b['close']=c['close']; b['volume']+=c['volume']
        return list(buckets.values())

    def get_tick_data(self, symbol, timeframe='1m'):
        """Return only the current streamed price and candle timing. No REST/network calls."""
        now=time.time(); is_s='SENSEX' in str(symbol).upper(); tf={'1m':60,'3m':180,'5m':300,'10m':600,'15m':900,'30m':1800,'1h':3600}.get(timeframe,60)
        is_option='_' in str(symbol)
        ltp=0.0; inst=None
        if is_option:
            inst=self._find_option_from_ui(symbol)
            if inst:
                self.angel.subscribe_instrument(inst)
                ltp=self.angel.websocket_ltp(inst) or 0.0
                if not ltp:
                    q=self.angel.quote_cache.get(str(inst['token'])) or {}
                    ltp=float(q.get('ltp') or 0)
        else:
            ltp=self.sensex_spot if is_s else self.nifty_spot
        if not ltp:
            return {'symbol':symbol,'ltp':0,'countdown':'00:00','time':int(now)}
        key=(symbol,timeframe); chart=self.chart_cache.get(key); candles=chart.get('candles') if chart else None
        if candles:
            c=candles[-1]
            # Keep the streamed tick on the active candle. Create a new bucket only when the candle boundary changes.
            bucket=(int(now)//tf)*tf
            if int(c.get('time',0)) != bucket:
                candles.append({'time':bucket,'is_prev_day':False,'open':ltp,'high':ltp,'low':ltp,'close':ltp,'volume':0})
                if len(candles)>500: del candles[:-500]
            else:
                c['close']=ltp; c['high']=max(c['high'],ltp); c['low']=min(c['low'],ltp)
        countdown_seconds=max(0,int((((int(now)//tf)+1)*tf)-now))
        return {'symbol':symbol,'ltp':round(float(ltp),2),'countdown':f'{countdown_seconds//60:02d}:{countdown_seconds%60:02d}','time':int(now)}


    def get_instrument_chart_data(self,symbol,timeframe='1m'):
        is_s='SENSEX' in symbol.upper(); spot=self.sensex_spot if is_s else self.nifty_spot; tf={'1m':60,'3m':180,'5m':300,'10m':600,'15m':900,'30m':1800,'1h':3600}.get(timeframe,60)
        is_option='_' in symbol
        cache_key=(symbol,timeframe)
        cached=self.chart_cache.get(cache_key)
        now=time.time()
        if self.angel.enabled and cached and now-cached.get('fetched',0) < 10.0:
            candles=[dict(c) for c in cached['candles']]
        else:
            candles=[]
            if self.angel.enabled:
                inst = self._find_option_from_ui(symbol) if is_option else self.angel.find_index('SENSEX' if is_s else 'NIFTY')
                if inst:
                    if is_option: self.angel.subscribe_instrument(inst)
                    interval={60:'ONE_MINUTE',180:'THREE_MINUTE',300:'FIVE_MINUTE',600:'TEN_MINUTE',900:'FIFTEEN_MINUTE',1800:'THIRTY_MINUTE',3600:'ONE_HOUR'}[tf]
                    fresh=self.angel.candles(inst,interval,2)
                    if fresh: candles=fresh
            if not candles:
                base=self._resample(self.candles_1m,tf)
                candles=[dict(c) for c in base]
            if self.angel.enabled: self.chart_cache[cache_key]={'fetched':now,'candles':[dict(c) for c in candles]}
        # Keep the latest candle/tick moving without another historical REST request.
        if is_option:
            inst=self._find_option_from_ui(symbol)
            if inst:
                self.angel.subscribe_instrument(inst); ltp=self.angel.websocket_ltp(inst)
                if ltp is None:
                    q=self.angel.quote([inst]); z=q.get(str(inst['token']))
                    ltp=float(z.get('ltp',0)) if z else 0
            else: ltp=0.0
            parts=symbol.split('_'); exp=parts[1] if len(parts)>=4 else ''; strike=float(parts[2]) if len(parts)>=4 else 0; typ=parts[3] if len(parts)>=4 else 'CE'
            display=f"{'SENSEX' if is_s else 'NIFTY'} {exp} {int(strike)} {typ}"
            if ltp and candles:
                c=candles[-1]; c['close']=ltp; c['high']=max(c['high'],ltp); c['low']=min(c['low'],ltp)
            if not candles:
                dte=get_dte_from_expiry(exp); g=calc_deep_greeks(spot,strike,dte,is_sensex=is_s); ltp=ltp or (g['ce_ltp'] if typ=='CE' else g['pe_ltp'])
                candles=[{'time':int(now//60)*60,'is_prev_day':False,'open':ltp,'high':ltp,'low':ltp,'close':ltp,'volume':0}]
            gg=self.angel.greek('SENSEX' if is_s else 'NIFTY',exp).get((strike,typ),{}) if self.angel.enabled else {}
            if gg: greeks={'delta':float(gg.get('delta',0) or 0),'gamma':float(gg.get('gamma',0) or 0),'theta':float(gg.get('theta',0) or 0),'vega':float(gg.get('vega',0) or 0),'iv':float(gg.get('impliedVolatility',0) or 0)}
            else:
                g=calc_deep_greeks(spot,strike,get_dte_from_expiry(exp),is_sensex=is_s); greeks={'delta':g['ce_delta'] if typ=='CE' else g['pe_delta'],'gamma':g['gamma'],'theta':g['theta'],'vega':g['vega'],'iv':g['iv']}
        else:
            if candles:
                candles[-1]['close']=spot; candles[-1]['high']=max(candles[-1]['high'],spot); candles[-1]['low']=min(candles[-1]['low'],spot)
            display='SENSEX' if is_s else 'NIFTY 50'; ltp=spot; greeks={'delta':1.0,'gamma':0,'theta':0,'vega':0,'iv':0}
        closes=[c['close'] for c in candles]
        countdown_seconds=max(0, int((((int(now)//tf)+1)*tf)-now))
        countdown=f"{countdown_seconds//60:02d}:{countdown_seconds%60:02d}"
        return {'symbol':symbol,'display_title':display,'timeframe':timeframe,'ltp':ltp,'candles':candles,'countdown':countdown,'ema9':calc_ema_series(closes,9)[-1] if closes else 0,'ema15':calc_ema_series(closes,15)[-1] if closes else 0,'vwap':calc_vwap_series(candles)[-1] if candles else 0,'vwap_series':calc_vwap_series(candles),'ema9_series':calc_ema_series(closes,9),'ema15_series':calc_ema_series(closes,15),'strategy_markers':build_strategy_markers(candles),'greeks':greeks}

    def get_option_chain(self,symbol='NIFTY',expiry=None):
        is_s='SENSEX' in symbol.upper(); underlying='SENSEX' if is_s else 'NIFTY'; spot=self.sensex_spot if is_s else self.nifty_spot; step=100 if is_s else 50
        segment='BFO' if is_s else 'NFO'
        if self.angel.enabled:
            exps=sorted({str(x.get('expiry','')).upper() for x in self.angel.instruments if str(x.get('exch_seg','')).upper()==segment and str(x.get('name','')).upper()==underlying and str(x.get('instrumenttype','')).upper()=='OPTIDX' and x.get('expiry')}, key=lambda e: datetime.strptime(e,'%d%b%Y') if len(e)==9 else datetime.strptime(e,'%d%b%y'))
            preferred='15SEP2026' if '15SEP2026' in exps else ('15SEP26' if '15SEP26' in exps else None)
            selected=expiry if expiry in exps else (preferred or (exps[0] if exps else None))
            if selected:
                arr=self.angel.option_instruments(underlying,selected); strikes=sorted({round(float(x['strike'])/100,2) for x in arr}); atm=min(strikes,key=lambda x:abs(x-spot)) if strikes else round(spot/step)*step; strikes=sorted(strikes,key=lambda x:abs(x-atm))[:17]; strikes=sorted(strikes)
                insts=[x for x in arr if round(float(x['strike'])/100,2) in strikes]
                self.angel.subscribe_instruments(insts)
                base=self.live_chain_cache.get((symbol,selected))
                stale=not base or time.time()-base[0]>=5.0
                if stale:
                    q=self.angel.quote(insts)
                    gk=self.angel.greek(underlying,selected)
                    rows=[]
                    for s in strikes:
                        row={'strike':s,'expiry':selected}
                        for typ,prefix in [('CE','ce'),('PE','pe')]:
                            x=next((i for i in insts if round(float(i['strike'])/100,2)==s and str(i['symbol']).upper().endswith(typ)),None); z=q.get(str(x['token'])) if x else None; g=gk.get((s,typ),{})
                            row[prefix+'_token']=str(x['token']) if x else ''; row[prefix+'_symbol']=str(x.get('symbol','')) if x else ''; row[prefix+'_ltp']=float(z.get('ltp',0)) if z else 0; row[prefix+'_delta']=float(g.get('delta',0) or 0); row[prefix+'_oi']=str(z.get('openInterest',0) if z else 0); row[prefix+'_chg_oi']=str(z.get('changeOpenInterest',0) if z else 0); row[prefix+'_volume']=str(z.get('tradeVolume',z.get('volume',0)) if z else 0); row[prefix+'_iv']=float(g.get('impliedVolatility',0) or 0)
                        rows.append(row)
                    base=(time.time(),{'expiries':exps,'selected_expiry':selected,'chain':rows}); self.live_chain_cache[(symbol,selected)]=base
                result=base[1]
                # Refresh only LTPs from the WebSocket on every market response.
                for row in result['chain']:
                    for prefix in ('ce','pe'):
                        tok=row.get(prefix+'_token'); q=self.angel.quote_cache.get(tok) if tok else None
                        if q and q.get('ltp') is not None: row[prefix+'_ltp']=float(q['ltp'])
                        elif tok:
                            inst=next((i for i in insts if str(i.get('token'))==tok),None)
                            if inst: self.angel.subscribe_instrument(inst)
                return result
        exps=get_available_expiries(is_s); selected=expiry if expiry in exps else exps[0]; dte=get_dte_from_expiry(selected); atm=round(spot/step)*step; rows=[]
        for s in [atm+i*step for i in range(-8,9)]:
            g=calc_deep_greeks(spot,s,dte,is_sensex=is_s); rows.append({'strike':s,'expiry':selected,'ce_ltp':g['ce_ltp'],'ce_delta':g['ce_delta'],'ce_oi':'DEV','ce_chg_oi':'DEV','ce_volume':'DEV','ce_iv':g['iv'],'pe_ltp':g['pe_ltp'],'pe_delta':g['pe_delta'],'pe_oi':'DEV','pe_chg_oi':'DEV','pe_volume':'DEV','pe_iv':g['iv'],'gamma':g['gamma'],'theta':g['theta'],'vega':g['vega']})
        return {'expiries':exps,'selected_expiry':selected,'chain':rows}

    def reset(self):
        self.wallet={'initial':1000000.0,'balance':1000000.0,'used_margin':0.0,'realized_pnl':0.0}; self.positions=[]; self.pending_orders=[]; self.orders=[]; self.closed_trades=[]; self.bot['daily_pnl']=0.0; self.bot['trades_today']=0; self.bot['risk_lock']=False; self.bot['risk_lock_reason']=''; self.bot['last_entry_price']=0.0; self.bot['last_trade_ts']=0; self.bot['last_signal']='HOLD'; self.bot['last_confidence']=0; self.bot['last_reason']='Reset'; self._save_state()

state=SimulationState()

def invalidate_market_cache():
    state.market_cache={}; state.market_cache_ts=0

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,format,*args): return
    def _send_json(self,data,code=200):
        body=json.dumps(data).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Access-Control-Allow-Origin','*'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        parsed=urlparse(self.path)
        if parsed.path in ('/','/index.html'):
            with open('index.html','rb') as f: body=f.read()
            self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body); return
        if parsed.path=='/api/market':
            p=parse_qs(parsed.query); symbol=p.get('symbol',['NIFTY'])[0]; tf=p.get('tf',['1m'])[0]; expiry=p.get('expiry',[None])[0]
            cache_key=(symbol,tf,expiry)
            if state.market_cache.get('key')==cache_key and time.time()-state.market_cache_ts < 0.10:
                return self._send_json(state.market_cache['data'])
            with state.lock:
                market_errors=[]
                try:
                    chart=state.get_instrument_chart_data(symbol,tf)
                except Exception as e:
                    chart=state.chart_cache.get((symbol,tf)) if isinstance(state.chart_cache.get((symbol,tf)),dict) else None
                    if not chart: chart={'symbol':symbol,'display_title':symbol,'timeframe':tf,'ltp':state.sensex_spot if 'SENSEX' in symbol.upper() else state.nifty_spot,'candles':[],'countdown':'00:00','ema9':0,'ema15':0,'vwap':0,'vwap_series':[],'ema9_series':[],'ema15_series':[],'strategy_markers':[],'greeks':{'delta':1.0,'gamma':0,'theta':0,'vega':0,'iv':0}}
                    market_errors.append('Chart: '+str(e))
                try:
                    chain=state.get_option_chain(symbol,expiry)
                except Exception as e:
                    cached_chain=state.live_chain_cache.get((symbol,expiry))
                    chain=cached_chain[1] if cached_chain else {'chain':[],'expiries':[],'selected_expiry':expiry}
                    market_errors.append('Option chain: '+str(e))
                unreal=sum(x.get('pnl',0) for x in state.positions); trades=list(state.closed_trades); wins=sum(1 for t in trades if t['pnl']>0); gp=sum(t['pnl'] for t in trades if t['pnl']>0); gl=abs(sum(t['pnl'] for t in trades if t['pnl']<0)); pf=round(gp/gl,2) if gl else (gp if gp else 0)
                data={'nifty_spot':state.nifty_spot,'sensex_spot':state.sensex_spot,'nifty_chg':round(state.nifty_spot-state.prev_close,2),'nifty_pct':round((state.nifty_spot-state.prev_close)/state.prev_close*100,2) if state.prev_close else 0,'sensex_chg':round(state.sensex_spot-state.sensex_prev_close,2) if state.sensex_prev_close else 0,'sensex_pct':round((state.sensex_spot-state.sensex_prev_close)/state.sensex_prev_close*100,2) if state.sensex_prev_close else 0,'chart':chart,'chain':chain.get('chain',[]),'expiries':chain.get('expiries',[]),'selected_expiry':chain.get('selected_expiry'),'wallet':{**state.wallet,'unrealized_pnl':round(unreal,2),'net_pnl':round(state.wallet['realized_pnl']+unreal,2)},'analytics':{'win_rate':round(wins/len(trades)*100,1) if trades else 0,'profit_factor':pf,'net_pnl':round(state.wallet['realized_pnl']+unreal,2),'total_trades':len(trades)},'positions':list(state.positions),'pending_orders':list(state.pending_orders),'orders':list(state.orders),'closed_trades':trades[:15],'market_source':'Angel One SmartAPI' if state.angel.enabled else 'Development mode','market_error':' | '.join([x for x in market_errors+[state.angel.last_error if state.angel.enabled else ''] if x]),'bot':state.bot,'bot_positions':len(state._bot_positions())}
                state.market_cache={'key':cache_key,'data':data}; state.market_cache_ts=time.time(); return self._send_json(data)
        if parsed.path=='/api/bot':
            with state.lock:
                body=state.bot.copy(); body['paper_only']=True; body['angel_enabled']=state.angel.enabled; body['open_positions']=len(state._bot_positions())
            return self._send_json(body)
        if parsed.path=='/api/bot/test':
            with state.lock:
                candles=state._bot_underlying_candles(state.bot['underlying']); candles=candles[:-1] if len(candles)>1 else candles; signal,conf,meta=strategy_signal(candles,state.bot['strategy']) if len(candles)>=25 else ('HOLD',0.0,{})
                state.bot['last_signal']=signal; state.bot['last_confidence']=conf; state.bot['signal_meta']=meta; state.bot['last_reason']=state._bot_reason(signal,meta) if meta else 'Waiting for enough candles…'
                return self._send_json({'ok':True,'bot':state.bot,'paper_only':True})
        if parsed.path=='/api/bot/replay':
            p=parse_qs(parsed.query)
            try:
                days=max(1,min(30,int(p.get('days',['5'])[0]))); sl=max(0.1,min(20,float(p.get('sl',[state.bot.get('stop_loss_pct',0.6)])[0]))); tp=max(0.1,min(50,float(p.get('tp',[state.bot.get('target_pct',1.2)])[0])))
            except Exception: days,sl,tp=5,0.6,1.2
            strategy=p.get('strategy',[state.bot.get('strategy','EMA_CROSS')])[0]; under=p.get('underlying',[state.bot.get('underlying','NIFTY')])[0]; mode=p.get('mode',[state.bot.get('instrument_mode','ATM_OPTIONS')])[0]
            try: timeframe=max(60,min(3600,int(p.get('tf',['60'])[0]))); lot_multiplier=max(1,min(20,int(p.get('lots',['1'])[0])))
            except Exception: timeframe,lot_multiplier=60,1
            trade_type=p.get('trade_type',['INTRADAY'])[0].upper(); trade_type='BTST' if trade_type=='BTST' else 'INTRADAY'
            if not state.angel.enabled: return self._send_json({'ok':False,'error':'Replay needs Angel One historical market data. Enable ANGELONE_ENABLED.'},400)
            if mode=='INDEX':
                interval=TF_INTERVALS.get(timeframe,'ONE_MINUTE'); inst=state.angel.find_index(under); candles=state.angel.candles(inst,interval,days) if inst else []
                r=backtest_strategy(candles,strategy,sl,tp,state.bot.get('initial_balance',1000000.0),qty=lot_multiplier,trade_type=trade_type); r['replay']=True; r['underlying']=under; r['mode']='INDEX'; r['data_note']='Historical underlying candles; paper research only.'
            else:
                r=_option_backtest(strategy,under,mode,days,sl,tp,state.bot.get('initial_balance',1000000.0),state.angel,timeframe,lot_multiplier,trade_type); r['replay']=True
            if r.get('ok'): state._store_research('REPLAY',r,days,sl,tp,lot_multiplier)
            return self._send_json(r,200 if r.get('ok') else 400)
        if parsed.path=='/api/backtest':
            p=parse_qs(parsed.query); strategy=p.get('strategy',[state.bot.get('strategy','EMA_CROSS')])[0]
            try: days=max(1,min(60,int(p.get('days',['2'])[0]))); sl=max(0.1,min(20,float(p.get('sl',['0.6'])[0]))); tp=max(0.1,min(50,float(p.get('tp',['1.2'])[0])))
            except Exception: days,sl,tp=2,0.6,1.2
            candles=state._bot_underlying_candles(state.bot.get('underlying','NIFTY'))
            if state.angel.enabled:
                inst=state.angel.find_index(state.bot.get('underlying','NIFTY')); fresh=state.angel.candles(inst,'ONE_MINUTE',days) if inst else []
                if fresh: candles=fresh
            result=backtest_strategy(candles,strategy,sl,tp,state.bot.get('initial_balance',1000000.0))
            return self._send_json(result,200 if result.get('ok') else 400)
        if parsed.path=='/api/bot/compare':
            p=parse_qs(parsed.query)
            try: days=max(1,min(60,int(p.get('days',['10'])[0]))); sl=max(0.1,min(20,float(p.get('sl',[state.bot.get('stop_loss_pct',0.6)])[0]))); tp=max(0.1,min(50,float(p.get('tp',[state.bot.get('target_pct',1.2)])[0])))
            except Exception: days,sl,tp=10,0.6,1.2
            under=p.get('underlying',[state.bot.get('underlying','NIFTY')])[0]
            candles=state._bot_underlying_candles(under)
            if state.angel.enabled:
                inst=state.angel.find_index(under); fresh=state.angel.candles(inst,'ONE_MINUTE',days) if inst else []
                if fresh: candles=fresh
            results=[]
            for stg in STRATEGIES:
                r=backtest_strategy(candles,stg,sl,tp,state.bot.get('initial_balance',1000000.0))
                if r.get('ok'):
                    state.bot['strategy_stats'][stg]={'trades':r.get('trades',0),'wins':r.get('wins',0),'loss':r.get('losses',0),'pnl':r.get('net_pnl',0),'status':'TESTED'}
                results.append({k:r.get(k) for k in ('strategy','trades','wins','losses','win_rate','net_pnl','return_pct','profit_factor','max_drawdown')})
            state._save_state()
            return self._send_json({'ok':True,'days':days,'underlying':under,'results':results})
        if parsed.path=='/api/research/matrix':
            p=parse_qs(parsed.query)
            try: days=max(1,min(30,int(p.get('days',['5'])[0]))); sl=max(0.1,min(20,float(p.get('sl',['0.6'])[0]))); tp=max(0.1,min(50,float(p.get('tp',['1.2'])[0])))
            except Exception: days,sl,tp=5,0.6,1.2
            under=p.get('underlying',[state.bot.get('underlying','NIFTY')])[0]; mode=p.get('mode',[state.bot.get('instrument_mode','ATM_OPTIONS')])[0]; trade_type=p.get('trade_type',['INTRADAY'])[0].upper(); trade_type='BTST' if trade_type=='BTST' else 'INTRADAY'
            try: tfs=[int(x.strip()) for x in p.get('tfs',['60,180,300,600,900,1800,3600'])[0].split(',') if x.strip() and int(x.strip()) in TF_INTERVALS]
            except Exception: tfs=[60,180,300,600,900,1800,3600]
            try: lots=[]
            except Exception: lots=[]
            for raw in p.get('lots',['1,2,5,10'])[0].split(','):
                try:
                    v=max(1,min(20,int(raw.strip())))
                    if v not in lots: lots.append(v)
                except Exception: pass
            if not tfs: return self._send_json({'ok':False,'error':'No valid timeframes selected.'},400)
            if not lots: return self._send_json({'ok':False,'error':'No valid lot sizes selected.'},400)
            if not state.angel.enabled: return self._send_json({'ok':False,'error':'Research matrix needs Angel One historical market data.'},400)
            import uuid
            job_id=uuid.uuid4().hex[:12]
            with MATRIX_JOBS_LOCK:
                # Clean old jobs and reject duplicate work while one is running.
                now=time.time()
                for jid,j in list(MATRIX_JOBS.items()):
                    if now-j.get('created',now)>3600: MATRIX_JOBS.pop(jid,None)
                if any(j.get('status')=='running' for j in MATRIX_JOBS.values()):
                    return self._send_json({'ok':False,'error':'A research matrix is already running. Please wait for it to finish.','running':True},409)
                MATRIX_JOBS[job_id]={'status':'running','created':now,'progress':0,'total':len(tfs)*len(STRATEGIES)*len(lots),'results':[],'errors':[],'params':{'days':days,'underlying':under,'mode':mode,'trade_type':trade_type,'tfs':tfs,'lots':lots}}
            def run_matrix():
                results=[]; errors=[]; shared={}; base_by_tf={}; total=len(tfs)*len(STRATEGIES)*len(lots); started=time.time()
                try:
                    # Prefetch each underlying timeframe once; strategy and lot work is local.
                    for tf in tfs:
                        interval=TF_INTERVALS[tf]; inst=state.angel.find_index(under); candles=state.angel.candles(inst,interval,days) if inst else []
                        if candles: base_by_tf[tf]=candles
                        else: errors.append(f'{tf/60:g}m: no historical candles returned')
                    done=0
                    with MATRIX_JOBS_LOCK: MATRIX_JOBS[job_id]['progress']=0; MATRIX_JOBS[job_id]['prefetch']=True
                    for tf in tfs:
                        candles=base_by_tf.get(tf)
                        if not candles: done += len(STRATEGIES)*len(lots); continue
                        for stg in STRATEGIES:
                            try:
                                bal=state.bot.get('initial_balance',1000000.0)
                                if mode=='INDEX': one=backtest_strategy(candles,stg,sl,tp,bal,qty=1,trade_type=trade_type); one.update({'underlying':under,'mode':'INDEX','timeframe':tf,'lot_multiplier':1})
                                else: one=_option_backtest(stg,under,mode,days,sl,tp,bal,state.angel,tf,1,trade_type,shared,base_override=candles)
                                if not one.get('ok'): raise RuntimeError(one.get('error','failed'))
                                for lm in lots:
                                    r=_scale_research_result(one,lm); r.update({'underlying':under,'mode':mode,'timeframe':tf,'lot_multiplier':lm})
                                    state._store_research('MATRIX',r,days,sl,tp,lm)
                                    results.append({k:r.get(k) for k in ('strategy','underlying','mode','timeframe','trade_type','lot_multiplier','trades','win_rate','net_pnl','profit_factor','max_drawdown','rr_ratio')})
                                    done+=1
                                    with MATRIX_JOBS_LOCK:
                                        elapsed=max(time.time()-started,.001); rate=done/elapsed
                                        MATRIX_JOBS[job_id].update({'progress':done,'results':results[-200:],'errors':errors[-50:],'elapsed':round(elapsed,1),'eta':round(max(total-done,0)/rate,1)})
                            except Exception as e:
                                errors.append(f'{stg} {tf/60:g}m: {e}'); done+=len(lots)
                                with MATRIX_JOBS_LOCK: MATRIX_JOBS[job_id].update({'progress':done,'results':results[-200:],'errors':errors[-50:]})
                    with MATRIX_JOBS_LOCK: MATRIX_JOBS[job_id].update({'status':'done','progress':total,'results':results,'errors':errors,'finished':time.time(),'elapsed':round(time.time()-started,1),'eta':0})
                except Exception as e:
                    with MATRIX_JOBS_LOCK: MATRIX_JOBS[job_id].update({'status':'failed','error':str(e),'results':results,'errors':errors,'finished':time.time()})
            threading.Thread(target=run_matrix,daemon=True,name='research-matrix').start()
            return self._send_json({'ok':True,'async':True,'job_id':job_id,'status':'running','total':len(tfs)*len(STRATEGIES)*len(lots),'days':days,'underlying':under,'mode':mode,'trade_type':trade_type,'starting_balance':state.bot.get('initial_balance',1000000.0),'retention_days':30,'note':'Matrix runs in the background to avoid HTTP/Render timeout. Research is paper-only; no Angel One orders are sent.'})
        if parsed.path=='/api/research/matrix_status':
            p=parse_qs(parsed.query); job_id=p.get('job_id',[''])[0]
            with MATRIX_JOBS_LOCK: job=dict(MATRIX_JOBS.get(job_id,{}))
            if not job: return self._send_json({'ok':False,'error':'Matrix job not found or expired.'},404)
            total=max(1,int(job.get('total',0))); progress=int(job.get('progress',0)); job['percent']=round(progress/total*100,1)
            return self._send_json({'ok':True,'job_id':job_id,**job})
        if parsed.path=='/api/research/intelligence':
            p=parse_qs(parsed.query)
            try: limit=max(20,min(500,int(p.get('limit',['500'])[0])))
            except Exception: limit=500
            try:
                db=state._db()
                rows=db.execute("SELECT id,created_at,strategy,underlying,mode,timeframe,trade_type,lot_multiplier,result_json FROM research_runs WHERE kind='MATRIX' ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
                db.close()
                groups={}
                for row in rows:
                    try: r=json.loads(row[8] or '{}')
                    except Exception: r={}
                    trades=int(r.get('trades') or 0); pnl=float(r.get('net_pnl') or 0); pf=float(r.get('profit_factor') or 0); dd=float(r.get('max_drawdown') or 0); wr=float(r.get('win_rate') or 0); ret=float(r.get('return_pct') or 0); avg_r=float(r.get('avg_r') or 0)
                    if trades<=0: continue
                    key=(row[2],row[3],row[4],int(row[5] or 0),row[6],int(row[7] or 1))
                    g=groups.setdefault(key,{'strategy':row[2],'underlying':row[3],'mode':row[4],'timeframe':int(row[5] or 0),'trade_type':row[6],'lot_multiplier':int(row[7] or 1),'runs':0,'trades':0,'wins':0,'pnl':0.0,'dd':0.0,'pf_sum':0.0,'wr_sum':0.0,'avg_r_sum':0.0,'best_pnl':None,'worst_pnl':None})
                    g['runs']+=1; g['trades']+=trades; g['wins']+=int(r.get('wins') or 0); g['pnl']+=pnl; g['dd']=max(g['dd'],dd); g['pf_sum']+=pf; g['wr_sum']+=wr; g['avg_r_sum']+=avg_r; g['best_pnl']=pnl if g['best_pnl'] is None else max(g['best_pnl'],pnl); g['worst_pnl']=pnl if g['worst_pnl'] is None else min(g['worst_pnl'],pnl)
                ranked=[]
                for g in groups.values():
                    wr=g['wins']/g['trades']*100 if g['trades'] else 0; pf=g['pf_sum']/g['runs'] if g['runs'] else 0; avg_r=g['avg_r_sum']/g['runs'] if g['runs'] else 0
                    consistency=max(0.0,min(1.0,1-(abs(g['best_pnl']-g['worst_pnl'])/(abs(g['pnl'])+abs(g['dd'])+1)))) if g['runs']>1 else 0.5
                    sample=min(1.0,g['trades']/30.0)
                    profit=max(0.0,min(1.0,(g['pnl']/(abs(g['dd'])+1)+1)/3))
                    pf_score=max(0.0,min(1.0,pf/2.5))
                    dd_score=max(0.0,1-min(1.0,g['dd']/(abs(g['pnl'])+abs(g['dd'])+1)))
                    score=100*(0.30*profit+0.25*pf_score+0.20*(wr/100)+0.15*dd_score+0.10*sample*consistency)
                    g.update({'win_rate':round(wr,1),'profit_factor':round(pf,2),'avg_r':round(avg_r,3),'score':round(score,1),'consistency':round(consistency*100,1),'sample_score':round(sample*100,1),'max_drawdown':round(g['dd'],2),'net_pnl':round(g['pnl'],2),'return_pct':round(g['pnl']/state.bot.get('initial_balance',1000000.0)*100,2)})
                    ranked.append(g)
                ranked.sort(key=lambda x:(x['score'],x['net_pnl'],x['profit_factor']),reverse=True)
                consensus={}
                for g in ranked:
                    k=g['strategy']; c=consensus.setdefault(k,{'strategy':k,'tests':0,'positive':0,'trades':0,'pnl':0.0}); c['tests']+=g['runs']; c['positive']+=1 if g['net_pnl']>0 else 0; c['trades']+=g['trades']; c['pnl']+=g['net_pnl']
                for c in consensus.values(): c['positive_rate']=round(c['positive']/c['tests']*100,1) if c['tests'] else 0; c['pnl']=round(c['pnl'],2)
                return self._send_json({'ok':True,'count':len(ranked),'ranked':ranked[:100],'strategy_summary':sorted(consensus.values(),key=lambda x:(x['positive_rate'],x['pnl']),reverse=True),'note':'Ranking uses stored MATRIX research only; it is a research score, not a profitability guarantee. More trades and out-of-sample validation are preferred.'})
            except Exception as e:
                return self._send_json({'ok':False,'error':'Intelligence failed: '+str(e)},500)
        if parsed.path=='/api/research/walkforward':
            p=parse_qs(parsed.query); strategy=p.get('strategy',['EMA_CROSS'])[0]; under=p.get('underlying',[state.bot.get('underlying','NIFTY')])[0]; mode=p.get('mode',['INDEX'])[0]
            try: days=max(5,min(60,int(p.get('days',['30'])[0]))); tf=max(60,min(3600,int(p.get('tf',['300'])[0]))); sl=max(0.1,min(20,float(p.get('sl',['0.6'])[0]))); tp=max(0.1,min(50,float(p.get('tp',['1.2'])[0]))); lots=max(1,min(20,int(p.get('lots',['1'])[0])))
            except Exception: days,tf,sl,tp,lots=30,300,0.6,1.2,1
            if not state.angel.enabled: return self._send_json({'ok':False,'error':'Walk-forward validation needs Angel One historical market data.'},400)
            inst=state.angel.find_index(under); interval=TF_INTERVALS.get(tf,'FIVE_MINUTE'); candles=state.angel.candles(inst,interval,days) if inst else []
            candles=_align_candles(candles)
            if len(candles)<80: return self._send_json({'ok':False,'error':'Not enough candles for train/test split.'},400)
            split=int(len(candles)*0.70); train=candles[:split]; test=candles[split:]
            tr=backtest_strategy(train,strategy,sl,tp,state.bot.get('initial_balance',1000000.0),qty=lots,trade_type='INTRADAY')
            te=backtest_strategy(test,strategy,sl,tp,state.bot.get('initial_balance',1000000.0),qty=lots,trade_type='INTRADAY')
            return self._send_json({'ok':True,'strategy':strategy,'underlying':under,'mode':mode,'timeframe':tf,'lots':lots,'candles':len(candles),'split':split,'train':{k:tr.get(k) for k in ('candles','trades','wins','losses','win_rate','net_pnl','return_pct','profit_factor','max_drawdown','avg_r')},'test':{k:te.get(k) for k in ('candles','trades','wins','losses','win_rate','net_pnl','return_pct','profit_factor','max_drawdown','avg_r')},'note':'70% chronological train / 30% out-of-sample test. No future candles are used to create the test result.'})
        if parsed.path=='/api/research/history':
            p=parse_qs(parsed.query)
            try: limit=max(1,min(500,int(p.get('limit',['200'])[0])))
            except Exception: limit=200
            try:
                db=state._db(); where=[]; args=[]
                for col,key in [('strategy','strategy'),('underlying','underlying'),('mode','mode'),('trade_type','trade_type'),('kind','kind')]:
                    val=p.get(key,[''])[0].strip()
                    if val and val.upper()!='ALL': where.append(col+'=?'); args.append(val.upper())
                sql="SELECT id,created_at,kind,strategy,underlying,mode,timeframe,trade_type,days,sl,tp,rr,lot_multiplier,result_json FROM research_runs"
                if where: sql += ' WHERE ' + ' AND '.join(where)
                sql += ' ORDER BY id DESC LIMIT ?'; args.append(limit)
                rows=db.execute(sql,tuple(args)).fetchall(); db.close()
                out=[]
                for row in rows:
                    out.append({'id':row[0],'created_at':row[1],'kind':row[2],'strategy':row[3],'underlying':row[4],'mode':row[5],'timeframe':row[6],'trade_type':row[7],'days':row[8],'sl':row[9],'tp':row[10],'rr':row[11],'lot_multiplier':row[12],'result':json.loads(row[13]) if row[13] else {}})
                return self._send_json({'ok':True,'runs':out,'retention_days':30,'db_path':state.DB_PATH})
            except Exception as e: return self._send_json({'ok':False,'error':str(e)},500)
        if parsed.path=='/api/research/export.csv':
            p=parse_qs(parsed.query); kind=p.get('kind',['runs'])[0].lower()
            import csv,io
            try:
                db=state._db(); buf=io.StringIO(); w=csv.writer(buf)
                if kind=='trades':
                    w.writerow(['run_id','trade_id','stored_at','trade_json'])
                    rows=db.execute("SELECT run_id,id,created_at,trade_json FROM research_trades ORDER BY run_id DESC,id").fetchall()
                    for r in rows: w.writerow(r)
                    filename='research_trades_30d.csv'
                else:
                    w.writerow(['run_id','created_at','kind','strategy','underlying','mode','timeframe_sec','trade_type','days','stop_pct','target_pct','rr','lot_multiplier','candles','trades','wins','losses','win_rate','net_pnl','return_pct','profit_factor','max_drawdown'])
                    rows=db.execute("SELECT id,created_at,kind,strategy,underlying,mode,timeframe,trade_type,days,sl,tp,rr,lot_multiplier,result_json FROM research_runs ORDER BY id DESC").fetchall()
                    for r in rows:
                        z=json.loads(r[13]) if r[13] else {}
                        w.writerow([r[0],r[1],r[2],r[3],r[4],r[5],r[6],r[7],r[8],r[9],r[10],r[11],r[12],z.get('candles'),z.get('trades'),z.get('wins'),z.get('losses'),z.get('win_rate'),z.get('net_pnl'),z.get('return_pct'),z.get('profit_factor'),z.get('max_drawdown')])
                    filename='research_runs_30d.csv'
                db.close(); body=buf.getvalue().encode('utf-8-sig'); self.send_response(200); self.send_header('Content-Type','text/csv; charset=utf-8'); self.send_header('Content-Disposition','attachment; filename="'+filename+'"'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body); return
            except Exception as e: return self._send_json({'ok':False,'error':str(e)},500)
        if parsed.path=='/api/research/trades':
            p=parse_qs(parsed.query)
            try: run_id=int(p.get('run_id',['0'])[0])
            except Exception: run_id=0
            try:
                db=state._db(); rows=db.execute("SELECT id,created_at,trade_json FROM research_trades WHERE run_id=? ORDER BY id",(run_id,)).fetchall(); db.close(); return self._send_json({'ok':True,'run_id':run_id,'trades':[{'id':r[0],'created_at':r[1],'trade':json.loads(r[2])} for r in rows]})
            except Exception as e: return self._send_json({'ok':False,'error':str(e)},500)
        if parsed.path=='/api/tick':
            p=parse_qs(parsed.query); symbol=p.get('symbol',['NIFTY'])[0]; tf=p.get('tf',['1m'])[0]
            try:
                return self._send_json(state.get_tick_data(symbol,tf))
            except Exception as e:
                return self._send_json({'symbol':symbol,'ltp':0,'countdown':'00:00','error':str(e)},200)
        if parsed.path=='/health': self._send_json({'ok':True,'angel_enabled':state.angel.enabled,'angel_error':state.angel.last_error}); return
        self.send_error(404)
    def do_POST(self):
        parsed=urlparse(self.path); n=int(self.headers.get('Content-Length',0)); raw=self.rfile.read(n).decode() if n else '{}'
        try: payload=json.loads(raw)
        except: payload={}
        with state.lock:
            if parsed.path=='/api/bot/control':
                action=str(payload.get('action','')).upper()
                if action=='START':
                    state._reset_bot_session_if_needed()
                    if state.bot.get('risk_lock'): return self._send_json({'ok':False,'error':state.bot.get('risk_lock_reason','Risk lock active'),'bot':state.bot,'bot_positions':len(state._bot_positions())},400)
                    state.bot['enabled']=True; state.bot['last_reason']='Bot running — waiting for a qualified signal…'
                elif action=='STOP': state.bot['enabled']=False; state.bot['last_reason']='Bot stopped'
                elif action=='RESET_RISK': state.bot['risk_lock']=False; state.bot['risk_lock_reason']=''; state.bot['daily_pnl']=0.0; state.bot['trades_today']=0; state.bot['trade_count_today']=0; state.bot['last_reason']='Risk counters reset'
                elif action=='CONFIG':
                    for k in ('strategy','underlying','instrument_mode'):
                        if k in payload and payload[k] in (list(STRATEGIES) if k=='strategy' else ['NIFTY','SENSEX'] if k=='underlying' else ['INDEX','ATM_OPTIONS','ITM_1','ITM_2','ITM_3','OTM_1','OTM_2','OTM_3']): state.bot[k]=payload[k]
                    for k in ('qty','risk_per_trade','max_daily_loss','stop_loss_pct','target_pct','max_trades_per_day','max_open_positions','cooldown_sec'):
                        if k in payload:
                            try: state.bot[k]=max(0.01,float(payload[k]))
                            except: pass
                    state.bot['qty']=int(max(1,state.bot.get('qty',1))); state.bot['max_trades_per_day']=int(max(1,state.bot.get('max_trades_per_day',5))); state.bot['max_open_positions']=int(max(1,state.bot.get('max_open_positions',1))); state.bot['cooldown_sec']=int(max(5,state.bot.get('cooldown_sec',60)))
                state._save_state(); self._send_json({'ok':True,'bot':{**state.bot,'open_positions':len(state._bot_positions())},'paper_only':True}); return
            if parsed.path=='/api/order':
                symbol=payload.get('symbol','NIFTY'); action=payload.get('action','BUY'); qty=int(payload.get('qty',65)); ot=payload.get('order_type','MARKET'); lp=float(payload.get('limit_price',0)); sl=float(payload.get('stop_loss',0)); tp=float(payload.get('target',0)); tsl=float(payload.get('trailing_sl',0)); ltp,_,_=state._get_live_instrument_ltp(symbol)
                if not ltp: return self._send_json({'error':'Live price unavailable'},400)
                if ot=='LIMIT' and not ((action=='BUY' and ltp<=lp) or (action=='SELL' and ltp>=lp)):
                    oid=f'ord_{state.order_counter}'; state.order_counter+=1; state.pending_orders.append({'id':oid,'symbol':symbol,'action':action,'qty':qty,'limit_price':lp,'order_type':'LIMIT','stop_loss':sl,'target':tp,'trailing_sl':tsl,'time':datetime.now(IST).strftime('%H:%M:%S')}); state._save_state()
                else: state._execute_fill(symbol,action,qty,ltp if ot=='MARKET' else lp,sl,tp,tsl)
                invalidate_market_cache(); self._send_json({'status':'ok'}); return
            if parsed.path=='/api/exit':
                before=len(state.positions); state._internal_exit(payload.get('id')); invalidate_market_cache();
                self._send_json({'status':'ok' if len(state.positions)<before else 'not_found'}, 200 if len(state.positions)<before else 404); return
            if parsed.path=='/api/cancel_order': state.pending_orders=[x for x in state.pending_orders if x['id']!=payload.get('id')]; state._save_state(); invalidate_market_cache(); self._send_json({'status':'ok'}); return
            if parsed.path=='/api/reset': state.reset(); invalidate_market_cache(); self._send_json({'status':'ok'}); return
            if parsed.path=='/api/update_brackets':
                for p in state.positions:
                    if p['id']==payload.get('id'):
                        for k in ('stop_loss','target','trailing_sl'):
                            if k in payload: p[k]=float(payload[k])
                state._save_state(); invalidate_market_cache(); self._send_json({'status':'ok'}); return
            self._send_json({'status':'ok'})

class Server(socketserver.ThreadingMixIn,socketserver.TCPServer):
    allow_reuse_address=True; daemon_threads=True

if __name__=='__main__':
    PORT=int(os.getenv('PORT',8000)); print('Server running on port',PORT); print('MARKET SOURCE:', 'Angel One SmartAPI' if state.angel.enabled else 'Development mode'); Server(('0.0.0.0',PORT),Handler).serve_forever()
