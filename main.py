#!/usr/bin/env python3

import os, re, sys, time, json, traceback, html, random, hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional
import requests
from dotenv import load_dotenv
load_dotenv()

# =========================
# ENVs
# =========================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
ALTRADY_WEBHOOK_URL = os.getenv("ALTRADY_WEBHOOK_URL", "").strip()
ALTRADY_API_KEY = os.getenv("ALTRADY_API_KEY", "").strip()
ALTRADY_API_SECRET = os.getenv("ALTRADY_API_SECRET", "").strip()
ALTRADY_EXCHANGE = os.getenv("ALTRADY_EXCHANGE", "BYBI").strip().upper()
QUOTE = os.getenv("QUOTE", "USDT").strip().upper()

LEVERAGE = int(os.getenv("LEVERAGE", "10"))
TP1_PCT = float(os.getenv("TP1_PCT", "20"))
TP2_PCT = float(os.getenv("TP2_PCT", "20"))
TP3_PCT = float(os.getenv("TP3_PCT", "20"))
TP4_PCT = float(os.getenv("TP4_PCT", "20"))
TP5_PCT = float(os.getenv("TP5_PCT", "20"))
DCA1_QTY_PCT = float(os.getenv("DCA1_QTY_PCT", "150"))

STOP_PROTECTION_TYPE = os.getenv("STOP_PROTECTION_TYPE", "FOLLOW_TAKE_PROFIT").strip().upper()
STOP_LOSS_ORDER_TYPE = os.getenv("STOP_LOSS_ORDER_TYPE", "STOP_LOSS_MARKET").strip().upper()
ENTRY_EXPIRATION_MIN = int(os.getenv("ENTRY_EXPIRATION_MIN", "180"))
ENTRY_WAIT_MINUTES = int(os.getenv("ENTRY_WAIT_MINUTES", "0"))
ENTRY_TRIGGER_BUFFER_PCT = float(os.getenv("ENTRY_TRIGGER_BUFFER_PCT", "0.0"))
ENTRY_EXPIRATION_PRICE_PCT = float(os.getenv("ENTRY_EXPIRATION_PRICE_PCT", "0.0"))
TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

POLL_BASE_SECONDS = int(os.getenv("POLL_BASE_SECONDS", "60"))
POLL_OFFSET_SECONDS = int(os.getenv("POLL_OFFSET_SECONDS", "3"))
POLL_JITTER_MAX = int(os.getenv("POLL_JITTER_MAX", "7"))
DISCORD_FETCH_LIMIT = int(os.getenv("DISCORD_FETCH_LIMIT", "50"))
STATE_FILE = Path(os.getenv("STATE_FILE", "state_ao.json"))
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "30"))  # empfehlenswert >0

ALLOWED_PROVIDERS = {s.strip().lower() for s in os.getenv("ALLOWED_PROVIDERS", "haseeb1111").split(",") if s.strip()}

# =========================
# Startup Checks
# =========================
if not all([DISCORD_TOKEN, CHANNEL_ID, ALTRADY_WEBHOOK_URL, ALTRADY_API_KEY, ALTRADY_API_SECRET]):
    print("ENV fehlt!")
    sys.exit(1)

HEADERS = {"Authorization": DISCORD_TOKEN, "User-Agent": "DiscordToAltrady-AO/2.0"}

# =========================
# Utils
# =========================
def load_state():
    if STATE_FILE.exists():
        try: return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except: pass
    return {"last_id": None, "last_trade_ts": 0.0, "seen_hashes": []}

def save_state(st): 
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st), encoding="utf-8")
    tmp.replace(STATE_FILE)

def sleep_until_next_tick():
    now = time.time()
    base = (now // POLL_BASE_SECONDS) * POLL_BASE_SECONDS
    next_tick = base + POLL_BASE_SECONDS + POLL_OFFSET_SECONDS
    if now < base + POLL_OFFSET_SECONDS:
        next_tick = base + POLL_OFFSET_SECONDS
    time.sleep(max(0, next_tick - now + random.uniform(0, POLL_JITTER_MAX)))

def fetch_messages_after(channel_id: str, after_id: Optional[str] = None):
    collected = []
    params = {"limit": min(DISCORD_FETCH_LIMIT, 100)}
    if after_id: params["after"] = after_id
    while True:
        r = requests.get(f"https://discord.com/api/v10/channels/{channel_id}/messages", headers=HEADERS, params=params, timeout=15)
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 5)) + 0.5)
            continue
        r.raise_for_status()
        data = r.json()
        if not data: break
        collected.extend(data)
        if len(data) < params["limit"]: break
        params["after"] = str(max(int(m["id"]) for m in data))
    return collected

def clean_text(s: str) -> str:
    if not s: return ""
    s = html.unescape(s)
    s = re.sub(r"[*_`~]+", "", s)
    s = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", s)
    s = re.sub(r"[ \t\u00A0]+", " ", s)
    return " ".join(line.strip() for line in s.split("\n") if line.strip())

def message_text(m: dict) -> str:
    parts = [m.get("content", "")]
    for embed in m.get("embeds", []):
        if embed.get("title"): parts.append(embed["title"])
        if embed.get("description"): parts.append(embed["description"])
        for field in embed.get("fields", []):
            parts.append(field.get("name", ""))
            parts.append(field.get("value", ""))
        if embed.get("footer", {}).get("text"): parts.append(embed["footer"]["text"])
    return clean_text("\n".join(filter(None, parts)))

def to_price(s: str) -> float:
    return float(re.sub(r"[^\d.]", "", s.replace(",", "")))

# =========================
# ROBUSTER AO TRADES APP PARSER (2025)
# =========================
def signal_hash(sig: dict) -> str:
    key = f"{sig['base']}{sig['side']}{sig['entry']}{sig['stop_loss']}{''.join(str(t) for t in sig['tps'])}"
    return hashlib.md5(key.encode()).hexdigest()

def parse_ao_signal(txt: str) -> Optional[dict]:
    txt_u = txt.upper()

    # --- Nur neue Signale (enthält "NEW SIGNAL" oder "NEW TRADE SIGNAL") ---
    if not any(x in txt_u for x in ["NEW SIGNAL", "NEW TRADE SIGNAL"]):
        return None

    # --- Provider (haseeb1111 oder andere) ---
    provider = None
    if "HASEEB1111" in txt_u:
        provider = "haseeb1111"
    else:
        m = re.search(r"(?:Trader|Caller|Signal by)[\s:*·•-]+([A-Za-z0-9_.]+)", txt, re.I)
        if m: provider = m.group(1).strip()

    if not provider or (ALLOWED_PROVIDERS and provider.lower() not in ALLOWED_PROVIDERS):
        return None

    # --- Side + Pair ---
    m = re.search(r"(LONG|SHORT)\s+SIGNAL\s*[-–—]\s*([A-Z0-9]+)\s*/\s*([A-Z0-9]+)", txt, re.I)
    if not m:
        return None
    side = "long" if m.group(1).upper() == "LONG" else "short"
    base = m.group(2).upper()
    quote_in_signal = m.group(3).upper()
    if quote_in_signal != QUOTE:
        print(f"Quote {quote_in_signal} ≠ {QUOTE} → ignoriert")
        return None

    # --- Entry ---
    m = re.search(r"Entry[\s:*·•]+\$?([\d.,]+)", txt, re.I)
    if not m: return None
    entry = to_price(m.group(1))

    # --- TPs ---
    tps = []
    for i in range(1, 6):
        m = re.search(fr"TP{i}[\s:*·•→]+\$?([\d.,]+)", txt, re.I)
        if m:
            tps.append(to_price(m.group(1)))
    if not tps: return None

    # --- Stop Loss ---
    m = re.search(r"Stop Loss[\s:*·•]+\$?([\d.,]+)", txt, re.I)
    if not m: return None
    stop_loss = to_price(m.group(1))

    # --- DCA1 optional ---
    dca1 = None
    m = re.search(r"DCA1[\s:*·•]+\$?([\d.,]+)", txt, re.I)
    if m: dca1 = to_price(m.group(1))

    return {
        "provider": provider,
        "base": base,
        "side": side,
        "entry": entry,
        "tps": tps,
        "dca1": dca1,
        "stop_loss": stop_loss,
    }

# =========================
# Altrady Payload (unverändert, nur kleiner Log-Fix)
# =========================
def _percent_from_entry(entry: float, target: float) -> float:
    return (target / entry - 1.0) * 100.0

def build_altrady_open_payload_ao(sig: dict) -> dict:
    base = sig["base"]
    side = sig["side"]
    symbol = f"{ALTRADY_EXCHANGE}_{QUOTE}_{base}"

    sl_pct = abs(_percent_from_entry(sig["entry"], sig["stop_loss"]))
    trigger_price = sig["entry"] * (1.0 - ENTRY_TRIGGER_BUFFER_PCT/100.0 if side == "long" else 1.0 + ENTRY_TRIGGER_BUFFER_PCT/100.0)
    expire_price = sig["entry"] * (1.0 + ENTRY_EXPIRATION_PRICE_PCT/100.0 if side == "long" else 1.0 - ENTRY_EXPIRATION_PRICE_PCT/100.0) if ENTRY_EXPIRATION_PRICE_PCT > 0 else None

    tp_pcts = [_percent_from_entry(sig["entry"], tp) for tp in sig["tps"]]
    splits = [TP1_PCT, TP2_PCT, TP3_PCT, TP4_PCT, TP5_PCT]
    take_profits = [
        {"price_percentage": float(f"{p:.6f}"), "position_percentage": s}
        for p, s in zip(tp_pcts, splits) if s > 0
    ]

    dca_orders = [{"price": sig["dca1"], "quantity_percentage": DCA1_QTY_PCT}] if sig["dca1"] and DCA1_QTY_PCT > 0 else []

    payload = {
        "api_key": ALTRADY_API_KEY,
        "api_secret": ALTRADY_API_SECRET,
        "exchange": ALTRADY_EXCHANGE,
        "action": "open",
        "symbol": symbol,
        "side": side,
        "order_type": "limit",
        "signal_price": sig["entry"],
        "leverage": LEVERAGE,
        "entry_condition": {"price": float(f"{trigger_price:.10f}")},
        "take_profit": take_profits,
        "stop_loss": {
            "order_type": STOP_LOSS_ORDER_TYPE,
            "stop_percentage": float(f"{sl_pct:.6f}"),
            "protection_type": STOP_PROTECTION_TYPE
        },
        "dca_orders": dca_orders,
        "entry_expiration": {"time": ENTRY_EXPIRATION_MIN}
    }
    if expire_price: payload["entry_expiration"]["price"] = float(f"{expire_price:.10f}")
    if ENTRY_WAIT_MINUTES > 0:
        payload["entry_condition"]["time"] = ENTRY_WAIT_MINUTES
        payload["entry_condition"]["operator"] = "OR"
    if TEST_MODE: payload["test"] = True

    print(f"\nAO {base} {side.upper()} @ {sig['entry']} | SL {sig['stop_loss']} | TPs: {len(tps)}")
    return payload

def post_to_altrady(payload: dict):
    print("Sende Trade an Altrady...")
    for _ in range(3):
        try:
            r = requests.post(ALTRADY_WEBHOOK_URL, json=payload, timeout=20)
            if r.status_code in (200, 204):
                print("Trade erfolgreich übermittelt!")
                return True
            elif r.status_code == 429:
                time.sleep(2)
                continue
            else:
                print(f"Fehler {r.status_code}: {r.text}")
        except Exception as e:
            print(f"Exception: {e}")
            time.sleep(3)
    return False

# =========================
# Main
# =========================
def main():
    print("Discord → Altrady AO Bot (2025 AO Trades APP ready)".center(60, "="))
    state = load_state()
    seen_hashes = set(state.get("seen_hashes", []))

    if state.get("last_id") is None:
        try:
            latest = fetch_messages_after(CHANNEL_ID, None)
            if latest:
                state["last_id"] = str(latest[0]["id"])
                save_state(state)
        except: pass

    print("Überwache Channel...")

    # === DEBUG TEST (setze ENV DEBUG_TEST=1 auf Railway) ===
    if os.getenv("DEBUG_TEST") == "1":
        test = """NEW SIGNAL • BEAT • Entry $0.87071
                  SHORT SIGNAL - BEAT/USDT
                  Trader: haseeb1111
                  Entry: 0.87071
                  TP1: 0.86374
                  TP2: 0.85678
                  Stop Loss: 0.87671"""
        sig = parse_ao_signal(test)
        print("DEBUG PARSE:", sig)
        sys.exit(0)

    while True:
        try:
            msgs = fetch_messages_after(CHANNEL_ID, state.get("last_id"))
            if not msgs:
                print(f"[{datetime.now():%H:%M:%S}] Warte auf neue Signale...")
            else:
                max_id = state.get("last_id") or "0"
                for msg in sorted(msgs, key=lambda x: int(x["id"])):
                    mid = msg["id"]
                    if int(mid) <= int(max_id): continue
                    raw = message_text(msg)
                    if not raw: continue

                    if COOLDOWN_SECONDS and (time.time() - state.get("last_trade_ts", 0) < COOLDOWN_SECONDS):
                        continue

                    sig = parse_ao_signal(raw)
                    if sig:
                        h = signal_hash(sig)
                        if h in seen_hashes:
                            print(f"Duplicate Signal erkannt → ignoriert")
                            continue

                        print(f"\nNEUES SIGNAL von {sig['provider']}: {sig['base']} {sig['side'].upper()}")
                        payload = build_altrady_open_payload_ao(sig)
                        post_to_altrady(payload)

                        seen_hashes.add(h)
                        state["last_trade_ts"] = time.time()
                        state["seen_hashes"] = list(seen_hashes)[-500:]  # keep last 500

                    max_id = mid

                state["last_id"] = max_id
                save_state(state)

        except KeyboardInterrupt:
            print("\nBeendet.")
            break
        except Exception as e:
            print("Fehler:", e)
            traceback.print_exc()
            time.sleep(10)
        finally:
            sleep_until_next_tick()

if __name__ == "__main__":
    main()
