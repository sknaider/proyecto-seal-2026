#!/usr/bin/env python3
"""
dum_ip_enrichment.py — Enriquecimiento de IPs de auth.log con ipquery.io.
Parte del sistema de guardia nocturna de DUM.

Uso:
  python3 dum_ip_enrichment.py
  python3 dum_ip_enrichment.py --log /var/log/auth.log --output results/security_audit.md
  python3 dum_ip_enrichment.py --dry-run  # solo extrae IPs, no consulta API

Asignado por JARVIS (feedback_027). Ejecutar periódicamente desde DUM.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

AUTH_LOG = "/var/log/auth.log"
API_BASE = "https://api.ipquery.io"
DEFAULT_OUTPUT = os.path.expanduser("~/IA/proyecto-seal/claude-code-analysis/security_audit.md")
RISK_THRESHOLD = 50  # risk_score >= this → HIGH RISK


def extract_failed_ips(log_path: str) -> dict[str, int]:
    """Extract IPs from failed SSH login attempts in auth.log."""
    pattern = re.compile(
        r"Failed (?:password|publickey|keyboard-interactive) for (?:invalid user )?\S+ from (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
    )
    ip_counts: dict[str, int] = {}

    try:
        with open(log_path, "r", errors="replace") as f:
            for line in f:
                m = pattern.search(line)
                if m:
                    ip = m.group(1)
                    ip_counts[ip] = ip_counts.get(ip, 0) + 1
    except PermissionError:
        # Try with sudo via journalctl fallback
        print(f"  Permission denied for {log_path}, trying journalctl...")
        try:
            result = subprocess.run(
                ["journalctl", "-u", "ssh", "--since", "24 hours ago", "--no-pager"],
                capture_output=True, text=True, timeout=30
            )
            for line in result.stdout.splitlines():
                m = pattern.search(line)
                if m:
                    ip = m.group(1)
                    ip_counts[ip] = ip_counts.get(ip, 0) + 1
        except Exception as e:
            print(f"  journalctl also failed: {e}")

    return ip_counts


def enrich_ip(ip: str) -> dict:
    """Query ipquery.io for IP intelligence."""
    try:
        r = requests.get(f"{API_BASE}/{ip}", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": str(e), "ip": ip}


def parse_risk(data: dict) -> dict:
    """Extract risk fields from ipquery.io response."""
    risk = data.get("risk", {})
    location = data.get("location", {})
    isp_info = data.get("isp", {})
    return {
        "ip": data.get("ip", "?"),
        "country": location.get("country", "?"),
        "city": location.get("city", "?"),
        "isp": isp_info.get("isp", "?"),
        "asn": isp_info.get("asn", "?"),
        "risk_score": risk.get("risk_score", 0),
        "is_vpn": risk.get("is_vpn", False),
        "is_tor": risk.get("is_tor", False),
        "is_proxy": risk.get("is_proxy", False),
        "is_datacenter": risk.get("is_datacenter", False),
        "is_anonymous": risk.get("is_anonymous", False),
        "error": data.get("error"),
    }


def write_report(results: list[dict], ip_counts: dict[str, int], output_path: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    high_risk = [r for r in results if not r.get("error") and
                 (r["risk_score"] >= RISK_THRESHOLD or r["is_tor"])]
    normal = [r for r in results if r not in high_risk]

    lines = [
        f"# Security Audit — IP Enrichment Report",
        f"",
        f"**Generated:** {ts}  ",
        f"**Log source:** {AUTH_LOG}  ",
        f"**Unique IPs analyzed:** {len(results)}  ",
        f"**Total failed attempts:** {sum(ip_counts.values())}  ",
        f"**High risk IPs:** {len(high_risk)}  ",
        f"",
    ]

    if high_risk:
        lines += [
            f"## ⚠️ HIGH RISK IPs (score ≥ {RISK_THRESHOLD} or TOR)",
            f"",
            f"| IP | Attempts | Country | ISP | Risk Score | Flags |",
            f"|---|---|---|---|---|---|",
        ]
        for r in sorted(high_risk, key=lambda x: -x["risk_score"]):
            flags = []
            if r["is_tor"]: flags.append("TOR")
            if r["is_vpn"]: flags.append("VPN")
            if r["is_proxy"]: flags.append("PROXY")
            if r["is_datacenter"]: flags.append("DC")
            attempts = ip_counts.get(r["ip"], "?")
            lines.append(
                f"| {r['ip']} | {attempts} | {r['country']} | {r['isp'][:30]} | "
                f"**{r['risk_score']}** | {', '.join(flags) or '—'} |"
            )
        lines.append("")

    lines += [
        f"## All IPs",
        f"",
        f"| IP | Attempts | Country | ISP | Risk Score | VPN | TOR | Proxy | DC |",
        f"|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda x: -ip_counts.get(x["ip"], 0)):
        if r.get("error"):
            lines.append(f"| {r['ip']} | {ip_counts.get(r['ip'], '?')} | ERROR | {r['error'][:40]} | — | — | — | — | — |")
        else:
            attempts = ip_counts.get(r["ip"], "?")
            lines.append(
                f"| {r['ip']} | {attempts} | {r['country']} | {r['isp'][:30]} | "
                f"{r['risk_score']} | {'✓' if r['is_vpn'] else '—'} | "
                f"{'⚠️' if r['is_tor'] else '—'} | "
                f"{'✓' if r['is_proxy'] else '—'} | "
                f"{'✓' if r['is_datacenter'] else '—'} |"
            )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nReport saved: {output_path}")
    if high_risk:
        print(f"\n⚠️  {len(high_risk)} HIGH RISK IPs detected!")
        for r in high_risk[:5]:
            score = r['risk_score']
            flags = [k for k in ['is_tor','is_vpn','is_proxy'] if r.get(k)]
            print(f"   {r['ip']:16s} attempts={ip_counts.get(r['ip'],0):4d} score={score} {flags}")


def main():
    parser = argparse.ArgumentParser(description="IP enrichment for DUM security monitoring")
    parser.add_argument("--log", default=AUTH_LOG)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--dry-run", action="store_true", help="Extract IPs only, no API calls")
    parser.add_argument("--max-ips", type=int, default=200, help="Max IPs to query (API rate limit)")
    args = parser.parse_args()

    print(f"Extracting failed login IPs from {args.log}...")
    ip_counts = extract_failed_ips(args.log)

    if not ip_counts:
        print("No failed login attempts found.")
        return

    print(f"Found {len(ip_counts)} unique IPs, {sum(ip_counts.values())} total attempts")

    # Sort by attempt count, limit to max_ips
    sorted_ips = sorted(ip_counts.keys(), key=lambda ip: -ip_counts[ip])[:args.max_ips]

    if args.dry_run:
        print("\nDRY RUN — top IPs by attempt count:")
        for ip in sorted_ips[:20]:
            print(f"  {ip:16s} {ip_counts[ip]:4d} attempts")
        return

    print(f"\nQuerying ipquery.io for {len(sorted_ips)} IPs...")
    results = []
    for i, ip in enumerate(sorted_ips, 1):
        sys.stdout.write(f"\r  [{i:3d}/{len(sorted_ips)}] {ip}          ")
        sys.stdout.flush()
        data = enrich_ip(ip)
        results.append(parse_risk(data))
        if i < len(sorted_ips):
            time.sleep(0.1)  # gentle rate limiting

    print()
    write_report(results, ip_counts, args.output)

    # Summary to stdout
    high = [r for r in results if not r.get("error") and
            (r["risk_score"] >= RISK_THRESHOLD or r["is_tor"])]
    print(f"\nSummary: {len(ip_counts)} unique IPs | {len(high)} high risk | "
          f"{sum(ip_counts.values())} total attempts")


if __name__ == "__main__":
    main()
