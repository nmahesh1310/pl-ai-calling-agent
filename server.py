import os, json, asyncio, logging, sys, base64, requests, io, struct, time
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn

# ================= ENV =================
load_dotenv()
PORT = int(os.getenv("PORT", 10000))
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")

# ================= AUDIO =================
SAMPLE_RATE = 16000
MIN_CHUNK_SIZE = 3200
SPEECH_THRESHOLD = 520

SILENCE_CHUNKS = 10
MIN_SPEECH_CHUNKS = 6
POST_TTS_DELAY = 0.6

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True
)
log = logging.getLogger("voicebot")

# ================= APP =================
app = FastAPI()

# ================= SCRIPT =================
PITCH_1 = (
    "Hi, my name is Neeraja, calling from Rupeek. "
    "You have a pre approved personal loan at zero interest."
)

PITCH_2 = (
    "The process is completely digital with no paperwork. "
    "You can receive instant disbursal in just sixty seconds. "
    "This is a limited time offer. Are you interested?"
)

STEPS = [
    "First, download the Rupeek app from the Play Store.",
    "Next, complete your KYC using Aadhaar.",
    "Then select your loan amount and confirm disbursal."
]

# ================= INTENT =================
def classify(text: str):
    t = text.lower().strip()

    if not t or len(t) < 2:
        return "EMPTY"

    if any(x in t for x in ["uh", "um", "hmm", "okay", "right"]):
        return "FILLER"

    if any(x in t for x in ["yes", "yeah", "interested", "sure", "ok"]):
        return "YES"

    if any(x in t for x in ["no", "not interested", "not able", "can't", "later"]):
        return "NO"

    if any(x in t for x in ["process", "steps", "procedure", "how does it work"]):
        return "PROCESS"

    if any(x in t for x in ["limit", "amount", "how much"]):
        return "LIMIT"

    if any(x in t for x in ["interest", "emi", "rate"]):
        return "INTEREST"

    if any(x in t for x in ["done", "completed", "finished"]):
        return "DONE"

    if any(x in t for x in ["agent", "human", "representative"]):
        return "HUMAN"

    return "UNKNOWN"

# ================= AUDIO =================
def is_speech(pcm):
    energy = sum(abs(int.from_bytes(pcm[i:i+2], "little", signed=True))
                 for i in range(0, len(pcm)-1, 2))
    return (energy / max(len(pcm)//2, 1)) > SPEECH_THRESHOLD

def pcm_to_wav(pcm):
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + len(pcm)))
    buf.write(b"WAVEfmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE,
                           SAMPLE_RATE * 2, 2, 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", len(pcm)))
    buf.write(pcm)
    return buf.getvalue()

def stt_safe(pcm):
    try:
        r = requests.post(
            "https://api.sarvam.ai/speech-to-text",
            headers={"api-subscription-key": SARVAM_API_KEY},
            files={"file": ("audio.wav", pcm_to_wav(pcm), "audio/wav")},
            data={"language_code": "en-IN"},
            timeout=10
        )
        return r.json().get("transcript", "").strip() if r.status_code == 200 else ""
    except:
        return ""

def tts(text):
    r = requests.post(
        "https://api.sarvam.ai/text-to-speech",
        headers={
            "api-subscription-key": SARVAM_API_KEY,
            "Content-Type": "application/json"
        },
        json={
            "text": text,
            "target_language_code": "en-IN",
            "speech_sample_rate": "16000"
        },
        timeout=10
    )
    r.raise_for_status()
    return base64.b64decode(r.json()["audios"][0])

async def speak(ws, text, session):
    log.info(f"🗣 BOT → {text}")
    session["bot_speaking"] = True
    pcm = await asyncio.to_thread(tts, text)
    for i in range(0, len(pcm), MIN_CHUNK_SIZE):
        await ws.send_text(json.dumps({
            "event": "media",
            "media": {"payload": base64.b64encode(pcm[i:i+MIN_CHUNK_SIZE]).decode()}
        }))
    await asyncio.sleep(POST_TTS_DELAY)
    session["bot_speaking"] = False

# ================= WS =================
@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    await ws.accept()
    log.info("🎧 Call connected")

    session = {
        "started": False,
        "bot_speaking": False
    }

    buf, speech = b"", b""
    silence_chunks, speech_chunks = 0, 0

    try:
        while True:
            try:
                msg = await ws.receive()
            except RuntimeError:
                log.info("🔌 Client disconnected safely")
                break

            if "text" not in msg:
                continue

            data = json.loads(msg["text"])

            if data.get("event") == "start" and not session["started"]:
                await speak(ws, PITCH_1, session)
                await speak(ws, PITCH_2, session)
                session["started"] = True
                continue

            if data.get("event") != "media" or session["bot_speaking"]:
                continue

            buf += base64.b64decode(data["media"]["payload"])

            if len(buf) < MIN_CHUNK_SIZE:
                continue

            frame, buf = buf[:MIN_CHUNK_SIZE], buf[MIN_CHUNK_SIZE:]

            if is_speech(frame):
                speech += frame
                speech_chunks += 1
                silence_chunks = 0
            else:
                silence_chunks += 1

            if speech_chunks < MIN_SPEECH_CHUNKS and silence_chunks < SILENCE_CHUNKS:
                continue

            text = await asyncio.to_thread(stt_safe, speech)
            speech, speech_chunks, silence_chunks = b"", 0, 0

            if not text:
                continue

            intent = classify(text)
            log.info(f"🗣 USER → {text} | intent={intent}")

            if intent == "YES":
                await speak(ws, "Great. Let me quickly explain the process.", session)
                for step in STEPS:
                    await speak(ws, step, session)
                continue

            if intent == "PROCESS":
                await speak(ws,
                    "The process is simple. Download the app, complete KYC, "
                    "select the amount, and get instant disbursal.",
                    session
                )
                continue

            if intent == "LIMIT":
                await speak(ws,
                    "Your approved loan limit is already available inside the Rupeek app.",
                    session
                )
                continue

            if intent == "INTEREST":
                await speak(ws,
                    "This is a zero interest offer if repaid on time. "
                    "In case of delay, standard interest applies as shown in the app.",
                    session
                )
                continue

            if intent == "NO":
                await speak(ws,
                    "No problem at all. Thank you for your time. Have a great day.",
                    session
                )
                await ws.close()
                break

            if intent == "DONE":
                await speak(ws,
                    "Great! Whenever you’re ready, just open the Rupeek app and check your pre-approved loan limit.",
                    session
                )
                await ws.close()
                break

            if intent == "HUMAN":
                await speak(ws,
                    "Sure, I will connect you to a representative.",
                    session
                )
                await ws.close()
                break

            if intent in ["FILLER", "UNKNOWN"]:
                await speak(ws,
                    "No worries. Take your time. Let me know if you want to understand the process or loan amount.",
                    session
                )

    except WebSocketDisconnect:
        log.info("🔌 Call disconnected")

# ================= START =================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
