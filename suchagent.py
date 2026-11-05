#!/usr/bin/env python3
"""
Suchagent für terraristik.com-Kleinanzeigen.

Läuft alle paar Minuten über GitHub Actions:
  1. liest neue Telegram-Befehle (/add, /del, /liste ...) und passt die Stichworte an
  2. lädt die neuesten Anzeigen von terraristik.com
  3. schickt bei jedem Treffer eine Telegram-Nachricht

Der Stand (bereits gesehene Anzeigen, Telegram-Besitzer, Pause ...) liegt in state.json,
die Stichworte in keywords.txt. Beide Dateien speichert der Workflow nach jedem Lauf.
"""

import html
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

BASE = "https://www.terraristik.com"
LIST_URL = BASE + "/tb/kaufen-und-verkaufen/terraristik-anzeigen/01/"
SEARCH_URL = LIST_URL + "?ftsearch={q}"
PAGES = 2                      # 2 Seiten = die neuesten 40 Anzeigen pro Lauf
MAX_SEEN = 1500                # so viele Anzeigen-Nummern merken wir uns
FAIL_ALERT_AFTER = 12          # nach so vielen Fehlversuchen in Folge (ca. 1 Stunde): Warnung schicken

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / "state.json"
KEYWORDS_FILE = HERE / "keywords.txt"

AD_LINK_RE = re.compile(r"/tb/kaufen-und-verkaufen/[^/]+/a(\d+)/?")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; privater-Suchagent/1.0)",
    "Accept-Language": "de-AT,de;q=0.9,en;q=0.5",
}

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TG = f"https://api.telegram.org/bot{TOKEN}"


# --------------------------------------------------------------------------- Hilfen

def log(*a):
    print(*a, flush=True)


def normalize(text: str) -> str:
    """Kleinbuchstaben, Umlaute/Akzente vereinheitlichen, Leerraum zusammenfassen.
    'Königspython' und 'Koenigspython' werden dadurch gleich."""
    t = text.lower().replace("ß", "ss")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("ae", "a").replace("oe", "o").replace("ue", "u")
    t = re.sub(r"[^a-z0-9.\-]+", " ", t)
    return " " + re.sub(r"\s+", " ", t).strip() + " "


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log("state.json beschädigt – starte neu")
    return {}


def save_state(state: dict) -> None:
    state["seen"] = state.get("seen", [])[-MAX_SEEN:]
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def load_keywords() -> list[str]:
    if not KEYWORDS_FILE.exists():
        return []
    kws = []
    for line in KEYWORDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            kws.append(line)
    return kws


KEYWORDS_HEADER = """# Suchbegriffe für den Suchagent – ein Begriff pro Zeile.
# Groß-/Kleinschreibung und Umlaute sind egal.
# Mehrere Wörter in einer Zeile = alle müssen in der Anzeige vorkommen (Reihenfolge egal).
# Ein Wort mit Minus davor schließt aus, z. B.:  pumilio -suche
# Mit | reicht eine von mehreren Varianten, z. B.:  Clemmys|Tropfenschildkröte adult|geschlechtsreif
# Zeilen mit # am Anfang werden ignoriert.
# Du kannst die Liste hier bearbeiten oder per Telegram (/add, /del).
"""


def save_keywords(kws: list[str]) -> None:
    KEYWORDS_FILE.write_text(KEYWORDS_HEADER + "\n" + "\n".join(kws) + ("\n" if kws else ""),
                             encoding="utf-8")


def _alternatives(word: str) -> list[str]:
    """'adult|geschlechtsreif' -> ['adult', 'geschlechtsreif'] (normalisiert, ohne Leere)."""
    return [a for a in (normalize(x).strip() for x in word.split("|")) if a]


def keyword_words(keyword: str) -> list[str]:
    """Teilt einen Suchbegriff in Wörter. Leerzeichen um | werden ignoriert,
    'adult | geschlechtsreif' ist also dasselbe wie 'adult|geschlechtsreif'."""
    return re.sub(r"\s*\|\s*", "|", keyword.strip()).split()


def keyword_matches(keyword: str, norm_text: str) -> bool:
    """Alle Wörter müssen vorkommen. Bei 'a|b|c' reicht eines davon.
    Wörter mit Minus davor (auch '-a|b') dürfen nicht vorkommen."""
    must, must_not = [], []
    for word in keyword_words(keyword):
        if word.startswith("-") and len(word) > 1:
            must_not += _alternatives(word[1:])
        else:
            alts = _alternatives(word)
            if alts:
                must.append(alts)
    if not must:
        return False
    return (all(any(a in norm_text for a in alts) for alts in must)
            and not any(w in norm_text for w in must_not))


# --------------------------------------------------------------------------- Telegram

def tg(method: str, **params):
    if not TOKEN:
        return None
    try:
        r = requests.post(f"{TG}/{method}", json=params, timeout=30)
        data = r.json()
        if not data.get("ok"):
            log("Telegram-Fehler:", method, data)
        return data
    except Exception as e:  # noqa: BLE001
        log("Telegram nicht erreichbar:", e)
        return None


def send(chat_id, text: str) -> None:
    # Telegram erlaubt max. 4096 Zeichen pro Nachricht
    while text:
        chunk, text = text[:4000], text[4000:]
        tg("sendMessage", chat_id=chat_id, text=chunk, parse_mode="HTML",
           disable_web_page_preview=False)
        time.sleep(0.4)


HELP = (
    "<b>Befehle</b>\n"
    "/add Begriff – Suchbegriff hinzufügen (mehrere mit Komma trennen)\n"
    "/del Begriff – Suchbegriff entfernen (oder Nummer aus /liste)\n"
    "/liste – alle Suchbegriffe anzeigen\n"
    "/suche Begriff – jetzt online nach einem Begriff suchen (ohne zu speichern)\n"
    "/pause – Benachrichtigungen anhalten\n"
    "/weiter – Benachrichtigungen fortsetzen\n"
    "/status – Stand des Agenten\n"
    "/hilfe – diese Übersicht\n\n"
    "Tipps: Mehrere Wörter in einem Begriff müssen alle vorkommen, z. B. "
    "<code>/add oophaga pumilio</code>. Mit Minus schließt du Wörter aus: "
    "<code>/add pumilio -suche</code>.\n"
    "Mit | reicht eine von mehreren Varianten: "
    "<code>/add Clemmys|Tropfenschildkröte adult|geschlechtsreif</code> meldet Anzeigen mit "
    "(Clemmys oder Tropfenschildkröte) und (adult oder geschlechtsreif).\n"
    "Befehle werden bei der nächsten Prüfung verarbeitet (meist innerhalb von 5 Minuten)."
)


def format_list(kws: list[str]) -> str:
    if not kws:
        return "Noch keine Suchbegriffe. Füge welche mit /add hinzu."
    lines = [f"{i}. {html.escape(k)}" for i, k in enumerate(kws, 1)]
    return "<b>Deine Suchbegriffe:</b>\n" + "\n".join(lines)


def site_queries(query: str) -> list[str]:
    """Was an die Volltextsuche der Seite geschickt wird.
    Ohne | wie bisher der ganze Begriff. Mit | wird nach jeder Variante des ersten
    Wortes einzeln gesucht ('Clemmys|Tropfenschildkröte adult' -> 'Clemmys',
    'Tropfenschildkröte'); den Rest prüft danach keyword_matches."""
    if "|" not in query:
        return [query]
    for word in keyword_words(query):
        if not word.startswith("-"):
            return [a for a in word.split("|") if a.strip()][:6]
    return []


def search_now(query: str, limit: int = 5) -> str:
    """Nutzt die Volltextsuche der Seite, um aktuell vorhandene Anzeigen zu zeigen."""
    ads, ids = [], set()
    try:
        for i, q in enumerate(site_queries(query)):
            if i:
                time.sleep(1)  # höflich zur Seite sein
            for ad in parse_listing(fetch(SEARCH_URL.format(q=quote_plus(q)))):
                if ad["id"] not in ids:
                    ids.add(ad["id"])
                    ads.append(ad)
    except Exception as e:  # noqa: BLE001
        return f"(Online-Suche gerade nicht möglich: {html.escape(str(e))})"
    ads.sort(key=lambda a: int(a["id"]), reverse=True)  # neueste zuerst
    hits = [a for a in ads if keyword_matches(query, a["norm"])]
    if not hits:
        return f"Aktuell keine Anzeigen zu „{html.escape(query)}“ online."
    out = [f"<b>Aktuell online zu „{html.escape(query)}“</b> (neueste {min(limit, len(hits))}):"]
    for a in hits[:limit]:
        out.append(f"• <a href=\"{a['url']}\">{html.escape(a['title'])}</a>"
                   + (f" – {html.escape(a['age'])}" if a["age"] else ""))
    return "\n".join(out)


def handle_updates(state: dict, kws: list[str]) -> list[str]:
    """Verarbeitet neue Telegram-Nachrichten. Gibt die (evtl. geänderten) Stichworte zurück."""
    if not TOKEN:
        log("Kein TELEGRAM_TOKEN gesetzt – Telegram übersprungen")
        return kws
    offset = state.get("tg_offset", 0)
    data = tg("getUpdates", offset=offset, timeout=0, allowed_updates=["message"])
    if not data or not data.get("ok"):
        return kws

    for upd in data.get("result", []):
        state["tg_offset"] = upd["update_id"] + 1
        msg = upd.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if not chat_id or not text:
            continue

        owner = state.get("owner")
        if owner is None:
            # Wer dem Bot zuerst schreibt, wird Besitzer – danach reagiert er nur noch auf diese Person.
            state["owner"] = owner = chat_id
            send(chat_id, "👋 <b>Suchagent verbunden!</b>\nAb jetzt bekommst du hier eine Nachricht, "
                          "sobald eine neue Anzeige zu deinen Suchbegriffen erscheint.\n\n" + HELP)
            if not text.startswith("/") or text.split()[0].lower() in ("/start", "/hilfe", "/help"):
                continue
        if chat_id != owner:
            log("Nachricht von fremdem Chat ignoriert:", chat_id)
            continue

        cmd, _, arg = text.partition(" ")
        cmd = cmd.lower().split("@")[0]
        arg = arg.strip()

        if cmd in ("/start", "/hilfe", "/help"):
            send(chat_id, HELP)

        elif cmd == "/add":
            if not arg:
                send(chat_id, "Bitte so: <code>/add Uroplatus</code>")
                continue
            added, existing = [], []
            for item in [x.strip() for x in arg.split(",") if x.strip()]:
                if any(normalize(item) == normalize(k) for k in kws):
                    existing.append(item)
                else:
                    kws.append(item)
                    added.append(item)
            reply = []
            if added:
                reply.append("✅ Hinzugefügt: " + ", ".join(html.escape(a) for a in added))
            if existing:
                reply.append("ℹ️ Schon in der Liste: " + ", ".join(html.escape(e) for e in existing))
            send(chat_id, "\n".join(reply))
            for item in added:
                send(chat_id, search_now(item))

        elif cmd in ("/del", "/remove", "/delete"):
            if not arg:
                send(chat_id, "Bitte so: <code>/del Uroplatus</code> oder <code>/del 3</code>\n\n"
                     + format_list(kws))
                continue
            removed = []
            for item in [x.strip() for x in arg.split(",") if x.strip()]:
                if item.isdigit() and 1 <= int(item) <= len(kws):
                    removed.append(kws[int(item) - 1])
                else:
                    removed += [k for k in kws if normalize(k) == normalize(item)]
            if removed:
                kws = [k for k in kws if k not in removed]
                send(chat_id, "🗑 Entfernt: " + ", ".join(html.escape(r) for r in removed))
            else:
                send(chat_id, "Nicht gefunden.\n\n" + format_list(kws))

        elif cmd in ("/liste", "/list"):
            send(chat_id, format_list(kws))

        elif cmd in ("/suche", "/search"):
            send(chat_id, search_now(arg) if arg else "Bitte so: <code>/suche Phelsuma</code>")

        elif cmd == "/pause":
            state["paused"] = True
            send(chat_id, "⏸ Benachrichtigungen pausiert. Mit /weiter geht's wieder los.")

        elif cmd in ("/weiter", "/resume"):
            state["paused"] = False
            send(chat_id, "▶️ Benachrichtigungen laufen wieder.")

        elif cmd == "/status":
            last = state.get("last_ok", "noch nie")
            send(chat_id,
                 f"<b>Status</b>\nLetzte erfolgreiche Prüfung: {html.escape(last)} (UTC)\n"
                 f"Suchbegriffe: {len(kws)}\n"
                 f"Pausiert: {'ja' if state.get('paused') else 'nein'}\n"
                 f"Treffer bisher gemeldet: {state.get('hits_total', 0)}")

        else:
            # Text ohne Befehl: als Suchbegriff anbieten statt zu ignorieren
            send(chat_id, f"Meintest du <code>/add {html.escape(text)}</code>?\n"
                          "Schicke /hilfe für alle Befehle.")
    return kws


# --------------------------------------------------------------------------- Seite lesen

def fetch(url: str) -> str:
    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            r.encoding = r.encoding or "utf-8"
            return r.text
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Seite nicht erreichbar: {last_err}")


def _ad_ids_in(tag) -> set[str]:
    ids = set()
    for a in tag.find_all("a", href=True):
        m = AD_LINK_RE.search(a["href"])
        if m:
            ids.add(m.group(1))
    return ids


def parse_listing(page_html: str) -> list[dict]:
    """Findet alle Anzeigen auf einer Übersichtsseite.

    Jede Anzeige hat eine Überschrift mit Link .../a<Nummer>/. Von dort gehen wir im HTML
    so weit nach oben, wie der Bereich nur diese eine Anzeige enthält – das ist der
    Anzeigen-Kasten mit Kategorie, Text, Alter und Ort."""
    soup = BeautifulSoup(page_html, "html.parser")
    ads, done = [], set()
    for a in soup.find_all("a", href=True):
        m = AD_LINK_RE.search(a["href"])
        if not m:
            continue
        ad_id = m.group(1)
        if ad_id in done:
            continue
        # Die Überschrift kann um den Link liegen oder im Link stecken (<a><h3>…</h3></a>)
        heading = a.find_parent(["h1", "h2", "h3", "h4", "h5"]) or a.find(["h1", "h2", "h3", "h4", "h5"])
        if heading is None:
            continue  # Bild-Links o. ä. – die Überschrift kommt noch
        done.add(ad_id)

        box = heading
        while box.parent is not None and box.parent.name not in ("body", "html", "[document]"):
            if _ad_ids_in(box.parent) - {ad_id}:
                break
            box = box.parent

        title = (a.get("title") or a.get_text(" ", strip=True)).strip()
        url = a["href"] if a["href"].startswith("http") else BASE + a["href"]
        full_text = box.get_text(" ", strip=True)

        category = ""
        for el in box.find_all(["b", "strong"]):
            t = el.get_text(" ", strip=True)
            if re.match(r"^(Verkaufe|Suche|Tausche|Biete|Verschenke)\b", t):
                category = t
                break

        place = ""
        marker = box.find("img", alt=re.compile(r"^Ort$", re.I))
        if marker is not None:
            bits = []
            for sib in marker.next_siblings:
                if getattr(sib, "name", None) == "br":
                    break
                if getattr(sib, "name", None) == "img":
                    continue
                s = sib.get_text(" ", strip=True) if hasattr(sib, "get_text") else str(sib).strip()
                if s:
                    bits.append(s)
            place = " ".join(bits).strip()
            flag = marker.find_next("img", alt=re.compile(r"^Fahne", re.I))
            if flag is not None:
                place = (place + " " + flag["alt"].replace("Fahne", "").strip()).strip()

        age = ""
        m_age = re.search(r"\b(vor \d+ (?:Minuten?|Stunden?|Tagen?)|gestern|heute)\b", full_text)
        if m_age:
            age = m_age.group(1)

        ads.append({
            "id": ad_id,
            "title": title,
            "url": url,
            "category": category,
            "place": place,
            "age": age,
            "norm": normalize(title + " " + full_text),
        })
    return ads


def fetch_newest() -> list[dict]:
    ads, seen_ids = [], set()
    for page in range(PAGES):
        url = LIST_URL if page == 0 else f"{LIST_URL}?split={page * 20}&type_selection=0"
        for ad in parse_listing(fetch(url)):
            if ad["id"] not in seen_ids:
                seen_ids.add(ad["id"])
                ads.append(ad)
        time.sleep(1.5)  # höflich zur Seite sein
    return ads


def format_hit(ad: dict, matched: list[str]) -> str:
    lines = ["🦎 <b>Neue Anzeige für „" + html.escape(", ".join(matched)) + "“</b>",
             f"<a href=\"{ad['url']}\">{html.escape(ad['title'])}</a>"]
    meta = " · ".join(x for x in (ad["category"], ad["place"], ad["age"]) if x)
    if meta:
        lines.append(html.escape(meta))
    return "\n".join(lines)


# --------------------------------------------------------------------------- Hauptablauf

def main() -> int:
    state = load_state()
    kws = load_keywords()
    kws_before = list(kws)

    kws = handle_updates(state, kws)
    if kws != kws_before:
        save_keywords(kws)

    owner = state.get("owner")
    first_run = "seen" not in state

    try:
        ads = fetch_newest()
        if not ads:
            raise RuntimeError("Keine Anzeigen auf der Seite gefunden – hat sich das Layout geändert?")
    except Exception as e:  # noqa: BLE001
        state["fails"] = state.get("fails", 0) + 1
        log("Fehler:", e)
        if owner and state["fails"] == FAIL_ALERT_AFTER:
            send(owner, f"⚠️ Der Suchagent konnte terraristik.com {FAIL_ALERT_AFTER}× hintereinander "
                        f"nicht lesen.\nFehler: <code>{html.escape(str(e))}</code>\n"
                        "Ich melde mich wieder, sobald es klappt.")
        save_state(state)
        return 0

    if owner and state.get("fails", 0) >= FAIL_ALERT_AFTER:
        send(owner, "✅ terraristik.com ist wieder erreichbar, der Suchagent läuft normal.")
    state["fails"] = 0
    state["last_ok"] = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M")

    seen = set(state.get("seen", []))
    new_ads = [a for a in ads if a["id"] not in seen]
    log(f"{len(ads)} Anzeigen gelesen, davon {len(new_ads)} neu, {len(kws)} Suchbegriffe")

    if first_run:
        log("Erster Lauf: aktuelle Anzeigen werden als bekannt markiert, keine Meldungen")
    elif owner and not state.get("paused") and kws:
        for ad in sorted(new_ads, key=lambda a: int(a["id"])):
            matched = [k for k in kws if keyword_matches(k, ad["norm"])]
            if matched:
                send(owner, format_hit(ad, matched))
                state["hits_total"] = state.get("hits_total", 0) + 1
                log("Treffer:", ad["id"], ad["title"], matched)

    state["seen"] = state.get("seen", []) + [a["id"] for a in sorted(new_ads, key=lambda a: int(a["id"]))]
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
