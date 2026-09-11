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
        self._load_master()
        if self.enabled:
            if self.login():
                self._start_websocket()

    def _request(self, url, payload=None, headers=None, timeout=8):
        data = None if payload is None else json.dumps(payload).encode()
        h = {'Content-Type':'application/json','Accept':'application/json', **(headers or {})}
        req=urllib.request.Request(url,data=data,headers=h,method='POST' if payload is not None else 'GET')
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())

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
        exch = 'bse_cm' if name == 'SENSEX' else 'nse_cm'
        candidates=[x for x in self.instruments if str(x.get('exch_seg','')).lower()==exch and str(x.get('instrumenttype','')).upper() in ('INDEX','') and (str(x.get('symbol','')).upper()==name or str(x.get('name','')).upper()==name)]
        if candidates: return candidates[0]
        fallback={'NIFTY':os.getenv('ANGELONE_NIFTY_TOKEN','99926000'),'SENSEX':os.getenv('ANGELONE_SENSEX_TOKEN','99919000')}
        tok=fallback.get(name)
        return {'token':tok,'symbol':name,'name':name,'exch_seg':'BSE' if name=='SENSEX' else 'NSE','exch_seg_raw':exch,'instrumenttype':'INDEX','lotsize':'1'} if tok else None

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

    def candles(self, inst, interval='ONE_MINUTE', days=2):
        if not self.enabled or not self.jwt or not inst: return []
        end=datetime.now(IST); start=end-timedelta(days=days)
        body={'exchange':inst['exch_seg'].upper(),'symboltoken':str(inst['token']),'interval':interval,
              'fromdate':start.strftime('%Y-%m-%d %H:%M'),'todate':end.strftime('%Y-%m-%d %H:%M')}
        try:
            res=self._request(CANDLE_URL,body,self._auth_headers())
            rows=res.get('data') or []
            return [{'time':int(datetime.fromisoformat(str(r[0]).replace('Z','+00:00')).timestamp()) if isinstance(r[0],str) else int(r[0]),
                     'is_prev_day':False,'open':float(r[1]),'high':float(r[2]),'low':float(r[3]),'close':float(r[4]),'volume':int(float(r[5] or 0))} for r in rows]
        except Exception as e:
            self.last_error=str(e); return []

    def option_instruments(self, underlying, expiry):
        u=underlying.upper()
        return [x for x in self.instruments if str(x.get('exch_seg','')).upper()=='NFO' and str(x.get('instrumenttype','')).upper()=='OPTIDX' and str(x.get('name','')).upper()==u and str(x.get('expiry','')).upper()==expiry.upper() and str(x.get('symbol','')).upper().endswith(('CE','PE'))]

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


class SimulationState:
    def __init__(self):
        self.lock=threading.Lock(); self.nifty_spot=23765.0; self.sensex_spot=81200.0; self.prev_close=23897.70; self.sensex_prev_close=0.0
        self.wallet={'initial':100000.0,'balance':100000.0,'used_margin':0.0,'realized_pnl':0.0}
        self.positions=[]; self.pending_orders=[]; self.orders=[]; self.closed_trades=[]; self.candles_1m=[]; self.order_counter=100
        self.live_chain_cache={}; self.chart_cache={}; self.market_cache={}; self.market_cache_ts=0; self.angel=AngelOneData(); self._init_history(); self._running=True
        threading.Thread(target=self._tick_loop,daemon=True).start()

    def _init_history(self):
        if self.angel.enabled:
            inst=self.angel.find_index('NIFTY'); c=self.angel.candles(inst,'ONE_MINUTE',2)
            if c:
                self.candles_1m=c; q=self.angel.quote([inst]); z=q.get(str(inst['token']))
                if z: self.nifty_spot=float(z.get('ltp',self.nifty_spot)); self.prev_close=float(z.get('close',self.prev_close))
                self.sensex_spot=self._sensex_ltp(); self.sensex_prev_close=self.sensex_spot; return
        # Existing development fallback remains isolated to ANGELONE_ENABLED=0.
        now_ts=int(time.time()); cur=(now_ts//60)*60; p=self.nifty_spot; out=[]
        for i in range(90):
            t=cur-(89-i)*60; c=round(p+random.uniform(-2,2),2); o=p; h=max(o,c)+random.random(); l=min(o,c)-random.random(); out.append({'time':t,'is_prev_day':False,'open':o,'high':h,'low':l,'close':c,'volume':random.randint(1000,5000)}); p=c
        self.candles_1m=out

    def _sensex_ltp(self):
        if not self.angel.enabled: return round(self.nifty_spot*3.41,2)
        inst=self.angel.find_index('SENSEX'); q=self.angel.quote([inst]) if inst else {}; z=q.get(str(inst['token'])) if inst else None
        return float(z['ltp']) if z and z.get('ltp') is not None else self.sensex_spot

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
            if ltp is not None: setattr(self,attr,float(ltp))

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
                        if p.get('stop_loss') and p['ltp']<=p['stop_loss']: self._internal_exit(p['id'],'SL')
                        elif p.get('target') and p['ltp']>=p['target']: self._internal_exit(p['id'],'TARGET')
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

    def _execute_fill(self,symbol,action,qty,price,sl=0,target=0,tsl=0):
        pos_id=f'pos_{self.order_counter}'; self.order_counter+=1; ltp,delta,theta=self._get_live_instrument_ltp(symbol); cost=round(price*qty,2)
        self.wallet['balance']=round(self.wallet['balance']-cost,2); self.wallet['used_margin']=round(self.wallet['used_margin']+cost,2)
        self.positions.append({'id':pos_id,'symbol':symbol,'action':action,'qty':qty,'buy_price':price,'ltp':ltp,'pnl':0.0,'stop_loss':sl,'target':target,'trailing_sl':tsl,'delta':delta,'theta':theta})
        self.orders.insert(0,{'id':pos_id,'time':datetime.now(IST).strftime('%H:%M:%S'),'symbol':symbol,'action':action,'qty':qty,'price':price,'status':'FILLED'})

    def _internal_exit(self,pos_id,reason='MANUAL'):
        for i,p in enumerate(self.positions):
            if p['id']==pos_id:
                p=self.positions.pop(i); cost=round(p['buy_price']*p['qty'],2); pnl=p['pnl']; self.wallet['used_margin']=max(0,round(self.wallet['used_margin']-cost,2)); self.wallet['balance']=round(self.wallet['balance']+cost+pnl,2); self.wallet['realized_pnl']=round(self.wallet['realized_pnl']+pnl,2)
                self.closed_trades.insert(0,{'id':p['id'],'symbol':p['symbol'],'action':p['action'],'qty':p['qty'],'buy_price':p['buy_price'],'exit_price':p['ltp'],'pnl':pnl,'reason':reason,'time':datetime.now(IST).strftime('%H:%M:%S')}); return

    def _resample(self,candles,tf):
        if tf<=60:return candles
        buckets={}
        for c in candles:
            bt=(c['time']//tf)*tf
            if bt not in buckets: buckets[bt]={'time':bt,'is_prev_day':False,'open':c['open'],'high':c['high'],'low':c['low'],'close':c['close'],'volume':c['volume']}
            else:
                b=buckets[bt]; b['high']=max(b['high'],c['high']); b['low']=min(b['low'],c['low']); b['close']=c['close']; b['volume']+=c['volume']
        return list(buckets.values())

    def get_instrument_chart_data(self,symbol,timeframe='1m'):
        is_s='SENSEX' in symbol.upper(); spot=self.sensex_spot if is_s else self.nifty_spot; tf={'1m':60,'3m':180,'5m':300,'15m':900}.get(timeframe,60)
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
                    interval={60:'ONE_MINUTE',180:'THREE_MINUTE',300:'FIVE_MINUTE',900:'FIFTEEN_MINUTE'}[tf]
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
        return {'symbol':symbol,'display_title':display,'timeframe':timeframe,'ltp':ltp,'candles':candles,'countdown':countdown,'ema9':calc_ema_series(closes,9)[-1] if closes else 0,'ema15':calc_ema_series(closes,15)[-1] if closes else 0,'vwap':calc_vwap_series(candles)[-1] if candles else 0,'vwap_series':calc_vwap_series(candles),'ema9_series':calc_ema_series(closes,9),'ema15_series':calc_ema_series(closes,15),'greeks':greeks}

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
        self.wallet={'initial':100000.0,'balance':100000.0,'used_margin':0.0,'realized_pnl':0.0}; self.positions=[]; self.pending_orders=[]; self.orders=[]; self.closed_trades=[]

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
                chart=state.get_instrument_chart_data(symbol,tf); chain=state.get_option_chain(symbol,expiry); unreal=sum(x.get('pnl',0) for x in state.positions); trades=list(state.closed_trades); wins=sum(1 for t in trades if t['pnl']>0); gp=sum(t['pnl'] for t in trades if t['pnl']>0); gl=abs(sum(t['pnl'] for t in trades if t['pnl']<0)); pf=round(gp/gl,2) if gl else (gp if gp else 0)
                data={'nifty_spot':state.nifty_spot,'sensex_spot':state.sensex_spot,'nifty_chg':round(state.nifty_spot-state.prev_close,2),'nifty_pct':round((state.nifty_spot-state.prev_close)/state.prev_close*100,2),'sensex_chg':round(state.sensex_spot-state.sensex_prev_close,2) if state.sensex_prev_close else 0,'sensex_pct':round((state.sensex_spot-state.sensex_prev_close)/state.sensex_prev_close*100,2) if state.sensex_prev_close else 0,'chart':chart,'chain':chain['chain'],'expiries':chain['expiries'],'selected_expiry':chain['selected_expiry'],'wallet':{**state.wallet,'unrealized_pnl':round(unreal,2),'net_pnl':round(state.wallet['realized_pnl']+unreal,2)},'analytics':{'win_rate':round(wins/len(trades)*100,1) if trades else 0,'profit_factor':pf,'net_pnl':round(state.wallet['realized_pnl']+unreal,2),'total_trades':len(trades)},'positions':list(state.positions),'pending_orders':list(state.pending_orders),'orders':list(state.orders),'closed_trades':trades[:15],'market_source':'Angel One SmartAPI' if state.angel.enabled else 'Development mode','market_error':state.angel.last_error if state.angel.enabled else ''}
                state.market_cache={'key':cache_key,'data':data}; state.market_cache_ts=time.time(); return self._send_json(data)
        if parsed.path=='/health': self._send_json({'ok':True,'angel_enabled':state.angel.enabled,'angel_error':state.angel.last_error}); return
        self.send_error(404)
    def do_POST(self):
        parsed=urlparse(self.path); n=int(self.headers.get('Content-Length',0)); raw=self.rfile.read(n).decode() if n else '{}'
        try: payload=json.loads(raw)
        except: payload={}
        with state.lock:
            if parsed.path=='/api/order':
                symbol=payload.get('symbol','NIFTY'); action=payload.get('action','BUY'); qty=int(payload.get('qty',65)); ot=payload.get('order_type','MARKET'); lp=float(payload.get('limit_price',0)); sl=float(payload.get('stop_loss',0)); tp=float(payload.get('target',0)); tsl=float(payload.get('trailing_sl',0)); ltp,_,_=state._get_live_instrument_ltp(symbol)
                if not ltp: return self._send_json({'error':'Live price unavailable'},400)
                if ot=='LIMIT' and not ((action=='BUY' and ltp<=lp) or (action=='SELL' and ltp>=lp)):
                    oid=f'ord_{state.order_counter}'; state.order_counter+=1; state.pending_orders.append({'id':oid,'symbol':symbol,'action':action,'qty':qty,'limit_price':lp,'order_type':'LIMIT','stop_loss':sl,'target':tp,'trailing_sl':tsl,'time':datetime.now(IST).strftime('%H:%M:%S')})
                else: state._execute_fill(symbol,action,qty,ltp if ot=='MARKET' else lp,sl,tp,tsl)
                invalidate_market_cache(); self._send_json({'status':'ok'}); return
            if parsed.path=='/api/exit':
                before=len(state.positions); state._internal_exit(payload.get('id')); invalidate_market_cache();
                self._send_json({'status':'ok' if len(state.positions)<before else 'not_found'}, 200 if len(state.positions)<before else 404); return
            if parsed.path=='/api/cancel_order': state.pending_orders=[x for x in state.pending_orders if x['id']!=payload.get('id')]; invalidate_market_cache(); self._send_json({'status':'ok'}); return
            if parsed.path=='/api/reset': state.reset(); invalidate_market_cache(); self._send_json({'status':'ok'}); return
            if parsed.path=='/api/update_brackets':
                for p in state.positions:
                    if p['id']==payload.get('id'):
                        for k in ('stop_loss','target','trailing_sl'):
                            if k in payload: p[k]=float(payload[k])
                invalidate_market_cache(); self._send_json({'status':'ok'}); return
            self._send_json({'status':'ok'})

class Server(socketserver.ThreadingMixIn,socketserver.TCPServer):
    allow_reuse_address=True; daemon_threads=True

if __name__=='__main__':
    PORT=int(os.getenv('PORT',8000)); print('Server running on port',PORT); print('MARKET SOURCE:', 'Angel One SmartAPI' if state.angel.enabled else 'Development mode'); Server(('0.0.0.0',PORT),Handler).serve_forever()
