# Homelab Research Agent

An autonomous research pipeline controlled from Telegram. Add topics from your phone, trigger research runs, and get formatted reports uploaded to Google Drive with an email notification when done.

## Architecture

```
Telegram Bot (telegram_bot.py)
    │
    ├── /add, /remove, /list, /clear  →  topics.txt
    │
    ├── /run [n n ...]  ──────────────────────────────────────────────────┐
    │                                                                     │
    └── /revise <n> <instruction>  ────────────────────────────────────┐ │
                                                                       ▼ ▼
                                                               agent.py pipeline
                                                                       │
                                              ┌────────────────────────┤
                                              │  Per-topic:            │
                                              │  [Researcher Agent]    │
                                              │    └─ web search       │
                                              │  [Writer Agent]        │
                                              │    └─ markdown report  │
                                              │    └─ .drafts/<slug>.md│
                                              └────────────────────────┤
                                                                       │
                                                         [Publisher Agent]
                                                           ├─ Google Drive upload
                                                           └─ Gmail notification
```

## Prerequisites

- Python 3.12+
- A Google account
- A Telegram account
- An Anthropic API key

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/parush/homelab-research-agent.git
cd homelab-research-agent

python -m venv .venv
source .venv/bin/activate

pip install "crewai[anthropic]" python-telegram-bot google-auth-oauthlib \
            google-auth-httplib2 markdown requests python-dotenv
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```
ANTHROPIC_API_KEY=       # from console.anthropic.com/settings/keys
TELEGRAM_BOT_TOKEN=      # from @BotFather (see below)
TELEGRAM_CHAT_ID=        # your Telegram user ID (see below)
OPENAI_API_KEY=dummy     # prevents CrewAI from defaulting to OpenAI
```

### 3. Set up Telegram bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot` and follow the prompts — copy the **token** into `TELEGRAM_BOT_TOKEN`
3. Start your new bot (search for it, hit Start)
4. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` in your browser
5. Send any message to your bot, refresh the URL — find `"id"` under `"chat"` and copy it into `TELEGRAM_CHAT_ID`

### 4. Set up Google Drive & Gmail

**Enable APIs:**
1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a project
2. Enable **Google Drive API**: APIs & Services → Library → search "Google Drive API" → Enable
3. Enable **Gmail API**: same steps for "Gmail API"

**Create OAuth credentials:**
1. APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID
2. Application type: **Desktop App** → name it anything → Create
3. Download the JSON → save as `credentials.json` in the project folder

**Authenticate:**
```bash
python get_token.py
```
It prints a URL — open it in any browser, sign in with your Google account, copy the code shown, paste it back in the terminal. This saves `google_creds.json` which auto-refreshes forever.

### 5. Add topics

Edit `topics.txt` — one topic per line, `#` for comments:

```
# Research topics
AWS Neptune and graph database trends
Advances in Kafka Streams and event-driven architecture
AI agents and multi-agent frameworks in production
```

### 6. Run

**Option A — directly:**
```bash
python agent.py               # all topics
python agent.py --topics 1,3  # specific topics by number
python agent.py --revise 2 "focus more on open source tools"
```

**Option B — via Telegram bot:**
```bash
nohup python telegram_bot.py >> bot.log 2>&1 &
```

Then control everything from your phone.

---

## Telegram Commands

| Command | Description |
|---|---|
| `/list` | Show current topics |
| `/add <topic>` | Add a topic |
| `/remove <n>` | Remove topic by number |
| `/clear` | Clear all topics |
| `/run` | Research all topics |
| `/run 1 3` | Research specific topics by number |
| `/revise <n> <instruction>` | Re-research a topic with a new focus |
| `/status` | Check if agent is currently running |

---

## Output

Reports are uploaded to a folder named `!Personal-Research-Assistant` in your Google Drive, versioned per run:

```
!Personal-Research-Assistant/
├── aws-neptune-updates v1
├── aws-neptune-updates v2
└── kafka-streams-architecture v1
```

You also receive a Gmail notification with links to all uploaded docs.

---

## Logs

```bash
tail -f agent.log   # research pipeline output
tail -f bot.log     # telegram bot output
```

---

## Project Structure

```
homelab-research-agent/
├── agent.py           # main research pipeline
├── telegram_bot.py    # telegram bot controller
├── get_token.py       # one-time Google OAuth setup
├── topics.txt         # your research topics
├── .env.example       # environment variable template
├── .drafts/           # temporary report files (gitignored)
├── agent.log          # pipeline logs (gitignored)
└── bot.log            # bot logs (gitignored)
```

---

## Security Notes

Never commit these files — they are in `.gitignore`:
- `.env` — contains API keys
- `google_creds.json` — Google OAuth tokens
- `credentials.json` — Google OAuth client secret
