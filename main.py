#!/usr/bin/env python3
import os, re, json, time, requests
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

HEADERS = {"Authorization": DISCORD_TOKEN}

def fetch_last_10():
    url = f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages?limit=10"
    r = requests.get(url, headers=HEADERS)
    r.raise_for_status()
    return r.json()

def clean(txt):
    return re.sub(r"[<@&!>]|\*\*|__|\*\*|```|`|_|\*", "", txt).strip()

print("Lese letzte 10 Nachrichten aus deinem Channel...\n")
msgs = fetch_last_10()

for m in msgs:
    content = m.get("content", "")
    author = m["author"]["username"]
    text = content
    for emb in m.get("embeds", []):
        text += " | " + (emb.get("title") or "") + " | " + (emb.get("description") or "")
        for f in emb.get("fields", []):
            text += " | " + f.get("name","") + " | " + f.get("value","")
    text = clean(text)
    
    print(f"[{m['id']}] {author}: {text[:200]}{'...' if len(text)>200 else ''}")
    
    if "NEW SIGNAL" in text.upper() or "NEW TRADE SIGNAL" in text.upper():
        print("NEUES SIGNAL GEFUNDEN!")
        print(f"→ Ganzer Text:\n{text}\n")
        break
else:
    print("\nKEIN 'NEW SIGNAL' in den letzten 10 Nachrichten gefunden.")
    print("Mögliche Gründe:")
    print("   • Falscher CHANNEL_ID")
    print("   • Bot hat keine Rechte im Channel")
    print("   • Du bist nicht im richtigen Server")
    print("   • AO Trades APP postet in einen anderen Channel")
