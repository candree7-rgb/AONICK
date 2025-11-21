#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, sys, time, json, traceback, html, random
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple
import requests
from dotenv import load_dotenv

load_dotenv()

# =========================
# ENVs
# =========================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
CHANNEL_ID    = os.getenv("CHANNEL_ID", "").strip()

ALTRADY_WEBHOOK_URL = os.getenv("ALTRADY_WEBHOOK_URL", "").strip()
ALTRADY_API_KEY     = os.getenv("ALTRADY_API_KEY", "").strip()
ALTRADY_API_SECRET  = os.getenv("ALTRADY_API_SECRET", "").strip()
ALTRADY_EXCHANGE    = os.getenv("ALTRADY_EXCHANGE", "BYBI").strip()

QUOTE = os.getenv("QUOTE", "USDT").strip().upper()

# Fester Hebel für alle Trades
LEVERAGE = int(os.getenv("LEVERAGE", "10"))

# TP-Splits (bis zu 5 TPs, Summe idealerweise 100)
TP1_PCT = float(os.getenv("TP1_PCT", "20"))
TP2_PCT = float(os.getenv("TP2_PCT", "20"))
TP3_PCT = float(os.getenv("TP3_PCT", "20"))
TP4_PCT = float(os.getenv("TP4_PCT", "20"))
TP5_PCT = float(os.getenv("TP5_PCT", "20"))

# DCA-Grösse (nur DCA1 wird verwendet)
DCA1_QTY_PCT = float(os.getenv("DCA1_QTY_PCT", "150"))

# Stop-Protektion (z.B. FOLLOW_TAKE_PROFIT / DISABLE / TRAILING etc. laut Altrady)
STOP_PROTECTION_TYPE = os.getenv("STOP_PROTECTION_TYPE", "FOLLOW_TAKE_PROFIT").strip().upper()

# Limit-Order Ablauf (Zeit)
ENTRY_EXPIRATION_MIN = int(os.getenv("ENTRY_EXPIRATION_MIN", "180"))

# Entry-Condition / Expiration-Price in Gewinnrichtung (z.B. 0.8% in TP-Richtung)
ENTRY_WAIT_MINUTES         = int(os.getenv("ENTRY_WAIT_MINUTES", "0"))
ENTRY_TRIGGER_BUFFER_PCT   = float(os.getenv("ENTRY_TRIGGER_BUFFER_PCT", "0.0"))
ENTRY_EXPIRATION_PRICE_PCT = float(os.getenv("ENTRY_EXPIRATION_PRICE_PCT", "0.0"))

TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

# Poll-Steuerung
POLL_BASE_SECONDS   = int(os.getenv("POLL_BASE_SECONDS", "60"))
POLL_OFFSET_SECONDS = int(os.getenv("POLL_OFFSET_SECONDS", "3"))
POLL_JITTER_MAX     = int(os.getenv("POLL_JITTER_MAX", "7"))

DISCORD_FETCH_LIMIT = int(os.getenv("DISCORD_FETCH_LIMIT", "50"))
STATE_FILE          = Path(os.getenv("STATE_FILE", "state_ao.json"))

COOLDOWN_SECONDS    = int(os.getenv("COOLDOWN_SECONDS", "0"))

# Erlaubte Signal-Provider (z.B. "haseeb1111,andereruser")
ALLOWED_PROVIDERS = {
    s.strip().lower()
    for s in os.getenv("ALLOWED_PROVIDERS", "").split(",")
    if s.strip()
}

# =========================
# Startup Checks
# =========================
if not DISCORD_TOKEN or not CHANNEL_ID or not ALTRADY_WEBHOOK_URL:
    print("❌ ENV fehlt: DISCORD_TOKEN, CHANNEL_ID, ALTRADY_WEBHOOK_URL")
    sys.exit(1)

if not ALTRADY_API_KEY or not ALTRADY_API_SECRET:
    print("❌ API Keys fehlen: ALTRADY_API_KEY, ALTRADY_API_SECRET")
    sys.exit(1)

HEADERS = {
    "Authorization": DISCORD_TOKEN,
    "User-Agent": "DiscordToAltrady-AO/1.0"
}

# =========================
# Utils
# =========================
def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_id": None, "last_trade_ts": 0.0}

def save_state(st: dict):
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st), encoding="utf-8")
    tmp.replace(STATE_FILE)

def sleep_until_next_tick():
    now = time.time()
    period_start = (now // POLL_BASE_SECONDS) * POLL_BASE_SECONDS
    next_tick = period_start + POLL_BASE_SECONDS + POLL_OFFSET_SECONDS
    if now < period_start + POLL_OFFSET_SECONDS:
        next_tick = period_start + POLL_OFFSET_SECONDS
    jitter = random.uniform(0, max(0, POLL_JITTER_MAX))
    time.sleep(max(0, next_tick - now + jitter))

def fetch_messages_after(channel_id: str, after_id: Optional[str], limit: int = 50):
    collected = []
    params = {"limit": max(1, min(limit, 100))}
    if after_id:
        params["after"] = str(after_id)

    while True:
        r = requests.get(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers=HEADERS, params=params, timeout=15
        )
        if r.status_code == 429:
            retry = 5
            try:
                if r.headers.get("Content-Type", "").startswith("application/json"):
                    retry = float(r.json().get("retry_after", 5))
            except Exception:
                pass
            time.sleep(retry + 0.5)
            continue
        r.raise_for_status()
        page = r.json() or []
        collected.extend(page)
        if len(page) < params["limit"]:
            break
        max_id = max(int(m.get("id", "0")) for m in page if "id" in m)
        params["after"] = str(max_id)
    return collected

# =========================
# Text Processing
# =========================
MD_LINK   = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")
MD_MARK   = re.compile(r"[*_`~]+")
MULTI_WS  = re.compile(r"[ \t\u00A0]+")
NUM       = r"([0-9][0-9,]*\.?[0-9]*)"

def clean_markdown(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\r", "")
    s = html.unescape(s)
    s = MD_LINK.sub(r"\1", s)
    s = MD_MARK.sub("", s)
    s = MULTI_WS.sub(" ", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    return s.strip()

def to_price(s: str) -> float:
    return float(s.replace(",", ""))

def message_text(m: dict) -> str:
    parts = []
    parts.append(m.get("content") or "")
    embeds = m.get("embeds") or []
    for e in embeds:
        if not isinstance(e, dict):
            continue
        if e.get("title"):
            parts.append(str(e.get("title")))
        if e.get("description"):
            parts.append(str(e.get("description")))
        fields = e.get("fields") or []
        for f in fields:
            if not isinstance(f, dict):
                continue
            n = f.get("name") or ""
            v = f.get("value") or ""
            if n:
                parts.append(str(n))
            if v:
                parts.append(str(v))
        footer = (e.get("footer") or {}).get("text")
        if footer:
            parts.append(str(footer))
    return clean_markdown("\n".join([p for p in parts if p]))

# =========================
# AO Signal Parsing
# =========================

SIDE_PAIR_LINE = re.compile(
    r"(LONG|SHORT)\s+SIGNAL\s*-\s*([A-Z0-9]+)\s*/\s*([A-Z0-9]+)",
    re.I
)

ENTRY_LINE = re.compile(r"\bEntry\s*:\s*\$?\s*" + NUM, re.I)
TP_LINE    = re.compile(r"\bTP\s*(\d)\s*:\s*\$?\s*" + NUM, re.I)
DCA1_LINE  = re.compile(r"\bDCA1\s*:\s*\$?\s*" + NUM, re.I)
SL_LINE    = re.compile(r"Stop\s*Loss\s*:\s*\$?\s*" + NUM, re.I)

SIGNAL_BY_LINE = re.compile(r"Signal\s+by\s+([A-Za-z0-9_.]+)", re.I)
TRADER_LINE    = re.compile(r"Trader\s*:\s*([A-Za-z0-9_.]+)", re.I)

def parse_ao_signal(txt: str) -> Optional[dict]:
    """
    Erwartet die AO-Struktur wie aus deinen Beispielen.
    Gibt None zurück, wenn:
      - kein AO-Signal erkannt, oder
      - Provider nicht in ALLOWED_PROVIDERS
    """
    # Provider ermitteln
    provider = None
    m = SIGNAL_BY_LINE.search(txt)
    if m:
        provider = m.group(1).strip()
    else:
        m = TRADER_LINE.search(txt)
        if m:
            provider = m.group(1).strip()

    if provider:
        provider_l = provider.lower()
        if ALLOWED_PROVIDERS and provider_l not in ALLOWED_PROVIDERS:
            return None
    else:
        # Wenn Provider nicht gefunden wird, lieber nichts machen
        return None

    # Side + Pair
    m = SIDE_PAIR_LINE.search(txt)
    if not m:
        return None
    side_raw, base, quote = m.groups()
    side = "long" if side_raw.upper() == "LONG" else "short"
    base = base.upper()
    # quote wird nicht zwingend gebraucht, wir gehen von QUOTE-ENV aus

    # Entry
    m = ENTRY_LINE.search(txt)
    if not m:
        return None
    entry = to_price(m.group(1))

    # TPs (1–5, in Reihenfolge)
    tps = [None] * 5
    for m in TP_LINE.finditer(txt):
        idx = int(m.group(1))  # 1..5
        if 1 <= idx <= 5:
            tps[idx - 1] = to_price(m.group(2))

    # Mindestens TP1 muss existieren
    if tps[0] is None:
        return None

    # DCA1 (optional)
    m = DCA1_LINE.search(txt)
    dca1 = to_price(m.group(1)) if m else None

    # Stop Loss
    m = SL_LINE.search(txt)
    if not m:
        return None
    stop_loss = to_price(m.group(1))

    return {
        "provider": provider,
        "base": base,
        "side": side,
        "entry": entry,
        "tps": [tp for tp in tps if tp is not None],
        "dca1": dca1,
        "stop_loss": stop_loss,
    }

# =========================
# Altrady Payload
# =========================

def _percent_from_entry(entry: float, target: float) -> float:
    """Preis -> Prozent relativ zum Entry; >0 über Entry, <0 unter Entry."""
    return (target / entry - 1.0) * 100.0

def build_altrady_open_payload_ao(sig: dict) -> dict:
    base       = sig["base"]
    side       = sig["side"]
    entry      = sig["entry"]
    tps        = sig["tps"]      # Liste von 1–5 Preisen
    dca1       = sig["dca1"]     # oder None
    stop_price = sig["stop_loss"]

    symbol = f"{ALTRADY_EXCHANGE}_{QUOTE}_{base}"

    # Stop-Loss als Prozent-Distanz vom Entry (absolut)
    sl_pct = abs(_percent_from_entry(entry, stop_price))

    # Entry-Trigger bleibt preis-basiert
    if side == "long":
        trigger_price = entry * (1.0 - ENTRY_TRIGGER_BUFFER_PCT / 100.0)
        expire_price  = (
            entry * (1.0 + ENTRY_EXPIRATION_PRICE_PCT / 100.0)
            if ENTRY_EXPIRATION_PRICE_PCT > 0 else None
        )
    else:
        trigger_price = entry * (1.0 + ENTRY_TRIGGER_BUFFER_PCT / 100.0)
        expire_price  = (
            entry * (1.0 - ENTRY_EXPIRATION_PRICE_PCT / 100.0)
            if ENTRY_EXPIRATION_PRICE_PCT > 0 else None
        )

    # Take Profits als Prozent von Entry
    tp_pcts = [_percent_from_entry(entry, tp) for tp in tps]

    tp_split = [TP1_PCT, TP2_PCT, TP3_PCT, TP4_PCT, TP5_PCT]
    take_profits = []
    for i, pct in enumerate(tp_pcts):
        pos_pct = tp_split[i] if i < len(tp_split) else 0.0
        if pos_pct <= 0:
            continue
        take_profits.append({
            "price_percentage": float(f"{pct:.6f}"),
            "position_percentage": pos_pct
        })

    # DCA1, falls vorhanden
    dca_orders = []
    if dca1 is not None and DCA1_QTY_PCT > 0:
        dca_orders.append({
            "price": dca1,
            "quantity_percentage": DCA1_QTY_PCT
        })

    payload = {
        "api_key": ALTRADY_API_KEY,
        "api_secret": ALTRADY_API_SECRET,
        "exchange": ALTRADY_EXCHANGE,
        "action": "open",
        "symbol": symbol,
        "side": side,
        "order_type": "limit",
        "signal_price": entry,
        "leverage": LEVERAGE,
        "entry_condition": { "price": float(f"{trigger_price:.10f}") },
        "take_profit": take_profits,
        "stop_loss": {
            "stop_percentage": float(f"{sl_pct:.6f}"),
            "protection_type": STOP_PROTECTION_TYPE
        },
        "dca_orders": dca_orders,
        "entry_expiration": { "time": ENTRY_EXPIRATION_MIN }
    }

    if expire_price is not None:
        payload["entry_expiration"]["price"] = float(f"{expire_price:.10f}")

    if ENTRY_WAIT_MINUTES > 0:
        payload["entry_condition"]["time"] = ENTRY_WAIT_MINUTES
        payload["entry_condition"]["operator"] = "OR"

    if TEST_MODE:
        payload["test"] = True

    # Log
    print(f"\n📊 AO {base} {side.upper()} | {symbol} | Entry {entry}")
    print(
        f"   Trigger @ {trigger_price:.6f} | Expire in {ENTRY_EXPIRATION_MIN} min"
        + (f" oder Preis {expire_price:.6f}" if expire_price else "")
    )
    print(f"   SL: {stop_price} → Distanz {sl_pct:.2f}%")
    print("   TPs:",
          ", ".join([f"{tp:.6f}" for tp in tps]))
    if dca_orders:
        print("   DCA1:",
              ", ".join([f"{o['quantity_percentage']}%@{o['price']:.6f}" for o in dca_orders]))
    else:
        print("   DCA: -")
    return payload

# =========================
# HTTP
# =========================

def _post_one(url: str, payload: dict):
    print(f"   📤 Sende an {url} ...")
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, timeout=20)
            if r.status_code == 429:
                delay = 2.0
                try:
                    if r.headers.get("Content-Type", "").startswith("application/json"):
                        delay = float(r.json().get("retry_after", 2.0))
                except Exception:
                    pass
                time.sleep(delay + 0.25)
                continue

            if r.status_code == 204:
                print("   ✅ Erfolg! Pending order angelegt (wartet auf Trigger).")
                return r

            r.raise_for_status()
            print("   ✅ Erfolg!")
            return r
        except Exception as e:
            if attempt == 2:
                print(f"   ❌ Fehler bei {url}: {e}")
                raise
            time.sleep(1.5 * (attempt + 1))

# =========================
# Main
# =========================

def main():
    print("=" * 50)
    print("🚀 Discord → Altrady AO Bot")
    print("=" * 50)
    print(f"Exchange: {ALTRADY_EXCHANGE} | Leverage: {LEVERAGE}x")
    print(f"TP-Splits: {TP1_PCT}/{TP2_PCT}/{TP3_PCT}/{TP4_PCT}/{TP5_PCT}%")
    print(f"DCA1: {DCA1_QTY_PCT}%")
    print(
        f"Entry: Buffer {ENTRY_TRIGGER_BUFFER_PCT}% | Expire {ENTRY_EXPIRATION_MIN} min"
        + (f" + Expire-Price ±{ENTRY_EXPIRATION_PRICE_PCT}% (Gewinnrichtung)"
           if ENTRY_EXPIRATION_PRICE_PCT > 0 else "")
    )
    if ALLOWED_PROVIDERS:
        print("Erlaubte Provider:",
              ", ".join(sorted(ALLOWED_PROVIDERS)))
    else:
        print("⚠️ Keine ALLOWED_PROVIDERS gesetzt – es wird nichts getradet.")
    if COOLDOWN_SECONDS > 0:
        print(f"Cooldown: {COOLDOWN_SECONDS}s")
    if TEST_MODE:
        print("⚠️ TEST MODE aktiv (Orders werden nicht live ausgeführt, je nach Altrady-Einstellung)")
    print("-" * 50)

    state = load_state()
    last_id = state.get("last_id")
    last_trade_ts = float(state.get("last_trade_ts", 0.0))

    # Erststart: baseline auf aktuellste Message setzen (nicht rückwirkend)
    if last_id is None:
        try:
            page = fetch_messages_after(CHANNEL_ID, None, limit=1)
            if page:
                last_id = str(page[0]["id"])
                state["last_id"] = last_id
                save_state(state)
        except Exception:
            pass

    print("👀 Überwache Channel...\n")

    while True:
        try:
            msgs = fetch_messages_after(CHANNEL_ID, last_id, limit=DISCORD_FETCH_LIMIT)
            msgs_sorted = sorted(msgs, key=lambda m: int(m.get("id", "0")))
            max_seen = int(last_id or 0)

            if not msgs_sorted:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Warte auf Signale...")
            else:
                for m in msgs_sorted:
                    mid = int(m.get("id", "0"))
                    raw = message_text(m)

                    if COOLDOWN_SECONDS > 0 and (time.time() - last_trade_ts) < COOLDOWN_SECONDS:
                        max_seen = max(max_seen, mid)
                        continue

                    if raw:
                        sig = parse_ao_signal(raw)
                        if sig:
                            print(f"\n➡️ Erkanntes AO-Signal von {sig['provider']}")
                            payload = build_altrady_open_payload_ao(sig)
                            _post_one(ALTRADY_WEBHOOK_URL, payload)
                            last_trade_ts = time.time()
                            state["last_trade_ts"] = last_trade_ts

                    max_seen = max(max_seen, mid)

                last_id = str(max_seen)
                state["last_id"] = last_id
                save_state(state)

        except KeyboardInterrupt:
            print("\n👋 Beendet")
            break
        except Exception as e:
            print(f"❌ Fehler: {e}")
            traceback.print_exc()
            time.sleep(10)
        finally:
            sleep_until_next_tick()

if __name__ == "__main__":
    main()
