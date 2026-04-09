"""
Gradio web UI for the Personal Learning Coach.

Run:  python app.py
Then open http://localhost:7860 in your browser.
"""

import logging

import gradio as gr

from agent import CoachSession

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _session_info(session: CoachSession) -> str:
    p = session.ctx.profile
    name = p.get("name") or session.user_id
    count = p.get("session_count", 0)
    goals = ", ".join(p.get("learning_goals", [])) or "none yet"
    return f"**User:** {name} &nbsp;|&nbsp; **Sessions completed:** {count} &nbsp;|&nbsp; **Goals:** {goals}"


# ── Event handlers ────────────────────────────────────────────────────────────

def start_session(user_id: str, state: dict):
    # Close any existing session cleanly
    if state.get("session"):
        state["session"].close()
        state["session"] = None

    uid = user_id.strip() or "default"
    session = CoachSession(uid)
    session.start()
    state["session"] = session

    is_new = session.ctx.profile["session_count"] == 0
    if is_new:
        note = "New session started. Say hello to begin!"
    else:
        name = session.ctx.profile.get("name", uid)
        count = session.ctx.profile["session_count"]
        note = f"Session started. Welcome back, **{name}**! (Session {count + 1})"

    history = [[None, note]]
    info = _session_info(session)

    return (
        history,
        state,
        info,
        gr.update(interactive=True),   # msg_input
        gr.update(interactive=True),   # send_btn
        gr.update(interactive=True),   # end_btn
        gr.update(interactive=False),  # start_btn
        gr.update(interactive=False),  # user_id_input
    )


def chat(message: str, history: list, state: dict):
    session: CoachSession | None = state.get("session")
    if not session:
        return history + [[message, "Please start a session first."]], state, ""

    response = session.send(message)
    return history + [[message, response]], state, ""


def end_session(history: list, state: dict):
    session: CoachSession | None = state.get("session")
    if not session:
        # Nothing to end — just re-enable start controls
        return (
            history, state, "",
            gr.update(interactive=True),   # start_btn
            gr.update(interactive=True),   # user_id_input
            gr.update(interactive=False),  # msg_input
            gr.update(interactive=False),  # send_btn
            gr.update(interactive=False),  # end_btn
        )

    summary = session.close()
    state["session"] = None

    if summary.get("saved"):
        note = (
            f"**Session saved.**  "
            f"Topics: {summary['topics']}  |  Exchanges: {summary['turns']}"
        )
    else:
        note = "**Session ended.** No exchanges recorded."

    history = history + [[None, note]]

    return (
        history,
        state,
        "",   # clear session info
        gr.update(interactive=True),   # start_btn
        gr.update(interactive=True),   # user_id_input
        gr.update(interactive=False),  # msg_input
        gr.update(interactive=False),  # send_btn
        gr.update(interactive=False),  # end_btn
    )


# ── UI layout ─────────────────────────────────────────────────────────────────

with gr.Blocks(title="Personal Learning Coach", theme=gr.themes.Soft()) as demo:
    gr.Markdown(
        "# Personal Learning Coach\n"
        "AI-powered adaptive learning — your profile persists across sessions."
    )

    state = gr.State({})

    with gr.Row():
        user_id_input = gr.Textbox(
            label="User ID",
            placeholder="Enter your ID (blank = 'default')",
            scale=4,
        )
        start_btn = gr.Button("Start Session", variant="primary", scale=1)

    session_info = gr.Markdown("")

    chatbot = gr.Chatbot(label="Learning Session", height=480)

    with gr.Row():
        msg_input = gr.Textbox(
            label="Your message",
            placeholder="Type here and press Enter…",
            interactive=False,
            scale=5,
        )
        send_btn = gr.Button("Send", interactive=False, scale=1)

    end_btn = gr.Button("End Session & Save", interactive=False, variant="stop")

    # ── Wire up events ────────────────────────────────────────────────────────

    start_btn.click(
        start_session,
        inputs=[user_id_input, state],
        outputs=[chatbot, state, session_info, msg_input, send_btn, end_btn, start_btn, user_id_input],
    )

    send_btn.click(
        chat,
        inputs=[msg_input, chatbot, state],
        outputs=[chatbot, state, msg_input],
    )
    msg_input.submit(
        chat,
        inputs=[msg_input, chatbot, state],
        outputs=[chatbot, state, msg_input],
    )

    end_btn.click(
        end_session,
        inputs=[chatbot, state],
        outputs=[chatbot, state, session_info, start_btn, user_id_input, msg_input, send_btn, end_btn],
    )


if __name__ == "__main__":
    demo.launch()
