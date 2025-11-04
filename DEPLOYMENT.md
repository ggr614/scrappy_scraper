# UTC Crawler Scheduled Deployment Guide

## Overview

This guide covers deploying the UTC crawler on a Linux server with scheduled execution during off-hours (midnight to 4 AM).

## Architecture

```
Production Server
├── /opt/utc-crawler/          # Main application directory
│   ├── scraperv2.py          # Core crawler
│   ├── scheduled_crawler.py   # Time-aware wrapper
│   ├── scheduled_crawler.sh   # Shell wrapper for cron
│   ├── monitor_crawler.py     # Monitoring and alerts
│   ├── pdf_asset_extractor.py # PDF extraction utility
│   ├── .env                   # Environment configuration
│   ├── requirements.txt       # Python dependencies
│   ├── crawl_data/           # Output directory
│   │   ├── pages/            # HTML and JSON files
│   │   ├── mapping.jsonl     # URL mappings
│   │   ├── errors.jsonl      # Error log
│   │   └── frontier.json     # Resume state
│   └── logs/                 # Execution logs
└── /etc/cron.d/utc-crawler   # Cron configuration
```

## Deployment Steps

### 1. Server Setup

```bash
# Create application directory
sudo mkdir -p /opt/utc-crawler
sudo chown $USER:$USER /opt/utc-crawler
cd /opt/utc-crawler

# Create Python virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create necessary directories
mkdir -p crawl_data logs
```

### 2. Configuration

Create `.env` file:
```bash
# Production configuration
USER_AGENT="UTC-Production-Crawler/1.0"
SEED_URL="https://utc.edu"
DOMAIN="utc.edu"
MAX_PAGES=0                    # Unlimited
RATE_LIMIT_SECONDS=1.5         # Respectful crawling
TIMEOUT=15                     # Conservative timeout
BASE_DIR="crawl_data"
RESPECT_ROBOTS=true            # Follow robots.txt
```

### 3. Cron Configuration

Create `/etc/cron.d/utc-crawler`:
```bash
# UTC Crawler - Runs during off-hours only
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin

# Primary crawl attempt - midnight
0 0 * * * www-data cd /opt/utc-crawler && ./scheduled_crawler.sh

# Backup attempts if needed
30 1 * * * www-data cd /opt/utc-crawler && ./scheduled_crawler.sh
0 3 * * * www-data cd /opt/utc-crawler && ./scheduled_crawler.sh

# Daily monitoring report - 5 AM
0 5 * * * www-data cd /opt/utc-crawler && python3 monitor_crawler.py --report --format json > logs/daily_report.json

# Health check every hour during business hours
0 9-17 * * 1-5 www-data cd /opt/utc-crawler && python3 monitor_crawler.py --alert --webhook "https://your-webhook-url"
```

### 4. File Permissions

```bash
# Make scripts executable
chmod +x scheduled_crawler.sh
chmod +x *.py

# Set proper ownership (if running as www-data)
sudo chown -R www-data:www-data /opt/utc-crawler

# Secure environment file
chmod 600 .env
```

### 5. Systemd Service (Optional)

Create `/etc/systemd/system/utc-crawler-monitor.service` for persistent monitoring:

```ini
[Unit]
Description=UTC Crawler Monitor
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/utc-crawler
Environment=PATH=/opt/utc-crawler/venv/bin
ExecStart=/opt/utc-crawler/venv/bin/python3 monitor_crawler.py --daemon
Restart=always
RestartSec=60

[Install]
WantedBy=multi-user.target
```

## Monitoring and Alerts

### Health Checks

```bash
# Manual status check
python3 monitor_crawler.py

# Detailed report
python3 monitor_crawler.py --report

# Health check with alerts
python3 monitor_crawler.py --alert --webhook "https://slack-webhook-url"
```

### Log Management

Logs are automatically managed:
- Daily execution logs in `logs/cron_YYYYMMDD_HHMMSS.log`
- Crawler logs in `logs/crawler_YYYYMMDD.log`  
- Automatic cleanup (30 day retention)

### Key Metrics to Monitor

1. **Crawl Progress**: Pages crawled vs. frontier size
2. **Error Rate**: Errors per hour/day
3. **Completion Status**: Whether crawling is complete
4. **Performance**: Pages per hour, disk usage
5. **Health**: Process running, time window compliance

## Webhook Integration

### Slack Example
```json
{
  "text": "UTC Crawler Alert: WARNING",
  "attachments": [{
    "color": "warning",
    "fields": [
      {"title": "Issues", "value": "High error rate: 25 errors in 24h", "short": false}
    ]
  }]
}
```

### Discord Example
```json
{
  "content": "🚨 UTC Crawler Alert",
  "embeds": [{
    "title": "Crawler Status: WARNING",
    "description": "High error rate detected",
    "color": 16776960
  }]
}
```

## Operational Procedures

### Starting Fresh Crawl
```bash
# Clear previous state
rm -f crawl_data/frontier.json crawl_data/seen.txt

# Test run
python3 scheduled_crawler.py --dry-run

# Force run (ignore time window)
python3 scheduled_crawler.py --force
```

### Troubleshooting

1. **Check if crawler is running**:
   ```bash
   python3 monitor_crawler.py
   ```

2. **View recent logs**:
   ```bash
   tail -f logs/crawler_$(date +%Y%m%d).log
   ```

3. **Check cron execution**:
   ```bash
   grep UTC /var/log/cron.log
   ```

4. **Manual execution**:
   ```bash
   ./scheduled_crawler.sh
   ```

### Backup Strategy

```bash
# Daily backup of crawl data
0 6 * * * www-data tar -czf /backup/utc-crawler-$(date +\%Y\%m\%d).tar.gz -C /opt utc-crawler/crawl_data
```

## Performance Tuning

### High-Volume Sites
- Increase `RATE_LIMIT_SECONDS` to 2.0+
- Monitor server resources (CPU, memory, disk I/O)
- Consider implementing concurrent crawling limits

### Network Issues
- Increase `TIMEOUT` values
- Implement exponential backoff in retry logic
- Monitor DNS resolution times

## Security Considerations

1. **Access Control**: Restrict file permissions
2. **Environment Variables**: Secure .env file (600 permissions)
3. **User Account**: Run as dedicated user (www-data)
4. **Network**: Consider firewall rules for outbound requests
5. **Logs**: Avoid logging sensitive information

## Maintenance

### Weekly Tasks
- Review error logs and patterns
- Check disk usage growth
- Verify crawl completion status
- Update dependencies if needed

### Monthly Tasks  
- Review and archive old logs
- Check for UTC site structure changes
- Update robots.txt compliance if needed
- Performance optimization review

## Disaster Recovery

### Backup Components
- Configuration files (`.env`, cron, systemd)
- Crawl data and state (`crawl_data/`)
- Application logs (`logs/`)

### Recovery Procedure
1. Restore application files and configuration
2. Recreate virtual environment and dependencies
3. Restore crawl data and frontier state
4. Restart cron jobs and monitoring
5. Verify operation with test run

## Contact and Support

- **Application Logs**: `/opt/utc-crawler/logs/`
- **System Logs**: `/var/log/cron.log`, `/var/log/syslog`
- **Configuration**: `/opt/utc-crawler/.env`
- **Cron Jobs**: `/etc/cron.d/utc-crawler`