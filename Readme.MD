# Homelab Research Agent

An autonomous research pipeline that reads topics from a file, researches each one using web search, writes structured reports, and uploads them to Google Drive — controllable from Telegram.

## Architecture

```
topics.txt
    ↓
[Researcher agents] — one per topic, web search
    ↓
[Writer agents] — one per topic, structured markdown
    ↓
.drafts/<slug>.md
    ↓
[Publisher agent] — uploads to Google Drive, sends email
```

## Setup

### 1. Install dependencies
```bash
python -m venv .venv
source .venv/bin/activate
pip install crewai"[anthropic]" python-telegram-bot google-auth-oauthlib google-auth-httplib2 markdown requests python-dotenv
```

### 2. Configure environment
Copy the example and fill in your keys:
```bash
cp .env.example .env
```

### 3. Authenticate with Google
```bash
python get_token.py
```
Follow the prompts — opens a browser URL, paste back the code. Run once; token auto-refreshes.

### 4. Add topics
```bash
# topics.txt — one per line, # for comments
AWS Neptune and graph database trends
Advances in Kafka Streams and event-driven architecture
```

### 5. Run
```bash
# Directly
python agent.py

# Specific topics by number
python agent.py --topics 1,3

# Revise a topic
python agent.py --revise 2 "focus more on open source alternatives"

# Via Telegram bot
python telegram_bot.py
```

## Telegram Commands

| Command | Description |
|---|---|
| `/list` | Show current topics |
| `/add <topic>` | Add a topic |
| `/remove <n>` | Remove topic by number |
| `/clear` | Clear all topics |
| `/run` | Research all topics |
| `/run 1 3` | Research specific topics |
| `/revise <n> <instruction>` | Re-research topic with focus |
| `/status` | Check if agent is running |

## Environment Variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key (console.anthropic.com) |
| `TELEGRAM_BOT_TOKEN` | From @BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram user ID |

## Google Setup

1. [Google Cloud Console](https://console.cloud.google.com/) → create project
2. Enable **Google Drive API** and **Gmail API**
3. Create OAuth 2.0 credentials (Desktop App) → download as `credentials.json`
4. Run `python get_token.py`

## Output

Reports are uploaded to a `!Personal-Research-Assistant` folder in Google Drive, named:
```
<topic-slug> v1
<topic-slug> v2   ← versioned on each run
```

## Logs

```bash
tail -f agent.log   # agent runs
tail -f bot.log     # telegram bot
```
