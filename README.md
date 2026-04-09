# Personal Learning Coach

**Pattern: Stateful Personalization** — a persistent user profile accumulates across sessions. Each session loads the profile and injects it into the system prompt; the agent calls `update_profile()` whenever it learns something new. The next session opens with a personalised recap drawn from what was saved. The conversation thread is ephemeral; the profile is the only thing that persists.

## Architecture

```
Session 1                              Session 2
    │                                      │
    ▼                                      ▼
Load profile (empty)              Load profile (populated)
    │                                      │
    ▼                                      ▼
Inject into system prompt         Inject into system prompt
"New learner — no profile yet"    "Name: Alex. Last session: covered
    │                              Python variables and loops..."
    ▼                                      │
Agent teaches, asks questions     Agent skips covered topics,
    │                              builds on prior session
    ▼                                      │
update_profile("name", "Alex")    update_profile("topic_covered",
update_profile("add_goal",         "python:list comprehensions")
  "learn Python for data science")       │
update_profile("topic_level",           ▼
  "python:beginner")              Save updated profile
    │
    ▼
Record session, save profile
profiles/alex.json
```

## Profile schema

```json
{
  "user_id": "alex",
  "name": "Alex",
  "learning_goals": ["learn Python for data science", "pass AWS Solutions Architect"],
  "topics": {
    "python": {
      "level": "beginner",
      "covered": ["variables", "loops", "functions", "list comprehensions"]
    }
  },
  "preferred_style": "example-heavy",
  "notes": ["responds well to real-world analogies", "struggles with recursion"],
  "session_count": 3,
  "last_session": "2026-03-31",
  "sessions": [
    {
      "date": "2026-03-29",
      "topics_touched": ["python"],
      "summary": "Covered: python. 8 exchanges."
    }
  ]
}
```

## Tool: `update_profile(field, value)`

| field | effect |
|-------|--------|
| `name` | Sets learner name |
| `preferred_style` | Sets `concise` / `detailed` / `example-heavy` / `balanced` |
| `add_goal` | Appends a learning goal |
| `add_note` | Appends a coach observation |
| `topic_level` | Sets level: `"python:intermediate"` |
| `topic_covered` | Marks a topic done: `"python:list comprehensions"` |

The agent calls this tool during the session; each update is logged so progress is visible.

## Files

```
20-learning-coach/
├── profiles/              # Per-user JSON profiles (one file per user_id)
│   └── .gitkeep
├── prompts/
│   └── system_prompt.txt  # {PROFILE} placeholder replaced at runtime
├── profile_store.py       # load(), save(), to_prompt_context(), record_session()
├── agent.py               # SessionContext, CoachSession, CLI entrypoint
├── app.py                 # Gradio web UI
├── requirements.txt
├── .env.example           # Copy to .env and fill in your values
├── .env                   # Not committed — your local secrets
└── README.md
```

## Setup

```bash
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
az login
```

Copy `.env.example` to `.env` and fill in your Azure AI Foundry project endpoint and model deployment name:

```
PROJECT_ENDPOINT=https://<your-resource>.services.ai.azure.com/api/projects/<your-project>
MODEL_DEPLOYMENT_NAME=gpt-4o
```

## Running

**CLI:**
```bash
python agent.py
```

**Web UI:**
```bash
python app.py
```
Then open `http://localhost:7860`.

Enter a user ID (or leave blank for `default`). On first run the agent introduces itself and builds a profile from scratch. On subsequent runs it personalises the greeting and skips already-covered material.

## Example session progression

**Session 1** (new user):
```
Coach: Hi! I'm your personal learning coach. I don't have a profile for you yet —
       what's your name and what would you like to learn?

You: I'm Alex. I want to learn Python from scratch for data science.

[profile] Profile updated: name = Alex
[profile] Profile updated: added goal 'learn Python for data science'
[profile] Profile updated: python level = beginner

Coach: Great to meet you, Alex! Let's start with variables...
```

**Session 2** (returning user, profile loaded):
```
Coach: Welcome back, Alex! Last session we covered Python variables and loops —
       you're making solid progress toward your data science goal. Ready to tackle
       functions today, or would you prefer to review anything first?
```

## Key implementation details

- **Profile injection at construction time** — `system_prompt.replace("{PROFILE}", context)` bakes the profile into the agent's instructions before it is created. No tool call is needed for the agent to "know" the learner.
- **`update_profile()` is append-only** — goals and notes are appended, never overwritten. Only `name`, `preferred_style`, and `topic_level` replace existing values.
- **Thread is ephemeral** — the Azure AI Foundry thread is created fresh each session and discarded at the end. Only the JSON profile crosses session boundaries, keeping memory bounded.
- **Atomic writes** — profiles are saved via a temp-file-then-rename pattern so a crash mid-write cannot corrupt a profile.
- **Session record** — at exit, `record_session()` appends a summary and increments `session_count`. The last 10 sessions are retained; older entries are trimmed.
- **Multi-user** — each user ID gets its own profile file. Run with different IDs to see completely different personalised experiences from the same agent.

## Extending to a database backend

Replace `profile_store.py` with any persistent store — `agent.py` and `app.py` call only `load()`, `save()`, `to_prompt_context()`, and `record_session()`:

```python
# CosmosDB example
def load(user_id: str) -> dict:
    return cosmos_container.read_item(user_id, partition_key=user_id)

def save(profile: dict):
    cosmos_container.upsert_item(profile)
```
