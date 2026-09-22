from signal_engine import SignalEngine

def candles(n=100,up=True):
    out=[];p=100
    for i in range(n):
        p += .25 if up else -.25
        out.append({'time':i*60,'open':p-.1,'high':p+.3,'low':p-.2,'close':p,'volume':1000+(500 if i==n-1 else 0)})
    return out

def chain():
    return [{'strike':100,'expiry':'22SEP26','ce_ltp':10,'ce_delta':.52,'ce_oi':20000,'ce_chg_oi':-100,'ce_volume':5000,'ce_iv':15,'ce_bid':9.9,'ce_ask':10.1,
             'pe_ltp':10,'pe_delta':-.48,'pe_oi':10000,'pe_chg_oi':500,'pe_volume':5000,'pe_iv':15,'pe_bid':9.9,'pe_ask':10.1}]

def test_missing_chain_is_no_trade():
    r=SignalEngine().evaluate('NIFTY',{'1h':candles(),'15m':candles(),'5m':candles()},[],100,None,market_open=True)
    assert r['signal']=='NO_TRADE'

def test_missing_history_is_no_trade():
    cs=candles(20)
    r=SignalEngine().evaluate('NIFTY',{'1h':cs,'15m':cs,'5m':cs},chain(),100,None,market_open=True)
    assert r['signal']=='NO_TRADE'
