#!/usr/bin/env python3
"""
Crawler Monitoring and Alerting System
-------------------------------------
Monitoring script for the scheduled UTC crawler with alerting capabilities.

Features:
- Progress monitoring and reporting
- Error detection and alerting
- Performance metrics tracking
- Optional email/webhook notifications
- Health checks and status reporting

Usage:
    python monitor_crawler.py [--report] [--alert] [--webhook URL]
"""

import argparse
import json
import smtplib
import sys
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import requests
import os


class CrawlerMonitor:
    def __init__(self, base_dir: str = "crawl_data", log_dir: str = "logs"):
        self.base_dir = Path(base_dir)
        self.log_dir = Path(log_dir)
        self.lock_file = Path("crawler.lock")
        
    def get_crawler_status(self) -> Dict:
        """Get comprehensive crawler status."""
        status = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "is_running": self._is_crawler_running(),
            "frontier_size": 0,
            "total_pages": 0,
            "total_errors": 0,
            "last_activity": None,
            "disk_usage_mb": 0,
            "recent_errors": [],
            "completion_percentage": 0.0
        }
        
        # Frontier analysis
        frontier_file = self.base_dir / "frontier.json"
        if frontier_file.exists():
            try:
                with open(frontier_file, 'r') as f:
                    frontier = json.load(f)
                status["frontier_size"] = len(frontier) if isinstance(frontier, list) else 0
            except (json.JSONDecodeError, FileNotFoundError):
                pass
        
        # Pages count and disk usage
        pages_dir = self.base_dir / "pages"
        if pages_dir.exists():
            json_files = list(pages_dir.glob("*.json"))
            status["total_pages"] = len(json_files)
            
            # Calculate disk usage
            total_size = 0
            for file_path in pages_dir.iterdir():
                if file_path.is_file():
                    total_size += file_path.stat().st_size
            status["disk_usage_mb"] = round(total_size / (1024 * 1024), 2)
            
            # Get last activity from most recent file
            if json_files:
                latest_file = max(json_files, key=lambda p: p.stat().st_mtime)
                status["last_activity"] = datetime.fromtimestamp(
                    latest_file.stat().st_mtime, 
                    timezone.utc
                ).isoformat()
        
        # Error analysis
        error_file = self.base_dir / "errors.jsonl"
        if error_file.exists():
            try:
                recent_errors = []
                with open(error_file, 'r') as f:
                    for line in f:
                        if line.strip():
                            try:
                                error = json.loads(line)
                                recent_errors.append(error)
                            except json.JSONDecodeError:
                                continue
                
                status["total_errors"] = len(recent_errors)
                # Get last 5 errors
                status["recent_errors"] = recent_errors[-5:] if recent_errors else []
            except FileNotFoundError:
                pass
        
        # Estimate completion percentage
        if status["frontier_size"] > 0 and status["total_pages"] > 0:
            total_discovered = status["total_pages"] + status["frontier_size"]
            status["completion_percentage"] = round(
                (status["total_pages"] / total_discovered) * 100, 1
            )
        elif status["frontier_size"] == 0 and status["total_pages"] > 0:
            status["completion_percentage"] = 100.0
        
        return status
    
    def _is_crawler_running(self) -> bool:
        """Check if crawler is currently running."""
        if not self.lock_file.exists():
            return False
        
        try:
            with open(self.lock_file, 'r') as f:
                pid = int(f.read().strip())
            
            # Check if process exists (Unix-specific)
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False
        except (ValueError, FileNotFoundError):
            return False
    
    def get_log_summary(self, hours: int = 24) -> Dict:
        """Get summary of recent log activity."""
        cutoff_time = datetime.now() - timedelta(hours=hours)
        summary = {
            "log_files_found": 0,
            "total_entries": 0,
            "error_entries": 0,
            "warning_entries": 0,
            "recent_messages": []
        }
        
        if not self.log_dir.exists():
            return summary
        
        # Find recent log files
        log_files = []
        for log_file in self.log_dir.glob("*.log"):
            if log_file.stat().st_mtime > cutoff_time.timestamp():
                log_files.append(log_file)
        
        summary["log_files_found"] = len(log_files)
        
        # Analyze log content
        for log_file in sorted(log_files, key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                with open(log_file, 'r') as f:
                    lines = f.readlines()
                
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    
                    summary["total_entries"] += 1
                    
                    if "ERROR" in line.upper():
                        summary["error_entries"] += 1
                        if len(summary["recent_messages"]) < 10:
                            summary["recent_messages"].append(("ERROR", line))
                    elif "WARNING" in line.upper():
                        summary["warning_entries"] += 1
                        if len(summary["recent_messages"]) < 10:
                            summary["recent_messages"].append(("WARNING", line))
            
            except Exception:
                continue
        
        return summary
    
    def check_health(self) -> Tuple[str, List[str]]:
        """
        Perform health check and return status with issues.
        Returns: (status, issues_list)
        Status: "healthy", "warning", "critical"
        """
        issues = []
        status = self.get_crawler_status()
        
        # Check if stuck (no activity for too long)
        if status["last_activity"]:
            last_activity = datetime.fromisoformat(status["last_activity"].replace('Z', '+00:00'))
            hours_since = (datetime.now(timezone.utc) - last_activity).total_seconds() / 3600
            
            if hours_since > 48:  # No activity for 2+ days
                issues.append(f"No crawler activity for {hours_since:.1f} hours")
            elif hours_since > 24:  # No activity for 1+ day
                issues.append(f"Limited crawler activity: {hours_since:.1f} hours since last update")
        
        # Check error rate
        log_summary = self.get_log_summary(24)
        if log_summary["error_entries"] > 50:
            issues.append(f"High error rate: {log_summary['error_entries']} errors in 24h")
        elif log_summary["error_entries"] > 20:
            issues.append(f"Elevated error rate: {log_summary['error_entries']} errors in 24h")
        
        # Check disk usage
        if status["disk_usage_mb"] > 10000:  # > 10GB
            issues.append(f"High disk usage: {status['disk_usage_mb']} MB")
        
        # Check if crawler should be running but isn't
        now = datetime.now()
        if 0 <= now.hour < 4:  # Should be running
            pages_dir = self.base_dir / "pages"
            has_existing_crawl = pages_dir.exists() and len(list(pages_dir.glob("*.json"))) > 0
            
            # Crawler should run if:
            # 1. There are pending pages in frontier, OR
            # 2. Fresh crawl (no existing pages and no frontier file exists)
            frontier_file = self.base_dir / "frontier.json"
            is_fresh_crawl = not has_existing_crawl and not frontier_file.exists()
            
            if not status["is_running"] and (status["frontier_size"] > 0 or is_fresh_crawl):
                if is_fresh_crawl:
                    issues.append("Crawler should be running but isn't (fresh crawl initialization needed)")
                else:
                    issues.append("Crawler should be running but isn't (within time window with pending pages)")
        
        # Determine overall health
        if any("critical" in issue.lower() or "high" in issue.lower() for issue in issues):
            return "critical", issues
        elif issues:
            return "warning", issues
        else:
            return "healthy", issues
    
    def send_email_alert(self, subject: str, body: str, 
                        smtp_host: str, smtp_port: int,
                        username: str, password: str,
                        to_email: str, from_email: str) -> bool:
        """Send email alert."""
        try:
            msg = MIMEMultipart()
            msg['From'] = from_email
            msg['To'] = to_email
            msg['Subject'] = subject
            
            msg.attach(MIMEText(body, 'plain'))
            
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.starttls()
            server.login(username, password)
            server.send_message(msg)
            server.quit()
            
            return True
        except Exception as e:
            print(f"Failed to send email: {e}")
            return False
    
    def send_webhook_alert(self, webhook_url: str, payload: Dict) -> bool:
        """Send webhook alert (e.g., Slack, Discord, Teams)."""
        try:
            response = requests.post(webhook_url, json=payload, timeout=10)
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"Failed to send webhook: {e}")
            return False
    
    def send_discord_webhook(self, webhook_url: str, title: str = None, 
                           description: str = None, color: int = None, 
                           fields: List[Dict] = None) -> bool:
        """Send Discord-compatible webhook with embed support."""
        payload = {"content": "🚨 UTC Crawler Alert"}
        
        if title or description or color or fields:
            embed = {}
            if title:
                embed["title"] = title
            if description:
                embed["description"] = description
            if color:
                embed["color"] = color
            if fields:
                embed["fields"] = fields
            
            payload["embeds"] = [embed]
        
        return self.send_webhook_alert(webhook_url, payload)
    
    def create_discord_report_embed(self, status: Dict, health_status: str, 
                                  issues: List[str]) -> Dict:
        """Create Discord embed for crawler status report."""
        # Color mapping
        colors = {
            "healthy": 3066993,   # Green
            "warning": 16776960,  # Yellow
            "critical": 15158332  # Red
        }
        
        embed = {
            "title": f"Crawler Status: {health_status.upper()}",
            "color": colors.get(health_status, 3066993),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fields": [
                {
                    "name": "📊 Progress",
                    "value": f"**Pages:** {status['total_pages']:,}\n**Remaining:** {status['frontier_size']:,}\n**Completion:** {status['completion_percentage']}%",
                    "inline": True
                },
                {
                    "name": "🔧 Status",
                    "value": f"**Running:** {'Yes' if status['is_running'] else 'No'}\n**Errors:** {status['total_errors']}\n**Disk:** {status['disk_usage_mb']} MB",
                    "inline": True
                }
            ]
        }
        
        if status["last_activity"]:
            try:
                last_activity = datetime.fromisoformat(status["last_activity"].replace('Z', '+00:00'))
                hours_since = (datetime.now(timezone.utc) - last_activity).total_seconds() / 3600
                embed["fields"].append({
                    "name": "⏰ Last Activity",
                    "value": f"{hours_since:.1f} hours ago",
                    "inline": True
                })
            except ValueError:
                pass
        
        if issues:
            issues_text = "\n".join([f"• {issue}" for issue in issues[:5]])
            if len(issues) > 5:
                issues_text += f"\n... and {len(issues) - 5} more"
            embed["fields"].append({
                "name": "⚠️ Issues",
                "value": issues_text,
                "inline": False
            })
        
        if status["recent_errors"]:
            error_text = "\n".join([
                f"• {error.get('url', 'Unknown')[:50]}{'...' if len(error.get('url', '')) > 50 else ''}"
                for error in status["recent_errors"][-3:]
            ])
            embed["fields"].append({
                "name": "🚨 Recent Errors",
                "value": error_text,
                "inline": False
            })
        
        return embed
    
    def send_discord_report(self, webhook_url: str) -> bool:
        """Send comprehensive Discord report."""
        status = self.get_crawler_status()
        health_status, issues = self.check_health()
        embed = self.create_discord_report_embed(status, health_status, issues)
        
        payload = {
            "content": "🚨 UTC Crawler Alert" if health_status != "healthy" else "📊 UTC Crawler Report",
            "embeds": [embed]
        }
        
        return self.send_webhook_alert(webhook_url, payload)
    
    def test_webhook(self, webhook_url: str, test_type: str = "basic") -> bool:
        """Test webhook connectivity with different message types."""
        test_messages = {
            "basic": {
                "content": "🔧 UTC Crawler Monitor - Webhook Test",
                "embeds": [{
                    "title": "Test Successful",
                    "description": "This is a test message from the UTC Crawler Monitor.",
                    "color": 3066993,  # Green
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "fields": [
                        {
                            "name": "Test Type",
                            "value": "Basic connectivity test",
                            "inline": False
                        },
                        {
                            "name": "Timestamp",
                            "value": datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC'),
                            "inline": True
                        }
                    ]
                }]
            },
            "warning": {
                "content": "🚨 UTC Crawler Alert",
                "embeds": [{
                    "title": "Crawler Status: WARNING",
                    "description": "High error rate detected",
                    "color": 16776960,  # Yellow
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "fields": [
                        {
                            "name": "Test Type",
                            "value": "Warning message test",
                            "inline": False
                        }
                    ]
                }]
            },
            "critical": {
                "content": "🚨 UTC Crawler Alert",
                "embeds": [{
                    "title": "Crawler Status: CRITICAL",
                    "description": "Crawler has stopped responding",
                    "color": 15158332,  # Red
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "fields": [
                        {
                            "name": "Test Type",
                            "value": "Critical alert test",
                            "inline": False
                        }
                    ]
                }]
            }
        }
        
        if test_type not in test_messages:
            print(f"Unknown test type: {test_type}. Available: {', '.join(test_messages.keys())}")
            return False
        
        print(f"Testing webhook with {test_type} message...")
        success = self.send_webhook_alert(webhook_url, test_messages[test_type])
        
        if success:
            print(f"✅ Webhook test ({test_type}) successful!")
        else:
            print(f"❌ Webhook test ({test_type}) failed!")
        
        return success
    
    def generate_report(self, format_type: str = "text") -> str:
        """Generate comprehensive status report."""
        status = self.get_crawler_status()
        log_summary = self.get_log_summary(24)
        health_status, issues = self.check_health()
        
        if format_type == "json":
            report = {
                "crawler_status": status,
                "log_summary": log_summary,
                "health": {
                    "status": health_status,
                    "issues": issues
                }
            }
            return json.dumps(report, indent=2)
        
        # Text format
        report_lines = [
            "UTC Crawler Status Report",
            "=" * 50,
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            f"Health Status: {health_status.upper()}",
        ]
        
        if issues:
            report_lines.extend(["", "Issues:"])
            for issue in issues:
                report_lines.append(f"  - {issue}")
        
        report_lines.extend([
            "",
            "Crawler Status:",
            f"  Running: {'Yes' if status['is_running'] else 'No'}",
            f"  Pages Crawled: {status['total_pages']:,}",
            f"  Pages Remaining: {status['frontier_size']:,}",
            f"  Completion: {status['completion_percentage']}%",
            f"  Total Errors: {status['total_errors']}",
            f"  Disk Usage: {status['disk_usage_mb']} MB",
            f"  Last Activity: {status['last_activity'] or 'Never'}",
            "",
            "Recent Activity (24h):",
            f"  Log Entries: {log_summary['total_entries']}",
            f"  Errors: {log_summary['error_entries']}",
            f"  Warnings: {log_summary['warning_entries']}",
        ])
        
        if status["recent_errors"]:
            report_lines.extend(["", "Recent Errors:"])
            for error in status["recent_errors"][-3:]:
                report_lines.append(f"  - {error.get('url', 'Unknown')}: {error.get('error', 'Unknown error')}")
        
        return "\n".join(report_lines)


def main():
    parser = argparse.ArgumentParser(description="UTC Crawler Monitor")
    parser.add_argument("--report", action="store_true",
                       help="Generate status report")
    parser.add_argument("--alert", action="store_true",
                       help="Check health and send alerts if needed")
    parser.add_argument("--webhook", type=str,
                       help="Webhook URL for alerts")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                       help="Report format")
    parser.add_argument("--test-webhook", type=str,
                       help="Test webhook with specified URL")
    parser.add_argument("--test-type", choices=["basic", "warning", "critical"], default="basic",
                       help="Type of test message to send")
    
    args = parser.parse_args()
    
    monitor = CrawlerMonitor()
    
    if args.report:
        report = monitor.generate_report(args.format)
        print(report)
        return 0
    
    if args.test_webhook:
        success = monitor.test_webhook(args.test_webhook, args.test_type)
        return 0 if success else 1
    
    if args.alert:
        health_status, issues = monitor.check_health()
        
        if health_status != "healthy":
            alert_msg = f"UTC Crawler Alert: {health_status.upper()}\n\n"
            alert_msg += "\n".join(f"- {issue}" for issue in issues)
            
            print(f"ALERT: {health_status}")
            print(alert_msg)
            
            if args.webhook:
                # Send Discord-compatible alert
                monitor.send_discord_report(args.webhook)
        else:
            print("Status: healthy")
        
        return 0 if health_status == "healthy" else 1
    
    # Default: show status
    status = monitor.get_crawler_status()
    print(f"Crawler Running: {status['is_running']}")
    print(f"Pages: {status['total_pages']:,}, Remaining: {status['frontier_size']:,}")
    print(f"Completion: {status['completion_percentage']}%")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())