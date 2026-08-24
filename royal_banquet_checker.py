"""
Disneyland Paris restaurant availability checker -- GitHub Actions version.

Watches one or more restaurants at once. Each "watch" can either alert on
ANY open time slot for its dates/party size, or be narrowed to only alert
for specific times (e.g. only an 11:45 AM slot, ignore everything else
that opens that day).

Does ONE check and exits -- GitHub Actions (or cron-job.org calling
workflow_dispatch) handles running it every 10 minutes.
"""

import json
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

import requests

# ============================== CONFIG ==================================

WATCHES = [
    {
        "name": "Royal Banquet",
        "restaurant_id": "H01R02",
        "dates": ["2026-09-25", "2026-09-26", "2026-09-27"],
        "party_sizes": [4, 6],
        "only_times": None,  # None = alert on ANY open time
    },
    {
        "name": "Agrabah Cafe",
        "restaurant_id": "P1AR06",
        "dates": ["2026-09-26"],
        "party_sizes": [2],
        "only_times": ["11:45 AM"],  # ONLY alert if this exact slot opens
    },
    {
        "name": "Pym Kitchen",
        "restaurant_id": "P2AR02",
        "dates": ["2026-09-26"],
        "party_sizes": [2],
        "only_times": ["03:30 PM"],  # ONLY alert if this exact slot opens
    },
]

# Disney's public front-end API key (seen in the site's own network traffic --
# not a login credential, just what their web page uses to call its own API)
X_API_KEY = "AaQHDoRgDa66dl2PQuTEe9DjyBlH8ylV4LxnldFY"

# --- Email settings, pulled from GitHub Secrets at run time ---
GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
NOTIFY_EMAIL = [e.strip() for e in os.environ["NOTIFY_EMAIL"].split(",")]

# ==========================================================================

API_URL = "https://dlp-is-sales-drs-book-dine.wdprapps.disney.com/prod/v4/book-dine/availabilities/en-usd"
BOOKING_URL_TEMPLATE = "https://bookrestaurants.disneylandparis.com/en-usd?id={restaurant_id}"
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


def check_one(restaurant_id: str, date: str, party_size: int) -> list[tuple[str, str]]:
    """Returns list of (meal_period, time) tuples that are currently available."""
    payload = {
        "partyMix": party_size,
        "session": 0,
        "restaurantId": restaurant_id,
        "sourceSite": "web",
        "date": date,
    }
    resp = requests.post(API_URL, headers=HEADERS, json=payload, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    available = []
    for entry in data:
        for meal_period in entry.get("mealPeriods", []):
            for slot in meal_period.get("slotList", []):
                if str(slot.get("available")).lower() == "true":
                    available.append((meal_period["mealPeriod"], slot["time"]))
    return available


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
    newly_found = []  # (restaurant_name, restaurant_id, date, party_size, meal_period, time)

    for watch in WATCHES:
        name = watch["name"]
        restaurant_id = watch["restaurant_id"]
        only_times = watch.get("only_times")

        for date in watch["dates"]:
            for party_size in watch["party_sizes"]:
                try:
                    available = check_one(restaurant_id, date, party_size)
                except Exception as e:
                    log(f"ERROR checking {name} {date} / party {party_size}: {e}")
                    continue

                if only_times:
                    available = [(mp, t) for mp, t in available if t in only_times]

                log(f"{name} {date} party {party_size}: "
                    f"{available if available else 'nothing matching'}")

                for meal_period, time_str in available:
                    key = f"{restaurant_id}|{date}|{party_size}|{time_str}"
                    if key not in seen_slots:
                        newly_found.append(
                            (name, restaurant_id, date, party_size, meal_period, time_str)
                        )
                        seen_slots.add(key)

    if newly_found:
        lines = []
        for name, restaurant_id, date, party_size, meal_period, time_str in newly_found:
            link = BOOKING_URL_TEMPLATE.format(restaurant_id=restaurant_id)
            lines.append(f"- {name}: {date}, party of {party_size}, {meal_period} {time_str}\n  {link}")
        body = "New restaurant openings just appeared:\n\n" + "\n\n".join(lines)
        try:
            send_email("A watched restaurant slot just opened up!", body)
            log(f"Email sent for {len(newly_found)} new slot(s).")
        except Exception as e:
            log(f"ERROR sending email: {e}")
    else:
        log("No new openings this run.")

    save_seen_slots(seen_slots)


if __name__ == "__main__":
    main()
