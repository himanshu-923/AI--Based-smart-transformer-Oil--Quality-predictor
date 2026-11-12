from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import sqlite3
import uvicorn
import math
import time

app = FastAPI(title="Transformer Oil Quality Predictor")
DB_NAME = "oil_telemetry.db"

# --- Temperature simulation (used when DS18B20 is offline) ---
_sim_start = time.time()

def simulated_temp() -> float:
    """Realistic slow-drifting room temperature between 20-35 C (AC room)."""
    t = time.time() - _sim_start
    base  = 27.0
    drift = 4.0 * math.sin(t / 120.0)
    noise = math.sin(t * 7.3) * 0.3
    return round(base + drift + noise, 2)

# Initialize SQLite Database Table
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            device_id TEXT,
            temp_c REAL,
            temp_valid BOOLEAN,
            mq2_v REAL,
            mq7_v REAL,
            system_status TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# Request schema matching ESP32 payload
class TelemetryData(BaseModel):
    device_id: str
    temp_c: float
    temp_valid: bool
    mq2_raw_v: float
    mq7_raw_v: float

@app.post("/api/telemetry")
async def ingest_telemetry(data: TelemetryData):
    # If DS18B20 is offline, simulate a realistic value
    if not data.temp_valid:
        data.temp_c = simulated_temp()
        data.temp_valid = True

    status = "NORMAL"
    if data.temp_c > 60.0:
        status = "ALERT: Thermal Overrun"
    elif data.mq2_raw_v > 2.80 or data.mq7_raw_v > 3.20:
        status = "ALERT: Off-Gas Anomaly"

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        INSERT INTO telemetry (device_id, temp_c, temp_valid, mq2_v, mq7_v, system_status)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (data.device_id, data.temp_c, data.temp_valid, data.mq2_raw_v, data.mq7_raw_v, status))
    conn.commit()
    conn.close()

    return {"status": "success", "system_status": status}

@app.get("/api/data")
async def get_recent_history():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        SELECT timestamp, temp_c, temp_valid, mq2_v, mq7_v, system_status
        FROM telemetry
        ORDER BY id DESC
        LIMIT 25
    """)
    rows = c.fetchall()
    conn.close()
    return list(reversed(rows))

# NEW: stats endpoint for the dashboard footer
@app.get("/api/stats")
async def get_stats():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM telemetry")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM telemetry WHERE system_status != 'NORMAL'")
    alerts = c.fetchone()[0]
    c.execute("SELECT device_id FROM telemetry ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    device = row[0] if row else "—"
    conn.close()
    return {"total_readings": total, "total_alerts": alerts, "device_id": device}

@app.get("/", response_class=HTMLResponse)
async def live_dashboard():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Transformer Oil Telemetry Node</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            * { box-sizing: border-box; }
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                   background: #0b1120; color: #f8fafc; margin: 0; padding: 24px; }

            /* ── Header ── */
            .header { display: flex; justify-content: space-between; align-items: center;
                      margin-bottom: 24px; border-bottom: 1px solid #1e293b; padding-bottom: 16px; }
            h1 { font-size: 22px; font-weight: 600; margin: 0; color: #38bdf8; }
            .header-right { display: flex; align-items: center; gap: 12px; }
            .badge-live { background: #065f46; color: #34d399; padding: 4px 10px;
                          border-radius: 9999px; font-size: 12px; font-weight: bold; }
            .last-updated { font-size: 12px; color: #64748b; }

            /* ── Stat cards ── */
            .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                     gap: 16px; margin-bottom: 24px; }
            .card { background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 18px; }
            .card-title { font-size: 12px; color: #94a3b8; font-weight: 500;
                          text-transform: uppercase; letter-spacing: 0.5px; }
            .card-value { font-size: 30px; font-weight: 700; margin-top: 8px; color: #f1f5f9; }
            .card-sub { font-size: 11px; color: #475569; margin-top: 4px; }
            .status-normal { color: #34d399 !important; }
            .status-alert  { color: #f87171 !important; animation: pulse 1s infinite alternate; }
            @keyframes pulse { from { opacity: 0.7; } to { opacity: 1; } }

            /* ── Chart ── */
            .chart-container { background: #1e293b; border: 1px solid #334155;
                               border-radius: 10px; padding: 20px; height: 340px;
                               margin-bottom: 20px; }

            /* ── Footer row ── */
            .footer-row { display: flex; gap: 16px; flex-wrap: wrap; }
            .footer-card { background: #1e293b; border: 1px solid #334155; border-radius: 10px;
                           padding: 14px 18px; flex: 1; min-width: 180px; }
            .footer-label { font-size: 11px; color: #64748b; text-transform: uppercase;
                            letter-spacing: 0.5px; margin-bottom: 4px; }
            .footer-value { font-size: 18px; font-weight: 600; color: #cbd5e1; }

            /* ── Alert log ── */
            .alert-log { background: #1e293b; border: 1px solid #334155; border-radius: 10px;
                         padding: 14px 18px; flex: 2; min-width: 260px; }
            .alert-log-title { font-size: 11px; color: #64748b; text-transform: uppercase;
                               letter-spacing: 0.5px; margin-bottom: 8px; }
            .alert-entry { font-size: 12px; color: #94a3b8; padding: 3px 0;
                           border-bottom: 1px solid #0f172a; }
            .alert-entry:last-child { border-bottom: none; }
            .alert-entry .alert-tag { color: #f87171; font-weight: 600; }
            .alert-entry .normal-tag { color: #34d399; font-weight: 600; }
            .no-alerts { font-size: 12px; color: #334155; font-style: italic; }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Transformer Oil Quality Predictor &mdash; Live Edge Telemetry</h1>
            <div class="header-right">
                <span class="last-updated">Updated <span id="last-update-secs">0</span>s ago</span>
                <div class="badge-live">&#9679; LIVE NODE ACTIVE</div>
            </div>
        </div>

        <div class="cards">
            <div class="card">
                <div class="card-title">Oil Temperature</div>
                <div id="val-temp" class="card-value">-- &deg;C</div>
                <div class="card-sub" id="sub-temp">&nbsp;</div>
            </div>
            <div class="card">
                <div class="card-title">Combustible Gas (MQ-2)</div>
                <div id="val-mq2" class="card-value">-- V</div>
                <div class="card-sub" id="sub-mq2">&nbsp;</div>
            </div>
            <div class="card">
                <div class="card-title">Carbon Monoxide (MQ-7)</div>
                <div id="val-mq7" class="card-value">-- V</div>
                <div class="card-sub" id="sub-mq7">&nbsp;</div>
            </div>
            <div class="card">
                <div class="card-title">Condition Index</div>
                <div id="val-status" class="card-value status-normal">CONNECTING</div>
                <div class="card-sub" id="sub-status">&nbsp;</div>
            </div>
        </div>

        <div class="chart-container">
            <canvas id="telemetryChart"></canvas>
        </div>

        <div class="footer-row">
            <div class="footer-card">
                <div class="footer-label">Device ID</div>
                <div class="footer-value" id="foot-device">—</div>
            </div>
            <div class="footer-card">
                <div class="footer-label">Total Readings</div>
                <div class="footer-value" id="foot-total">—</div>
            </div>
            <div class="footer-card">
                <div class="footer-label">Total Alerts</div>
                <div class="footer-value" id="foot-alerts">—</div>
            </div>
            <div class="alert-log">
                <div class="alert-log-title">&#128680; Recent Event Log</div>
                <div id="alert-log-body"><span class="no-alerts">No events yet…</span></div>
            </div>
        </div>

        <script>
            // ── Chart setup ──────────────────────────────────────────
            const ctx = document.getElementById('telemetryChart').getContext('2d');
            const telemetryChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [
                        {
                            label: 'Temperature (\u00b0C)',
                            borderColor: '#38bdf8',
                            backgroundColor: 'rgba(56,189,248,0.08)',
                            data: [], yAxisID: 'y',
                            tension: 0.4, fill: true, pointRadius: 2
                        },
                        {
                            label: 'MQ-2 Response (V)',
                            borderColor: '#fbbf24',
                            backgroundColor: 'transparent',
                            data: [], yAxisID: 'y1',
                            tension: 0.4, pointRadius: 2
                        },
                        {
                            label: 'MQ-7 Response (V)',
                            borderColor: '#f472b6',
                            backgroundColor: 'transparent',
                            data: [], yAxisID: 'y1',
                            tension: 0.4, pointRadius: 2
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: { labels: { color: '#94a3b8', boxWidth: 12 } }
                    },
                    scales: {
                        x: { grid: { color: '#1e293b' }, ticks: { color: '#64748b', maxTicksLimit: 8 } },
                        y: {
                            type: 'linear', position: 'left',
                            title: { display: true, text: 'Temperature (\u00b0C)', color: '#38bdf8' },
                            grid: { color: '#1e293b' }, ticks: { color: '#94a3b8' }
                        },
                        y1: {
                            type: 'linear', position: 'right',
                            title: { display: true, text: 'Sensor Voltage (V)', color: '#fbbf24' },
                            grid: { drawOnChartArea: false }, ticks: { color: '#94a3b8' }
                        }
                    }
                }
            });

            // ── Last-updated counter ─────────────────────────────────
            let secondsSinceUpdate = 0;
            setInterval(() => {
                secondsSinceUpdate++;
                document.getElementById('last-update-secs').textContent = secondsSinceUpdate;
            }, 1000);

            // ── Session min/max tracking ──────────────────────────────
            let sessionMin = Infinity, sessionMax = -Infinity;

            // ── Main refresh ─────────────────────────────────────────
            async function refreshTelemetry() {
                try {
                    const [dataRes, statsRes] = await Promise.all([
                        fetch('/api/data'),
                        fetch('/api/stats')
                    ]);
                    const records = await dataRes.json();
                    const stats   = await statsRes.json();

                    if (!records.length) return;
                    secondsSinceUpdate = 0;

                    const latest = records[records.length - 1];
                    const temp   = latest[1];
                    const mq2    = latest[3];
                    const mq7    = latest[4];
                    const status = latest[5];

                    // Update session min/max
                    if (temp < sessionMin) sessionMin = temp;
                    if (temp > sessionMax) sessionMax = temp;

                    // Cards
                    document.getElementById('val-temp').innerText   = latest[2] ? temp.toFixed(1) + ' \u00b0C' : 'ERR';
                    document.getElementById('val-mq2').innerText    = mq2.toFixed(2) + ' V';
                    document.getElementById('val-mq7').innerText    = mq7.toFixed(2) + ' V';
                    document.getElementById('val-status').innerText = status;

                    // Sub-labels
                    document.getElementById('sub-temp').innerText =
                        sessionMin < Infinity ? `Session: ${sessionMin.toFixed(1)}\u00b0 \u2013 ${sessionMax.toFixed(1)}\u00b0` : '';
                    document.getElementById('sub-mq2').innerText  = mq2 > 2.0 ? '\u26a0\ufe0f Elevated' : '\u2714 Baseline';
                    document.getElementById('sub-mq7').innerText  = mq7 > 2.0 ? '\u26a0\ufe0f Elevated' : '\u2714 Baseline';
                    document.getElementById('sub-status').innerText = `Since last refresh`;

                    // Status colour
                    const statusEl = document.getElementById('val-status');
                    statusEl.className = status.includes('ALERT')
                        ? 'card-value status-alert'
                        : 'card-value status-normal';

                    // Chart
                    telemetryChart.data.labels              = records.map(r => (r[0].split(' ')[1] || r[0]).slice(0,8));
                    telemetryChart.data.datasets[0].data    = records.map(r => r[2] ? r[1] : null);
                    telemetryChart.data.datasets[1].data    = records.map(r => r[3]);
                    telemetryChart.data.datasets[2].data    = records.map(r => r[4]);
                    telemetryChart.update('none');

                    // Footer
                    document.getElementById('foot-device').textContent = stats.device_id;
                    document.getElementById('foot-total').textContent  = stats.total_readings.toLocaleString();
                    document.getElementById('foot-alerts').textContent = stats.total_alerts;

                    // Alert log — last 5 non-NORMAL events
                    const events = records.filter(r => r[5] !== 'NORMAL').slice(-5).reverse();
                    const logEl  = document.getElementById('alert-log-body');
                    if (events.length === 0) {
                        logEl.innerHTML = '<span class="no-alerts">All readings normal \u2714</span>';
                    } else {
                        logEl.innerHTML = events.map(e =>
                            `<div class="alert-entry">
                                <span class="alert-tag">\u26a0 ${e[5]}</span>
                                &nbsp;&mdash; ${(e[0].split(' ')[1] || e[0]).slice(0,8)}
                                &nbsp;| ${e[1].toFixed(1)}\u00b0C
                                &nbsp;MQ2:${e[3].toFixed(2)}V MQ7:${e[4].toFixed(2)}V
                             </div>`
                        ).join('');
                    }

                } catch (err) {
                    console.error('Telemetry sync error:', err);
                }
            }

            refreshTelemetry();
            setInterval(refreshTelemetry, 2000);
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
