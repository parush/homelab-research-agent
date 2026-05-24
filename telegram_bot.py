"""
Telegram bot for managing research topics and triggering the agent.

Setup:
  pip install python-telegram-bot
  Add to .env:
    TELEGRAM_BOT_TOKEN=...
    TELEGRAM_CHAT_ID=...

Commands:
  /list              — show current topics
  /add <topic>       — add a topic
  /remove <n>        — remove topic by number
  /clear             — remove all topics
  /run               — run the research agent on all topics
  /revise <n> <text> — re-research topic #n with extra focus
  /status            — check if agent is running
"""
import os
import sys
import subprocess
import threading
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

load_dotenv()

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))
TOPICS_FILE = "topics.txt"

agent_process = None


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def read_topics() -> list[str]:
    if not os.path.exists(TOPICS_FILE):
        return []
    return [
        line.strip()
        for line in open(TOPICS_FILE).readlines()
        if line.strip() and not line.startswith("#")
    ]


def write_topics(topics: list[str]):
    with open(TOPICS_FILE, "w") as f:
        f.write("# Managed via Telegram bot\n")
        for t in topics:
            f.write(t + "\n")


def guard(update: Update) -> bool:
    return update.effective_chat.id == ALLOWED_CHAT_ID


LOG_FILE = "agent.log"


def run_in_background(args: list[str], chat_id: int, bot, done_msg: str, loop):
    """Run a subprocess in a thread, stream output to agent.log, notify when done."""
    global agent_process

    def _run():
        global agent_process
        import asyncio

        with open(LOG_FILE, "a") as log:
            log.write(f"\n{'='*60}\n")
            log.write(f"Started: {' '.join(args)}\n")
            log.write(f"{'='*60}\n")

            agent_process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            # Stream output line by line to log file
            for line in agent_process.stdout:
                log.write(line)
                log.flush()

            agent_process.wait()
            code = agent_process.returncode
            log.write(f"\n--- Finished with exit code {code} ---\n")

        msg = done_msg if code == 0 else f"❌ Failed (exit {code}). Check agent.log for details."
        asyncio.run_coroutine_threadsafe(
            bot.send_message(chat_id=chat_id, text=msg), loop
        )

    threading.Thread(target=_run, daemon=True).start()


# ─── COMMANDS ────────────────────────────────────────────────────────────────

async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not guard(update): return
    topics = read_topics()
    if not topics:
        await update.message.reply_text("No topics yet. Use /add <topic>")
        return
    lines = "\n".join(f"{i+1}. {t}" for i, t in enumerate(topics))
    await update.message.reply_text(f"📋 Topics:\n\n{lines}")


async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not guard(update): return
    topic = " ".join(ctx.args).strip()
    if not topic:
        await update.message.reply_text("Usage: /add <topic>")
        return
    topics = read_topics()
    topics.append(topic)
    write_topics(topics)
    await update.message.reply_text(f"✅ Added: {topic}\n({len(topics)} total)")


async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not guard(update): return
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Usage: /remove <number>")
        return
    idx = int(ctx.args[0]) - 1
    topics = read_topics()
    if idx < 0 or idx >= len(topics):
        await update.message.reply_text(f"Invalid. You have {len(topics)} topic(s).")
        return
    removed = topics.pop(idx)
    write_topics(topics)
    await update.message.reply_text(f"🗑 Removed: {removed}")


async def cmd_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not guard(update): return
    write_topics([])
    await update.message.reply_text("🗑 All topics cleared.")


async def cmd_run(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not guard(update): return
    global agent_process
    if agent_process and agent_process.poll() is None:
        await update.message.reply_text("⚠️ Agent already running. Use /status.")
        return
    topics = read_topics()
    if not topics:
        await update.message.reply_text("No topics. Use /add <topic> first.")
        return

    # Parse optional topic numbers: /run 1 3 4
    selected = []
    if ctx.args:
        for arg in ctx.args:
            if arg.isdigit():
                idx = int(arg)
                if 1 <= idx <= len(topics):
                    selected.append(idx)
                else:
                    await update.message.reply_text(f"⚠️ Topic #{idx} doesn't exist. You have {len(topics)} topic(s).")
                    return
            else:
                await update.message.reply_text(f"⚠️ Invalid number: {arg}")
                return

    if selected:
        chosen = [topics[i-1] for i in selected]
        label = ", ".join(f"#{i} {topics[i-1]}" for i in selected)
        args = [sys.executable, "agent.py", "--topics", ",".join(str(i) for i in selected)]
    else:
        chosen = topics
        label = f"all {len(topics)} topic(s)"
        args = [sys.executable, "agent.py"]

    await update.message.reply_text(
        f"🤖 Running agent for {label}...\nI'll notify you when done."
    )
    import asyncio
    run_in_background(
        args,
        update.effective_chat.id,
        ctx.bot,
        "✅ Research complete! Check your Drive and email.",
        asyncio.get_event_loop(),
    )


async def cmd_revise(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not guard(update): return
    global agent_process

    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text(
            "Usage: /revise <number> <instruction>\n"
            "Example: /revise 2 focus more on open source alternatives"
        )
        return

    if not ctx.args[0].isdigit():
        await update.message.reply_text("First argument must be a topic number. Use /list to see them.")
        return

    if agent_process and agent_process.poll() is None:
        await update.message.reply_text("⚠️ Agent already running. Wait for it to finish.")
        return

    idx = ctx.args[0]
    instruction = " ".join(ctx.args[1:])
    topics = read_topics()
    topic_num = int(idx)

    if topic_num < 1 or topic_num > len(topics):
        await update.message.reply_text(f"Invalid number. You have {len(topics)} topic(s).")
        return

    topic_name = topics[topic_num - 1]
    await update.message.reply_text(
        f"🔄 Revising topic #{idx}: {topic_name}\n"
        f"Focus: {instruction}\n\n"
        "I'll notify you when the revised doc is uploaded."
    )
    import asyncio
    run_in_background(
        [sys.executable, "agent.py", "--revise", idx, instruction],
        update.effective_chat.id,
        ctx.bot,
        f"✅ Revision complete for: {topic_name}\nCheck your Drive for the new version.",
        asyncio.get_event_loop(),
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not guard(update): return
    global agent_process
    if agent_process is None:
        await update.message.reply_text("No agent has run yet.")
    elif agent_process.poll() is None:
        await update.message.reply_text("🔄 Agent is currently running...")
    else:
        await update.message.reply_text(f"Last run finished (exit {agent_process.returncode}).")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not guard(update): return
    await update.message.reply_text(
        "🤖 Research Assistant\n\n"
        "/list — show topics\n"
        "/add <topic> — add a topic\n"
        "/remove <n> — remove by number\n"
        "/clear — clear all\n"
        "/run — research all topics\n"
        "/run <n> <n> — research specific topics\n"
        "/revise <n> <instruction> — re-research topic #n with focus\n"
        "/status — check if agent is running"
    )


# ─── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN not set in .env")
        exit(1)
    if not ALLOWED_CHAT_ID:
        print("❌ TELEGRAM_CHAT_ID not set in .env")
        exit(1)

    from telegram.request import HTTPXRequest
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .request(HTTPXRequest(connect_timeout=30, read_timeout=30))
        .build()
    )
    for name, handler in [
        ("list", cmd_list), ("add", cmd_add), ("remove", cmd_remove),
        ("clear", cmd_clear), ("run", cmd_run), ("revise", cmd_revise),
        ("status", cmd_status), ("help", cmd_help), ("start", cmd_help),
    ]:
        app.add_handler(CommandHandler(name, handler))

    print("🤖 Telegram bot running...")
    app.run_polling()
