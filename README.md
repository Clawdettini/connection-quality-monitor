# Connection Quality Monitor

A small, self-contained executable for continuously measuring home internet connection quality and collecting timestamped evidence for ISP complaints.

It uses only Python's standard library plus the system `ping` command. No Python packages are required.

By default it stores samples in a SQLite database: `connection-quality.sqlite`. CSV/JSONL output remains available with `--storage files` or `--storage both`.

## What it records

Each sample writes SQLite rows with:

- ICMP ping latency, jitter, and packet loss
- DNS resolution latency and failures
- HTTP(S) response latency, status, and failures
- A small download throughput sample

Default targets:

- Ping: `1.1.1.1`, `8.8.8.8`, `9.9.9.9`
- DNS: `cloudflare.com`, `google.com`, `wikipedia.org`
- HTTP: Google/GStatic 204 endpoints and Wikipedia
- Download: Cloudflare speed endpoint, 1 MB by default

## Quick install

```bash
sudo curl -fsSL https://raw.githubusercontent.com/Clawdettini/connection-quality-monitor/main/connection-quality-monitor \
  -o /usr/local/bin/connection-quality-monitor
sudo chmod +x /usr/local/bin/connection-quality-monitor
```

Run one sample:

```bash
connection-quality-monitor --once
```

Run continuously:

```bash
connection-quality-monitor --interval 60 --out-dir ~/connection-quality-data
```

## Install as a systemd service

```bash
sudo curl -fsSL https://raw.githubusercontent.com/Clawdettini/connection-quality-monitor/main/connection-quality-monitor \
  -o /usr/local/bin/connection-quality-monitor
sudo chmod +x /usr/local/bin/connection-quality-monitor
sudo mkdir -p /var/log/connection-quality

sudo tee /etc/systemd/system/connection-quality-monitor.service >/dev/null <<'EOF'
[Unit]
Description=Internet connection quality monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/connection-quality-monitor --interval 60 --out-dir /var/log/connection-quality
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now connection-quality-monitor.service
```

Check it:

```bash
systemctl status connection-quality-monitor.service
journalctl -u connection-quality-monitor.service -n 50
```

Data will be written to:

```text
/var/log/connection-quality/connection-quality.sqlite
```

To also keep daily CSV/JSONL files, add `--storage both` to the service `ExecStart`.

## Command line options

```text
--out-dir DIR              Directory for logs and default SQLite database
--storage MODE             sqlite (default), files for CSV/JSONL, or both
--db-path PATH             SQLite database path; defaults to OUT_DIR/connection-quality.sqlite
--interval SECONDS         Seconds between samples, default 60
--timeout SECONDS          Timeout per network check, default 8
--once                     Run a single sample and exit
--ping-targets LIST        Comma-separated ICMP targets; empty disables ping
--ping-count N             Ping packets per target, default 5
--dns-hosts LIST           Comma-separated DNS hostnames; empty disables DNS
--http-urls LIST           Comma-separated HTTP(S) URLs; empty disables HTTP
--download-url URL         URL for small throughput test; empty disables download
--download-bytes N         Max bytes for throughput test, default 1000000
--download-every N         Run download checks at most every N seconds; 0 means every sample
--quiet                    Do not print per-sample summaries
--estimate-storage         Estimate one-year storage/traffic for the current config and exit
--report text|json         Print statistics from SQLite and exit
--html-report PATH         Write a self-contained HTML visualization and exit
--since ISO_TIMESTAMP      Restrict reports to samples at/after this timestamp
```

Example with custom targets:

```bash
connection-quality-monitor \
  --interval 30 \
  --ping-targets 1.1.1.1,8.8.8.8,your.router.ip \
  --dns-hosts cloudflare.com,google.com \
  --out-dir ~/connection-quality-data
```

Estimate the one-year storage and download traffic cost for a configuration:

```bash
connection-quality-monitor \
  --interval 60 \
  --ping-targets 1.1.1.1,8.8.8.8 \
  --dns-hosts cloudflare.com \
  --http-urls https://www.google.com/generate_204 \
  --download-url "" \
  --estimate-storage
```

Run cheap checks every minute but throughput checks only every 15 minutes:

```bash
connection-quality-monitor --interval 60 --download-every 900
```

The default `--download-every 0` preserves the original behavior: the download
test runs every sample when `--download-url` is configured.

## Storage efficiency

SQLite stores repeated check targets in a normalized `targets` table and stores
only `target_id` on each sample row. This reduces repeated URL/hostname text in
long-running databases while keeping reports readable.

For low-storage, low-traffic long-running monitoring, prefer SQLite only and
disable or slow down download checks:

```bash
connection-quality-monitor \
  --storage sqlite \
  --interval 60 \
  --ping-targets 1.1.1.1,8.8.8.8 \
  --dns-hosts cloudflare.com \
  --http-urls https://www.google.com/generate_204 \
  --download-url ""
```

## Reporting and visualization

Print a text summary:

```bash
connection-quality-monitor --report text --out-dir /var/log/connection-quality
```

Print machine-readable JSON:

```bash
connection-quality-monitor --report json --out-dir /var/log/connection-quality
```

Create a self-contained HTML dashboard:

```bash
connection-quality-monitor --html-report ~/connection-quality-report.html --out-dir /var/log/connection-quality
```

Restrict a report to newer samples:

```bash
connection-quality-monitor --report text --since 2026-06-01T00:00:00+00:00 --out-dir /var/log/connection-quality
```

The HTML report embeds the SQLite-derived data and uses browser canvas charts, so it can be copied or opened without a web server.

## Notes for ISP complaints

The SQLite database is easiest to query locally. Example:

```bash
sqlite3 /var/log/connection-quality/connection-quality.sqlite \
  "SELECT s.timestamp_utc, t.check_type, t.target, s.ok, s.latency_ms, s.packet_loss_pct, s.throughput_mbps, s.error FROM samples s JOIN targets t ON t.id = s.target_id ORDER BY s.id DESC LIMIT 20;"
```

If your ISP wants spreadsheet-style evidence, either export from SQLite:

```bash
sqlite3 -header -csv /var/log/connection-quality/connection-quality.sqlite \
  "SELECT s.timestamp_utc, s.local_time, t.check_type, t.target, s.ok, s.latency_ms, s.jitter_ms, s.packet_loss_pct, s.status_code, s.bytes, s.throughput_mbps, s.error FROM samples s JOIN targets t ON t.id = s.target_id;" > connection-quality.csv
```

Or run the monitor with `--storage both` to create daily CSV files automatically.

Useful columns include:

- `timestamp_utc` / `local_time`
- `check_type`
- `target`
- `ok`
- `latency_ms`
- `jitter_ms`
- `packet_loss_pct`
- `throughput_mbps`
- `error`

For stronger evidence, let it run for several days and keep the raw CSV files.