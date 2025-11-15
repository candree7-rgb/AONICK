#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, sys, time, json, html, random
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

# Webhook #1
ALTRADY_WEBHOOK_URL   = os.getenv("ALTRADY_WEBHOOK_URL", "").strip()
ALTRADY_API_KEY       = os.getenv("ALTRADY_API_KEY", "").strip()
ALTRADY_API_SECRET    = os.getenv("ALTRADY_API_SECRET", "").strip()
ALTRADY_EXCHANGE      = os.getenv("ALTRADY_EXCHANGE", "BYBIF").strip()

# Optionaler Webhook #2
ALTRADY_WEBHOOK_URL_2 = os.getenv("ALTRADY_WEBHOOK_URL_2", "").strip()
ALTRADY_API_KEY_2     = os.getenv("ALTRADY_API_KEY_2", "").strip()
ALTRADY_API_SECRET_2  = os.getenv("ALTRADY_API_SECRET_2", "").strip()
ALTRADY_EXCHANGE_2    = os.getenv("ALTRADY_EXCHANGE_2", "").strip()

QUOTE = os.getenv("QUOTE", "USDT").strip().upper()

# Hebel
FIXED_LEVERAGE = int(os.getenv("FIXED_LEVERAGE", "5"))

# ======= TP-Splits (5 TPs) + Runner =======
TP1_PCT = float(os.getenv("TP1_PCT", "22"))
TP2_PCT = float(os.getenv("TP2_PCT", "20"))
TP3_PCT = float(os.getenv("TP3_PCT", "20"))
TP4_PCT = float(os.getenv("TP4_PCT", "18"))
TP5_PCT = float(os.getenv("TP5_PCT", "10"))
RUNNER_PCT = float(os.getenv("RUNNER_PCT", "10"))

# Trailing für Runner
RUNNER_TRAILING_DIST = float(os.getenv("RUNNER_TRAILING_DIST", "1.5"))
RUNNER_TP_MULTIPLIER = float(os.getenv("RUNNER_TP_MULTIPLIER", "1.5"))
USE_RUNNER_AFTER_TP5 = os.getenv("USE_RUNNER_AFTER_TP5", "true").lower() == "true"

# ======= Stop-Loss Modus (Fallback) =======
STOP_PROTECTION_TYPE   = os.getenv("STOP_PROTECTION_TYPE", "FOLLOW_TAKE_PROFIT").strip().upper()
BASE_STOP_MODE         = os.getenv("BASE_STOP_MODE", "DCA1").strip().upper()
SL_BUFFER_PCT          = float(os.getenv("SL_BUFFER_PCT", "4.0"))
STOP_FIXED_PERCENTAGE  = float(os.getenv("STOP_FIXED_PERCENTAGE", "9.0"))

# ======= Signal-SL Handling =======
RESPECT_SIGNAL_SL         = os.getenv("RESPECT_SIGNAL_SL", "true").lower() == "true"
NO_DCA_IF_SIGNAL_SL       = os.getenv("NO_DCA_IF_SIGNAL_SL", "true").lower() == "true"
ALLOW_INVERTED_SIGNAL_SL  = os.getenv("ALLOW_INVERTED_SIGNAL_SL", "false").lower() == "true"

# DCA Größen
DCA1_QTY_PCT = float(os.getenv("DCA1_QTY_PCT", "150"))
DCA2_QTY_PCT = float(os.getenv("DCA2_QTY_PCT", "0"))
DCA3_QTY_PCT = float(os.getenv("DCA3_QTY_PCT", "0"))

# Fallback DCA-Distanzen
DCA1_DIST_PCT = float(os.getenv("DCA1_DIST_PCT", "5"))
DCA2_DIST_PCT = float(os.getenv("DCA2_DIST_PCT", "10"))
DCA3_DIST_PCT = float(os.getenv("DCA3_DIST_PCT", "20"))

# Limit-Order Ablauf
ENTRY_EXPIRATION_MIN = int(os.getenv("ENTRY_EXPIRATION_MIN", "180"))

# Entry-Condition
ENTRY_WAIT_MINUTES         = int(os.getenv("ENTRY_WAIT_MINUTES", "0"))
ENTRY_TRIGGER_BUFFER_PCT   = float(os.getenv("ENTRY_TRIGGER_BUFFER_PCT", "0.0"))
ENTRY_EXPIRATION_PRICE_PCT = float(os.getenv("ENTRY_EXPIRATION_PRICE_PCT", "0.0"))

TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

# Poll-Steuerung
POLL_BASE_SECONDS   = int(os.getenv("POLL_BASE_SECONDS", "60"))
POLL_OFFSET_SECONDS = int(os.getenv("POLL_OFFSET_SECONDS", "3"))
POLL_JITTER_MAX     = int(os.getenv("POLL_JITTER_MAX", "7"))

DISCORD_FETCH_LIMIT = int(os.getenv("DISCORD_FETCH_LIMIT", "50"))
STATE_FILE          = Path(os.getenv("STATE_FILE", "state.json"))

# Cooldown nach Order-Open
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "0"))

# =========================
# Startup Checks
# =========================
if not DISCORD_TOKEN or not CHANNEL_ID or not ALTRADY_WEBHOOK_URL:
    print("❌ ENV fehlt: DISCORD_TOKEN, CHANNEL_ID, ALTRADY_WEBHOOK_URL")
    sys.exit(1)

if not ALTRADY_API_KEY or not ALTRADY_API_SECRET:
    print("❌ API Keys fehlen: ALTRADY_API_KEY, ALTRADY_API_SECRET")
    sys.exit(1)

def _auth_header(token: str) -> str:
    t = (token or "").strip()
    if t.lower().startswith(("bot ", "bearer ")):
        return t
    scheme = os.getenv("DISCORD_AUTH_SCHEME", "").strip().lower()
    if scheme in ("bot", "bearer"):
        return f"{scheme.title()} {t}"
    return t

HEADERS = {
    "Authorization": _auth_header(DISCORD_TOKEN),
    "User-Agent": "DiscordToAltrady/2.7-AO-Format-Fix"
}

# =========================
# Utils
# =========================
def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except:
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
        r = requests.get(f"https://discord.com/api/v10/channels/{channel_id}/messages",
                         headers=HEADERS, params=params, timeout=15)
        if r.status_code == 429:
            retry = 5
            try:
                if r.headers.get("Content-Type","").startswith("application/json"):
                    retry = float(r.json().get("retry_after", 5))
            except:
                pass
            time.sleep(retry + 0.5)
            continue
        r.raise_for_status()
        page = r.json() or []
        collected.extend(page)
        if len(page) < params["limit"]:
            break
        max_id = max(int(m.get("id","0")) for m in page if "id" in m)
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
    if not s: return ""
    s = s.replace("\r", "")
    s = html.unescape(s)
    s = MD_LINK.sub(r"\1", s)
    s = MD_MARK.sub("", s)
    s = MULTI_WS.sub(" ", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    return s.strip()

def to_price(s: str) -> float:
    s = s.strip()
    if ',' in s and '.' not in s:
        s = s.replace(',', '.')
    else:
        s = s.replace(',', '')
    return float(s)

def message_text(m: dict) -> str:
    parts = []
    parts.append(m.get("content") or "")
    embeds = m.get("embeds") or []
    for e in embeds:
        if not isinstance(e, dict):
            continue
        if e.get("title"): parts.append(str(e.get("title")))
        if e.get("description"): parts.append(str(e.get("description")))
        fields = e.get("fields") or []
        for f in fields:
            if not isinstance(f, dict):
                continue
            n = f.get("name") or ""
            v = f.get("value") or ""
            if n: parts.append(str(n))
            if v: parts.append(str(v))
        footer = (e.get("footer") or {}).get("text")
        if footer: parts.append(str(footer))
    return clean_markdown("\n".join([p for p in parts if p]))

# =========================
# Signal Parsing (FIXED)
# =========================
PAIR_LINE_OLD   = re.compile(r"(^|\n)\s*([A-Z0-9]+)\s+(LONG|SHORT)\s+Signal\s*(\n|$)", re.I)
HDR_SLASH_PAIR  = re.compile(r"([A-Z0-9]+)\s*/\s*[A-Z0-9]+\b.*\b(LONG|SHORT)\b", re.I)
HDR_COIN_DIR    = re.compile(r"Coin\s*:\s*([A-Z0-9]+).*?Direction\s*:\s*(LONG|SHORT)", re.I | re.S)

# ✅ FIX: BUY/SELL Pattern - flexibler
BUY_SELL_PAIR   = re.compile(r"\b(BUY|SELL)\s+([A-Z0-9]+?)(?:USDT|/USDT)\b", re.I)

# ✅ FIX: Entry - alle Varianten (mit/ohne $, mit/ohne :)
ENTRY_DOLLAR    = re.compile(r"\bEntry\s*[:$]?\s*\$?\s*"+NUM, re.I)
ENTER_ON_TRIGGER = re.compile(r"Enter\s+on\s+Trigger\s*:\s*\$?\s*"+NUM, re.I)
ENTRY_COLON      = re.compile(r"\bEntry\s*:\s*\$?\s*"+NUM, re.I)
ENTRY_SECTION    = re.compile(r"\bENTRY\b\s*\n\s*\$?\s*"+NUM, re.I)

# TP-Patterns (bereits korrekt)
TP1_LINE  = re.compile(r"\bTP\s*1\s*:\s*\$?\s*"+NUM, re.I)
TP2_LINE  = re.compile(r"\bTP\s*2\s*:\s*\$?\s*"+NUM, re.I)
TP3_LINE  = re.compile(r"\bTP\s*3\s*:\s*\$?\s*"+NUM, re.I)
TP4_LINE  = re.compile(r"\bTP\s*4\s*:\s*\$?\s*"+NUM, re.I)
TP5_LINE  = re.compile(r"\bTP\s*5\s*:\s*\$?\s*"+NUM, re.I)

# DCA-Patterns
DCA1_LINE = re.compile(r"\bDCA\s*#?\s*1\s*:\s*\$?\s*"+NUM, re.I)
DCA2_LINE = re.compile(r"\bDCA\s*#?\s*2\s*:\s*\$?\s*"+NUM, re.I)
DCA3_LINE = re.compile(r"\bDCA\s*#?\s*3\s*:\s*\$?\s*"+NUM, re.I)

# SL-Pattern
SL_LINE   = re.compile(r"\bSL\s*:\s*\$?\s*"+NUM, re.I)

def find_base_side(txt: str):
    """Extrahiert Coin-Symbol und Long/Short."""
    # 1) BUY/SELL PARTIUSDT → (PARTI, long/short)
    mb = BUY_SELL_PAIR.search(txt)
    if mb:
        side = "long" if mb.group(1).upper() == "BUY" else "short"
        base = mb.group(2).upper()
        # ✅ FIX: Entferne USDT-Suffix falls vorhanden
        if base.endswith("USDT"):
            base = base[:-4]
        return base, side

    # 2) Alt-Header-Varianten
    mh = HDR_SLASH_PAIR.search(txt)
    if mh:
        return mh.group(1).upper(), ("long" if mh.group(2).upper()=="LONG" else "short")
    mo = PAIR_LINE_OLD.search(txt)
    if mo:
        return mo.group(2).upper(), ("long" if mo.group(3).upper()=="LONG" else "short")
    mc = HDR_COIN_DIR.search(txt)
    if mc:
        return mc.group(1).upper(), ("long" if mc.group(2).upper()=="LONG" else "short")
    return None, None

def find_entry(txt: str) -> Optional[float]:
    """Findet Entry-Preis."""
    # Prio 1: Entry $X oder Entry: $X
    m = ENTRY_DOLLAR.search(txt)
    if m:
        return to_price(m.group(1))
    
    # Prio 2: Andere Varianten
    for rx in (ENTER_ON_TRIGGER, ENTRY_COLON, ENTRY_SECTION):
        m = rx.search(txt)
        if m:
            return to_price(m.group(1))
    return None

def _grab_opt(rx, txt):
    """Helper: Optionalen Wert extrahieren."""
    m = rx.search(txt)
    return to_price(m.group(1)) if m else None

def find_tp_dca_sl(txt: str):
    """Extrahiert TPs, DCAs und SL."""
    tps = []
    for rx in (TP1_LINE, TP2_LINE, TP3_LINE, TP4_LINE, TP5_LINE):
        val = _grab_opt(rx, txt)
        tps.append(val)
    
    d1 = _grab_opt(DCA1_LINE, txt)
    d2 = _grab_opt(DCA2_LINE, txt)
    d3 = _grab_opt(DCA3_LINE, txt)
    sl = _grab_opt(SL_LINE, txt)
    
    return tps, [d1, d2, d3], sl

def backfill_dcas_if_missing(side: str, entry: float, dcas: list) -> list:
    """Berechnet fehlende DCAs."""
    d1, d2, d3 = dcas
    if d1 is None:
        d1 = entry * (1 + DCA1_DIST_PCT/100.0) if side=="short" else entry * (1 - DCA1_DIST_PCT/100.0)
    return [d1, d2, d3]

def plausible(side: str, entry: float, tps: list, d1: Optional[float]) -> bool:
    """Prüft ob TPs und DCA1 logisch sind."""
    # TPs prüfen
    for i, tp in enumerate(tps, 1):
        if tp is None:
            continue
        if side == "long" and tp <= entry:
            print(f"[PLAUSIBILITY] ❌ TP{i} {tp} ist <= Entry {entry} bei LONG")
            return False
        if side == "short" and tp >= entry:
            print(f"[PLAUSIBILITY] ❌ TP{i} {tp} ist >= Entry {entry} bei SHORT")
            return False
    
    # DCA1-Check (tolerant)
    if d1 is None:
        return True
    
    if side == "long" and d1 >= entry:
        print(f"[PLAUSIBILITY] ⚠️ DCA1 {d1} ist >= Entry {entry} bei LONG → Ignoriere DCA1")
        return True
    if side == "short" and d1 <= entry:
        print(f"[PLAUSIBILITY] ⚠️ DCA1 {d1} ist <= Entry {entry} bei SHORT → Ignoriere DCA1")
        return True
    
    return True

def _sl_plausible(side: str, entry: float, sl: float) -> bool:
    """Prüft ob SL logisch ist."""
    if sl is None:
        return True
    is_valid = (sl < entry) if side == "long" else (sl > entry)
    if not is_valid:
        print(f"[SL-CHECK] ⚠️ SL {sl} ist auf falscher Seite bei {side.upper()} (Entry {entry})")
    return is_valid

def parse_signal_from_text(txt: str):
    """Hauptfunktion: Parst Signal aus Text."""
    
    # Schritt 1: Coin + Richtung
    base, side = find_base_side(txt)
    if not base or not side:
        print(f"[PARSE] ❌ Kein Base/Side gefunden")
        return None
    
    # Schritt 2: Entry
    entry = find_entry(txt)
    if entry is None:
        print(f"[PARSE] ❌ Kein Entry gefunden für {base} {side.upper()}")
        return None

    # Schritt 3: TPs, DCAs, SL
    tps, dcas, sl_price = find_tp_dca_sl(txt)
    
    # Min. TP1-3 nötig
    if any(tp is None for tp in tps[:3]):
        print(f"[PARSE] ❌ TPs unvollständig (brauche min. TP1-3): {tps[:3]}")
        return None

    # Schritt 4: DCA-Backfill
    d1, d2, d3 = backfill_dcas_if_missing(side, entry, dcas)

    # Schritt 5: Plausibilität
    if not plausible(side, entry, tps, d1):
        print(f"[PARSE] ❌ Plausibilitäts-Check fehlgeschlagen")
        return None

    # Schritt 6: SL-Check
    if sl_price is not None and not _sl_plausible(side, entry, sl_price):
        if not ALLOW_INVERTED_SIGNAL_SL:
            print(f"[PARSE] ⚠️ Invertierter SL ignoriert")
            sl_price = None

    # ✅ Signal OK
    signal = {
        "base": base, 
        "side": side, 
        "entry": entry,
        "tp1": tps[0], "tp2": tps[1], "tp3": tps[2], 
        "tp4": tps[3], "tp5": tps[4],
        "dca1": d1, 
        "dca2": None,
        "dca3": None,
        "sl_price": sl_price
    }
    
    print(f"[PARSE] ✅ Signal: {base} {side.upper()} @ {entry}")
    print(f"        TPs: {tps[0]}/{tps[1]}/{tps[2]}/{tps[3] or '-'}/{tps[4] or '-'}")
    print(f"        DCA1: {d1:.6f}, SL: {sl_price if sl_price else 'Fallback'}")
    
    return signal

# =========================
# Altrady Payload
# =========================
def _percent_from_entry(entry: float, target: float) -> float:
    return (target / entry - 1.0) * 100.0

def _compute_stop_percentage(entry: float, d1: Optional[float], d2: Optional[float]) -> float:
    mode = BASE_STOP_MODE
    if mode == "FIXED":
        return float(STOP_FIXED_PERCENTAGE)
    anchor_price = d1 if d1 is not None else entry
    if mode == "DCA2" and d2 is not None:
        anchor_price = d2
    anchor_dist = abs((anchor_price - entry) / entry) * 100.0
    return anchor_dist + SL_BUFFER_PCT

def build_altrady_open_payload(sig: dict, exchange: str, api_key: str, api_secret: str) -> dict:
    base, side, entry = sig["base"], sig["side"], sig["entry"]
    tp1, tp2, tp3, tp4, tp5 = sig["tp1"], sig["tp2"], sig["tp3"], sig["tp4"], sig["tp5"]
    d1, d2, d3 = sig["dca1"], sig["dca2"], sig["dca3"]
    sl_price = sig.get("sl_price")

    symbol = f"{exchange}_{QUOTE}_{base}"

    # Stop-Loss
    if RESPECT_SIGNAL_SL and sl_price is not None:
        stop_percentage = abs((sl_price - entry) / entry) * 100.0
    else:
        stop_percentage = _compute_stop_percentage(entry, d1, d2)

    # Entry-Trigger
    if side == "long":
        trigger_price = entry * (1.0 - ENTRY_TRIGGER_BUFFER_PCT/100.0)
        expire_price  = entry * (1.0 - ENTRY_EXPIRATION_PRICE_PCT/100.0) if ENTRY_EXPIRATION_PRICE_PCT > 0 else None
    else:
        trigger_price = entry * (1.0 + ENTRY_TRIGGER_BUFFER_PCT/100.0)
        expire_price  = entry * (1.0 + ENTRY_EXPIRATION_PRICE_PCT/100.0) if ENTRY_EXPIRATION_PRICE_PCT > 0 else None

    # TPs
    def pct_or_none(tp):
        return _percent_from_entry(entry, tp) if tp is not None else None

    tp_pct = [pct_or_none(tp) for tp in (tp1, tp2, tp3, tp4, tp5)]
    tp_split = [TP1_PCT, TP2_PCT, TP3_PCT, TP4_PCT, TP5_PCT]

    take_profits = []
    for pct, split in zip(tp_pct, tp_split):
        if pct is not None and split > 0:
            take_profits.append({"price_percentage": float(f"{pct:.6f}"), "position_percentage": split})

    # Runner
    runner_pct = None
    if USE_RUNNER_AFTER_TP5 and RUNNER_PCT > 0:
        anchor = tp5 if tp5 is not None else tp3
        if anchor is not None:
            runner_price = anchor * RUNNER_TP_MULTIPLIER if side == "long" else anchor / RUNNER_TP_MULTIPLIER
            runner_pct = _percent_from_entry(entry, runner_price)
            take_profits.append({
                "price_percentage": float(f"{runner_pct:.6f}"),
                "position_percentage": RUNNER_PCT,
                "trailing_distance": RUNNER_TRAILING_DIST
            })

    # DCAs
    dca_orders = []
    use_dcas = not (RESPECT_SIGNAL_SL and sl_price is not None and NO_DCA_IF_SIGNAL_SL)
    if use_dcas and DCA1_QTY_PCT > 0 and d1 is not None:
        # ✅ FIX: Prüfe nochmal ob DCA1 plausibel ist
        dca_valid = (d1 < entry) if side == "long" else (d1 > entry)
        if dca_valid:
            dca_orders.append({"price": d1, "quantity_percentage": DCA1_QTY_PCT})

    payload = {
        "api_key": api_key,
        "api_secret": api_secret,
        "exchange": exchange,
        "action": "open",
        "symbol": symbol,
        "side": side,
        "order_type": "limit",
        "signal_price": entry,
        "leverage": FIXED_LEVERAGE,
        "entry_condition": { "price": float(f"{trigger_price:.10f}") },
        "take_profit": take_profits,
        "stop_loss": {
            "stop_percentage": float(f"{stop_percentage:.6f}"),
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
    print(f"\n📊 {base} {side.upper()}  |  {symbol}  |  Entry {entry}")
    print(f"   Trigger @ {trigger_price:.6f}  |  Expire in {ENTRY_EXPIRATION_MIN} min" + (f" oder Preis {expire_price:.6f}" if expire_price else ""))
    print(f"   SL-Modus: {'SignalSL' if (RESPECT_SIGNAL_SL and sl_price is not None) else BASE_STOP_MODE}  → {stop_percentage:.2f}%")
    if runner_pct is not None:
        print(f"   Runner% ≈ {runner_pct:.6f}  |  Trail {RUNNER_TRAILING_DIST:.2f}%")
    if dca_orders:
        dca_str = ", ".join([f"{o['quantity_percentage']}%@{o['price']:.6f}" for o in dca_orders])
    else:
        dca_str = "–"
    print("   DCAs: " + dca_str)
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
                    if r.headers.get("Content-Type","").startswith("application/json"):
                        delay = float(r.json().get("retry_after", 2.0))
                except:
                    pass
                time.sleep(delay + 0.25)
                continue

            if r.status_code == 204:
                print("   ✅ Erfolg! Pending order angelegt.")
                return r

            r.raise_for_status()
            print("   ✅ Erfolg!")
            return r
        except Exception as e:
            if attempt == 2:
                print(f"   ❌ Fehler: {e}")
                raise
            time.sleep(1.5 * (attempt + 1))

def post_to_all_webhooks(payloads_and_urls: List[Tuple[str, dict]]):
    last_resp = None
    for i, (url, payload) in enumerate(payloads_and_urls, 1):
        print(f"→ Webhook #{i} von {len(payloads_and_urls)}")
        try:
       
