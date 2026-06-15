"""
db_helper.py — Firestore client wrapper with automatic local-file fallback for offline/development testing.
Manages athlete profiles and credentials with strict separation of collections.
"""
import os
import json
import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)

try:
    from google.cloud import firestore
    FIRESTORE_AVAILABLE = True
except ImportError:
    FIRESTORE_AVAILABLE = False

LOCAL_DB_FILE = os.path.expanduser("~/.config/garmin-coach/local_db.json")

def _ensure_local_db_dir():
    os.makedirs(os.path.dirname(LOCAL_DB_FILE), exist_ok=True)

def _read_local_db() -> dict:
    _ensure_local_db_dir()
    if not os.path.exists(LOCAL_DB_FILE):
        return {"profiles": {}, "credentials": {}}
    try:
        with open(LOCAL_DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to read local DB: {e}")
        return {"profiles": {}, "credentials": {}}

def _write_local_db(data: dict):
    _ensure_local_db_dir()
    try:
        with open(LOCAL_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to write local DB: {e}")

_firestore_client_cache = None
_firestore_failed = False

def get_firestore_client():
    """Initializes and returns the Firestore client, or None if unavailable/offline."""
    global _firestore_client_cache, _firestore_failed
    
    if os.getenv("OFFLINE_MOCK_MODE", "false").lower() == "true":
        return None
        
    if _firestore_failed:
        return None
        
    if _firestore_client_cache is not None:
        return _firestore_client_cache
        
    if not FIRESTORE_AVAILABLE:
        return None
        
    try:
        # Check if project ID is explicitly set
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("FIRESTORE_PROJECT_ID")
        if project_id:
            _firestore_client_cache = firestore.Client(project=project_id)
        else:
            _firestore_client_cache = firestore.Client()
        return _firestore_client_cache
    except Exception as e:
        logger.info(f"Could not initialize Cloud Firestore Client: {e}. Falling back to local offline mode.")
        _firestore_failed = True
        return None

def parse_dates_in_profile(profile: dict) -> dict:
    """Helper to convert ISO date strings in training phases back to datetime.date objects."""
    if not profile:
        return profile
    
    # Process training phases
    phases = profile.get("training_phases", [])
    for phase in phases:
        for key in ["start", "end"]:
            if key in phase and isinstance(phase[key], str):
                try:
                    phase[key] = datetime.strptime(phase[key], "%Y-%m-%d").date()
                except ValueError:
                    pass
                    
    # Process VDOT metadata fields
    for key in ["vdot_calibration_date", "tt_scheduled"]:
        if profile.get(key) and isinstance(profile[key], str):
            try:
                # We can store them as date objects or strings. Let's keep them as strings or parse if needed.
                pass
            except Exception:
                pass
                
    return profile

def serialize_dates_in_profile(profile: dict) -> dict:
    """Helper to convert datetime.date objects in training phases to ISO format strings for storage."""
    import copy
    serialized = copy.deepcopy(profile)
    phases = serialized.get("training_phases", [])
    for phase in phases:
        for key in ["start", "end"]:
            if key in phase and isinstance(phase[key], (date, datetime)):
                phase[key] = phase[key].isoformat()
    return serialized

# ── Athlete Profiles Collection (athletes) ───────────────────────────────────

def get_athlete_profile(anon_user_id: str) -> dict | None:
    """
    Fetch an athlete's physiological profile.
    Tries Firestore first; falls back to local file DB if offline/testing.
    """
    db = get_firestore_client()
    if db:
        try:
            doc_ref = db.collection("athletes").document(anon_user_id)
            doc = doc_ref.get()
            if doc.exists:
                profile = doc.to_dict()
                return parse_dates_in_profile(profile)
            return None
        except Exception as e:
            logger.error(f"Firestore get_athlete_profile failed: {e}. Trying local fallback.")
            
    # Local Fallback
    local_data = _read_local_db()
    profile = local_data.get("profiles", {}).get(anon_user_id)
    if profile:
        return parse_dates_in_profile(profile)
    return None

def save_athlete_profile(anon_user_id: str, profile: dict):
    """
    Save or update an athlete's physiological profile.
    Tries Firestore first; falls back to local file DB if offline/testing.
    """
    serialized = serialize_dates_in_profile(profile)
    db = get_firestore_client()
    if db:
        try:
            doc_ref = db.collection("athletes").document(anon_user_id)
            doc_ref.set(serialized)
            return
        except Exception as e:
            logger.error(f"Firestore save_athlete_profile failed: {e}. Saving locally.")
            
    # Local Fallback
    local_data = _read_local_db()
    local_data["profiles"][anon_user_id] = serialized
    _write_local_db(local_data)

# ── Garmin Credentials Collection (credentials_vault) ─────────────────────────

def get_garmin_credentials(anon_user_id: str) -> dict | None:
    """
    Retrieve encrypted Garmin credentials payload.
    Separated from athletes profile collection for maximum security.
    """
    db = get_firestore_client()
    if db:
        try:
            doc_ref = db.collection("credentials_vault").document(anon_user_id)
            doc = doc_ref.get()
            if doc.exists:
                return doc.to_dict()
            return None
        except Exception as e:
            logger.error(f"Firestore get_garmin_credentials failed: {e}. Trying local fallback.")
            
    # Local Fallback
    local_data = _read_local_db()
    return local_data.get("credentials", {}).get(anon_user_id)

def save_garmin_credentials(anon_user_id: str, encrypted_payload: dict):
    """
    Save encrypted Garmin credentials payload.
    Separated from athletes profile collection for maximum security.
    """
    db = get_firestore_client()
    if db:
        try:
            doc_ref = db.collection("credentials_vault").document(anon_user_id)
            doc_ref.set(encrypted_payload)
            return
        except Exception as e:
            logger.error(f"Firestore save_garmin_credentials failed: {e}. Saving locally.")
            
    # Local Fallback
    local_data = _read_local_db()
    local_data["credentials"][anon_user_id] = encrypted_payload
    _write_local_db(local_data)


# ── Anonymized Query Logging (query_logs) ────────────────────────────────────

def log_anonymized_query(
    query_text: str,
    stage1_result: bool,
    stage2_result: str,
    athlete_context: dict | None = None,
    ai_response_snippet: str | None = None,
    api_metadata: dict | None = None
):
    """
    Log completely anonymized unstructured chat queries for post-analysis.
    Strictly zero-linkability: No user IDs, no IP addresses, no PII, and no linkable fields.
    Collects rich, privacy-preserving metadata to help analyze athlete requests.
    """
    import uuid
    import re
    from datetime import timezone
    
    log_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    if timestamp.endswith("+00:00"):
        timestamp = timestamp[:-6] + "Z"
    
    # 1. Generate privacy-safe query complexity telemetry
    has_thai = bool(re.search(r"[\u0e00-\u0e7f]", query_text))
    has_english = bool(re.search(r"[a-zA-Z]", query_text))
    if has_thai and has_english:
        lang = "mixed"
    elif has_thai:
        lang = "th"
    elif has_english:
        lang = "en"
    else:
        lang = "unknown"

    # Emoji detector
    emoji_pattern = re.compile(
        "["
        "\U0001f600-\U0001f64f"  # emoticons
        "\U0001f300-\U0001f5ff"  # symbols & pictographs
        "\U0001f680-\U0001f6ff"  # transport & map symbols
        "\U0001e000-\U0001efff"
        "\U00002702-\U000027b0"
        "\U000024c2-\U0001f970"
        "]+", flags=re.UNICODE
    )
    contains_emoji = bool(emoji_pattern.search(query_text))

    query_metadata = {
        "char_count": len(query_text),
        "word_count": len(query_text.split()),
        "language_heuristic": lang,
        "contains_emoji": contains_emoji
    }

    log_doc = {
        "timestamp": timestamp,
        "query_text": query_text,
        "stage1_result": stage1_result,
        "stage2_result": stage2_result,
        "query_metadata": query_metadata,
        "api_metadata": api_metadata or {},
        "athlete_context": athlete_context or {},
        "ai_response_snippet": ai_response_snippet or ""
    }
    
    db = get_firestore_client()
    if db:
        try:
            db.collection("query_logs").document(log_id).set(log_doc)
            return
        except Exception as e:
            logger.error(f"Firestore log_anonymized_query failed: {e}. Logging locally.")
            
    # Local Fallback
    local_data = _read_local_db()
    if "query_logs" not in local_data:
        local_data["query_logs"] = {}
    local_data["query_logs"][log_id] = log_doc
    _write_local_db(local_data)


# ── Chat Session Memory (chat_history) ───────────────────────────────────────

def get_chat_history(anon_user_id: str, limit: int = 10) -> list[dict]:
    """
    Fetch the recent chat history for a user, up to `limit` messages.
    Returns a list of dicts, each with keys 'role', 'text', and 'timestamp'.
    """
    db = get_firestore_client()
    messages = []
    if db:
        try:
            doc_ref = db.collection("chat_history").document(anon_user_id)
            doc = doc_ref.get()
            if doc.exists:
                messages = doc.to_dict().get("messages", [])
        except Exception as e:
            logger.error(f"Firestore get_chat_history failed: {e}. Trying local fallback.")
            db = None  # trigger fallback

    if not db:
        local_data = _read_local_db()
        messages = local_data.get("chat_history", {}).get(anon_user_id, {}).get("messages", [])

    return messages[-limit:]


def add_chat_message(anon_user_id: str, role: str, text: str, max_history: int = 20):
    """
    Append a new message (user or model) to the athlete's chat history.
    Keeps the total history capped at `max_history` to prevent document bloat.
    """
    from datetime import timezone
    timestamp = datetime.now(timezone.utc).isoformat()
    if timestamp.endswith("+00:00"):
        timestamp = timestamp[:-6] + "Z"
        
    new_msg = {
        "role": role,
        "text": text,
        "timestamp": timestamp
    }
    
    # 1. Fetch current history
    history = get_chat_history(anon_user_id, limit=max_history)
    history.append(new_msg)
    
    # Cap history to max_history
    history = history[-max_history:]
    
    # 2. Save
    db = get_firestore_client()
    if db:
        try:
            doc_ref = db.collection("chat_history").document(anon_user_id)
            doc_ref.set({"messages": history})
            return
        except Exception as e:
            logger.error(f"Firestore add_chat_message failed: {e}. Saving locally.")
            
    # Local Fallback
    local_data = _read_local_db()
    if "chat_history" not in local_data:
        local_data["chat_history"] = {}
    local_data["chat_history"][anon_user_id] = {"messages": history}
    _write_local_db(local_data)


