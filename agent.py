"""
Personal Learning Coach
Pattern: Stateful Personalization

State persists across sessions in profiles/{user_id}.json.
At startup: profile is loaded and injected into the system prompt.
During session: agent calls update_profile() to record what it learns.
At shutdown: session is recorded; profile is saved back to disk.

Session 1: Agent asks for name, goals, current level.
Session 2: Agent opens with a personalised recap and continues from where it left off.
Session N: Progressively personalised — different content, style, pacing per learner.

The profile is the only state that persists — the conversation thread is ephemeral.
"""

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import FunctionTool, ToolSet
from dotenv import load_dotenv

import profile_store

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv(Path(__file__).parent / ".env")

log = logging.getLogger(__name__)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}\n"
            f"Check your .env file."
        )
    return value


PROJECT_ENDPOINT = _require_env("PROJECT_ENDPOINT")
MODEL_DEPLOYMENT_NAME = _require_env("MODEL_DEPLOYMENT_NAME")

RAW_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system_prompt.txt").read_text()


# ── Session context ───────────────────────────────────────────────────────────

@dataclass
class SessionContext:
    """Holds mutable session state: profile and topics touched this session."""

    profile: dict
    session_topics: list[str] = field(default_factory=list)

    def _split_subject_value(self, value: str) -> tuple[str, str] | None:
        """Split 'subject:value' and return (subject, rest), or None on bad format."""
        parts = value.split(":", 1)
        if len(parts) != 2 or not parts[0].strip():
            return None
        return parts[0].strip(), parts[1].strip()

    def _track_subject(self, subject: str):
        self.session_topics.append(subject)

    def update_profile(self, field: str, value: str) -> str:
        """
        Update a field in the learner's persistent profile.
        Call this whenever you learn something new about the learner.

        Args:
            field: Which field to update. One of:
                   "name"             — learner's name (string)
                   "preferred_style"  — concise | detailed | example-heavy | balanced
                   "add_goal"         — append a learning goal (string)
                   "add_note"         — append a coach observation (string)
                   "topic_level"      — set level for a subject, format: "subject:level"
                                        e.g. "python:intermediate"
                   "topic_covered"    — mark a topic as covered, format: "subject:topic"
                                        e.g. "python:list comprehensions"
            value: The value to set or append.

        Returns:
            Confirmation string describing what was updated.
        """
        p = self.profile

        if field == "name":
            p["name"] = value
            return f"Profile updated: name = {value}"

        elif field == "preferred_style":
            p["preferred_style"] = value
            return f"Profile updated: preferred_style = {value}"

        elif field == "add_goal":
            goals = p.setdefault("learning_goals", [])
            if value not in goals:
                goals.append(value)
            return f"Profile updated: added goal '{value}'"

        elif field == "add_note":
            notes = p.setdefault("notes", [])
            notes.append(value)
            if len(notes) > 20:
                p["notes"] = notes[-20:]
            return f"Profile updated: added note '{value}'"

        elif field == "topic_level":
            parsed = self._split_subject_value(value)
            if not parsed:
                return "Error: topic_level value must be 'subject:level', e.g. 'python:intermediate'"
            subject, level = parsed
            p.setdefault("topics", {}).setdefault(subject, {})["level"] = level
            self._track_subject(subject)
            return f"Profile updated: {subject} level = {level}"

        elif field == "topic_covered":
            parsed = self._split_subject_value(value)
            if not parsed:
                return "Error: topic_covered value must be 'subject:topic', e.g. 'python:loops'"
            subject, topic = parsed
            covered = p.setdefault("topics", {}).setdefault(subject, {}).setdefault("covered", [])
            if topic not in covered:
                covered.append(topic)
            self._track_subject(subject)
            return f"Profile updated: marked '{topic}' as covered under {subject}"

        else:
            return (
                f"Unknown field '{field}'. Valid fields: "
                "name, preferred_style, add_goal, add_note, topic_level, topic_covered"
            )

    def get_profile_summary(self) -> str:
        """
        Return the current learner profile as a JSON summary.
        Use this to check what has been covered before planning the session.

        Returns:
            JSON string with the full learner profile.
        """
        return json.dumps(self.profile, indent=2)


# ── Coach session ─────────────────────────────────────────────────────────────

class CoachSession:
    """Manages a single learning coach session lifecycle."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.ctx = SessionContext(profile=profile_store.load(user_id))
        self.turn = 0
        self._client = AIProjectClient(
            endpoint=PROJECT_ENDPOINT,
            credential=DefaultAzureCredential(),
        )
        self._agent = None
        self._thread = None

    def start(self):
        """Create the Azure agent and a fresh thread. Call once before send()."""
        context = profile_store.to_prompt_context(self.ctx.profile)
        system_prompt = RAW_SYSTEM_PROMPT.replace("{PROFILE}", context)

        toolset = ToolSet()
        toolset.add(FunctionTool({self.ctx.update_profile, self.ctx.get_profile_summary}))

        self._agent = self._client.agents.create_agent(
            model=MODEL_DEPLOYMENT_NAME,
            name="learning-coach",
            instructions=system_prompt,
            tools=toolset.definitions,
        )
        self._thread = self._client.agents.threads.create()
        log.debug("Agent %s created, thread %s created", self._agent.id, self._thread.id)

    def send(self, user_message: str) -> str:
        """Send a user message and return the assistant's text response."""
        self.turn += 1
        self._client.agents.messages.create(
            thread_id=self._thread.id, role="user", content=user_message
        )
        run = self._client.agents.runs.create(
            thread_id=self._thread.id, agent_id=self._agent.id
        )

        while run.status in ("queued", "in_progress", "requires_action"):
            if self._has_tool_calls(run):
                outputs = self._dispatch(run)
                run = self._client.agents.runs.submit_tool_outputs(
                    thread_id=self._thread.id, run_id=run.id, tool_outputs=outputs
                )
            else:
                run = self._client.agents.runs.get(
                    thread_id=self._thread.id, run_id=run.id
                )

        if run.status == "failed":
            log.error("Run failed: %s", run.last_error)
            return f"[Error: run failed — {run.last_error}]"

        msgs = self._client.agents.messages.list(thread_id=self._thread.id)
        for msg in msgs:
            if msg.role == "assistant":
                for c in msg.content:
                    if hasattr(c, "text"):
                        return c.text.value
        return "[No response received]"

    def close(self) -> dict:
        """Save profile, clean up Azure resources. Returns session summary dict."""
        summary: dict = {}
        if self.turn > 0:
            # dict.fromkeys preserves insertion order while deduplicating
            topics = list(dict.fromkeys(self.ctx.session_topics))
            topics_str = ", ".join(topics) if topics else "general discussion"
            session_summary = f"Covered: {topics_str}. {self.turn} exchanges."
            profile_store.record_session(self.ctx.profile, topics, session_summary)
            profile_store.save(self.ctx.profile)
            summary = {"topics": topics_str, "turns": self.turn, "saved": True}
            log.debug("Session recorded: %s", session_summary)

        if self._agent:
            try:
                self._client.agents.delete_agent(self._agent.id)
            except Exception as e:
                log.warning("Failed to delete agent: %s", e)
        self._client.close()
        return summary

    def _has_tool_calls(self, run) -> bool:
        return (
            run.status == "requires_action"
            and run.required_action is not None
            and run.required_action.submit_tool_outputs is not None
        )

    def _dispatch(self, run) -> list[dict]:
        fn_map = {
            "update_profile": self.ctx.update_profile,
            "get_profile_summary": self.ctx.get_profile_summary,
        }
        outputs = []
        for call in run.required_action.submit_tool_outputs.tool_calls:
            fn_name = call.function.name
            fn_args = json.loads(call.function.arguments)
            result = fn_map[fn_name](**fn_args)
            log.info("[tool:%s] %s", fn_name, result)
            outputs.append({"tool_call_id": call.id, "output": result})
        return outputs


# ── CLI entrypoint ────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    user_id = input("User ID (press Enter for 'default'): ").strip() or "default"
    session = CoachSession(user_id)

    try:
        session.start()
        is_new = session.ctx.profile["session_count"] == 0
        print("\nPersonal Learning Coach")
        if not is_new:
            name = session.ctx.profile.get("name", user_id)
            count = session.ctx.profile["session_count"]
            print(f"Welcome back, {name}! (Session {count + 1})")
        print("Type 'exit' to end the session.\n")

        while True:
            user_input = input("You: ").strip()
            if user_input.lower() in {"exit", "quit", "bye"}:
                break
            if not user_input:
                continue
            response = session.send(user_input)
            print(f"\nCoach: {response}\n")

    finally:
        summary = session.close()
        if summary.get("saved"):
            print(f"\nSession saved. Profile: profiles/{user_id}.json")
            print(f"Topics this session: {summary['topics']}")


if __name__ == "__main__":
    main()
