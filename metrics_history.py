import sqlite3
import time
import os
import threading
import json
import logging

DB_PATH = os.environ.get('METRICS_DB_PATH', '/services/cicd-manager/metrics_history.db')

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics_raw (
                ts INTEGER NOT NULL,
                host TEXT NOT NULL,
                metric TEXT NOT NULL,
                val REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_ts ON metrics_raw (host, metric, ts)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics_hourly (
                ts INTEGER NOT NULL,
                host TEXT NOT NULL,
                metric TEXT NOT NULL,
                val_avg REAL NOT NULL,
                val_min REAL NOT NULL,
                val_max REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hourly_ts ON metrics_hourly (host, metric, ts)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics_daily (
                ts INTEGER NOT NULL,
                host TEXT NOT NULL,
                metric TEXT NOT NULL,
                val_avg REAL NOT NULL,
                val_min REAL NOT NULL,
                val_max REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_ts ON metrics_daily (host, metric, ts)")

def record_points(points):
    """points is a list of (ts, host, metric, val) tuples."""
    if not points:
        return
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.executemany("INSERT INTO metrics_raw (ts, host, metric, val) VALUES (?, ?, ?, ?)", points)
    except Exception as e:
        logging.error(f"Error inserting metrics: {e}")

def downsample_and_cleanup():
    """Downsamples raw -> hourly (> 24h) and hourly -> daily (> 30d). Retains daily for 1 year."""
    now = int(time.time())
    one_day_ago = now - 86400
    thirty_days_ago = now - (30 * 86400)
    one_year_ago = now - (365 * 86400)

    try:
        with sqlite3.connect(DB_PATH, timeout=30) as conn:
            # 1. Downsample raw data older than 24h into metrics_hourly (bucket by 3600s)
            conn.execute("""
                INSERT INTO metrics_hourly (ts, host, metric, val_avg, val_min, val_max)
                SELECT (ts / 3600) * 3600, host, metric, AVG(val), MIN(val), MAX(val)
                FROM metrics_raw
                WHERE ts < ?
                GROUP BY (ts / 3600), host, metric
            """, (one_day_ago,))
            
            # Delete old raw
            conn.execute("DELETE FROM metrics_raw WHERE ts < ?", (one_day_ago,))

            # 2. Downsample hourly older than 30d into metrics_daily (bucket by 86400s)
            conn.execute("""
                INSERT INTO metrics_daily (ts, host, metric, val_avg, val_min, val_max)
                SELECT (ts / 86400) * 86400, host, metric, AVG(val_avg), MIN(val_min), MAX(val_max)
                FROM metrics_hourly
                WHERE ts < ?
                GROUP BY (ts / 86400), host, metric
            """, (thirty_days_ago,))

            # Delete old hourly
            conn.execute("DELETE FROM metrics_hourly WHERE ts < ?", (thirty_days_ago,))

            # 3. Clean daily older than 1 year
            conn.execute("DELETE FROM metrics_daily WHERE ts < ?", (one_year_ago,))
    except Exception as e:
        logging.error(f"Error downsampling metrics: {e}")

def query_history(host, metric, range_key='1d'):
    """Query metrics dynamically according to requested range."""
    now = int(time.time())
    
    if range_key == '1h':
        start_ts = now - 3600
        step = 30
    elif range_key == '6h':
        start_ts = now - (6 * 3600)
        step = 60
    elif range_key == '24h' or range_key == '1d':
        start_ts = now - 86400
        step = 120
    elif range_key == '7d':
        start_ts = now - (7 * 86400)
        step = 1800 # 30 min
    elif range_key == '30d':
        start_ts = now - (30 * 86400)
        step = 3600 # 1 hour
    else: # 90d / 1y
        start_ts = now - (90 * 86400)
        step = 86400

    results = []
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            if range_key in ['1h', '6h', '24h', '1d']:
                # Query raw table with bucketed averages
                cur = conn.execute(f"""
                    SELECT (ts / ?) * ? as bucket, AVG(val) as avg_val
                    FROM metrics_raw
                    WHERE host = ? AND metric = ? AND ts >= ?
                    GROUP BY bucket
                    ORDER BY bucket ASC
                """, (step, step, host, metric, start_ts))
            elif range_key in ['7d', '30d']:
                # Query union of raw and hourly
                cur = conn.execute(f"""
                    SELECT (ts / ?) * ? as bucket, AVG(val_avg) as avg_val FROM (
                        SELECT ts, val as val_avg FROM metrics_raw WHERE host = ? AND metric = ? AND ts >= ?
                        UNION ALL
                        SELECT ts, val_avg FROM metrics_hourly WHERE host = ? AND metric = ? AND ts >= ?
                    )
                    GROUP BY bucket
                    ORDER BY bucket ASC
                """, (step, step, host, metric, start_ts, host, metric, start_ts))
            else:
                # Query daily
                cur = conn.execute(f"""
                    SELECT ts as bucket, val_avg as avg_val
                    FROM metrics_daily
                    WHERE host = ? AND metric = ? AND ts >= ?
                    ORDER BY bucket ASC
                """, (host, metric, start_ts))
                
            for row in cur:
                results.append({"t": row["bucket"], "v": round(row["avg_val"], 2)})
    except Exception as e:
        logging.error(f"Error querying history: {e}")
        
    return results
