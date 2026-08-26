"""Everything that changes between your laptop and the server lives here.

Nothing else in the codebase reads os.environ directly. One place to look
when something is misconfigured.
"""
import os

from dotenv import load_dotenv

load_dotenv()

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")

# Our test number. This WhatsApp Business Account also carries the restaurant
# bot's number, so the webhook receives traffic for both. Anything not addressed
# to this id is somebody else's conversation and must not be stored here.
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
OFFICER_NUMBER = os.environ.get("OFFICER_NUMBER", "")

# Set on Render, absent on your laptop. Its presence is the only switch between
# Postgres and a local SQLite file.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DATABASE_PATH = os.environ.get("DATABASE_PATH", "aapdaai.db")

GRAPH_API = "https://graph.facebook.com/v21.0"

# --- the AI layer -----------------------------------------------------------
# Optional by design. Without a key the rules-based parser still runs and the
# service still works - it just reads less. Nothing here may become a hard
# dependency, because it is the part most likely to be down on demo day.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_API = "https://api.groq.com/openai/v1"
GROQ_TEXT_MODEL = os.environ.get("GROQ_TEXT_MODEL", "llama-3.3-70b-versatile")
GROQ_AUDIO_MODEL = os.environ.get("GROQ_AUDIO_MODEL", "whisper-large-v3")
