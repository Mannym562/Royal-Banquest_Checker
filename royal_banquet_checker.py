"""
Royal Banquet (Disneyland Paris) availability checker -- GitHub Actions version.

Unlike the original version, this script does ONE check and then exits.
GitHub Actions is what handles "run this every 10 minutes" -- see
.github/workflows/check.yml in this same repo.

Email credentials are read from environment variables (set as GitHub
Secrets), not hardcoded in this file, so nothing sensitive ends up in your
repo.
"""

import json
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

import requests

# ============================== CONFIG ==================================

RESTAURANT_ID = "H01R02"  # Royal Banquet
TARGET_DATES = ["2026-09-25", "2026-09-26", "2026-09-27"]
PARTY_SIZES = [4, 6]

# Disney's public front-end API key (seen in the site's own network traffic --
# not a login credential, just what their web page uses to call its own API)
X_API_KEY = "AaQHDoRgDa66dl2PQuTEe9DjyBlH8ylV4LxnldFY"

# --- Email settings, pulled from GitHub Secrets at run time ---
GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
# NOTIFY_EMAIL secret can be one address, or several separated by commas
NOTIFY_EMAIL = [e.strip() for e in os.environ["NOTIFY_EMAIL"].split(",")]

# ==========================================================================

API_URL = "https://dlp-is-sales-drs-book-dine.wdprapps.disney.com/prod/v4/book-dine/availabilities/en-usd"
STATE_FILE = Path(__file__).parent / "seen_slots.json"

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "origin": "https://bookrestaurants.disneylandparis.com",
    "referer": "https://bookrestaurants.disneylandparis.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "x-api-key": X_API_KEY,
}


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def load_seen_slots() -> set:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_seen_slots(slots: set) -> None:
    STATE_FILE.write_text(json.dumps(sorted(slots)))


def check_one(date: str, party_size: int) -> list[str]:
    payload = {
        "partyMix": party_size,
        "session": 0,
        "restaurantId": RESTAURANT_ID,
        "sourceSite": "web",
        "date": date,
    }
    resp = requests.post(API_URL, headers=HEADERS, json=payload, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    available_times = []
    for entry in data:
        for meal_period in entry.get("mealPeriods", []):
            for slot in meal_period.get("slotList", []):
                if str(slot.get("available")).lower() == "true":
                    available_times.append(f"{meal_period['mealPeriod']} {slot['time']}")
    return available_times


def send_email(subject: str, body: str) -> None:
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = ", ".join(NOTIFY_EMAIL)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, NOTIFY_EMAIL, msg.as_string())


def main():
    seen_slots = load_seen_slots()
    newly_found = []

    for date in TARGET_DATES:
        for party_size in PARTY_SIZES:
            try:
                available = check_one(date, party_size)
            except Exception as e:
                log(f"ERROR checking {date} / party {party_size}: {e}")
                continue

            log(f"{date} party {party_size}: {available if available else 'nothing open'}")

            for time_str in available:
                key = f"{date}|{party_size}|{time_str}"
                if key not in seen_slots:
                    newly_found.append((date, party_size, time_str))
                    seen_slots.add(key)

    if newly_found:
        lines = [f"- {d}, party of {p}, {t}" for d, p, t in newly_found]
        body = "New Royal Banquet openings just appeared:\n\n" + "\n".join(lines)
        body += "\n\nBook now: https://bookrestaurants.disneylandparis.com/en-usd?id=H01R02"
        try:
            send_email("Royal Banquet table just opened up!", body)
            log(f"Email sent for {len(newly_found)} new slot(s).")
        except Exception as e:
            log(f"ERROR sending email: {e}")
    else:
        log("No new openings this run.")

    save_seen_slots(seen_slots)


if __name__ == "__main__":
    main()
