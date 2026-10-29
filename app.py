# ============================================================
# STOCK SCANNER — Render Deployment
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
# GLOBAL STATE (stores latest scan in memory)
# ============================================================

latest_signals = []
scan_history = []
last_scan_time = "Never"
scan_status = "Idle"

# ============================================================
# CONFIGURATION
# ============================================================

ACCOUNT_SIZE = 100000
RISK_PER_TRADE = 0.02
MAX_OPEN_TRADES = 5
MIN_SIGNAL_STRENGTH = 2

# Focused stock list (30 stocks — optimized for Render free tier)
STOCKS = {
    # US Mega Caps
    "AAPL": "Apple", "MSFT": "Microsoft", "GOOGL": "Alphabet",
    "AMZN": "Amazon", "NVDA": "Nvidia", "META": "Meta",
    "TSLA": "Tesla", "JPM": "JP Morgan", "V": "Visa",
    "CRM": "Salesforce", "AMD": "AMD", "NFLX": "Netflix",
    # US ETFs
    "SPY": "S&P 500", "QQQ": "Nasdaq 100", "IWM": "Russell 2000",
    "XLF": "Financials", "XLE": "Energy", "XLK": "Tech",
    # India Nifty 50
    "RELIANCE.NS": "Reliance", "TCS.NS": "TCS",
    "HDFCBANK.NS": "HDFC Bank", "INFY.NS": "Infosys",
    "ICICIBANK.NS": "ICICI Bank", "SBIN.NS": "SBI",
    "BHARTIARTL.NS": "Bharti Airtel", "ITC.NS": "ITC",
    "HCLTECH.NS": "HCL Tech", "BAJFINANCE.NS": "Bajaj Finance",
    "WIPRO.NS": "Wipro", "TATASTEEL.NS": "Tata Steel",
}

STRATEGY_PARAMS = {
    "Weekly Momentum": {"sl": 8, "tp": 15, "hold": 25},
    "BB Bounce":       {"sl": 5, "tp": 10, "hold": 15},
    "5% Drop Buy":     {"sl": 5, "tp": 10, "hold": 15},
}


# ============================================================
# INDICATORS
# ============================================================

def add_indicators(df):
    d = df.copy()
    d['SMA50']  = d['Close'].rolling(50).mean()
    d['SMA200'] = d['Close'].rolling(200).mean()
    d['EMA20']  = d['Close'].ewm(span=20).mean()

    delta = d['Close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    d['RSI'] = 100 - (100 / (1 + gain / loss.replace(0, 0.001)))

    d['BB_Mid'] = d['Close'].rolling(20).mean()
    bb_std = d['Close'].rolling(20).std()
    d['BB_Lower'] = d['BB_Mid'] - 2 * bb_std

    d['ROC_3'] = d['Close'].pct_change(3) * 100
    d['ROC_5'] = d['Close'].pct_change(5) * 100
    return d


# ============================================================
# STRATEGIES
# ============================================================

def check_weekly_momentum(d):
    i = len(d) - 1
    if d['ROC_5'].iloc[i] > 5:
        return True, "5-day momentum >5%"
    return False, ""

def check_bb_bounce(d):
    i = len(d) - 1
    touched = d['Low'].iloc[i] <= d['BB_Lower'].iloc[i] * 1.01
    bounce = d['Close'].iloc[i] > d['BB_Lower'].iloc[i]
    near = d['Low'].iloc[i-1] <= d['BB_Lower'].iloc[i-1] * 1.02 if i > 0 else False
    if (touched and bounce) or (near and bounce):
        return True, "BB lower band bounce"
    return False, ""

def check_drop_buy(d):
    i = len(d) - 1
    if i < 2:
        return False, ""
    dropped = d['ROC_3'].iloc[i-1] < -5 or d['ROC_3'].iloc[i] < -5
    bouncing = d['Close'].iloc[i] > d['Close'].iloc[i-1]
    if dropped and bouncing:
        return True, "5% drop + bounce"
    return False, ""

STRATEGIES = [
    ("Weekly Momentum", check_weekly_momentum),
    ("BB Bounce",       check_bb_bounce),
    ("5% Drop Buy",     check_drop_buy),
]


# ============================================================
# SCANNER CORE
# ============================================================

def run_scan():
    global latest_signals, last_scan_time, scan_status

    scan_status = "Scanning..."
    signals = []

    for ticker, name in STOCKS.items():
        is_india = ".NS" in ticker
        market = "🇮🇳" if is_india else "🇺🇸"

        try:
            df = yf.download(ticker, period="6mo", interval="1d", progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if len(df) < 50:
                continue

            df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
            d = add_indicators(df)

            active = []
            reasons = []
            for sname, sfunc in STRATEGIES:
                triggered, reason = sfunc(d)
                if triggered:
                    active.append(sname)
                    reasons.append(reason)

            if len(active) >= MIN_SIGNAL_STRENGTH:
                last = d.iloc[-1]
                entry = round(float(last['Close']), 2)
                best_sl = min(STRATEGY_PARAMS[s]['sl'] for s in active)
                best_tp = max(STRATEGY_PARAMS[s]['tp'] for s in active)
                best_hold = max(STRATEGY_PARAMS[s]['hold'] for s in active)
                sl = round(entry * (1 - best_sl / 100), 2)
                tp = round(entry * (1 + best_tp / 100), 2)

                risk_amt = ACCOUNT_SIZE * RISK_PER_TRADE
                risk_per_share = abs(entry - sl)
                shares = int(risk_amt / risk_per_share) if risk_per_share > 0 else 0

                rsi_val = round(float(last['RSI']), 1) if not pd.isna(last['RSI']) else 50

                signals.append({
                    'ticker': ticker,
                    'name': name,
                    'market': market,
                    'price': entry,
                    'strategies': " + ".join(active),
                    'count': len(active),
                    'sl': sl, 'sl_pct': best_sl,
                    'tp': tp, 'tp_pct': best_tp,
                    'hold': best_hold,
                    'shares': shares,
                    'position': round(shares * entry, 2),
                    'risk': round(shares * risk_per_share, 2),
                    'rsi': rsi_val,
                    'reason': " | ".join(reasons),
                })

        except Exception as e:
            print(f"Error scanning {ticker}: {e}")
            continue

        time.sleep(0.5)  # rate limit

    # Sort by strategy count then RSI
    signals.sort(key=lambda x: (-x['count'], x['rsi']))
    for i, s in enumerate(signals):
        s['rank'] = i + 1

    latest_signals = signals[:MAX_OPEN_TRADES]
    last_scan_time = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    scan_status = f"Done — {len(signals)} signals found"

    scan_history.append({
        'time': last_scan_time,
        'signals': len(signals),
        'top': signals[0]['ticker'] if signals else "None"
    })

    return signals


# ============================================================
# HTML DASHBOARD
# ============================================================

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>📡 Stock Scanner</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #0f172a; color: #e2e8f0;
            padding: 20px; max-width: 1200px; margin: 0 auto;
        }
        h1 { color: #38bdf8; margin-bottom: 5px; font-size: 1.8em; }
        .subtitle { color: #94a3b8; margin-bottom: 20px; font-size: 0.9em; }
        .status-bar {
            display: flex; gap: 20px; flex-wrap: wrap;
            background: #1e293b; padding: 15px 20px;
            border-radius: 10px; margin-bottom: 20px;
        }
        .status-item { text-align: center; }
        .status-label { color: #64748b; font-size: 0.75em; text-transform: uppercase; }
        .status-value { color: #38bdf8; font-size: 1.2em; font-weight: bold; }
        .status-value.green { color: #4ade80; }
        .status-value.yellow { color: #fbbf24; }
        .status-value.red { color: #f87171; }
        .btn {
            background: #2563eb; color: white; border: none;
            padding: 12px 24px; border-radius: 8px; cursor: pointer;
            font-size: 1em; font-weight: bold; margin: 10px 0;
            text-decoration: none; display: inline-block;
        }
        .btn:hover { background: #1d4ed8; }
        .btn:disabled { background: #475569; cursor: not-allowed; }
        .signal-card {
            background: #1e293b; border-radius: 12px;
            padding: 20px; margin-bottom: 15px;
            border-left: 4px solid #4ade80;
        }
        .signal-card.strong { border-left-color: #f97316; }
        .signal-card.mega { border-left-color: #ef4444; }
        .signal-header {
            display: flex; justify-content: space-between;
            align-items: center; margin-bottom: 10px;
        }
        .signal-ticker { font-size: 1.4em; font-weight: bold; color: #f8fafc; }
        .signal-badge {
            background: #22c55e; color: white; padding: 4px 10px;
            border-radius: 20px; font-size: 0.8em;
        }
        .signal-grid {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 10px; margin-top: 10px;
        }
        .signal-metric {
            background: #0f172a; padding: 10px; border-radius: 8px;
        }
        .metric-label { color: #64748b; font-size: 0.75em; }
        .metric-value { color: #e2e8f0; font-size: 1.1em; font-weight: bold; }
        .metric-value.green { color: #4ade80; }
        .metric-value.red { color: #f87171; }
        .no-signals {
            text-align: center; padding: 60px 20px;
            background: #1e293b; border-radius: 12px;
        }
        .no-signals h2 { color: #64748b; margin-bottom: 10px; }
        .no-signals p { color: #475569; }
        .history { margin-top: 30px; }
        .history h3 { color: #94a3b8; margin-bottom: 10px; }
        .history-item {
            background: #1e293b; padding: 10px 15px;
            border-radius: 8px; margin-bottom: 5px;
            display: flex; justify-content: space-between;
            font-size: 0.9em;
        }
        .footer {
            text-align: center; color: #475569;
            margin-top: 30px; font-size: 0.8em;
        }
    </style>
</head>
<body>
    <h1>📡 Stock Scanner Dashboard</h1>
    <p class="subtitle">3 Winning Strategies × 30 Stocks | Auto-scans daily</p>

    <div class="status-bar">
        <div class="status-item">
            <div class="status-label">Last Scan</div>
            <div class="status-value">{{ last_scan }}</div>
        </div>
        <div class="status-item">
            <div class="status-label">Status</div>
            <div class="status-value green">{{ status }}</div>
        </div>
        <div class="status-item">
            <div class="status-label">Signals</div>
            <div class="status-value {% if signal_count > 0 %}green{% else %}yellow{% endif %}">
                {{ signal_count }}
            </div>
        </div>
        <div class="status-item">
            <div class="status-label">Account</div>
            <div class="status-value">${{ "{:,.0f}".format(account) }}</div>
        </div>
        <div class="status-item">
            <div class="status-label">Risk/Trade</div>
            <div class="status-value">{{ risk }}%</div>
        </div>
    </div>

    <a href="/scan" class="btn">🔄 Run Scan Now</a>
    <a href="/api/signals" class="btn" style="background:#475569">📊 API (JSON)</a>

    <h2 style="margin: 20px 0 10px; color: #94a3b8;">
        🎯 Active Signals (Top {{ max_trades }})
    </h2>

    {% if signals %}
        {% for s in signals %}
        <div class="signal-card {% if s.count >= 3 %}mega{% elif s.count >= 2 %}strong{% endif %}">
            <div class="signal-header">
                <span class="signal-ticker">
                    {{ s.market }} #{{ s.rank }} {{ s.ticker }}
                    <span style="color:#94a3b8; font-size:0.7em">{{ s.name }}</span>
                </span>
                <span class="signal-badge">
                    {% if s.count >= 3 %}🔥🔥🔥{% elif s.count >= 2 %}🔥🔥{% else %}🔥{% endif %}
                    {{ s.count }} strategies
                </span>
            </div>
            <p style="color:#94a3b8; font-size:0.85em; margin-bottom:10px">
                {{ s.strategies }} — {{ s.reason }}
            </p>
            <div class="signal-grid">
                <div class="signal-metric">
                    <div class="metric-label">Entry Price</div>
                    <div class="metric-value">${{ "%.2f"|format(s.price) }}</div>
                </div>
                <div class="signal-metric">
                    <div class="metric-label">Stop Loss</div>
                    <div class="metric-value red">${{ "%.2f"|format(s.sl) }} (-{{ s.sl_pct }}%)</div>
                </div>
                <div class="signal-metric">
                    <div class="metric-label">Take Profit</div>
                    <div class="metric-value green">${{ "%.2f"|format(s.tp) }} (+{{ s.tp_pct }}%)</div>
                </div>
                <div class="signal-metric">
                    <div class="metric-label">Max Hold</div>
                    <div class="metric-value">{{ s.hold }} days</div>
                </div>
                <div class="signal-metric">
                    <div class="metric-label">Shares</div>
                    <div class="metric-value">{{ s.shares }}</div>
                </div>
                <div class="signal-metric">
                    <div class="metric-label">Position</div>
                    <div class="metric-value">${{ "{:,.0f}".format(s.position) }}</div>
                </div>
                <div class="signal-metric">
                    <div class="metric-label">Risk</div>
                    <div class="metric-value red">${{ "{:,.0f}".format(s.risk) }}</div>
                </div>
                <div class="signal-metric">
                    <div class="metric-label">RSI</div>
                    <div class="metric-value {% if s.rsi < 30 %}green{% elif s.rsi > 70 %}red{% endif %}">
                        {{ s.rsi }}
                    </div>
                </div>
            </div>
        </div>
        {% endfor %}
    {% else %}
        <div class="no-signals">
            <h2>😴 No Signals Today</h2>
            <p>Market is quiet. Sitting in cash is a valid position.</p>
            <p style="margin-top:10px">The scanner checks for 2+ strategies agreeing.<br>
            Good trades don't happen every day — that's the point.</p>
        </div>
    {% endif %}

    {% if history %}
    <div class="history">
        <h3>📜 Scan History</h3>
        {% for h in history[-10:]|reverse %}
        <div class="history-item">
            <span>{{ h.time }}</span>
            <span>{{ h.signals }} signals | Top: {{ h.top }}</span>
        </div>
        {% endfor %}
    </div>
    {% endif %}

    <div class="footer">
        <p>Strategies: Weekly Momentum | BB Bounce | 5% Drop Buy</p>
        <p>Auto-refreshes when triggered by cron job. Account: ${{ "{:,.0f}".format(account) }}</p>
    </div>
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
        status=scan_status,
        signal_count=len(latest_signals),
        account=ACCOUNT_SIZE,
        risk=RISK_PER_TRADE * 100,
        max_trades=MAX_OPEN_TRADES,
        history=scan_history,
    )


@app.route('/scan')
def trigger_scan():
    """Trigger a new scan (called by cron job or manually)."""
    def do_scan():
        run_scan()
    thread = threading.Thread(target=do_scan)
    thread.start()
    return jsonify({
        "status": "Scan started",
        "message": "Results will be ready in ~60 seconds. Refresh the dashboard."
    })


@app.route('/api/signals')
def api_signals():
    """JSON API for signals."""
    return jsonify({
        "last_scan": last_scan_time,
        "status": scan_status,
        "signal_count": len(latest_signals),
        "signals": latest_signals,
        "history": scan_history[-20:],
    })


@app.route('/health')
def health():
    """Health check for Render."""
    return "OK", 200


# ============================================================
# STARTUP
# ============================================================

def startup_scan():
    """Run initial scan when app starts."""
    time.sleep(5)  # wait for Flask to fully start
    print("🚀 Running startup scan...")
    run_scan()
    print(f"✅ Startup scan complete: {len(latest_signals)} signals")

if __name__ == '__main__':
    # Run startup scan in background
    threading.Thread(target=startup_scan, daemon=True).start()
    app.run(host='0.0.0.0', port=10000)
