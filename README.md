# Terraristik-Suchagent

Prüft alle paar Minuten die neuesten Kleinanzeigen auf terraristik.com und schickt dir eine
Telegram-Nachricht, sobald eine neue Anzeige zu einem deiner Suchbegriffe erscheint.
Läuft kostenlos auf GitHub – dein PC muss dafür nicht eingeschaltet sein.

## Einrichtung (einmalig, ca. 15 Minuten)

### 1. Telegram-Bot anlegen
1. In Telegram nach **@BotFather** suchen (blauer Haken) und öffnen.
2. `/newbot` schicken.
3. Einen Namen eingeben (z. B. `Mein Terraristik Agent`).
4. Einen Benutzernamen eingeben, der auf `bot` endet (z. B. `jakob_terra_bot`).
5. BotFather antwortet mit einem **Token** (sieht aus wie `1234567890:AAH...`). Kopieren und geheim halten.

### 2. GitHub-Konto und Repository
1. Auf <https://github.com> kostenlos registrieren.
2. Rechts oben **+ → New repository**.
3. Name: `terraristik-suchagent`. Sichtbarkeit: **Public** (siehe Hinweis unten). Auf **Create repository** klicken.

### 3. Dateien hochladen
1. Im neuen Repository auf **uploading an existing file** klicken.
2. Den Inhalt des entpackten Ordners hineinziehen: `suchagent.py`, `requirements.txt`, `keywords.txt`, `README.md` und den Ordner `.github`.
3. Unten auf **Commit changes** klicken.
4. Prüfen, ob die Datei `.github/workflows/suchagent.yml` im Repository auftaucht.
   Falls nicht (am Mac sind Ordner mit Punkt oft unsichtbar): **Add file → Create new file**,
   als Namen `.github/workflows/suchagent.yml` eintippen, den Inhalt der Datei hineinkopieren, **Commit changes**.

### 4. Telegram-Token hinterlegen
1. Im Repository: **Settings → Secrets and variables → Actions → New repository secret**.
2. Name: `TELEGRAM_TOKEN`  ·  Secret: das Token von BotFather  ·  **Add secret**.

### 5. Starten
1. In Telegram deinen neuen Bot öffnen und auf **Starten** tippen (schickt `/start`).
   Wichtig: Wer dem Bot als Erstes schreibt, wird sein Besitzer – danach reagiert er nur noch auf dich.
2. Im Repository auf **Actions** gehen. Falls gefragt, Workflows aktivieren.
3. Links **Suchagent** wählen → **Run workflow** → **Run workflow**.
4. Nach ca. einer Minute meldet sich der Bot mit „Suchagent verbunden!“.

Ab jetzt läuft alles automatisch. Beim allerersten Lauf merkt sich der Agent die vorhandenen
Anzeigen, gemeldet werden nur Anzeigen, die danach neu erscheinen.

## Bedienung über Telegram

| Befehl | Wirkung |
|---|---|
| `/add Uroplatus` | Suchbegriff hinzufügen (mehrere mit Komma: `/add Uroplatus, Phelsuma`) und sofort zeigen, was aktuell online ist |
| `/del Uroplatus` oder `/del 2` | Suchbegriff entfernen |
| `/liste` | alle Suchbegriffe |
| `/suche Abronia` | einmalig online suchen, ohne zu speichern |
| `/pause` / `/weiter` | Meldungen anhalten / fortsetzen |
| `/status` | letzte Prüfung, Anzahl Treffer |
| `/hilfe` | Übersicht |

**Wie gesucht wird:** Titel und Text jeder Anzeige werden durchsucht. Groß-/Kleinschreibung
und Umlaute sind egal (`Königspython` findet auch `Koenigspython`). Mehrere Wörter in einem
Begriff müssen alle vorkommen, egal in welcher Reihenfolge. Ein Wort mit Minus schließt aus:
`/add pumilio -suche` meldet keine Suchanzeigen. Abkürzungen wie `O. pumilio` am besten
zusätzlich als eigenen Begriff anlegen.

Befehle werden beim nächsten Durchlauf verarbeitet, also meist nach 5–15 Minuten.
Die Liste kannst du alternativ auch direkt in `keywords.txt` auf GitHub bearbeiten.

## Gut zu wissen
- **Takt:** eingestellt sind 5 Minuten. GitHub startet geplante Läufe manchmal verzögert, real sind es oft 5–15 Minuten.
- **Public oder Private:** In einem öffentlichen Repository ist GitHub Actions unbegrenzt kostenlos. Dafür sind `keywords.txt` und `state.json` für jeden sichtbar, der das Repository findet (dein Token bleibt geheim). Bei einem privaten Repository sind ca. 2000 Freiminuten pro Monat dabei; dann in `suchagent.yml` die Zeile `cron: "*/5 * * * *"` auf `cron: "*/30 * * * *"` ändern.
- **Störungen:** Kann der Agent die Seite mehrmals hintereinander nicht lesen, bekommst du eine Warnung per Telegram, und eine Entwarnung, sobald es wieder klappt.
- **Pausieren für längere Zeit:** Actions → Suchagent → „…“ → Disable workflow.
