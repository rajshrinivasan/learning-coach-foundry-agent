"""
Profile store — loads, saves, and updates learner profiles from JSON files.

Each user has a profile at profiles/{user_id}.json.
The profile is the single source of truth across all sessions.
"""

import json
import logging
import os
import tempfile
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

PROFILES_DIR = Path(os.environ.get("PROFILES_DIR", "")) or Path(__file__).parent / "profiles"

DEFAULT_PROFILE: dict = {
    "user_id": "",
    "name": "",
    "learning_goals": [],
    "topics": {},           # {subject: {level: str, covered: [str]}}
    "preferred_style": "balanced",   # concise | detailed | example-heavy | balanced
    "notes": [],            # observations about the learner (capped at 20)
    "session_count": 0,
    "last_session": None,
    "sessions": [],         # [{date, topics_touched, summary}], last 10 only
}

_MAX_NOTES = 20


def _ensure_profiles_dir():
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def load(user_id: str) -> dict:
    _ensure_profiles_dir()
    path = PROFILES_DIR / f"{user_id}.json"
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load profile for '%s': %s — using blank profile", user_id, e)
    # Return a deep copy so callers can't mutate DEFAULT_PROFILE
    profile = {
        **DEFAULT_PROFILE,
        "user_id": user_id,
        "learning_goals": [],
        "topics": {},
        "notes": [],
        "sessions": [],
    }
    return profile


def save(profile: dict):
    """Atomically write profile to disk (write-to-temp + rename)."""
    _ensure_profiles_dir()
    user_id = profile["user_id"]
    path = PROFILES_DIR / f"{user_id}.json"
    try:
        fd, tmp_path = tempfile.mkstemp(dir=PROFILES_DIR, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(profile, f, indent=2)
        os.replace(tmp_path, path)  # atomic on POSIX; best-effort on Windows
        log.debug("Profile saved: %s", path)
    except OSError as e:
        log.error("Failed to save profile for '%s': %s", user_id, e)
        raise


def to_prompt_context(profile: dict) -> str:
    """Render the profile as a readable block for injection into the system prompt."""
    if not profile.get("name") and profile["session_count"] == 0:
        return "New learner — no profile yet. Introduce yourself and ask for their name and learning goals."

    lines = []
    if profile.get("name"):
        lines.append(f"Name: {profile['name']}")
    lines.append(f"Sessions completed: {profile['session_count']}")
    if profile.get("last_session"):
        lines.append(f"Last session: {profile['last_session']}")
    if profile.get("preferred_style"):
        lines.append(f"Preferred style: {profile['preferred_style']}")
    if profile.get("learning_goals"):
        lines.append(f"Goals: {', '.join(profile['learning_goals'])}")

    if profile.get("topics"):
        lines.append("\nTopics:")
        for subject, data in profile["topics"].items():
            level = data.get("level", "unknown")
            covered = data.get("covered", [])
            covered_str = f" — covered: {', '.join(covered)}" if covered else ""
            lines.append(f"  {subject}: {level}{covered_str}")

    if profile.get("notes"):
        lines.append("\nCoach notes:")
        for note in profile["notes"][-5:]:   # last 5 notes only
            lines.append(f"  - {note}")

    if profile.get("sessions"):
        last = profile["sessions"][-1]
        lines.append(f"\nLast session summary: {last.get('summary', 'No summary')}")

    return "\n".join(lines)


def record_session(profile: dict, topics_touched: list[str], summary: str):
    """Append a session record and increment the counter."""
    profile["session_count"] = profile.get("session_count", 0) + 1
    profile["last_session"] = date.today().isoformat()
    profile.setdefault("sessions", []).append({
        "date": date.today().isoformat(),
        "topics_touched": topics_touched,
        "summary": summary,
    })
    # Keep last 10 sessions only
    if len(profile["sessions"]) > 10:
        profile["sessions"] = profile["sessions"][-10:]
