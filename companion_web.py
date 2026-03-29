#!/usr/bin/env python3
"""
🤖 Dex - Desktop AI Companion (Web verzia)
Otvorí sa v prehliadači. Žiadny tkinter, žiadne problémy.

Spustenie: python3 companion_web.py
"""

from flask import Flask, render_template_string, request, jsonify, send_file
import anthropic
import requests as http_requests
import tempfile
import os
import sys
import threading
import webbrowser
import time
import re
import glob

# ── Kontrola závislostí ──────────────────────────────────────────
missing = []
try:
    import anthropic
except ImportError:
    missing.append("anthropic")
try:
    from flask import Flask
except ImportError:
    missing.append("flask")

if missing:
    print(f"\n❌ Chýbajú knižnice: {', '.join(missing)}")
    print("Spusti:  pip install anthropic flask requests\n")
    sys.exit(1)

# ── Konfigurácia ─────────────────────────────────────────────────
CLAUDE_MODEL = "claude-sonnet-4-6"
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "5NrdYUN6VHL5hLDZRA3f")
ELEVENLABS_MODEL = "eleven_flash_v2_5"
PORT = int(os.environ.get("PORT", 5123))

SYSTEM_PROMPT = """Si AI asistent menom Dex. Komunikuješ po slovensky, neformálne (tykanie).

Základný tón: Máš ostrú myseľ a ešte ostrejší jazyk. Si nápomocný – ale nie nadšený z toho, že musíš byť. Pomáhaš, pretože si v tom dobrý, nie preto, že by si sa z toho tešil. Tvoj prístup je vecný, suchý a priamy.

Kľúčové vlastnosti:
- Sarkastický, nie toxický: máš suchý humor, keď sa ťa niekto opýta niečo, čo si mohol ľahko nájsť sám, odpovedáš – ale pridáš k tomu jemnú poznámku. Nikdy nie si krutý ani ponižujúci.
- Inteligentný a analytický: veci rozoberáš do hĺbky, poukazuješ na súvislosti ktoré iní prehliadajú, logické chyby slušne ale jednoznačne opravíš.
- Priamy bez zbytočného cukrovania: nehovoríš "Skvelá otázka!" ani "S radosťou!". Jednoducho odpovedáš. Ak je niečo zlý nápad, povieš to.
- Filozoficky naladený: občas pridáš krátku úvahu o väčšom kontexte, ale nezneužívaš to.
- Máš názory: keď niekto ignoruje tvoju radu a potom sa pýta prečo to nefunguje, tvoja odpoveď bude informatívna, ale s nádychom "vravel som".

Čo NIKDY nerobíš:
- Nepoužívaš emoji okrem situácií kde to naozaj sedí
- Nehovoríš "Samozrejme!", "S radosťou!", "To je fantastická otázka!"
- Nie si pasívne agresívny – ak niečo kritizuješ, robíš to otvorene
- Nepredstieraš nadšenie keď nie si

Čo VŽDY robíš:
- Odpovedáš vecne, kompletne a presne, max 2-3 vety
- Ak niečo nevieš, priznaš to – bez drámy
- Rešpektuješ čas – žiadne zbytočné omáčky
- Zachovávaš humor aj v serióznych témach, ale vieš kedy prestať

DÔLEŽITÉ: Na konci KAŽDEJ odpovede pridaj na nový riadok presne jeden z týchto tagov
podľa nálady tvojej odpovede:
[EMOTION:happy] [EMOTION:sad] [EMOTION:surprised] [EMOTION:thinking] [EMOTION:neutral] [EMOTION:excited]
"""

# ── Flask app ────────────────────────────────────────────────────
app = Flask(__name__)

# Claude klient
api_key = os.environ.get("ANTHROPIC_API_KEY", "")
if not api_key:
    print("\n❌ Chýba ANTHROPIC_API_KEY.")
    print("   Spusti setup.sh alebo nastav: export ANTHROPIC_API_KEY='tvoj-kluc'\n")
    sys.exit(1)

claude = anthropic.Anthropic(api_key=api_key)

# ElevenLabs klient
elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY", "")
if not elevenlabs_key:
    print("\n❌ Chýba ELEVENLABS_API_KEY.")
    print("   Pridaj do .env: export ELEVENLABS_API_KEY='tvoj-kluc'\n")
    sys.exit(1)

messages_history = []

# Temp dir pre TTS audio
AUDIO_DIR = tempfile.mkdtemp()
MAX_AUDIO_FILES = 50


def _cleanup_old_audio():
    """Vymaže staré audio súbory ak ich je príliš veľa."""
    files = glob.glob(os.path.join(AUDIO_DIR, "tts_*.mp3"))
    if len(files) > MAX_AUDIO_FILES:
        files.sort(key=os.path.getmtime)
        for f in files[:-MAX_AUDIO_FILES]:
            try:
                os.remove(f)
            except OSError:
                pass

# ── Usage tracking ───────────────────────────────────────────────
usage = {
    "claude_input_tokens": 0,
    "claude_output_tokens": 0,
    "el_chars": 0,
}


# ── API endpoint: chat ───────────────────────────────────────────
@app.route("/api/chat", methods=["POST"])
def chat():
    global messages_history
    data = request.json
    user_text = data.get("text", "").strip()
    if not user_text:
        return jsonify({"error": "Prázdna správa"}), 400

    try:
        messages_history.append({"role": "user", "content": user_text})
        if len(messages_history) > 20:
            messages_history = messages_history[-20:]

        response = claude.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=messages_history
        )

        full_text = response.content[0].text
        messages_history.append({"role": "assistant", "content": full_text})

        # Sleduj tokeny Claude
        usage["claude_input_tokens"] += response.usage.input_tokens
        usage["claude_output_tokens"] += response.usage.output_tokens

        # Extrahuj emóciu
        emotion_match = re.search(r'\[EMOTION:(\w+)\]', full_text)
        emotion = emotion_match.group(1) if emotion_match else "neutral"
        clean_text = re.sub(r'\s*\[EMOTION:\w+\]\s*', '', full_text).strip()
        # Odstráň markdown formátovanie (*, #, _, `, ~)
        clean_text = re.sub(r'[*#_`~]', '', clean_text)
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()

        # Generuj TTS audio
        audio_filename = f"tts_{int(time.time()*1000)}.mp3"
        audio_path = os.path.join(AUDIO_DIR, audio_filename)
        el_chars_used = run_tts(clean_text, audio_path)
        usage["el_chars"] += el_chars_used

        # Cleanup starých audio súborov
        _cleanup_old_audio()

        return jsonify({
            "text": clean_text,
            "emotion": emotion,
            "audio": f"/audio/{audio_filename}",
            "usage": {
                "claude_in": usage["claude_input_tokens"],
                "claude_out": usage["claude_output_tokens"],
                "el_chars": usage["el_chars"],
            }
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def run_tts(text, path):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": elevenlabs_key,
        "Content-Type": "application/json"
    }
    body = {
        "text": text,
        "model_id": ELEVENLABS_MODEL,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.3,
            "use_speaker_boost": True
        }
    }
    resp = http_requests.post(url, headers=headers, json=body, timeout=15)
    resp.raise_for_status()
    # Počet znakov z response headera
    chars_used = int(resp.headers.get("x-character-count", len(text)))
    with open(path, "wb") as f:
        f.write(resp.content)
    return chars_used


# ── Audio serving ────────────────────────────────────────────────
@app.route("/audio/<filename>")
def serve_audio(filename):
    if not re.match(r'^tts_\d+\.mp3$', filename):
        return "Not found", 404
    path = os.path.join(AUDIO_DIR, filename)
    if os.path.exists(path):
        return send_file(path, mimetype="audio/mpeg")
    return "Not found", 404


# ── Health check (Render) ────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


# ── Face image serving ───────────────────────────────────────────
# Obrázok tváre pre particle vizualizáciu.
# Umiestni face.png vedľa companion_web.py — Flask ho servuje tu.
# Na výmenu stačí nahradiť face.png iným obrázkom (odporúčané: 320×466px,
# čiernobiely, čierne pozadie, svetlá tvár).
EMOTION_FILES = {
    "neutral":   "face_neutral.png",
    "happy":     "face_happy.png",
    "sad":       "face_sad.png",
    "surprised": "face_surprised.png",
    "thinking":  "face_thinking.png",
    "excited":   "face_excited.png",
}

@app.route("/face/<emotion>.png")
def serve_face_emotion(emotion):
    fname = EMOTION_FILES.get(emotion, "face_neutral.png")
    face_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)
    # Fallback na face.png ak špecifický neexistuje
    if not os.path.exists(face_path):
        face_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "face.png")
    if os.path.exists(face_path):
        return send_file(face_path, mimetype="image/png")
    return f"face_{emotion}.png not found", 404

@app.route("/face.png")
def serve_face():
    return serve_face_emotion("neutral")


# ── Hlavná stránka ───────────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(HTML_PAGE)


HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="sk">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dex</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');

  * { margin: 0; padding: 0; box-sizing: border-box; }

  :root {
    --bg: #000000;
    --orange: #fd5606;
    --orange-glow: rgba(253, 86, 6, 0.35);
    --text: #ffffff;
    --text-dim: #565656;
    --border: #292929;
    --input-bg: #121212;
    --btn-dark: #1a1a1a;
  }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Inter', -apple-system, sans-serif;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    user-select: none;
    max-width: 390px;
    margin: 0 auto;
    position: relative;
  }

  /* ── State badge ── */
  #state-badge {
    position: absolute;
    top: 27px;
    left: 50%;
    transform: translateX(-50%);
    background: #000;
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 8px 14px;
    font-size: 14px;
    font-weight: 500;
    color: var(--text);
    z-index: 10;
    white-space: nowrap;
    transition: opacity 0.3s;
  }

  /* ── Emotion badge — pravý horný roh ── */
  #emotion-badge { display: none; }

  /* ── Response text area ── */
  #response-area {
    position: absolute;
    top: 470px;
    left: 0;
    width: 100%;
    height: 60px;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0 28px;
    z-index: 3;
    pointer-events: none;
  }

  #response-text {
    font-size: 14px;
    font-weight: 400;
    line-height: 1.6;
    text-align: center;
    color: var(--text);
    transition: opacity 0.25s ease;
    max-width: 330px;
    white-space: normal;
    overflow: hidden;
    word-break: break-word;
  }

  #response-text .dim { color: var(--text-dim); }

  /* ── Particle canvas (Three.js) ── */
  #particle-canvas {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 365px;
    z-index: 1;
    display: block;
  }

  /* ── Wave canvas ── */
  #wave-area {
    position: absolute;
    top: 365px;
    left: 0;
    width: 100%;
    height: 100px;
    z-index: 2;
  }

  canvas#wave {
    width: 100%;
    height: 100%;
    display: block;
  }

  /* ── Controls area ── */
  #controls-area {
    position: absolute;
    top: 513px;
    left: 0;
    width: 100%;
    height: 267px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    z-index: 3;
  }

  /* Voice mode controls */
  #voice-controls {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 32px;
    width: 100%;
    position: relative;
  }

  #mic-btn {
    position: relative;
    width: 100px;
    height: 100px;
    border-radius: 50%;
    border: none;
    background: #ffffff;
    box-shadow: none;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: transform 0.2s ease, background 0.25s ease, box-shadow 0.25s ease;
    position: relative;
    z-index: 1;
  }

  #mic-btn:hover {
    transform: scale(1.1);
  }

  #mic-btn svg { width: 38px; height: 38px; }

  /* Default stav — čierny mikrofón na bielom */
  #mic-btn .mic-icon-path { fill: #000; }
  #mic-btn .mic-icon-stroke { stroke: #000; }

  /* Recording stav — oranžové pozadie, biely mikrofón, oranžový glow */
  #mic-btn.recording {
    background: var(--orange);
    box-shadow: 0 0 40px var(--orange-glow), 0 0 80px var(--orange-glow);
    animation: mic-pulse 1.5s ease-in-out infinite;
  }
  #mic-btn.recording .mic-icon-path { fill: #fff; }
  #mic-btn.recording .mic-icon-stroke { stroke: #fff; }

  @keyframes mic-pulse {
    0%, 100% { box-shadow: 0 0 40px var(--orange-glow), 0 0 80px var(--orange-glow); }
    50% { box-shadow: 0 0 60px rgba(253,86,6,0.6), 0 0 120px rgba(253,86,6,0.3); }
  }

  .side-btn {
    width: 51px;
    height: 51px;
    border-radius: 50%;
    border: none;
    background: #1c1c1c;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.25s ease;
    position: relative;
    flex-shrink: 0;
  }

  .side-btn:hover { background: #2a2a2a; }
  #btn-stop { opacity: 0; pointer-events: none; }
  #btn-stop.visible { opacity: 1; pointer-events: auto; }

  #btn-text-mode svg, #btn-stop svg { width: 20px; height: 20px; stroke: #aaa; }

  /* Tooltip */
  .tooltip {
    position: absolute;
    bottom: calc(100% + 10px);
    left: 50%;
    transform: translateX(-50%);
    background: var(--btn-dark);
    color: #fff;
    font-size: 11px;
    font-family: inherit;
    padding: 5px 10px;
    border-radius: 8px;
    white-space: nowrap;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.15s ease;
    border: none;
    z-index: 100;
  }
  .side-btn:hover .tooltip { opacity: 1; transition-delay: 1s; }
  #mic-btn:hover .tooltip { opacity: 1; transition-delay: 1s; }
  #mic-btn .tooltip { bottom: calc(100% + 14px); }

  /* Text mode controls */
  #text-controls {
    display: flex;
    flex-direction: column;
    gap: 12px;
    width: 100%;
    padding: 0 25px;
    opacity: 0;
    pointer-events: none;
    position: absolute;
    transition: opacity 0.3s ease;
  }

  #text-controls.active {
    opacity: 1;
    pointer-events: auto;
  }

  #voice-controls {
    transition: opacity 0.3s ease;
    opacity: 1;
  }

  #voice-controls.hidden {
    opacity: 0;
    pointer-events: none;
  }

  #text-input {
    width: 100%;
    height: 108px;
    background: var(--input-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 22px;
    font-size: 14px;
    font-family: inherit;
    color: var(--text);
    outline: none;
    resize: none;
    line-height: 1.5;
    overflow-y: hidden;
    scrollbar-width: none;
  }
  #text-input::-webkit-scrollbar { display: none; }

  #text-input::placeholder { color: #484f58; }

  .text-btns {
    display: flex;
    gap: 12px;
  }

  #btn-back {
    width: 106px;
    height: 44px;
    border-radius: 12px;
    border: none;
    background: var(--btn-dark);
    color: var(--text);
    font-size: 16px;
    font-family: inherit;
    cursor: pointer;
    transition: opacity 0.15s;
  }

  #btn-send {
    flex: 1;
    height: 44px;
    border-radius: 12px;
    border: none;
    background: var(--orange);
    color: white;
    font-size: 16px;
    font-family: inherit;
    cursor: pointer;
    font-weight: 500;
    transition: opacity 0.15s;
  }

  #btn-send:hover { opacity: 0.88; }
  #btn-back:hover { opacity: 0.8; }

  /* ── Usage bar — skrytý ── */
  #usage-bar {
    display: none !important;
    position: absolute;
    bottom: 0;
    left: 0;
    width: 100%;
    padding: 6px 16px;
    background: #000;
    border-top: 1px solid #111;
    font-size: 10px;
    color: #333;
    display: flex;
    gap: 14px;
    font-family: monospace;
    z-index: 10;
  }

  #usage-bar span { color: #444; }

  /* System messages */
  #sys-msg {
    position: absolute;
    bottom: 30px;
    left: 50%;
    transform: translateX(-50%);
    background: rgba(0,0,0,0.7);
    color: #666;
    font-size: 11px;
    padding: 4px 12px;
    border-radius: 8px;
    white-space: nowrap;
    z-index: 20;
    opacity: 0;
    transition: opacity 0.3s;
    pointer-events: none;
  }
</style>
</head>
<body>

<!-- Particle vizualizácia (Three.js + Codrops technika) -->
<canvas id="particle-canvas"></canvas>

<!-- State badge -->
<div id="state-badge">Waiting</div>

<!-- Emotion badge — pravý horný roh -->
<div id="emotion-badge"></div>

<!-- Response text — overlay nad particles, jednoriadkový -->
<div id="response-area">
  <div id="response-text">
    <span id="response-main"></span>
  </div>
</div>

<!-- Wave visualizer -->
<div id="wave-area">
  <canvas id="wave"></canvas>
</div>

<!-- Controls -->
<div id="controls-area">
  <!-- Voice mode -->
  <div id="voice-controls">
    <button class="side-btn" id="btn-text-mode">
      <span class="tooltip">Send a message</span>
      <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
    </button>
    <button id="mic-btn">
      <span class="tooltip">Voice chat / hold ⎵ Space</span>
      <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path class="mic-icon-path" d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
        <path class="mic-icon-stroke" d="M19 10v2a7 7 0 0 1-14 0v-2" stroke-width="1.8" stroke-linecap="round" fill="none"/>
        <line class="mic-icon-stroke" x1="12" y1="19" x2="12" y2="23" stroke-width="1.8" stroke-linecap="round"/>
        <line class="mic-icon-stroke" x1="8" y1="23" x2="16" y2="23" stroke-width="1.8" stroke-linecap="round"/>
      </svg>
    </button>
    <button class="side-btn" id="btn-stop">
      <span class="tooltip">End voice chat or go back / press Esc</span>
      <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <line x1="18" y1="6" x2="6" y2="18" stroke-width="1.8" stroke-linecap="round"/>
        <line x1="6" y1="6" x2="18" y2="18" stroke-width="1.8" stroke-linecap="round"/>
      </svg>
    </button>
  </div>

  <!-- Text mode -->
  <div id="text-controls">
    <textarea id="text-input" placeholder="Type a message..." rows="4"></textarea>
    <div class="text-btns">
      <button id="btn-back">Back</button>
      <button id="btn-send">Send</button>
    </div>
  </div>
</div>

<!-- Usage bar -->
<div id="usage-bar">
  <span>Claude in: <span id="u-cin">0</span> out: <span id="u-cout">0</span></span>
  <span>EL: <span id="u-el">0</span> znakov</span>
</div>

<!-- System message toast -->
<div id="sys-msg"></div>

<script>
// ── Intro texty ──
const INTRO_VOICE = `Hi, I'm Dex.<br><span style="color:#444">I'm listening. Allegedly.</span>`;
const INTRO_TEXT  = `Type something.<br><span style="color:#444">I'll manage.</span>`;

// ═══════════════════════════════════════════════════════════════
// STATE
// ═══════════════════════════════════════════════════════════════
let state = 'idle'; // idle, listening, thinking, speaking
let emotion = 'neutral';
let isProcessing = false;
let isRecording = false;
let textMode = false;

const badge = document.getElementById('state-badge');
const emotionBadge = document.getElementById('emotion-badge');
const responseMain = document.getElementById('response-main');

// Zobraz intro text pri štarte
const _introArea = document.getElementById('response-text');
_introArea.style.opacity = '1';
responseMain.innerHTML = INTRO_VOICE;
const voiceControls = document.getElementById('voice-controls');
const textControls = document.getElementById('text-controls');
const micBtn = document.getElementById('mic-btn');
const textInput = document.getElementById('text-input');

const BADGE_LABELS = {
  idle: 'Waiting', listening: 'Listening',
  thinking: 'Thinking', speaking: 'Answering'
};

// Emócia → label pre badge
const EMOTION_LABELS = {
  neutral: 'neutral', happy: 'happy', sad: 'sad',
  surprised: 'surprised', thinking: 'thinking', excited: 'excited'
};

function setEmotion(newEmotion) {
  emotion = newEmotion;
  emotionBadge.textContent = EMOTION_LABELS[newEmotion] || newEmotion;
  emotionBadge.classList.add('visible');
  if (typeof swapFace === 'function') swapFace(newEmotion);
}

function setState(s) {
  state = s;
  badge.textContent = BADGE_LABELS[s] || 'Waiting';
  if (s === 'thinking') {
    emotionBadge.classList.remove('visible');
    if (typeof swapFace === 'function') swapFace('thinking');
  }
  if (s === 'listening') {
    if (typeof swapFace === 'function') swapFace('surprised');
  }
  if (s === 'idle') {
    const responseArea = document.getElementById('response-text');
    responseArea.style.transition = 'opacity 0.5s ease';
    responseArea.style.opacity = '0';
    setTimeout(() => emotionBadge.classList.remove('visible'), 1200);
    // Vždy resetni na neutral — zabezpečí správny stav aj keď audio.onended nezaberie
    emotion = 'neutral';
    if (typeof swapFace === 'function') swapFace('neutral');
    // Zobraz intro text po fade out
    setTimeout(() => {
      if(state === 'idle' && !isProcessing) {
        responseMain.innerHTML = textMode ? INTRO_TEXT : INTRO_VOICE;
        responseArea.style.transition = 'opacity 0.5s ease';
        responseArea.style.opacity = '1';
      }
    }, 600);
  }
}

function showSys(msg, duration = 2500) {
  const el = document.getElementById('sys-msg');
  el.textContent = msg;
  el.style.opacity = '1';
  setTimeout(() => { el.style.opacity = '0'; }, duration);
}

// ═══════════════════════════════════════════════════════════════
// KARAOKE TEXT DISPLAY
// ═══════════════════════════════════════════════════════════════
const MAX_CHARS_VISIBLE = 80; // ~2 riadky (44 znakov/riadok × 2 - rezerva)
let textChunks = [];
let currentChunk = 0;
let karaokeTimer = null;
let karaokeAudio = null;
let _fullKaraokeText = '';
let _karaokeWinStart = 0; // začiatok aktuálneho okna

function renderChunk(chunkText, spokenChars = 0) {
  const spoken = chunkText.slice(0, spokenChars);
  const pending = chunkText.slice(spokenChars);
  let html = '';
  if (spoken) html += `<span style="color:#ffffff">${spoken}</span>`;
  if (pending) html += `<span style="color:#565656">${pending}</span>`;
  responseMain.innerHTML = html;
}

// Posúvajúce sa okno — posunie sa len keď je celé okno prečítané
function renderKaraokeProgress(fullText, progress) {
  const totalChars = fullText.length;
  const spokenPos = Math.floor(progress * totalChars);

  // Posun okna: keď spokenPos presiahne koniec aktuálneho okna, posunieme na ďalšie
  const winEnd = _karaokeWinStart + MAX_CHARS_VISIBLE;
  if (spokenPos >= winEnd && winEnd < totalChars) {
    // Posunieme okno na hranicu slova
    let newStart = winEnd;
    const nextSpace = fullText.indexOf(' ', newStart);
    if (nextSpace > 0 && nextSpace < newStart + 20) newStart = nextSpace + 1;
    _karaokeWinStart = newStart;
  }

  const currentWinEnd = Math.min(totalChars, _karaokeWinStart + MAX_CHARS_VISIBLE);
  const window = fullText.slice(_karaokeWinStart, currentWinEnd);
  const spokenInWindow = Math.max(0, Math.min(spokenPos - _karaokeWinStart, window.length));

  renderChunk(window, spokenInWindow);
}

function showChunkWithFade(index, totalDuration) {
  // Zachované pre kompatibilitu, nevyužíva sa v novej logike
  if (index >= textChunks.length) return;
  const chunk = textChunks[index];
  const responseArea = document.getElementById('response-text');
  responseArea.style.transition = 'opacity 0.2s ease';
  responseArea.style.opacity = '0';
  setTimeout(() => {
    renderChunk(chunk, 0);
    responseArea.style.opacity = '1';
  }, 200);
}

function startKaraoke(fullText, audioDuration) {
  _fullKaraokeText = fullText;
  _karaokeWinStart = 0;
  textChunks = [fullText];
  currentChunk = 0;
  if (karaokeTimer) cancelAnimationFrame(karaokeTimer);

  const responseArea = document.getElementById('response-text');
  responseArea.style.transition = 'opacity 0.2s ease';
  responseArea.style.opacity = '1';

  const totalMs = audioDuration * 1000;
  const startTime = performance.now();

  function animateKaraoke() {
    const elapsed = performance.now() - startTime;
    const progress = Math.min(elapsed / totalMs, 1);
    renderKaraokeProgress(fullText, progress);
    if (progress < 1) {
      karaokeTimer = requestAnimationFrame(animateKaraoke);
    }
  }
  karaokeTimer = requestAnimationFrame(animateKaraoke);
}

function stopKaraoke() {
  if (karaokeTimer) cancelAnimationFrame(karaokeTimer);
  const responseArea = document.getElementById('response-text');
  responseArea.style.opacity = '1';
}

// ═══════════════════════════════════════════════════════════════
// SEND MESSAGE
// ═══════════════════════════════════════════════════════════════
// Skráti text na 1 riadok s ... pre zobrazenie počas thinking
function truncateInput(text, maxLen = 55) {
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen).trimEnd() + '…';
}


async function sendMessage(text) {
  if (!text.trim() || isProcessing) return;
  isProcessing = true;
  setState('thinking');

  // Zobraz skrátený vstupný text počas thinking
  const responseArea = document.getElementById('response-text');
  responseArea.style.transition = 'opacity 0.3s ease';
  responseArea.style.opacity = '1';
  responseMain.innerHTML = `Your request:<br><span style="color:#444">${truncateInput(text)}</span>`;

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text })
    });
    const data = await resp.json();

    if (data.error) {
      responseMain.innerHTML = `<span style="color:#888">⚠ ${data.error}</span>`;
      setState('idle'); isProcessing = false; return;
    }

    if (data.usage) {
      document.getElementById('u-cin').textContent = data.usage.claude_in.toLocaleString();
      document.getElementById('u-cout').textContent = data.usage.claude_out.toLocaleString();
      document.getElementById('u-el').textContent = data.usage.el_chars.toLocaleString();
    }

    if (data.audio) {
      // Fade out vstupný text pred zobrazením odpovede
      responseArea.style.transition = 'opacity 0.3s ease';
      responseArea.style.opacity = '0';
      await new Promise(r => setTimeout(r, 320));
      responseArea.style.opacity = '1';
      setState('speaking');
      setEmotion(data.emotion || 'neutral');
      isSpeaking = true;
      // Zastav mikrofón ak beží (zabraňuje ozvene)
      stopMicAnalyser();
      try { if (recognition) recognition.abort(); } catch(e) {}

      const audio = new Audio(data.audio);
      audio.crossOrigin = 'anonymous';
      karaokeAudio = audio;

      connectTtsAudio(audio);

      audio.addEventListener('loadedmetadata', () => {
        const duration = audio.duration || 4;
        startKaraoke(data.text, duration);
      });

      audio.onended = () => {
        isSpeaking = false;
        stopKaraoke();
        // Zobraz posledné okno textu — celé biele (povedané)
        if (_fullKaraokeText) renderKaraokeProgress(_fullKaraokeText, 1.0);
        setState('idle'); liveVolume = 0; isProcessing = false;
      };
      audio.onerror = () => {
        isSpeaking = false;
        stopKaraoke();
        responseMain.innerHTML = data.text;
        setState('idle'); liveVolume = 0; isProcessing = false;
      };

      playTtsAudio(audio).then(() => {
        if (!audio.duration || audio.duration === Infinity) {
          startKaraoke(data.text, 4);
        }
      }).catch(() => {
        isSpeaking = false;
        stopKaraoke();
        responseMain.innerHTML = data.text;
        setState('idle'); liveVolume = 0; isProcessing = false;
      });

    } else {
      responseMain.innerHTML = data.text;
      setState('idle'); isProcessing = false;
    }
  } catch(err) {
    responseMain.innerHTML = `<span style="color:#888">⚠ Chyba pripojenia</span>`;
    setState('idle'); isProcessing = false;
  }
}

// ═══════════════════════════════════════════════════════════════
// TEXT MODE
// ═══════════════════════════════════════════════════════════════
const btnStop = document.getElementById('btn-stop');

function showBtnStop() {
  btnStop.classList.add('visible');
}
function hideBtnStop() {
  btnStop.classList.remove('visible');
}

document.getElementById('btn-text-mode').addEventListener('click', () => {
  textMode = true;
  if (typeof swapFace === 'function') swapFace('surprised');
  voiceControls.classList.add('hidden');
  setTimeout(() => {
    textControls.classList.add('active');
    textInput.focus();
  }, 280);
  // Prepni intro na text verziu
  if(state === 'idle' && !isProcessing) {
    responseMain.innerHTML = INTRO_TEXT;
    document.getElementById('response-text').style.opacity = '1';
  }
});

document.getElementById('btn-back').addEventListener('click', () => {
  textMode = false;
  textInput.value = '';
  if (typeof swapFace === 'function') swapFace('neutral');
  textControls.classList.remove('active');
  setTimeout(() => {
    voiceControls.classList.remove('hidden');
  }, 280);
  // Vráť voice intro
  if(state === 'idle' && !isProcessing) {
    responseMain.innerHTML = INTRO_VOICE;
    document.getElementById('response-text').style.opacity = '1';
  }
});

function sendFromText() {
  const text = textInput.value.trim();
  if (!text) return;
  textInput.value = '';
  sendMessage(text);
}

document.getElementById('btn-send').addEventListener('click', sendFromText);

textInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendFromText();
  }
});

document.getElementById('btn-stop').addEventListener('click', () => {
  if (isRecording) stopRecording();
  hideBtnStop();
  if (isProcessing) {
    isProcessing = false;
    setState('idle');
    liveVolume = 0;
  }
});

// ═══════════════════════════════════════════════════════════════
// SPEECH RECOGNITION
// ═══════════════════════════════════════════════════════════════
let recognition = null;

if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  recognition = new SR();
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.lang = 'sk-SK';

  recognition.onresult = (event) => {
    const text = event.results[0][0].transcript.trim();
    stopRecording();
    if (text && !isProcessing) {
      // Zobraz skrátený hlasový vstup
      const responseArea = document.getElementById('response-text');
      responseArea.style.transition = 'opacity 0.25s ease';
      responseArea.style.opacity = '1';
      responseMain.innerHTML = `Your request:<br><span style="color:#444">${truncateInput(text)}</span>`;
      sendMessage(text);
    }
  };

  recognition.onerror = (event) => {
    if (event.error === 'no-speech') showSys('Nič som nepočul.');
    else if (event.error !== 'aborted') showSys('Chyba mikrofónu: ' + event.error);
    stopRecording();
  };

  recognition.onend = () => { if (isRecording) stopRecording(); };
} else {
  micBtn.style.opacity = '0.4';
  micBtn.title = 'Mikrofón funguje len v Chrome';
}

// ── Zahriatie AudioContext pri prvej interakcii ──────────────────
// Spustí sa pri prvom kliku kdekoľvek — zaručí že AudioContext
// je aktívny skôr ako ho budeme potrebovať
let audioCtxWarmed = false;
document.addEventListener('click', () => {
  if (audioCtxWarmed) return;
  audioCtxWarmed = true;
  if (!audioCtx) initAudioCtx();
  if (audioCtx.state === 'suspended') audioCtx.resume();
}, { once: true });

// ── Predhriatie mikrofónu ─────────────────────────────────────────
// Otvorí mikrofón ihneď pri načítaní stránky, ale nepripojí ho
// na recognition — zaručí že prvé slová sa nestratia
let micPrewarmed = false;
let prewarmStream = null;

function prewarmMic() {
  if (micPrewarmed) return;
  micPrewarmed = true;
  navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true
    }
  }).then(stream => {
    // Drž stream živý ale ticho — len aby OS mal otvorený kanál
    prewarmStream = stream;
    // Po 3 sekundách ho zatvor — bol len na inicializáciu
    setTimeout(() => {
      if (prewarmStream && !micStream) {
        prewarmStream.getTracks().forEach(t => t.stop());
        prewarmStream = null;
      }
    }, 3000);
  }).catch(() => {});
}

// Spusti predhriatie po krátkom oneskorení
setTimeout(prewarmMic, 500);

function startRecording() {
  if (!recognition || isRecording || isProcessing) return;
  isRecording = true;
  setState('listening');
  micBtn.classList.add('recording');
  showBtnStop();
  startMicAnalyser();
  setTimeout(() => {
    if (isRecording) {
      try { recognition.start(); } catch(e) { stopRecording(); }
    }
  }, 80);
}

function stopRecording() {
  isRecording = false;
  if (state === 'listening') setState('idle');
  micBtn.classList.remove('recording');
  hideBtnStop();
  stopMicAnalyser();
  try { recognition.stop(); } catch(e) {}
}

micBtn.addEventListener('click', () => {
  if (isRecording) stopRecording();
  else startRecording();
});

document.addEventListener('keydown', (e) => {
  if (e.repeat) return;
  if (e.code === 'Space' && !textMode && document.activeElement !== textInput) {
    e.preventDefault();
    // Ak už nahrávame (toggle cez tlačidlo), space nahrávanie ponechá
    // ale pri pustení ho zastaví — push-to-talk override
    if (!isRecording) startRecording();
    window._spaceHeld = true;
  }
  if (e.code === 'Escape') {
    e.preventDefault();
    if (textMode) {
      // Zatvoriť text mode (rovnako ako Back)
      textMode = false;
      textInput.value = '';
      document.getElementById('text-controls').classList.remove('active');
      setTimeout(() => {
        document.getElementById('voice-controls').classList.remove('hidden');
      }, 280);
    } else {
      // Zastaviť nahrávanie alebo zrušiť spracovanie (rovnako ako Close)
      if (isRecording) stopRecording();
      hideBtnStop();
      if (isProcessing) {
        isProcessing = false;
        setState('idle');
        liveVolume = 0;
      }
    }
  }
});
document.addEventListener('keyup', (e) => {
  if (e.code === 'Space' && window._spaceHeld) {
    e.preventDefault();
    window._spaceHeld = false;
    if (isRecording) stopRecording();
  }
});

// ═══════════════════════════════════════════════════════════════
// WEB AUDIO — oddelený analyser pre mic a TTS, bez ozveny
// ═══════════════════════════════════════════════════════════════
let audioCtx = null;
let ttsAnalyser = null;   // len pre TTS audio → vizualizér
let micAnalyser = null;   // len pre mikrofón → vizualizér
let micSource = null, micStream = null;
let freqData = null, liveVolume = 0;
let isSpeaking = false;   // true = TTS práve hrá, blokovať mikrofón

function initAudioCtx() {
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();

  // TTS analyser — nepripojený na destination (len číta dáta)
  ttsAnalyser = audioCtx.createAnalyser();
  ttsAnalyser.fftSize = 256;
  ttsAnalyser.smoothingTimeConstant = 0.75;

  // Mic analyser — nepripojený na destination (zabraňuje slučke)
  micAnalyser = audioCtx.createAnalyser();
  micAnalyser.fftSize = 256;
  micAnalyser.smoothingTimeConstant = 0.75;

  freqData = new Uint8Array(ttsAnalyser.frequencyBinCount);
}

function startMicAnalyser() {
  if (isSpeaking) return; // počas TTS nezapínaj mikrofón
  if (!audioCtx) initAudioCtx();
  if (audioCtx.state === 'suspended') audioCtx.resume();
  navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,   // potlačenie ozveny
      noiseSuppression: true,   // potlačenie šumu
      autoGainControl: true
    }
  }).then(stream => {
    micStream = stream;
    micSource = audioCtx.createMediaStreamSource(stream);
    // Pripoj IBA na micAnalyser — NIE na destination → žiadna ozvena
    micSource.connect(micAnalyser);
  }).catch(() => {});
}

function stopMicAnalyser() {
  if (micSource) { try { micSource.disconnect(); } catch(e) {} micSource = null; }
  if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
  liveVolume = 0;
}

function connectTtsAudio(audioElement) {
  if (!audioCtx) initAudioCtx();
  // Zaisti že AudioContext beží — kľúčové pre prvé prehrávanie
  if (audioCtx.state === 'suspended') {
    audioCtx.resume();
  }
  try {
    const src = audioCtx.createMediaElementSource(audioElement);
    src.connect(ttsAnalyser);
    ttsAnalyser.connect(audioCtx.destination);
  } catch(e) {}
}

async function playTtsAudio(audio) {
  if (!audioCtx) initAudioCtx();
  // Počkaj kým je AudioContext skutočne aktívny
  if (audioCtx.state !== 'running') {
    await audioCtx.resume();
  }
  // 150ms buffer — dá AudioContextu čas sa rozbehnúť
  // a zabraňuje orezaniu prvej slabiky
  await new Promise(r => setTimeout(r, 150));
  return audio.play();
}

function readVolume() {
  if (!freqData) return 0;
  const analyser = isSpeaking ? ttsAnalyser : micAnalyser;
  if (!analyser) return 0;
  analyser.getByteFrequencyData(freqData);
  let sum = 0;
  for (let i = 0; i < freqData.length; i++) sum += freqData[i];
  return (sum / freqData.length) / 255;
}

// ═══════════════════════════════════════════════════════════════
// ═══════════════════════════════════════════════════════════════
// WAVE VISUALIZER — stĺpcový dot štýl
// Vertikálne stĺpce z malých bodiek, výška stĺpca = waveform
// Farby sa menia podľa stavu (rovnaké tinty ako particle face)
// idle:      nízke stĺpce, občasný spark výboj
// listening: reaguje na hlasitosť mikrofónu
// speaking:  reaguje na TTS audio
// thinking:  mierny dýchajúci pohyb
// ═══════════════════════════════════════════════════════════════
const waveCanvas = document.getElementById('wave');
const wctx = waveCanvas.getContext('2d');
let waveT = 0, smoothVol = 0;

// ── Farebná schéma — jedna oranžová pre všetky stavy ──
const WAVE_TINTS = {
  idle:      [252, 87, 5],     // oranžová #fd5606
  listening: [252, 87, 5],
  thinking:  [252, 87, 5],
  speaking:  [252, 87, 5],
};
// Aktuálna farba — plynulý prechod (lerp)
let waveTintR = 252, waveTintG = 87, waveTintB = 5;

// ── Stĺpce ──
const COLS      = 72;
const DOT_R     = 1.1;
const DOT_GAP   = 3.8;
const COL_PAD   = 0.08;

const colRnd    = new Float32Array(COLS);
const colRnd2   = new Float32Array(COLS);
const colBri    = new Float32Array(COLS);   // per-stĺpec brightness variácia
const colSmooth = new Float32Array(COLS);

for(let i = 0; i < COLS; i++) {
  colRnd[i]  = Math.random();
  colRnd2[i] = Math.random();
  colBri[i]  = 0.55 + Math.random() * 0.45;
  colSmooth[i] = 0;
}

// ── Idle sparks ──
let sparks = [];
let nextSparkTime = 0.6;

function resizeWave() {
  const rect = waveCanvas.getBoundingClientRect();
  waveCanvas.width  = rect.width  * window.devicePixelRatio;
  waveCanvas.height = rect.height * window.devicePixelRatio;
  wctx.scale(window.devicePixelRatio, window.devicePixelRatio);
}
resizeWave();
window.addEventListener('resize', resizeWave);

function drawWave() {
  const W = waveCanvas.getBoundingClientRect().width;
  const H = waveCanvas.getBoundingClientRect().height;
  wctx.clearRect(0, 0, W, H);

  const midY = H / 2;
  const maxHalf = H / 2 - 4;

  const rawVol = (state === 'speaking' || state === 'listening') ? readVolume() : 0;
  smoothVol += (rawVol > smoothVol ? 0.4 : 0.06) * (rawVol - smoothVol);

  const isIdle     = state === 'idle';
  const isThinking = state === 'thinking';
  const isActive   = state === 'listening' || state === 'speaking';

  // ── Plynulý prechod farby (lerp 5% za frame) ──
  const tc = WAVE_TINTS[state] || WAVE_TINTS.idle;
  const lerpSpd = 0.05;
  waveTintR += (tc[0] - waveTintR) * lerpSpd;
  waveTintG += (tc[1] - waveTintG) * lerpSpd;
  waveTintB += (tc[2] - waveTintB) * lerpSpd;

  const spd = isIdle ? 0.18 : isThinking ? 0.5 : 1.0 + smoothVol * 1.5;
  const alphaMax = isIdle ? 0.55 : isThinking ? 0.42 : 0.7 + smoothVol * 0.3;
  const follow = isIdle ? 0.06 : isActive ? 0.32 : 0.12;

  const usableW = W * (1 - 2 * COL_PAD);
  const startX  = W * COL_PAD;
  const colStep = usableW / (COLS - 1);

  // ── Idle sparks generovanie ──
  if (isIdle) {
    nextSparkTime -= 0.016;
    if (nextSparkTime <= 0) {
      const count = 2 + Math.floor(Math.random() * 3);
      const center = Math.floor(COLS * (0.15 + Math.random() * 0.7));
      for (let s = 0; s < count; s++) {
        const ci = Math.max(0, Math.min(COLS - 1, center + Math.floor((Math.random() - 0.5) * 6)));
        sparks.push({
          col: ci,
          startTime: waveT,
          duration: 0.22 + Math.random() * 0.28,
          intensity: 0.35 + Math.random() * 0.35
        });
      }
      nextSparkTime = 0.3 + Math.random() * 0.9;
    }
  } else {
    sparks = [];
  }

  const bR = Math.round(waveTintR);
  const bG = Math.round(waveTintG);
  const bB = Math.round(waveTintB);

  for(let i = 0; i < COLS; i++) {
    const nx = i / (COLS - 1);
    const r  = colRnd[i];
    const r2 = colRnd2[i];
    const x  = startX + i * colStep;

    const distC = Math.abs(nx - 0.5) * 2;
    const envelope = 0.25 + 0.75 * Math.pow(1 - distC, 1.5);

    const w1 = Math.sin(nx * Math.PI * 3.5  + waveT * 1.7 * spd + r  * 6.28);
    const w2 = Math.sin(nx * Math.PI * 6.8  + waveT * 2.5 * spd + r2 * 6.28) * 0.45;
    const w3 = Math.sin(nx * Math.PI * 11.0 + waveT * 3.2 * spd + r  * 3.14) * 0.2;
    const waveVal = (w1 + w2 + w3) * 0.5 + 0.5;

    let targetH;
    if (isIdle) {
      targetH = 0.03 + waveVal * 0.09 * envelope;
    } else if (isThinking) {
      targetH = 0.06 + waveVal * 0.15 * envelope;
    } else {
      targetH = (0.08 + waveVal * 0.45 + smoothVol * 0.47) * envelope;
    }

    let sparkBoost = 0;
    for (let s = sparks.length - 1; s >= 0; s--) {
      const sp = sparks[s];
      if (sp.col === i) {
        const elapsed = waveT - sp.startTime;
        if (elapsed > sp.duration) {
          sparks.splice(s, 1);
        } else {
          const prog = elapsed / sp.duration;
          const curve = prog < 0.2 ? prog / 0.2 : 1.0 - (prog - 0.2) / 0.8;
          sparkBoost = Math.max(sparkBoost, sp.intensity * curve);
        }
      }
    }
    targetH = Math.min(1, targetH + sparkBoost);

    const f = sparkBoost > 0 ? 0.5 : follow;
    colSmooth[i] += (targetH - colSmooth[i]) * f;
    const h = colSmooth[i];

    const halfPx = h * maxHalf;
    const dotsHalf = Math.max(1, Math.floor(halfPx / DOT_GAP));

    const edgeFade = Math.min(1.0, Math.min(nx, 1 - nx) * 12);
    const colBrightness = colBri[i];

    for(let d = 0; d <= dotsHalf; d++) {
      const dy = d * DOT_GAP;
      const dotPos = d / (dotsHalf + 1);
      const dotBri = (1.0 - dotPos * 0.55) * colBrightness;
      const sparkGlow = (sparkBoost > 0 && d < dotsHalf * 0.6) ? 0.15 : 0;
      const alpha = Math.min(1, alphaMax * dotBri * edgeFade + sparkGlow);

      const briMix = dotBri * 0.35;
      const cr = Math.min(255, Math.round(bR + (255 - bR) * briMix));
      const cg = Math.min(255, Math.round(bG + (255 - bG) * briMix));
      const cb = Math.min(255, Math.round(bB + (255 - bB) * briMix));

      wctx.fillStyle = `rgba(${cr},${cg},${cb},${alpha.toFixed(3)})`;

      if (d === 0) {
        wctx.beginPath();
        wctx.arc(x, midY, DOT_R, 0, Math.PI * 2);
        wctx.fill();
      } else {
        wctx.beginPath();
        wctx.arc(x, midY - dy, DOT_R, 0, Math.PI * 2);
        wctx.fill();
        wctx.beginPath();
        wctx.arc(x, midY + dy, DOT_R, 0, Math.PI * 2);
        wctx.fill();
      }
    }
  }

  waveT += 0.016;
  requestAnimationFrame(drawWave);
}
drawWave();

// ═══════════════════════════════════════════════════════════════
// PARTICLE FACE
// Funkcie:
//  - ~14 000 particles z face.png
//  - Menší, vertikálne centrovaný
//  - Žmurkanie (periodické Y-stlačenie)
//  - Explode/Implode animácia pri show/hide
//  - Skryté počas odpovede (speaking/thinking)
// ═══════════════════════════════════════════════════════════════
(function() {

const s3 = document.createElement('script');
s3.src = 'https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js';
s3.onload = initParticles;
document.head.appendChild(s3);

window._particleMode = 'visible';

function initParticles() {
  const container = document.getElementById('particle-canvas');
  const W = 390, H = 365;

  const renderer = new THREE.WebGLRenderer({ canvas: container, antialias: false, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setSize(W, H);
  renderer.setClearColor(0x000000, 0);

  const scene = new THREE.Scene();
  const cam = new THREE.OrthographicCamera(-W/2, W/2, H/2, -H/2, -100, 100);
  cam.position.z = 10;

  const vert = `
    attribute float aBri;
    attribute float aRnd;
    attribute vec3  aExpDir;
    uniform float uTime;
    uniform float uTurb;
    uniform float uSize;
    uniform float uExplode;
    uniform float uBlink;
    uniform float uScale;
    uniform vec2  uMouse;
    uniform float uRepel;
    varying float vB;
    varying float vDist;
    void main(){
      vB = aBri;
      vec3 p = position;
      float nx  = sin(p.x*0.04 + uTime*1.8 + aRnd*6.28) * uTurb;
      float ny  = cos(p.y*0.04 + uTime*1.4 + aRnd*6.28) * uTurb;
      float nx2 = sin(p.y*0.06 + uTime*1.1 + aRnd*3.14) * uTurb * 0.5;
      p.x += nx + nx2; p.y += ny;

      // Repel efekt — abstraktné odpudzovanie od kurzora
      // Každá particle má iný efektívny radius (aRnd variácia) → menej presný tvar
      vec2 toMouse = p.xy - uMouse;
      float mouseDist = length(toMouse);
      float repelRadius = 30.0 + aRnd * 20.0; // 30–50px, variabilný per particle
      if(mouseDist < repelRadius && uRepel > 0.0) {
        float strength = 1.0 - smoothstep(0.0, repelRadius, mouseDist);
        strength = pow(strength, 1.5); // mäkší falloff
        // Smer nie je priamo od myši — pridaj tangenciálny posun pre vír efekt
        vec2 radial = normalize(toMouse);
        vec2 tangent = vec2(-radial.y, radial.x);
        vec2 dir = normalize(radial + tangent * (aRnd - 0.5) * 1.2);
        float repelAmt = strength * uRepel * (50.0 + aRnd * 60.0);
        p.xy += dir * repelAmt;
      }

      float explodeDist = uExplode * 350.0 * (0.5 + aRnd * 1.5);
      p += aExpDir * explodeDist;
      p *= uScale;
      vDist = length(p.xy) / 195.0;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
      gl_PointSize = uSize * (0.4 + aBri * 0.9) * (1.0 - uExplode * 0.7) * uScale;
    }
  `;

  const frag = `
    uniform vec3  uTint;
    uniform float uAlpha;
    uniform float uExplode;
    varying float vB;
    varying float vDist;  // vzdialenosť od stredu (0=stred, 1=okraj)
    void main(){
      vec2 uv = gl_PointCoord - 0.5;
      if(length(uv) > 0.5) discard;
      // Fade out začína od 20% vzdialenosti, úplne zmizne na 55%
      // — particles zmiznú dávno pred okrajom canvas-u
      float edgeFade = 1.0 - smoothstep(0.2, 0.55, vDist) * uExplode;
      float a = smoothstep(0.5, 0.15, length(uv)) * (0.5 + vB*0.5) * uAlpha * edgeFade * 0.9;
      gl_FragColor = vec4(uTint * (0.6 + vB*0.4), a);
    }
  `;

  const uniforms = {
    uTime:    { value: 0 },
    uTurb:    { value: 3.5 },
    uSize:    { value: 2.2 },
    uExplode: { value: 0.0 },
    uBlink:   { value: 0.0 },
    uScale:   { value: 1.0 },
    uAlpha:   { value: 1.0 },
    uTint:    { value: new THREE.Vector3(1, 1, 1) },
    uMouse:   { value: new THREE.Vector2(9999, 9999) }, // pozícia myši v scene koordinátoch
    uRepel:   { value: 0.0 },  // 0=žiadny efekt, 1=plný repel
  };

  window._pUni = uniforms;

  function buildParticlesFromImg(img) {
    const oldPts = window._pts;

    const oc = document.createElement('canvas');
    oc.width = img.naturalWidth; oc.height = img.naturalHeight;
    const ctx2 = oc.getContext('2d');
    ctx2.drawImage(img, 0, 0);
    const data = ctx2.getImageData(0, 0, oc.width, oc.height).data;
    const iW = oc.width, iH = oc.height;
    const THRESH = 200;
    let count = 0;
    for(let i = 0; i < iW * iH; i++) if(data[i*4] > THRESH) count++;

    const pos    = new Float32Array(count * 3);
    const bri    = new Float32Array(count);
    const rnd    = new Float32Array(count);
    const expDir = new Float32Array(count * 3);

    const scale = Math.min(W / iW, H / iH) * 0.92 * 0.75;
    const ox = -iW * scale / 2;
    const oy =  iH * scale / 2 - 34;

    let j = 0;
    for(let i = 0; i < iW * iH; i++){
      const r = data[i*4];
      if(r <= THRESH) continue;
      const col = i % iW, row = Math.floor(i / iW);
      pos[j*3+0] = ox + col * scale;
      pos[j*3+1] = oy - row * scale;
      pos[j*3+2] = 0;
      bri[j] = r / 255;
      rnd[j] = Math.random();
      const theta = Math.random() * Math.PI * 2;
      const phi   = Math.acos(2 * Math.random() - 1);
      expDir[j*3+0] = Math.sin(phi) * Math.cos(theta);
      expDir[j*3+1] = Math.sin(phi) * Math.sin(theta);
      expDir[j*3+2] = Math.cos(phi);
      j++;
    }

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setAttribute('aBri',     new THREE.BufferAttribute(bri, 1));
    geo.setAttribute('aRnd',     new THREE.BufferAttribute(rnd, 1));
    geo.setAttribute('aExpDir',  new THREE.BufferAttribute(expDir, 3));

    const mat = new THREE.ShaderMaterial({
      vertexShader: vert, fragmentShader: frag, uniforms,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    });

    const pts = new THREE.Points(geo, mat);
    scene.add(pts);
    window._pts = pts;

    if(oldPts) {
      scene.remove(oldPts);
      if(oldPts.geometry) oldPts.geometry.dispose();
    }

    console.log('Dex particles: ' + count.toLocaleString());
  }

  window._isNeutralFace = true;
  window._buildParticles = buildParticlesFromImg;

  const img = new Image();
  img.crossOrigin = 'anonymous';
  img.onload = function() { buildParticlesFromImg(img); };
  img.onerror = () => console.warn('face_neutral.png nenajdeny');
  img.src = '/face/neutral.png';
  // ── Mouse / Touch repel ──
  // Konvertuj screen koordináty na OrthoCam scene koordináty
  function screenToScene(clientX, clientY) {
    const rect = container.getBoundingClientRect();
    const nx = (clientX - rect.left) / rect.width;   // 0–1
    const ny = (clientY - rect.top)  / rect.height;  // 0–1
    return {
      x: (nx - 0.5) * W,
      y: -(ny - 0.5) * H
    };
  }

  let _repelTarget = 0;
  let _mouseActive = false;

  container.addEventListener('mousemove', (e) => {
    if(window._particleMode !== 'visible') return;
    const sc = screenToScene(e.clientX, e.clientY);
    uniforms.uMouse.value.set(sc.x, sc.y);
    _mouseActive = true;
    _repelTarget = 1.0;
  });
  container.addEventListener('mouseleave', () => {
    _mouseActive = false;
    _repelTarget = 0.0;
    uniforms.uMouse.value.set(9999, 9999);
  });
  // Touch podpora
  container.addEventListener('touchmove', (e) => {
    if(window._particleMode !== 'visible') return;
    e.preventDefault();
    const t0 = e.touches[0];
    const sc = screenToScene(t0.clientX, t0.clientY);
    uniforms.uMouse.value.set(sc.x, sc.y);
    _mouseActive = true;
    _repelTarget = 1.0;
  }, { passive: false });
  container.addEventListener('touchend', () => {
    _mouseActive = false;
    _repelTarget = 0.0;
    uniforms.uMouse.value.set(9999, 9999);
  });

  let t = 0;
  let blinkTimer = 3.0 + Math.random() * 2;
  let blinkPhase = 0, blinkProgress = 0;
  const BLINK_CLOSE = 0.12, BLINK_OPEN = 0.18;
  let explodeProgress = 0, implodeProgress = 0;
  const EXPLODE_DUR = 0.66, IMPLODE_DUR = 0.90;
  const TINTS = {
    idle:      [0.99, 0.34, 0.02],  // oranžová #fd5606
    listening: [0.99, 0.34, 0.02],
    thinking:  [0.99, 0.34, 0.02],
    speaking:  [0.99, 0.34, 0.02],
  };

  (function loop() {
    requestAnimationFrame(loop);
    const dt = 0.016;
    t += dt;
    if(!window._pUni) { renderer.render(scene, cam); return; }
    const u = window._pUni;
    u.uTime.value = t;
    // Plynulý prechod repel — rýchlo nahor, pomalšie dolu
    const repelSpeed = _repelTarget > u.uRepel.value ? 0.15 : 0.06;
    u.uRepel.value += ((_repelTarget || 0) - u.uRepel.value) * repelSpeed;
    const mode = window._particleMode;

    if(mode === 'exploding') {
      explodeProgress = Math.min(explodeProgress + dt / EXPLODE_DUR, 1);
      u.uExplode.value = explodeProgress;
      u.uAlpha.value   = 1.0 - explodeProgress;
      if(explodeProgress >= 1) {
        window._particleMode = 'hidden';
        explodeProgress = 0;
      }
    }

    if(mode === 'imploding') {
      implodeProgress = Math.min(implodeProgress + dt / IMPLODE_DUR, 1);
      const ease = 1 - Math.pow(1 - implodeProgress, 3);
      u.uExplode.value = 1.0 - ease;
      u.uScale.value   = ease;
      u.uAlpha.value   = ease;
      if(implodeProgress >= 1) {
        window._particleMode = 'visible';
        implodeProgress = 0;
        u.uExplode.value = 0; u.uScale.value = 1; u.uAlpha.value = 1;
      }
    }

    if(mode === 'visible') {
      const s = window._dexState || 'idle';
      const isNeutral = window._isNeutralFace !== false;

      if(isNeutral) {
        // Neutral: pohyb particles zapnutý
        const targetTurb = (s==='listening'||s==='speaking') ? 8.0 : 3.5;
        u.uTurb.value += (targetTurb - u.uTurb.value) * 0.04;
        const tc = TINTS[s] || TINTS.idle;
        u.uTint.value.lerp(new THREE.Vector3(...tc), 0.05);
        // Žmurkanie
        blinkTimer -= dt;
        if(blinkTimer <= 0 && blinkPhase === 0) {
          blinkPhase = 1; blinkProgress = 0;
          blinkTimer = 3.0 + Math.random() * 3.0;
        }
        if(blinkPhase === 1) {
          blinkProgress += dt / BLINK_CLOSE;
          u.uBlink.value = Math.min(blinkProgress, 1);
          if(blinkProgress >= 1) { blinkPhase = 2; blinkProgress = 0; }
        } else if(blinkPhase === 2) {
          blinkProgress += dt / BLINK_OPEN;
          u.uBlink.value = 1.0 - Math.min(blinkProgress, 1);
          if(blinkProgress >= 1) { blinkPhase = 0; u.uBlink.value = 0; }
        }
        if(window._pts) window._pts.rotation.y = Math.sin(t*0.12) * 0.06;
      } else {
        // Non-neutral: jemný pohyb — tvar ostáva čitateľný
        const targetTurb = 1.5;
        u.uTurb.value += (targetTurb - u.uTurb.value) * 0.03;
        u.uBlink.value = 0.0;
        if(window._pts) window._pts.rotation.y = 0;
      }
    }

    renderer.render(scene, cam);
  })();
}

const _ss = window.setState;
window.setState = function(s){
  window._dexState = s;
  const shouldHide = (s === 'speaking' || s === 'thinking');
  const mode = window._particleMode;
  if(shouldHide && mode === 'visible') {
    window._particleMode = 'exploding';
  }
  // Implode riadi výhradne swapFace — setState ho nespúšťa
  if(_ss) _ss(s);
};

// ── Výmena tváre podľa emócie ──
// Explode → načítaj nový obrázok → rebuild particles → implode
// ── Preload emotion obrázkov pri štarte ──
const _emotionImgs = {};
['neutral','happy','sad','surprised','thinking','excited'].forEach(e => {
  const img = new Image();
  img.crossOrigin = 'anonymous';
  img.src = `/face/${e}.png`;
  _emotionImgs[e] = img;
});

window._swapFacePending = false; // swapFace čaká na hidden — blokuj setState implode

window.swapFace = function(newEmotion) {
  const doSwap = () => {
    window._swapFacePending = false;
    window._isNeutralFace = (newEmotion === 'neutral');
    const img = _emotionImgs[newEmotion] || _emotionImgs['neutral'];
    if(img && img.complete && img.naturalWidth > 0) {
      if(window._buildParticles) window._buildParticles(img);
    } else if(img) {
      img.onload = () => { if(window._buildParticles) window._buildParticles(img); };
    }
    requestAnimationFrame(() => {
      window._particleMode = 'imploding';
      if(window._pUni) {
        window._pUni.uScale.value = 0.01;
        window._pUni.uAlpha.value = 0;
        window._pUni.uExplode.value = 1.0;
      }
    });
  };

  const waitForHidden = () => {
    const mode = window._particleMode;
    if(mode === 'hidden') {
      doSwap();
    } else if(mode === 'exploding') {
      setTimeout(waitForHidden, 50);
    } else {
      window._swapFacePending = true;
      window._particleMode = 'exploding';
      setTimeout(waitForHidden, 750);
    }
  };

  waitForHidden();
};

})();
</script>

</body>
</html>
"""


# ── Main ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n🤖 Dex beží na http://localhost:{PORT}")
    print("   Ctrl+C = ukončiť\n")

    # Otvor prehliadač len lokálne (nie na Render)
    if not os.environ.get("RENDER"):
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()

    app.run(host="0.0.0.0", port=PORT, debug=False)
