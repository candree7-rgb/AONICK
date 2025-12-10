#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, re, sys, time, json, random, hashlib, html
from datetime import datetime
from pathlib import Path
from typing import Optional
import requests
from dotenv import load_dotenv
load_dotenv()

# ========================= ENVs =========================
DISCORD_TOKEN           = os.getenv("DISCORD_TOKEN", "").strip()
CHANNEL_ID              = os.getenv("CHANNEL_ID", "").strip()
ALTRADY_WEBHOOK_URL     = os.getenv("ALTRADY_WEBHOOK_URL", "").strip()
ALTRADY_API_KEY         = os.getenv("ALTRADY_API_KEY", "").strip()
ALTRADY_API_SECRET      = os.getenv("ALTRADY_API_SECRET", "").strip()
ALTRADY_EXCHANGE        = os.getenv("ALTRADY_EXCHANGE", "BYBI").strip().upper()
QUOTE                   = os.getenv("QUOTE", "USDT").strip().upper()

LEVERAGE                = int(os.getenv("LEVERAGE", "10"))
TP1_PCT                 = float(os.getenv("TP1_PCT", "20"))
TP2_PCT                 = float(os.getenv("TP2_PCT", "20"))
TP3_PCT                 = float(os.getenv("TP3_PCT", "20"))
TP4_PCT                 = float(os.getenv("TP4_PCT", "20"))
TP5_PCT                 = float(os.getenv("TP5_PCT", "20"))
DCA1_QTY_PCT            = float(os.getenv("DCA1_QTY_PCT", "150"))

STOP_PROTECTION_TYPE    = os.getenv("STOP_PROTECTION_TYPE", "FOLLOW_TAKE_PROFIT").strip().upper()
STOP_LOSS_ORDER_TYPE    = os.getenv("STOP_LOSS_ORDER_TYPE", "STOP_LOSS_MARKET").strip().upper()
ENTRY_EXPIRATION_MIN    = int(os.getenv("ENTRY_EXPIRATION_MIN", "180"))
ENTRY_WAIT_MINUTES      = int(os.getenv("ENTRY_WAIT_MINUTES", "0"))
ENTRY_TRIGGER_BUFFER_PCT= float(os.getenv("ENTRY_TRIGGER_BUFFER_PCT", "0.0"))
ENTRY_EXPIRATION_PRICE_PCT = float(os.getenv("ENTRY_EXPIRATION_PRICE_PCT", "0.0"))
TEST_MODE               = os.getenv("TEST_MODE", "false").lower() == "true"
COOLDOWN_SECONDS        = int(os.getenv("COOLDOWN_SECONDS", "0"))  # 0 = nur Altrady-Cooldown
POLL_BASE_SECONDS       = int(os.getenv("POLL_BASE_SECONDS", "45"))
POLL_JITTER_MAX         = int(os.getenv("POLL_JITTER_MAX", "15"))

ORDER_TYPE = os.getenv("ORDER_TYPE", "market").strip().lower()
if ORDER_TYPE not in ("market", "limit"):
    ORDER_TYPE = "market"

STATE_FILE              = Path("state_ao.json")
ALLOWED_PROVIDERS       = {s.strip().lower() for s in os.getenv("ALLOWED_PROVIDERS", "haseeb1111").split(",") if s.strip()}

# ========================= Startup Check =========================
if not all([DISCORD_TOKEN, CHANNEL_ID, ALTRADY_WEBHOOK_URL, ALTRADY_API_KEY, ALTRADY_API_SECRET]):
    print("Fehlende ENV-Variablen!")
    sys.exit(1)

HEADERS = {"Authorization": DISCORD_TOKEN, "User-Agent": "AO-Bot-Final/2025"}

# ========================= State =========================
def load_state():
    if STATE_FILE.exists():
        try: return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except: pass
    return {"last_id": None, "last_trade_ts": 0.0, "seen_hashes": []}

def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(STATE_FILE)

state = load_state()
seen_hashes = set(state.get("seen_hashes", []))

# Erster Start: nicht rückwirkend traden
if state.get("last_id") is None:
    print("Erster Start → setze Baseline auf aktuellste Nachricht")
    try:
        first = requests.get(f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages?limit=1", headers=HEADERS, timeout=10).json()
        if first:
            state["last_id"] = first[0]["id"]
            save_state(state)
    except: pass

last_id = state.get("last_id")

# ========================= Discord =========================
def fetch_messages(after_id: Optional[str] = None):
    url = f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages?limit=50"
    if after_id: url += f"&after={after_id}"
    for _ in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 429:
                time.sleep(float(r.json().get("retry_after", 5)) + 1)
                continue
            r.raise_for_status()
            return r.json()
        except: time.sleep(5)
    return []

def extract_text(msg):
    parts = [msg.get("content", "")]
    for embed in msg.get("embeds", []):
        parts.extend([embed.get("title",""), embed.get("description","")])
        for field in embed.get("fields", []):
            parts.extend([field.get("name",""), field.get("value","")])
    text = " | ".join(filter(None, parts))
    text = html.unescape(text)
    text = re.sub(r"[<@!>&]|\*\*|\*|__|_|`|<.*?>", "", text)
    return text.strip()

# ========================= Parser (2025 ready) =========================
def parse_signal(text: str) -> Optional[dict]:
    if not re.search(r"NEW SIGNAL|NEW TRADE SIGNAL", text, re.I):
        return None
    if "haseeb1111" not in text.lower():
        return None

    m = re.search(r"(LONG|SHORT)\s+SIGNAL\s*[-–—]?\s*([A-Z0-9]+)\s*/\s*([A-Z0-9]+)", text, re.I)
    if not m: return None
    side, base, q = m.groups()
    if q.upper() != QUOTE: return None
    side = "long" if side.upper() == "LONG" else "short"

    entry_match = re.search(r"Entry[:\s→]*\$?([0-9.,]+)", text, re.I)
    sl_match    = re.search(r"Stop Loss[:\s→]*\$?([0-9.,]+)", text, re.I)
    if not entry_match or not sl_match: return None

    entry = float(entry_match.group(1).replace(",", ""))
    sl    = float(sl_match.group(1).replace(",", ""))

    tps = []
    for i in range(1, 7):
        tp = re.search(fr"TP{i}[:\s→]*\$?([0-9.,]+)", text, re.I)
        if tp: tps.append(float(tp.group(1).replace(",", "")))
    if not tps: return None

    dca1_match = re.search(r"DCA1?[:\s→]*\$?([0-9.,]+)", text, re.I)
    dca1 = float(dca1_match.group(1).replace(",", "")) if dca1_match else None

    sig = {
        "provider": "haseeb1111", "base": base.upper(), "side": side,
        "entry": entry, "tps": tps, "stop_loss": sl, "dca1": dca1
    }

    sig_hash = hashlib.md5(f"{base}{side}{entry}{sl}".encode()).hexdigest()
    if sig_hash in seen_hashes:
        return None
    seen_hashes.add(sig_hash)
    state["seen_hashes"] = list(seen_hashes)[-500:]
    return sig

# ========================= Payload =========================
def build_payload(sig):
    entry, sl, side = sig["entry"], sig["stop_loss"], sig["side"]
    sl_pct = abs((sl / entry - 1) * 100)
    trigger = entry * (1 - ENTRY_TRIGGER_BUFFER_PCT/100 if side == "long" else 1 + ENTRY_TRIGGER_BUFFER_PCT/100)
    expire_price = None
    if ENTRY_EXPIRATION_PRICE_PCT > 0:
        expire_price = entry * (1 + ENTRY_EXPIRATION_PRICE_PCT/100 if side == "long" else 1 - ENTRY_EXPIRATION_PRICE_PCT/100)

    tp_pcts = [(tp / entry - 1) * 100 for tp in sig["tps"]]
    tp_splits = [TP1_PCT, TP2_PCT, TP3_PCT, TP4_PCT, TP5_PCT]
    take_profits = []
    for i, pct in enumerate(tp_pcts):
        split = tp_splits[i] if i < len(tp_splits) else 0
        if split > 0:
            take_profits.append({"price_percentage": round(pct, 6), "position_percentage": split})

    payload = {
        "api_key": ALTRADY_API_KEY, "api_secret": ALTRADY_API_SECRET, "exchange": ALTRADY_EXCHANGE,
        "action": "open", "symbol": f"{ALTRADY_EXCHANGE}_{QUOTE}_{sig['base']}", "side": side,
        "order_type": ORDER_TYPE, "signal_price": entry, "leverage": LEVERAGE,
        "entry_condition": {"price": round(trigger, 10)},
        "take_profit": take_profits,
        "stop_loss": {
            "order_type": STOP_LOSS_ORDER_TYPE,
            "stop_percentage": round(sl_pct, 6),
            "protection_type": STOP_PROTECTION_TYPE
        },
        "dca_orders": [{"price": sig["dca1"], "quantity_percentage": DCA1_QTY_PCT}] if sig["dca1"] else [],
        "entry_expiration": {"time": ENTRY_EXPIRATION_MIN}
    }
    if expire_price: payload["entry_expiration"]["price"] = round(expire_price, 10)
    if ENTRY_WAIT_MINUTES > 0:
        payload["entry_condition"].update({"time": ENTRY_WAIT_MINUTES, "operator": "OR"})
    if TEST_MODE: payload["test"] = True

    print(f"\nNEUES SIGNAL: {sig['base']} {side.upper()} @ {entry}")
    print(f"SL: {sl} ({sl_pct:.2f}%) | TPs: {len(sig['tps'])} | DCA1: {sig['dca1'] or '—'}")
    return payload

# ========================= Main Loop =========================
print("AO Trades → Altrady Bot 2025 – GESTARTET")

while True:
    try:
        msgs = fetch_messages(last_id)
        if not msgs:
            print(f"[{datetime.now():%H:%M:%S}] Warte auf neue Signale...")
        else:
            for msg in sorted(msgs, key=lambda x: x["id"]):
                if int(msg["id"]) <= int(last_id or 0): continue
                text = extract_text(msg)
                sig = parse_signal(text)
                if sig and (time.time() - state.get("last_trade_ts", 0) > COOLDOWN_SECONDS):
                    payload = build_payload(sig)
                    try:
                        requests.post(ALTRADY_WEBHOOK_URL, json=payload, timeout=20)
                        print("Trade an Altrady gesendet!")
                        state["last_trade_ts"] = time.time()
                    except: print("Senden fehlgeschlagen")
                last_id = msg["id"]

        state["last_id"] = last_id
        save_state(state)

    except Exception as e:
        print("Fehler:", e)
        import traceback; traceback.print_exc()

    finally:
        time.sleep(POLL_BASE_SECONDS + random.uniform(0, POLL_JITTER_MAX))
