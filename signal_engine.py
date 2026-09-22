"""TradeLab deterministic rule-based options signal engine.
Simulation/research only. Never places broker orders.
"""
from dataclasses import dataclass
import math, time

@dataclass
class EngineConfig:
    ema_fast:int=9; ema_slow:int=15; ema_trend:int=50; ema_long:int=200
    rsi_period:int=14; atr_period:int=14; swing_lookback:int=3
    volume_lookback:int=20; volume_expansion:float=1.20
    breakout_atr_mult:float=0.15; min_history:int=60
    min_score:float=75; min_score_gap:float=10; min_rr:float=2
    min_delta:float=.35; max_delta:float=.70; max_abs_theta:float=8
    max_spread_pct:float=2.5; min_option_volume:float=100; min_option_oi:float=1000
    max_iv:float=60; cooldown_seconds:int=180
    major_sr_buffer_atr:float=.20
    def __post_init__(self):
        self.weights={'trend':20,'structure':20,'momentum':15,'sr':10,'volume':10,'option_chain':15,'option_quality':10}

def n(x,d=0.0):
    try:
        x=float(x); return x if math.isfinite(x) else d
    except: return d

def ema(a,p):
    if not a:return []
    k=2/(p+1); out=[a[0]]
    for x in a[1:]:out.append(x*k+out[-1]*(1-k))
    return out

def rsi(a,p=14):
    if len(a)<p+1:return None
    g=l=0
    for x,y in zip(a[-p-1:-1],a[-p:]):
        d=y-x;g+=max(d,0);l+=max(-d,0)
    return 100 if l==0 else 100-100/(1+g/l)

def atr(cs,p=14):
    if len(cs)<p+1:return None
    tr=[];prev=None
    for c in cs[-p-1:]:
        h=n(c.get('high'));lo=n(c.get('low'));cl=n(c.get('close'))
        tr.append(max(h-lo,abs(h-prev),abs(lo-prev)) if prev is not None else h-lo);prev=cl
    return sum(tr[-p:])/p

def vwap(cs):
    pv=vol=0;day=None;out=None
    for c in cs:
        try:d=time.strftime('%Y-%m-%d',time.localtime(n(c.get('time'))))
        except:d=None
        if day and d!=day:pv=vol=0
        day=d;v=n(c.get('volume'))
        if v>0:pv+=((n(c.get('high'))+n(c.get('low'))+n(c.get('close')))/3)*v;vol+=v;out=pv/vol
    return out

def _slope(a,nv=6): return (a[-1]-a[-1-nv])/nv if len(a)>nv else 0

def _swings(cs,k=3):
    hi=[];lo=[]
    for i in range(k,len(cs)-k):
        h=n(cs[i].get('high'));l=n(cs[i].get('low'))
        if h>=max(n(x.get('high')) for x in cs[i-k:i+k+1]):hi.append(h)
        if l<=min(n(x.get('low')) for x in cs[i-k:i+k+1]):lo.append(l)
    return hi,lo

def analyze_tf(cs,cfg):
    if len(cs)<cfg.min_history:return {'trend':'NEUTRAL','rsi':None,'atr':None,'vwap':None}
    a=[n(x.get('close')) for x in cs];f=ema(a,cfg.ema_fast)[-1];s=ema(a,cfg.ema_slow)[-1];e50=ema(a,cfg.ema_trend)[-1]
    e200=ema(a,cfg.ema_long)[-1] if len(a)>=cfg.ema_long else None;vv=vwap(cs);rr=rsi(a,cfg.rsi_period);sl=_slope(ema(a,cfg.ema_fast))
    bull=(f>s)+(f>e50)+(e200 is not None and f>e200)+(sl>0)+(vv is not None and a[-1]>vv)
    bear=(f<s)+(f<e50)+(e200 is not None and f<e200)+(sl<0)+(vv is not None and a[-1]<vv)
    return {'trend':'BULLISH' if bull>=3 and bull>bear else 'BEARISH' if bear>=3 and bear>bull else 'NEUTRAL','rsi':rr,'atr':atr(cs,cfg.atr_period),'vwap':vv,'bull_votes':bull,'bear_votes':bear}

def structure(cs,cfg):
    hi,lo=_swings(cs,cfg.swing_lookback)
    if len(hi)<2 or len(lo)<2:return {'state':'UNCLEAR'}
    hh=hi[-1]>hi[-2];hl=lo[-1]>lo[-2];lh=hi[-1]<hi[-2];ll=lo[-1]<lo[-2]
    state='BULLISH_STRUCTURE' if hh and hl else 'BEARISH_STRUCTURE' if lh and ll else 'UNCLEAR'
    if len(cs)>=20 and (max(n(x.get('high')) for x in cs[-20:])-min(n(x.get('low')) for x in cs[-20:])) <= (atr(cs,cfg.atr_period) or 0)*4:state='RANGE'
    return {'state':state,'swing_high':hi[-1],'swing_low':lo[-1]}

def momentum(cs,cfg):
    a=[n(x.get('close')) for x in cs];rr=rsi(a,cfg.rsi_period);m=ema(a,12);s=ema(a,26);hist=(m[-1]-s[-1])-(m[-2]-s[-2]) if len(a)>26 else 0;sl=_slope(ema(a,cfg.ema_fast));roc=((a[-1]/a[-6])-1)*100 if len(a)>6 and a[-6] else 0
    state='OVEREXTENDED' if rr is not None and (rr>=80 or rr<=20) else 'BULLISH' if (rr or 50)>52 and hist>0 and sl>0 and roc>0 else 'BEARISH' if (rr or 50)<48 and hist<0 and sl<0 and roc<0 else 'NEUTRAL'
    return {'state':state,'rsi':rr,'roc':roc,'macd_hist_accel':hist,'ema_slope':sl}

def volume(cs,cfg):
    v=[n(x.get('volume')) for x in cs]
    if len(v)<cfg.volume_lookback+1:return {'state':'NEUTRAL','available':False,'ratio':None}
    av=sum(v[-cfg.volume_lookback-1:-1])/cfg.volume_lookback
    r=v[-1]/av if av else None
    return {'state':'STRONG_CONFIRMATION' if r is not None and r>=cfg.volume_expansion else 'WEAK_CONFIRMATION' if r is not None and r>=.8 else 'NEUTRAL','available':bool(av),'ratio':r,'current':v[-1],'average':av}

def sr(cs,cfg):
    a=atr(cs,cfg.atr_period) or max(n(cs[-1].get('close'))*.001,.01);last=n(cs[-1].get('close'))
    hi=max(n(x.get('high')) for x in cs[-20:]);lo=min(n(x.get('low')) for x in cs[-20:]);sup=lo;res=hi
    return {'support':sup,'resistance':res,'near_support':(last-sup)/a<=cfg.major_sr_buffer_atr,'near_resistance':(res-last)/a<=cfg.major_sr_buffer_atr,'atr':a}

def chain_bias(rows,cfg):
    if not rows:return {'bias':'UNAVAILABLE'}
    def s(p,k):return sum(n(r.get(p+'_'+k)) for r in rows)
    co,po=s('ce','oi'),s('pe','oi');cc,pc=s('ce','chg_oi'),s('pe','chg_oi')
    bias='BEARISH' if cc>pc*1.15 else 'BULLISH' if pc>cc*1.15 else 'BULLISH' if po<co*.85 else 'BEARISH' if co<po*.85 else 'NEUTRAL'
    return {'bias':bias,'call_oi':co,'put_oi':po,'call_chg_oi':cc,'put_chg_oi':pc,'pcr':po/co if co else None}

def _spread(r,p):
    b=n(r.get(p+'_bid'),None);a=n(r.get(p+'_ask'),None);l=n(r.get(p+'_ltp'),None)
    return (a-b)/l*100 if b is not None and a is not None and l and a>=b else None

def select_strike(rows,direction,cfg):
    p='ce' if direction=='CALL' else 'pe';best=None
    for r in rows:
        l=n(r.get(p+'_ltp'));d=n(r.get(p+'_delta'),None);iv=n(r.get(p+'_iv'),None);vol=n(r.get(p+'_volume'),0);oi=n(r.get(p+'_oi'),0);th=n(r.get(p+'_theta'),None);sp=_spread(r,p)
        if not l or d is None or abs(d)<cfg.min_delta or abs(d)>cfg.max_delta or vol<cfg.min_option_volume or oi<cfg.min_option_oi:continue
        if iv is not None and iv>cfg.max_iv:continue
        if sp is not None and sp>cfg.max_spread_pct:continue
        q=100*(.35*max(0,1-abs(abs(d)-.52)/.30)+.20*min(1,vol/(cfg.min_option_volume*5))+.15*min(1,oi/(cfg.min_option_oi*5))+.15*(1 if sp is None else max(0,1-sp/cfg.max_spread_pct))+.15*(1 if th is None else max(0,1-abs(th)/cfg.max_abs_theta)))
        if best is None or q>best[0]:best=(q,r)
    if not best:return None
    q,r=best;return {'strike_quality_score':round(q,1),'strike':r.get('strike'),'expiry':r.get('expiry'),'option_type':direction,'premium':n(r.get(p+'_ltp')),'delta':n(r.get(p+'_delta')),'gamma':n(r.get(p+'_gamma'),None),'theta':n(r.get(p+'_theta'),None),'vega':n(r.get(p+'_vega'),None),'iv':n(r.get(p+'_iv'),None),'oi':n(r.get(p+'_oi'),None),'oi_change':n(r.get(p+'_chg_oi'),None),'volume':n(r.get(p+'_volume'),None),'spread_pct':_spread(r,p),'symbol':r.get(p+'_symbol'),'token':r.get(p+'_token')}

class SignalEngine:
    def __init__(self,cfg=None):self.cfg=cfg or EngineConfig();self.last={}
    def evaluate(self,symbol,tf_data,chain,underlying_price,expiry=None,risk=None,market_open=True,now=None):
        now=time.time() if now is None else now;reasons=[];warnings=[]
        if not market_open:reasons.append('MARKET_CLOSED')
        if n(underlying_price)<=0:reasons.append('INVALID_UNDERLYING')
        for tf in ('1h','15m','5m'):
            if len(tf_data.get(tf,[]))<self.cfg.min_history:reasons.append('INSUFFICIENT_'+tf.upper()+'_HISTORY')
        if not chain:reasons.append('OPTION_CHAIN_UNAVAILABLE')
        if reasons:return self._out('NO_TRADE',symbol,underlying_price,expiry,reasons,warnings,{},None,0,0,now)
        t={k:analyze_tf(tf_data[k],self.cfg) for k in ('1h','15m','5m')};tr=[x['trend'] for x in t.values()]
        trend='BULLISH' if tr.count('BULLISH')>=2 and tr.count('BEARISH')==0 else 'BEARISH' if tr.count('BEARISH')>=2 and tr.count('BULLISH')==0 else 'NEUTRAL'
        st=structure(tf_data['5m'],self.cfg);mo=momentum(tf_data['5m'],self.cfg);vo=volume(tf_data['5m'],self.cfg);srx=sr(tf_data['5m'],self.cfg);oc=chain_bias(chain,self.cfg)
        if trend=='NEUTRAL':warnings.append('MULTI_TIMEFRAME_CONFLICT')
        if st['state'] in ('UNCLEAR','RANGE'):reasons.append('UNCLEAR_STRUCTURE')
        if vo['state']!='STRONG_CONFIRMATION':warnings.append('WEAK_VOLUME_CONFIRMATION')
        if mo['state']=='OVEREXTENDED':warnings.append('MOMENTUM_OVEREXTENDED')
        c=p=0;w=self.cfg.weights
        if trend=='BULLISH':c+=w['trend']
        if trend=='BEARISH':p+=w['trend']
        if st['state']=='BULLISH_STRUCTURE':c+=w['structure']
        if st['state']=='BEARISH_STRUCTURE':p+=w['structure']
        if mo['state']=='BULLISH':c+=w['momentum']
        if mo['state']=='BEARISH':p+=w['momentum']
        if trend=='BULLISH' and not srx['near_resistance']:c+=w['sr']
        if trend=='BEARISH' and not srx['near_support']:p+=w['sr']
        if vo['state']=='STRONG_CONFIRMATION':c+=w['volume'] if trend=='BULLISH' else 0;p+=w['volume'] if trend=='BEARISH' else 0
        if oc.get('bias')=='BULLISH':c+=w['option_chain']
        if oc.get('bias')=='BEARISH':p+=w['option_chain']
        direction='CALL' if c>=p else 'PUT';pick=select_strike(chain,direction,self.cfg)
        if not pick:reasons.append('NO_QUALIFYING_STRIKE')
        else:
            if pick['strike_quality_score']<60:reasons.append('LOW_STRIKE_QUALITY')
            q=min(10,pick['strike_quality_score']*.10);c+=q if direction=='CALL' else 0;p+=q if direction=='PUT' else 0
            if pick.get('theta') is not None and abs(pick['theta'])>self.cfg.max_abs_theta:reasons.append('EXCESSIVE_THETA')
            if pick.get('iv') is not None and pick['iv']>self.cfg.max_iv:reasons.append('ABNORMAL_IV')
            if pick.get('spread_pct') is not None and pick['spread_pct']>self.cfg.max_spread_pct:reasons.append('WIDE_OPTION_SPREAD')
        score=max(c,p);gap=abs(c-p)
        if score<self.cfg.min_score:reasons.append('SCORE_BELOW_THRESHOLD')
        if gap<self.cfg.min_score_gap:reasons.append('SCORE_GAP_TOO_SMALL')
        if direction=='CALL' and srx['near_resistance']:reasons.append('TOO_CLOSE_TO_RESISTANCE')
        if direction=='PUT' and srx['near_support']:reasons.append('TOO_CLOSE_TO_SUPPORT')
        if risk and any(bool(v) for v in risk.values() if isinstance(v,bool)):reasons.append('RISK_LIMIT_BLOCKED')
        if direction in ('CALL','PUT') and not (direction=='CALL' and trend=='BULLISH' or direction=='PUT' and trend=='BEARISH'):reasons.append('TREND_NOT_ALIGNED')
        if direction=='CALL' and mo['state']!='BULLISH':reasons.append('MOMENTUM_NOT_CONFIRMED')
        if direction=='PUT' and mo['state']!='BEARISH':reasons.append('MOMENTUM_NOT_CONFIRMED')
        if vo['state']!='STRONG_CONFIRMATION':reasons.append('UNCONFIRMED_VOLUME')
        premium=pick.get('premium') if pick else None;sl=round(premium*.94,2) if premium else None;entry=round(premium*1.005,2) if premium else None;target=round(entry+(entry-sl)*2,2) if entry and sl else None;rr=((target-entry)/(entry-sl)) if entry and sl and target else 0
        if rr<self.cfg.min_rr:reasons.append('POOR_RR')
        signal='NO_TRADE' if reasons or score<self.cfg.min_score or gap<self.cfg.min_score_gap else direction
        conf=max(0,min(100,score*.65+gap*2+(15 if trend!='NEUTRAL' else 0)))
        return self._out(signal,symbol,underlying_price,expiry,reasons,warnings,{'timeframes':t,'structure':st,'momentum':mo,'volume':vo,'support_resistance':srx,'option_chain':oc},pick,c,p,now,entry,sl,target,rr,conf)
    def _out(self,signal,symbol,spot,expiry,reasons,warnings,a,pick,c,p,ts,entry=None,sl=None,target=None,rr=0,conf=0):
        return {'signal':signal,'status':signal,'symbol':symbol,'expiry':pick.get('expiry') if pick else expiry,'strike':pick.get('strike') if pick else None,'option_type':pick.get('option_type') if pick else None,'underlying_price':spot,'entry_low':round(entry*.995,2) if entry else None,'entry_high':round(entry*1.01,2) if entry else None,'sl':sl,'targets':[target] if target else [],'rr':round(rr,2),'call_score':round(c,1),'put_score':round(p,1),'confidence':round(conf,1),'analysis':a,'option':pick or {},'reasons':list(dict.fromkeys(reasons)),'warnings':list(dict.fromkeys(warnings)),'state':'WATCH' if signal=='NO_TRADE' else 'CONFIRMED','timestamp':ts,'paper_only':True,'no_real_orders':True}
