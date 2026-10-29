import flask
from flask import Flask, render_template_string, jsonify
import yfinance as yf
import pandas as pd
import numpy as np
import datetime

app = Flask(__name__)

# Target Watchlist
WATCHLIST = [
    # US Tech & Growth
    "AAPL", "NVDA", "TSLA", "AMD", "MSFT", "AMZN", "GOOGL", "META",
    # Indian Bluechips
    "HCLTECH.NS", "TCS.NS", "BHARTIARTL.NS", "RELIANCE.NS", "INFY.NS"
]

def analyze_stock(symbol):
    try:
        df = yf.download(symbol, period="1y", interval="1d", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        if len(df) < 60:
            return None, []
            
        # Standard Indicators
        df['SMA_20'] = df['Close'].rolling(window=20).mean()
        df['SMA_50'] = df['Close'].rolling(window=50).mean()
        df['STD_20'] = df['Close'].rolling(window=20).std()
        df['BB_Upper'] = df['SMA_20'] + (df['STD_20'] * 2)
        df['BB_Lower'] = df['SMA_20'] - (df['STD_20'] * 2)
        df['Vol_SMA20'] = df['Volume'].rolling(window=20).mean()
        
        # Strategy Metrics
        df['Return_5d'] = (df['Close'] - df['Close'].shift(5)) / df['Close'].shift(5) * 100
        df['Drop_3d'] = (df['Close'] - df['Close'].shift(3)) / df['Close'].shift(3) * 100

        # ATR Calculation
        df['TR'] = np.maximum(
            df['High'] - df['Low'],
            np.maximum(
                abs(df['High'] - df['Close'].shift(1)),
                abs(df['Low'] - df['Close'].shift(1))
            )
        )
        df['ATR'] = df['TR'].rolling(window=14).mean()

        # -------------------------------------------------------------
        # 1. LIVE SIGNAL SCANNER
        # -------------------------------------------------------------
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        signals = []
        if latest['Return_5d'] >= 5.0 and latest['Close'] > latest['SMA_20']:
            signals.append("Weekly Momentum")
        if prev['Low'] <= prev['BB_Lower'] and latest['Close'] > latest['Open']:
            signals.append("BB Lower Bounce")
        if prev['Drop_3d'] <= -5.0 and latest['Close'] > latest['Open']:
            signals.append("5% Dip Buy")

        live_result = None
        if signals:
            entry = float(latest['Close'])
            atr = float(latest['ATR']) if not np.isnan(latest['ATR']) else entry * 0.03
            sl = round(entry - (1.5 * atr), 2)
            tp = round(entry + (3.0 * atr), 2)
            rr_ratio = round((tp - entry) / (entry - sl), 2) if (entry - sl) > 0 else 2.0
            
            live_result = {
                "symbol": symbol,
                "price": round(entry, 2),
                "signals": signals,
                "sl": sl,
                "tp": tp,
                "rr_ratio": f"1:{rr_ratio}",
                "date": latest.name.strftime('%Y-%m-%d')
            }

        # -------------------------------------------------------------
        # 2. HISTORICAL 6-MONTH AUTOPSY ENGINE
        # -------------------------------------------------------------
        history_signals = []
        lookback_days = 126
        start_idx = max(20, len(df) - lookback_days)
        
        for i in range(start_idx, len(df) - 1):
            row = df.iloc[i]
            prev_row = df.iloc[i-1]
            
            h_signals = []
            if row['Return_5d'] >= 5.0 and row['Close'] > row['SMA_20']:
                h_signals.append("Weekly Momentum")
            if prev_row['Low'] <= prev_row['BB_Lower'] and row['Close'] > row['Open']:
                h_signals.append("BB Bounce")
            if prev_row['Drop_3d'] <= -5.0 and row['Close'] > row['Open']:
                h_signals.append("Dip Buy")
                
            if h_signals:
                entry_price = float(row['Close'])
                atr = float(row['ATR']) if not np.isnan(row['ATR']) else entry_price * 0.03
                
                risk = 1.5 * atr
                reward = 3.0 * atr
                sl_price = round(entry_price - risk, 2)
                tp_price = round(entry_price + reward, 2)
                initial_rr = round(reward / risk, 1) if risk > 0 else 2.0
                
                # Forward simulation up to 25 trading days
                forward_df = df.iloc[i+1 : min(i+26, len(df))]
                status = "EXPIRED"
                autopsy = ""
                days_held = len(forward_df)
                return_pct = round((forward_df.iloc[-1]['Close'] - entry_price) / entry_price * 100, 1) if days_held > 0 else 0.0
                
                peak_high = entry_price
                trough_low = entry_price
                exit_day = 0
                
                for idx, (_, f_row) in enumerate(forward_df.iterrows()):
                    if f_row['High'] > peak_high:
                        peak_high = f_row['High']
                    if f_row['Low'] < trough_low:
                        trough_low = f_row['Low']
                        
                    if f_row['High'] >= tp_price and status == "EXPIRED":
                        status = "WIN"
                        exit_day = idx + 1
                        return_pct = round((tp_price - entry_price) / entry_price * 100, 1)
                    elif f_row['Low'] <= sl_price and status == "EXPIRED":
                        status = "LOSS"
                        exit_day = idx + 1
                        return_pct = round((sl_price - entry_price) / entry_price * 100, 1)

                # --- AUTOPSY DIAGNOSTIC GENERATOR ---
                if status == "WIN":
                    # Check post-target max extension
                    max_rr = round((peak_high - entry_price) / risk, 1) if risk > 0 else initial_rr
                    extra_pct = round(((peak_high - tp_price) / entry_price) * 100, 1)
                    if extra_pct > 1.0:
                        autopsy = f"🎯 Target hit in {exit_day}d. Extended +{extra_pct}% further (Peak R:R reached 1:{max_rr})."
                    else:
                        autopsy = f"🎯 Target hit in {exit_day}d. Exact hit before momentum faded."
                        
                elif status == "LOSS":
                    # Diagnose what went wrong
                    loss_rows = forward_df[forward_df['Low'] <= sl_price]
                    exit_bar = loss_rows.iloc[0] if len(loss_rows) > 0 else forward_df.iloc[-1]
                    
                    reasons = []
                    if exit_bar['Open'] < sl_price:
                        reasons.append("Sharp Gap-Down Slippage")
                    if row['Close'] < row['SMA_50']:
                        reasons.append("Trend Failure (Trading below 50-SMA)")
                    if 'Vol_SMA20' in row and exit_bar['Volume'] > (1.5 * row['Vol_SMA20']):
                        reasons.append("High Institutional Volume Selling")
                    
                    if not reasons:
                        reasons.append("Market Noise / Stop-loss Hunt")
                        
                    autopsy = f"🛑 SL Hit in {exit_day}d. Diagnostic: {', '.join(reasons)}."
                    
                else: # EXPIRED
                    autopsy = f"⏳ Held 25 days sideways. Net PnL: {return_pct}%. No SL/TP hit."
                
                history_signals.append({
                    "symbol": symbol,
                    "date": row.name.strftime('%Y-%m-%d'),
                    "strategies": ", ".join(h_signals),
                    "entry": round(entry_price, 2),
                    "sl": sl_price,
                    "tp": tp_price,
                    "initial_rr": f"1:{initial_rr}",
                    "status": status,
                    "return_pct": return_pct,
                    "autopsy": autopsy
                })
                
        return live_result, history_signals

    except Exception as e:
        print(f"Error analyzing {symbol}: {e}")
        return None, []

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

@app.route('/scan')
def scan():
    live_signals = []
    historical_logs = []
    
    for symbol in WATCHLIST:
        live_res, hist_res = analyze_stock(symbol)
        if live_res:
            live_signals.append(live_res)
        if hist_res:
            historical_logs.extend(hist_res)
            
    total_trades = len(historical_logs)
    wins = len([x for x in historical_logs if x['status'] == 'WIN'])
    losses = len([x for x in historical_logs if x['status'] == 'LOSS'])
    win_rate = round((wins / total_trades) * 100, 1) if total_trades > 0 else 0
    avg_return = round(sum([x['return_pct'] for x in historical_logs]) / total_trades, 1) if total_trades > 0 else 0

    return jsonify({
        "timestamp": datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC'),
        "live_signals": live_signals,
        "historical_stats": {
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "avg_return": avg_return
        },
        "historical_logs": sorted(historical_logs, key=lambda x: x['date'], reverse=True)[:35]
    })

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JARVIS Scanner & Signal Autopsy</title>
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Rajdhani:wght@500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #08090c;
            --panel-bg: #0f1117;
            --border-color: #1e222d;
            --neon-orange: #ff6600;
            --neon-orange-glow: rgba(255, 102, 0, 0.3);
            --text-color: #e1e3e6;
            --text-dim: #787b86;
            --green: #00e676;
            --red: #ff5252;
        }

        body {
            background-color: var(--bg-color);
            color: var(--text-color);
            font-family: 'Rajdhani', sans-serif;
            margin: 0;
            padding: 20px;
        }

        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 2px solid var(--neon-orange);
            padding-bottom: 15px;
            margin-bottom: 25px;
            box-shadow: 0 4px 20px var(--neon-orange-glow);
        }

        .title-box h1 {
            font-family: 'Orbitron', sans-serif;
            font-size: 26px;
            color: var(--neon-orange);
            margin: 0;
            letter-spacing: 2px;
            text-shadow: 0 0 10px var(--neon-orange-glow);
        }

        .btn-scan {
            background: linear-gradient(135deg, #ff6600, #b34700);
            color: #fff;
            border: none;
            padding: 12px 28px;
            font-family: 'Orbitron', sans-serif;
            font-weight: 700;
            border-radius: 4px;
            cursor: pointer;
            box-shadow: 0 0 15px var(--neon-orange-glow);
            transition: all 0.3s ease;
        }

        .btn-scan:hover {
            transform: translateY(-2px);
            box-shadow: 0 0 25px rgba(255, 102, 0, 0.6);
        }

        .section-title {
            font-family: 'Orbitron', sans-serif;
            font-size: 18px;
            color: var(--neon-orange);
            margin: 30px 0 15px 0;
            letter-spacing: 1.5px;
            border-left: 4px solid var(--neon-orange);
            padding-left: 10px;
        }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 15px;
            margin-bottom: 25px;
        }

        .stat-card {
            background: var(--panel-bg);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            padding: 15px;
            text-align: center;
        }

        .stat-value {
            font-family: 'Orbitron', sans-serif;
            font-size: 24px;
            font-weight: 700;
            color: var(--neon-orange);
            margin-top: 5px;
        }

        .stat-label {
            color: var(--text-dim);
            font-size: 13px;
            text-transform: uppercase;
        }

        .table-container {
            background: var(--panel-bg);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            overflow-x: auto;
            margin-bottom: 30px;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }

        th {
            background: #141721;
            padding: 14px;
            font-family: 'Orbitron', sans-serif;
            font-size: 12px;
            color: var(--neon-orange);
            border-bottom: 1px solid var(--border-color);
            text-transform: uppercase;
        }

        td {
            padding: 14px;
            border-bottom: 1px solid var(--border-color);
            font-size: 14px;
        }

        tr:hover {
            background: rgba(255, 102, 0, 0.03);
        }

        .badge-win { color: var(--green); font-weight: 700; background: rgba(0, 230, 118, 0.1); padding: 4px 8px; border-radius: 4px; }
        .badge-loss { color: var(--red); font-weight: 700; background: rgba(255, 82, 82, 0.1); padding: 4px 8px; border-radius: 4px; }
        .badge-exp { color: var(--text-dim); background: rgba(120, 123, 134, 0.1); padding: 4px 8px; border-radius: 4px; }

        #loader {
            display: none;
            color: var(--neon-orange);
            font-weight: bold;
            font-family: 'Orbitron', sans-serif;
        }
    </style>
</head>
<body>

    <div class="header">
        <div class="title-box">
            <h1>JARVIS QUANT SCANNER</h1>
            <div style="font-size: 12px; color: var(--text-dim); margin-top: 4px;">LIVE SIGNALS & 6-MONTH AUTOPSY RADAR</div>
        </div>
        <div>
            <span id="loader">RUNNING SCAN ENGINE...</span>
            <button class="btn-scan" onclick="runScan()">EXECUTE SCAN</button>
        </div>
    </div>

    <!-- LIVE SIGNALS SECTION -->
    <div class="section-title">TODAY'S LIVE TRADE SIGNALS</div>
    <div class="table-container">
        <table>
            <thead>
                <tr>
                    <th>Symbol</th>
                    <th>Signal Date</th>
                    <th>Entry Price</th>
                    <th>Signals Triggered</th>
                    <th>Stop Loss</th>
                    <th>Target (TP)</th>
                    <th>Initial R:R</th>
                </tr>
            </thead>
            <tbody id="live-table-body">
                <tr><td colspan="7" style="text-align:center; color: var(--text-dim);">Click 'EXECUTE SCAN' to fetch live market signals.</td></tr>
            </tbody>
        </table>
    </div>

    <!-- HISTORICAL AUDIT STATS -->
    <div class="section-title">6-MONTH HISTORICAL PERFORMANCE AUDIT</div>
    <div class="stats-grid">
        <div class="stat-card">
            <div class="stat-label">Total Signals Tracked</div>
            <div class="stat-value" id="stat-total">--</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Historical Win Rate</div>
            <div class="stat-value" id="stat-winrate" style="color: var(--green);">--</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Wins / Losses</div>
            <div class="stat-value" id="stat-wl">-- / --</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Avg Return / Trade</div>
            <div class="stat-value" id="stat-avg" style="color: var(--green);">--</div>
        </div>
    </div>

    <!-- HISTORICAL AUTOPSY LOG TABLE -->
    <div class="section-title">POST-MORTEM AUTOPSY & EXTENSION LOG</div>
    <div class="table-container">
        <table>
            <thead>
                <tr>
                    <th>Date</th>
                    <th>Symbol</th>
                    <th>Strategy</th>
                    <th>Entry (SL / TP)</th>
                    <th>Planned R:R</th>
                    <th>Outcome</th>
                    <th style="width: 40%;">Trade Autopsy Diagnostic & Post-Target Extension</th>
                </tr>
            </thead>
            <tbody id="hist-table-body">
                <tr><td colspan="7" style="text-align:center; color: var(--text-dim);">Click 'EXECUTE SCAN' to run historical autopsy.</td></tr>
            </tbody>
        </table>
    </div>

    <script>
        async function runScan() {
            document.getElementById('loader').style.display = 'inline-block';
            
            try {
                const response = await fetch('/scan');
                const data = await response.json();
                
                // Render Live Signals
                const liveBody = document.getElementById('live-table-body');
                liveBody.innerHTML = '';
                
                if (data.live_signals.length === 0) {
                    liveBody.innerHTML = '<tr><td colspan="7" style="text-align:center; color: var(--text-dim);">No active trade signals triggered today for the watchlist.</td></tr>';
                } else {
                    data.live_signals.forEach(sig => {
                        liveBody.innerHTML += `
                            <tr>
                                <td style="font-weight: bold; color: var(--neon-orange);">${sig.symbol}</td>
                                <td>${sig.date}</td>
                                <td>$${sig.price}</td>
                                <td>${sig.signals.join(', ')}</td>
                                <td style="color: var(--red);">$${sig.sl}</td>
                                <td style="color: var(--green);">$${sig.tp}</td>
                                <td><span style="color: var(--neon-orange); font-weight: bold;">${sig.rr_ratio}</span></td>
                            </tr>
                        `;
                    });
                }

                // Render Historical Stats
                const stats = data.historical_stats;
                document.getElementById('stat-total').innerText = stats.total_trades;
                document.getElementById('stat-winrate').innerText = `${stats.win_rate}%`;
                document.getElementById('stat-wl').innerText = `${stats.wins} / ${stats.losses}`;
                document.getElementById('stat-avg').innerText = `${stats.avg_return > 0 ? '+' : ''}${stats.avg_return}%`;

                // Render Historical Autopsy Table
                const histBody = document.getElementById('hist-table-body');
                histBody.innerHTML = '';
                
                data.historical_logs.forEach(log => {
                    let badgeClass = 'badge-exp';
                    if (log.status === 'WIN') badgeClass = 'badge-win';
                    if (log.status === 'LOSS') badgeClass = 'badge-loss';
                    
                    histBody.innerHTML += `
                        <tr>
                            <td>${log.date}</td>
                            <td style="font-weight: bold; color: var(--neon-orange);">${log.symbol}</td>
                            <td style="color: var(--text-dim);">${log.strategies}</td>
                            <td>$${log.entry} <span style="font-size:11px; color: var(--text-dim);">($${log.sl} / $${log.tp})</span></td>
                            <td style="font-weight: bold;">${log.initial_rr}</td>
                            <td><span class="${badgeClass}">${log.status} (${log.return_pct > 0 ? '+' : ''}${log.return_pct}%)</span></td>
                            <td style="color: #b0b3b8; font-size: 13px;">${log.autopsy}</td>
                        </tr>
                    `;
                });

            } catch (err) {
                alert('Scan Engine Error: ' + err);
            } finally {
                document.getElementById('loader').style.display = 'none';
            }
        }
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
