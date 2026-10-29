# ============================================================
# STOCK SCANNER — UI Upgrade (JARVIS Edition)
# Flask app with dashboard + daily scan endpoint
# ============================================================

from flask import Flask, render_template_string, jsonify
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import threading
import time
import warnings
warnings.filterwarnings('ignore')

app = Flask(__name__)

# ============================================================
# GLOBAL STATE
# ============================================================

latest_signals = []
scan_history = []
last_scan_time = "STANDBY"
scan_status = "System Idle"

# ============================================================
# CONFIGURATION
# ============================================================

ACCOUNT_SIZE = 100000
RISK_PER_TRADE = 0.02
MAX_OPEN_TRADES = 5
MIN_SIGNAL_STRENGTH = 2

STOCKS = {
    "AAPL": "Apple", "MSFT": "Microsoft", "GOOGL": "Alphabet",
    "AMZN": "Amazon", "NVDA": "Nvidia", "META": "Meta",
    "TSLA": "Tesla", "JPM": "JP Morgan", "V": "Visa",
    "CRM": "Salesforce", "AMD": "AMD", "NFLX": "Netflix",
    "SPY": "S&P 500", "QQQ": "Nasdaq 100", "IWM": "Russell 2000",
    "XLF": "Financials", "XLE": "Energy", "XLK": "Tech",
    "RELIANCE.NS": "Reliance", "TCS.NS": "TCS",
    "HDFCBANK.NS": "HDFC Bank", "INFY.NS": "Infosys",
    "ICICIBANK.NS": "ICICI Bank", "SBIN.NS": "SBI",
    "BHARTIARTL.NS": "Bharti Airtel", "ITC.NS": "ITC",
    "HCLTECH.NS": "HCL Tech", "BAJFINANCE.NS": "Bajaj",
    "WIPRO.NS": "Wipro", "TATASTEEL.NS": "Tata Steel",
}

STRATEGY_PARAMS = {
    "Weekly Momentum": {"sl": 8, "tp": 15, "hold": 25},
    "BB Bounce":       {"sl": 5, "tp": 10, "hold": 15},
    "5% Drop Buy":     {"sl": 5, "tp": 10, "hold": 15},
}

# ============================================================
# INDICATORS & STRATEGIES
# ============================================================

def add_indicators(df):
    d = df.copy()
    delta = d['Close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    d['RSI'] = 100 - (100 / (1 + gain / loss.replace(0, 0.001)))
    d['BB_Mid'] = d['Close'].rolling(20).mean()
    d['BB_Lower'] = d['BB_Mid'] - 2 * d['Close'].rolling(20).std()
    d['ROC_3'] = d['Close'].pct_change(3) * 100
    d['ROC_5'] = d['Close'].pct_change(5) * 100
    return d

def check_weekly_momentum(d):
    return d['ROC_5'].iloc[-1] > 5, "5d Mom > 5%"

def check_bb_bounce(d):
    i = len(d) - 1
    touched = d['Low'].iloc[i] <= d['BB_Lower'].iloc[i] * 1.01
    bounce = d['Close'].iloc[i] > d['BB_Lower'].iloc[i]
    near = d['Low'].iloc[i-1] <= d['BB_Lower'].iloc[i-1] * 1.02 if i > 0 else False
    return (touched and bounce) or (near and bounce), "BB Bounce"

def check_drop_buy(d):
    i = len(d) - 1
    if i < 2: return False, ""
    dropped = d['ROC_3'].iloc[i-1] < -5 or d['ROC_3'].iloc[i] < -5
    bouncing = d['Close'].iloc[i] > d['Close'].iloc[i-1]
    return dropped and bouncing, "5% Drop Buy"

STRATEGIES = [("Weekly Momentum", check_weekly_momentum), ("BB Bounce", check_bb_bounce), ("5% Drop Buy", check_drop_buy)]

# ============================================================
# SCANNER CORE
# ============================================================

def run_scan():
    global latest_signals, last_scan_time, scan_status
    scan_status = "SCANNING NETWORK..."
    signals = []

    for ticker, name in STOCKS.items():
        market = "IND" if ".NS" in ticker else "USA"
        try:
            df = yf.download(ticker, period="6mo", interval="1d", progress=False)
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
            if len(df) < 50: continue
            df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
            d = add_indicators(df)

            active, reasons = [], []
            for sname, sfunc in STRATEGIES:
                triggered, reason = sfunc(d)
                if triggered:
                    active.append(sname)
                    reasons.append(reason)

            if len(active) >= MIN_SIGNAL_STRENGTH:
                entry = round(float(d['Close'].iloc[-1]), 2)
                best_sl = min(STRATEGY_PARAMS[s]['sl'] for s in active)
                best_tp = max(STRATEGY_PARAMS[s]['tp'] for s in active)
                
                sl = round(entry * (1 - best_sl / 100), 2)
                tp = round(entry * (1 + best_tp / 100), 2)
                
                # Risk Reward Math
                risk_pt = entry - sl
                reward_pt = tp - entry
                rr = round(reward_pt / risk_pt, 2) if risk_pt > 0 else 0

                risk_amt = ACCOUNT_SIZE * RISK_PER_TRADE
                shares = int(risk_amt / risk_pt) if risk_pt > 0 else 0

                signals.append({
                    'ticker': ticker.replace('.NS', ''), 'name': name, 'market': market,
                    'price': entry, 'strategies': " + ".join(active), 'count': len(active),
                    'sl': sl, 'sl_pct': best_sl, 'tp': tp, 'tp_pct': best_tp, 'rr': rr,
                    'shares': shares, 'position': round(shares * entry, 2),
                    'risk': round(shares * risk_pt, 2)
                })
        except: continue
        time.sleep(0.5)

    signals.sort(key=lambda x: -x['count'])
    latest_signals = signals[:MAX_OPEN_TRADES]
    last_scan_time = datetime.utcnow().strftime('%b %d, %I:%M %p UTC').upper()
    scan_status = f"SYSTEM OPTIMAL // {len(signals)} SIGNALS"

    scan_history.append({'time': last_scan_time, 'signals': len(signals)})
    return signals

# ============================================================
# HTML DASHBOARD (JARVIS UI)
# ============================================================

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>J.A.R.V.I.S. | Stock Radar</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #060505;
            --bg-panel: #0d0a09;
            --border-dim: #2a1610;
            --accent-orange: #ff5a00;
            --accent-green: #00ff66;
            --accent-red: #ff3333;
            --text-main: #f0f0f0;
            --text-dim: #887a75;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', sans-serif;
            background: radial-gradient(circle at 50% 0%, #1a0a05 0%, var(--bg-base) 40%);
            background-color: var(--bg-base);
            color: var(--text-main);
            min-height: 100vh; padding: 30px 15px;
        }
        .container { max-width: 900px; margin: 0 auto; }
        
        /* HEADER */
        .header {
            background: var(--bg-panel); border: 1px solid var(--border-dim);
            border-radius: 12px; padding: 20px;
            display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 25px; box-shadow: 0 8px 30px rgba(0,0,0,0.5);
        }
        .logo-area { display: flex; align-items: center; gap: 15px; }
        .logo-box {
            background: var(--accent-orange); color: #000;
            font-weight: 800; font-size: 24px;
            width: 45px; height: 45px; border-radius: 8px;
            display: flex; justify-content: center; align-items: center;
            box-shadow: 0 0 15px rgba(255, 90, 0, 0.4);
        }
        .title-area h1 { font-size: 18px; letter-spacing: 1px; color: #fff; }
        .title-area span { color: var(--accent-orange); font-size: 11px; vertical-align: top; }
        .title-area p { color: var(--accent-orange); font-size: 12px; margin-top: 2px; letter-spacing: 0.5px; }
        .live-badge {
            background: rgba(0, 255, 102, 0.1); border: 1px solid var(--accent-green);
            color: var(--accent-green); padding: 6px 12px; border-radius: 20px;
            font-size: 11px; font-weight: 800; letter-spacing: 1px;
            display: flex; align-items: center; gap: 6px; box-shadow: 0 0 10px rgba(0, 255, 102, 0.2);
        }
        .dot { width: 6px; height: 6px; background: var(--accent-green); border-radius: 50%; box-shadow: 0 0 5px var(--accent-green); }
        
        /* STATS GRID */
        .stats-grid {
            display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 25px;
        }
        .stat-card {
            background: var(--bg-panel); border: 1px solid var(--border-dim);
            border-radius: 10px; padding: 15px; text-align: center;
        }
        .stat-label { color: var(--text-dim); font-size: 10px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 5px; }
        .stat-val { font-family: 'JetBrains Mono', monospace; font-size: 22px; font-weight: 700; }
        .stat-sub { color: var(--text-dim); font-size: 10px; margin-top: 5px; }
        .text-orange { color: var(--accent-orange); }
        .text-white { color: #fff; }
        
        /* SECTION HEADER */
        .section-header {
            display: flex; justify-content: space-between; align-items: center;
            border-bottom: 1px solid var(--border-dim); padding-bottom: 8px;
            margin-bottom: 15px; margin-top: 30px;
        }
        .section-header h2 { font-size: 12px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 1.5px; }
        .section-header span { font-size: 11px; font-weight: 600; color: var(--text-main); }
        .text-green { color: var(--accent-green); }

        /* RADAR / STANDBY STATE */
        .radar-box {
            background: var(--bg-panel); border: 1px solid var(--border-dim);
            border-radius: 12px; padding: 30px; display: flex; align-items: center; gap: 25px;
        }
        .circle-ui {
            width: 100px; height: 100px; border-radius: 50%;
            border: 4px solid var(--border-dim); border-top-color: var(--accent-orange);
            display: flex; justify-content: center; align-items: center; flex-direction: column;
            box-shadow: 0 0 20px rgba(255, 90, 0, 0.1);
        }
        .circle-ui span { font-family: 'JetBrains Mono', monospace; font-size: 20px; font-weight: 700; color: #fff; }
        .circle-ui small { font-size: 9px; color: var(--text-dim); letter-spacing: 1px; }
        .radar-text h2 { color: var(--accent-orange); font-size: 20px; margin-bottom: 5px; letter-spacing: 1px; }
        .radar-text p { color: var(--text-dim); font-size: 12px; margin-bottom: 10px; }
        .neutral-bar {
            background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.05);
            border-radius: 6px; padding: 10px 15px; display: flex; align-items: center; gap: 10px;
        }
        .neutral-bar span { background: #333; font-size: 10px; padding: 2px 6px; border-radius: 4px; font-weight: bold; }
        .neutral-bar p { color: #888; font-size: 11px; margin:0; }

        /* SIGNAL CARDS */
        .signal-card {
            background: var(--bg-panel); border: 1px solid var(--border-dim);
            border-radius: 12px; padding: 20px; margin-bottom: 15px;
        }
        .sig-head { display: flex; justify-content: space-between; border-bottom: 1px solid var(--border-dim); padding-bottom: 12px; margin-bottom: 15px; }
        .sig-ticker { font-size: 18px; font-weight: 800; color: #fff; letter-spacing: 1px; display: flex; align-items: center; gap: 10px; }
        .market-tag { background: #222; font-size: 10px; padding: 3px 6px; border-radius: 4px; color: #aaa; }
        .sig-type { color: var(--accent-orange); font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; }
        
        .target-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 15px; }
        .t-box { background: rgba(0,0,0,0.3); border: 1px solid var(--border-dim); border-radius: 8px; padding: 12px; text-align: center; }
        .t-label { color: var(--text-dim); font-size: 10px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px; }
        .t-val { font-family: 'JetBrains Mono', monospace; font-size: 16px; font-weight: 700; }
        
        .val-red { color: var(--accent-red); }
        .val-green { color: var(--accent-green); }
        .val-orange { color: var(--accent-orange); }

        .sig-footer { display: flex; justify-content: space-between; font-size: 11px; color: var(--text-dim); background: rgba(255,255,255,0.02); padding: 10px; border-radius: 6px; }
        .sig-footer span strong { color: #fff; font-family: 'JetBrains Mono', monospace; }

        /* BUTTON */
        .btn-scan {
            width: 100%; background: transparent; border: 1px solid var(--accent-orange);
            color: var(--accent-orange); padding: 15px; border-radius: 8px;
            font-size: 14px; font-weight: 600; letter-spacing: 2px; text-transform: uppercase;
            cursor: pointer; transition: all 0.3s; margin-top: 10px;
        }
        .btn-scan:hover { background: rgba(255, 90, 0, 0.1); box-shadow: 0 0 15px rgba(255, 90, 0, 0.2); }
        
        @media (max-width: 600px) {
            .stats-grid { grid-template-columns: repeat(2, 1fr); }
            .target-grid { grid-template-columns: repeat(2, 1fr); }
            .radar-box { flex-direction: column; text-align: center; }
        }
    </style>
</head>
<body>
    <div class="container">
        
        <!-- HEADER -->
        <div class="header">
            <div class="logo-area">
                <div class="logo-box">S</div>
                <div class="title-area">
                    <h1>SIGNAL AI <span>v2.0</span></h1>
                    <p>J.A.R.V.I.S. ENGINE (GLOBAL)</p>
                </div>
            </div>
            <div class="live-badge">
                <div class="dot"></div> LIVE
            </div>
        </div>

        <!-- STATS -->
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">System Status</div>
                <div class="stat-val text-white" style="font-size:16px; margin-top:4px;">{{ status }}</div>
                <div class="stat-sub">Auto-Scan Active</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Active Signals</div>
                <div class="stat-val text-orange">{{ signal_count }}</div>
                <div class="stat-sub">Max: {{ max_trades }} limit</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Capital Auth</div>
                <div class="stat-val text-white">${{ "{:,.0f}".format(account/1000) }}K</div>
                <div class="stat-sub">Spot Account</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Risk Per Trade</div>
                <div class="stat-val text-white">{{ risk }}%</div>
                <div class="stat-sub">Strict Limit</div>
            </div>
        </div>

        <div class="section-header">
            <h2>System Consensus Radar (Last Scan)</h2>
            <span>SOURCE <span class="text-white">YAHOO.FINANCE</span></span>
        </div>

        <!-- SIGNALS OR STANDBY -->
        {% if signals %}
            {% for s in signals %}
            <div class="signal-card">
                <div class="sig-head">
                    <div class="sig-ticker">
                        {{ s.ticker }} <span class="market-tag">{{ s.market }}</span>
                    </div>
                    <div class="sig-type">{{ s.strategies }}</div>
                </div>
                <div class="target-grid">
                    <div class="t-box">
                        <div class="t-label">Entry</div>
                        <div class="t-val text-white">${{ "%.2f"|format(s.price) }}</div>
                    </div>
                    <div class="t-box">
                        <div class="t-label">Stop Loss</div>
                        <div class="t-val val-red">-{{ s.sl_pct }}%</div>
                    </div>
                    <div class="t-box">
                        <div class="t-label">Target</div>
                        <div class="t-val val-green">+{{ s.tp_pct }}%</div>
                    </div>
                    <div class="t-box">
                        <div class="t-label">R:R</div>
                        <div class="t-val val-orange">1 : {{ s.rr }}</div>
                    </div>
                </div>
                <div class="sig-footer">
                    <span>Alloc: <strong>${{ "{:,.0f}".format(s.position) }}</strong> ({{ s.shares }} sh)</span>
                    <span>Risk: <strong>${{ "{:,.0f}".format(s.risk) }}</strong></span>
                </div>
            </div>
            {% endfor %}
        {% else %}
            <div class="radar-box">
                <div class="circle-ui">
                    <span>0%</span>
                    <small>CONSENSUS</small>
                </div>
                <div class="radar-text">
                    <h2>STANDBY // NO SIGNAL</h2>
                    <p>Threshold: 2+ Strategy Confluence<br>Last Scan: {{ last_scan }}</p>
                    <div class="neutral-bar">
                        <span>N</span>
                        <p>NEUTRAL - No Position / Vetoed</p>
                    </div>
                </div>
            </div>
        {% endif %}

        <button class="btn-scan" onclick="triggerScan()" id="scanBtn">INITIATE MANUAL SCAN</button>

    </div>

    <script>
        function triggerScan() {
            const btn = document.getElementById('scanBtn');
            btn.innerText = "SCANNING SYSTEM...";
            btn.style.opacity = "0.5";
            btn.disabled = true;
            fetch('/scan').then(() => {
                setTimeout(() => window.location.reload(), 3000);
            });
        }
    </script>
</body>
</html>
"""

# ============================================================
# ROUTES
# ============================================================

@app.route('/')
def dashboard():
    return render_template_string(
        DASHBOARD_HTML,
        signals=latest_signals,
        last_scan=last_scan_time,
        status="OPTIMAL" if latest_signals else "IDLE",
        signal_count=len(latest_signals),
        account=ACCOUNT_SIZE,
        risk=RISK_PER_TRADE * 100,
        max_trades=MAX_OPEN_TRADES
    )

@app.route('/scan')
def trigger_scan():
    threading.Thread(target=run_scan).start()
    return jsonify({"status": "Scan initiated"})

@app.route('/health')
def health(): return "OK", 200

def startup_scan():
    time.sleep(3)
    run_scan()

if __name__ == '__main__':
    threading.Thread(target=startup_scan, daemon=True).start()
    app.run(host='0.0.0.0', port=10000)
