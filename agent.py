import os
import re
import json
import logging
import datetime
import urllib.parse
import urllib.request
import requests
from dotenv import load_dotenv
from crewai import Agent, Task, Crew, Process
from crewai.tools import tool

load_dotenv()

# ─── LOGGING SETUP ───────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("agent.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger("agent")

if not os.environ.get("ANTHROPIC_API_KEY"):
    log.error("ANTHROPIC_API_KEY not set in .env")
    exit(1)

# claude-sonnet-4-20250514 is the best balance of intelligence and speed for agentic tasks
crew_llm = "anthropic/claude-haiku-4-5-20251001"   # bulk runs — fast and cheap
revision_llm = "anthropic/claude-sonnet-4-6"  # revisions — higher quality
TOPICS_FILE = "topics.txt"
DRIVE_FOLDER_NAME = "!Personal-Research-Assistant"


# ─── TOPIC HELPERS ───────────────────────────────────────────────────────────

def load_topics(path: str) -> list[str]:
    if not os.path.exists(path):
        log.error(f"'{path}' not found.")
        exit(1)
    topics = [
        line.strip()
        for line in open(path).readlines()
        if line.strip() and not line.startswith("#")
    ]
    if not topics:
        log.error(f"'{path}' is empty.")
        exit(1)
    log.info(f"Loaded {len(topics)} topic(s) from {path}")
    return topics


def topic_to_filename(topic: str) -> str:
    """'AWS Neptune Updates 2025' → 'aws-neptune-updates-2025'"""
    slug = re.sub(r"[^\w\s-]", "", topic.lower())
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    return slug[:60]


# ─── GOOGLE OAUTH ─────────────────────────────────────────────────────────────

def get_google_token() -> str:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    import datetime as _dt

    creds_path = "google_creds.json"
    if not os.path.exists(creds_path):
        raise RuntimeError("google_creds.json not found. Run: python get_token.py")

    data = json.load(open(creds_path))

    # Parse stored expiry if present
    expiry = None
    if data.get("token_expiry"):
        expiry = _dt.datetime.fromisoformat(data["token_expiry"])

    creds = Credentials(
        token=data["token"],
        refresh_token=data["refresh_token"],
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        token_uri=data["token_uri"],
        expiry=expiry,
    )

    # Refresh if expired or expiring within 5 minutes
    needs_refresh = (
        not creds.token
        or creds.expired
        or (creds.expiry and creds.expiry - _dt.datetime.utcnow() < _dt.timedelta(minutes=5))
    )
    if needs_refresh:
        log.info("Refreshing Google token...")
        creds.refresh(Request())
        data["token"] = creds.token
        data["token_expiry"] = creds.expiry.isoformat() if creds.expiry else None
        json.dump(data, open(creds_path, "w"), indent=2)
        log.info("Token refreshed.")

    return creds.token


# ─── DRIVE HELPERS ───────────────────────────────────────────────────────────

def get_or_create_folder(token: str) -> str:
    query = (
        f"name='{DRIVE_FOLDER_NAME}' and "
        "mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    resp = requests.get(
        "https://www.googleapis.com/drive/v3/files",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": query, "fields": "files(id,name)"},
        timeout=15,
    )
    resp.raise_for_status()
    files = resp.json().get("files", [])
    if files:
        return files[0]["id"]

    resp = requests.post(
        "https://www.googleapis.com/drive/v3/files",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"name": DRIVE_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def get_next_version(token: str, folder_id: str, base_name: str) -> int:
    """Count existing files with base_name in folder to get next version number."""
    query = f"name contains '{base_name}' and '{folder_id}' in parents and trashed=false"
    resp = requests.get(
        "https://www.googleapis.com/drive/v3/files",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": query, "fields": "files(id,name)"},
        timeout=15,
    )
    resp.raise_for_status()
    return len(resp.json().get("files", [])) + 1


def upload_html_to_drive(token: str, folder_id: str, title: str, html: str) -> tuple[str, str]:
    """Upload HTML as a Google Doc. Returns (file_id, link)."""
    metadata = json.dumps({
        "name": title,
        "mimeType": "application/vnd.google-apps.document",
        "parents": [folder_id],
    })
    boundary = "digest_boundary"
    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{metadata}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: text/html; charset=UTF-8\r\n\r\n"
        f"{html}\r\n"
        f"--{boundary}--"
    ).encode("utf-8")

    resp = requests.post(
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        data=body,
        timeout=60,
    )
    resp.raise_for_status()
    file_id = resp.json()["id"]

    # Make readable by anyone with link
    requests.post(
        f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"role": "reader", "type": "anyone"},
        timeout=30,
    )
    return file_id, f"https://docs.google.com/document/d/{file_id}/edit"


# ─── IMAGE GENERATION ────────────────────────────────────────────────────────

def generate_image_for_prompt(prompt: str) -> str | None:
    """Generate an inline image from a prompt via Pollinations.ai."""
    try:
        encoded = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=900&height=500&nologo=true"
        log.info(f"Generating image: {prompt[:70]}...")
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        import base64
        b64 = base64.b64encode(resp.content).decode()
        return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        log.warning(f"Image generation skipped: {e}")
        return None


def inject_images(markdown_content: str) -> str:
    """
    Replace ![IMAGE: <prompt>] placeholders with actual generated images.
    The writer agent inserts these where visuals genuinely help.
    """
    import re as _re
    def replace(m):
        prompt = m.group(1).strip()
        uri = generate_image_for_prompt(prompt)
        return f"![{prompt}]({uri})" if uri else f"*[Image unavailable: {prompt}]*"
    return _re.sub(r"!\[IMAGE:\s*([^\]]+)\]", replace, markdown_content)


# ─── MARKDOWN → HTML ─────────────────────────────────────────────────────────

def build_html(topic: str, markdown_content: str) -> str:
    """Convert markdown to styled HTML."""
    # Strip any leftover image placeholders
    import re as _re
    markdown_content = _re.sub(r"!\[IMAGE:[^\]]*\]", "", markdown_content)
    try:
        import markdown as md
        html_body = md.markdown(markdown_content, extensions=["tables", "fenced_code"])
    except ImportError:
        html_body = f"<pre>{markdown_content}</pre>"

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
  body {{ font-family: Arial, sans-serif; max-width: 860px; margin: auto; padding: 32px 24px; color: #222; }}
  h1 {{ color: #1a1a2e; font-size: 2rem; border-bottom: 2px solid #1a1a2e; padding-bottom: 8px; }}
  h2 {{ color: #16213e; font-size: 1.4rem; border-bottom: 1px solid #ddd; padding-bottom: 6px; margin-top: 32px; }}
  h3 {{ color: #0f3460; font-size: 1.1rem; }}
  p {{ line-height: 1.75; }} a {{ color: #3a7bd5; }}
  code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }}
  pre {{ background: #f4f4f4; padding: 14px; border-radius: 6px; overflow-x: auto; }}
  table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
  td, th {{ border: 1px solid #ddd; padding: 10px 12px; text-align: left; }}
  th {{ background: #f0f0f0; font-weight: bold; }}
  blockquote {{ border-left: 4px solid #ccc; margin: 0; padding: 8px 16px; color: #555; }}
  img {{ max-width: 100%; border-radius: 8px; margin: 16px 0; display: block; }}
  .meta {{ color: #888; font-size: 0.85rem; margin-bottom: 24px; }}
</style>
</head><body>
<p class="meta">Generated: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
{html_body}
</body></html>"""


# ─── CREWAI TOOLS ────────────────────────────────────────────────────────────

@tool("Web Search Tool")
def native_search_tool(query: str) -> str:
    """Search the web for information on a given topic."""
    try:
        log.info(f"Searching: {query}")
        url = f"https://duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8")
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
        clean = "\n\n".join([re.sub(r"<[^>]+>", "", s).strip() for s in snippets[:5]])
        return clean or "No results found."
    except Exception as e:
        return f"Search unavailable: {e}"


DRAFT_DIR = ".drafts"
os.makedirs(DRAFT_DIR, exist_ok=True)


@tool("Save Draft Report")
def save_draft_tool(slug: str, topic: str, markdown_content: str) -> str:
    """
    Save a markdown report to disk so the publisher can upload it to Google Drive.
    Args:
        slug: filename-safe topic identifier (e.g. "aws-neptune-updates")
        topic: full topic name (e.g. "AWS Neptune Updates")
        markdown_content: the complete markdown report text
    Returns confirmation with the file path.
    """
    path = os.path.join(DRAFT_DIR, f"{slug}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"<!-- topic: {topic} -->\n")
        f.write(markdown_content)
    log.info(f"Draft saved: {path} ({len(markdown_content)} chars)")
    return f"Draft saved: {path}"


@tool("Upload Draft to Google Drive")
def upload_draft_tool(slug: str) -> str:
    """
    Read a previously saved draft and upload it to Google Drive as a formatted doc.
    Args:
        slug: the topic slug used when the draft was saved
    Returns the shareable Google Drive link.
    """
    path = os.path.join(DRAFT_DIR, f"{slug}.md")
    if not os.path.exists(path):
        return f"Error: no draft found for slug '{slug}' at {path}"

    raw = open(path, encoding="utf-8").read()
    lines_raw = raw.split("\n")
    if lines_raw[0].startswith("<!-- topic:"):
        topic = lines_raw[0].replace("<!-- topic:", "").replace("-->", "").strip()
        markdown_content = "\n".join(lines_raw[1:])
    else:
        topic = slug
        markdown_content = raw

    try:
        log.info(f"Uploading to Drive: {topic}")
        token = get_google_token()
        folder_id = get_or_create_folder(token)
        version = get_next_version(token, folder_id, slug)
        title = f"{slug} v{version}"
        html = build_html(topic, markdown_content)
        file_id, link = upload_html_to_drive(token, folder_id, title, html)
        log.info(f"Drive upload complete: {title} → {link}")
        os.remove(path)
        return link
    except Exception as e:
        return f"Upload failed ({e}). Draft kept at {path}."



@tool("Send Email Notification")
def send_email_notification_tool(body: str) -> str:
    """Send an email to yourself with the research digest links."""
    log.info("Sending email notification...")
    try:
        import base64
        from email.mime.text import MIMEText

        token = get_google_token()
        profile = requests.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        profile.raise_for_status()
        email_address = profile.json()["emailAddress"]

        msg = MIMEText(body)
        msg["Subject"] = f"📰 Research Digest — {datetime.date.today()}"
        msg["To"] = email_address
        msg["From"] = email_address

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        resp = requests.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"raw": raw},
            timeout=30,
        )
        resp.raise_for_status()
        return f"Email sent to {email_address}."
    except Exception as e:
        return f"Email failed: {e}"


# ─── REVISION ────────────────────────────────────────────────────────────────

def revise_topic(topic_index: int, instruction: str) -> str:
    """
    Re-research and reupload a single topic with additional instruction.
    Called by telegram_bot.py for /revise command.
    """
    topics = load_topics(TOPICS_FILE)
    if topic_index < 1 or topic_index > len(topics):
        return f"Invalid topic number. You have {len(topics)} topic(s)."

    topic = topics[topic_index - 1]
    slug = topic_to_filename(topic)
    log.info(f"Revising topic: {topic} | {instruction}")

    researcher = Agent(
        role="Research Specialist",
        goal=f"Research '{topic}' with focus: {instruction}",
        backstory="You are a senior analyst finding the most current, accurate information.",
        tools=[native_search_tool],
        llm=revision_llm,
        verbose=True,
        allow_delegation=False,
    )
    research_task = Task(
        description=(
            f"Research '{topic}' thoroughly.\n"
            f"Pay special attention to: {instruction}\n"
            "Find recent developments, key facts, and notable insights."
        ),
        expected_output=f"Detailed research on '{topic}' focused on: {instruction}",
        agent=researcher,
    )

    writer = Agent(
        role="Content Writer",
        goal="Write a clean markdown report",
        backstory="You are a technology journalist making complex data scannable.",
        tools=[],
        llm=revision_llm,
        verbose=True,
        allow_delegation=False,
    )
    write_task = Task(
        description=(
            f"Write a markdown report on '{topic}'.\n"
            f"Special focus: {instruction}\n"
            "Include executive summary then detailed sections."
        ),
        expected_output="A complete markdown report.",
        agent=writer,
        context=[research_task],
    )

    publisher = Agent(
        role="Operations Agent",
        goal="Upload the revised report to Google Drive",
        backstory="You handle publishing.",
        tools=[save_draft_tool, upload_draft_tool, send_email_notification_tool],
        llm=revision_llm,
        verbose=True,
        allow_delegation=False,
    )
    publish_task = Task(
        description=(
            f"Upload the report using the Upload tool.\n"
            f"First call the Save Draft tool with slug='{slug}', topic='{topic}', and the full markdown_content.\n"
            f"Then call the Upload Draft tool with slug='{slug}' to upload it to Google Drive.\n"
            "Then send an email notification with the Drive link."
        ),
        expected_output="Drive link confirmation.",
        agent=publisher,
        context=[write_task],
    )

    crew = Crew(
        agents=[researcher, writer, publisher],
        tasks=[research_task, write_task, publish_task],
        process=Process.sequential,
    )
    return str(crew.kickoff())


# ─── CREW BUILDER ────────────────────────────────────────────────────────────

def build_crew(topics: list[str]) -> Crew:
    agents, research_tasks = [], []

    for i, topic in enumerate(topics):
        agent = Agent(
            role=f"Research Specialist #{i+1}",
            goal=f"Research everything relevant and recent about: {topic}",
            backstory="You are a senior analyst finding the most current, accurate information.",
            tools=[native_search_tool],
            llm=crew_llm,
            verbose=True,
            allow_delegation=False,
        )
        task = Task(
            description=(
                f"Research '{topic}' thoroughly.\n"
                "Find recent developments, key facts, trends, and notable insights."
            ),
            expected_output=f"Detailed research summary on '{topic}'.",
            agent=agent,
        )
        agents.append(agent)
        research_tasks.append(task)

    topics_meta = [{"slug": topic_to_filename(t), "name": t} for t in topics]

    # One writer agent + task per topic to avoid output truncation
    write_tasks = []
    for i, (meta, research_task) in enumerate(zip(topics_meta, research_tasks)):
        slug, topic = meta["slug"], meta["name"]
        w_agent = Agent(
            role=f"Content Writer #{i+1}",
            goal=f"Write a complete markdown report on: {topic}",
            backstory="You are a world-class technology journalist. You write thorough, well-structured reports.",
            tools=[],
            llm=crew_llm,
            verbose=True,
            allow_delegation=False,
        )
        w_task = Task(
            description=(
                f"Write a complete markdown research report on: {topic}\n\n"
                "Structure:\n"
                "# <Topic Title>\n"
                "## Executive Summary\n"
                "<2-3 paragraph summary>\n\n"
                "## <Section 1 heading>\n"
                "<content>\n\n"
                "## <Section 2 heading>\n"
                "<content>\n\n"
                "... and so on for all key areas.\n\n"
                "Rules:\n"
                "- Use ## for sections, ### for subsections\n"
                "- Use bullet points for lists\n"
                "- Use code blocks for code snippets\n"
                "- Write the COMPLETE report — do not truncate or summarise at the end\n"
                "- No image placeholders, no meta-commentary about the report itself"
            ),
            expected_output=f"A complete, well-structured markdown report on {topic}.",
            agent=w_agent,
            context=[research_task],
        )

        def make_callback(s, t):
            def save_draft_callback(output):
                import re as _re
                log.info(f"Writer finished for topic: {t}")
                text = str(output.raw) if hasattr(output, "raw") else str(output)
                text = _re.sub(r"---(?:BEGIN|END)[^\r\n]*---[\r\n]?", "", text).strip()
                path = os.path.join(DRAFT_DIR, f"{s}.md")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"<!-- topic: {t} -->\n{text}")
                log.info(f"Draft saved: {path} ({len(text)} chars)")
            return save_draft_callback


        w_task.callback = make_callback(slug, topic)
        write_tasks.append((w_agent, w_task))

    all_writers = [w for w, _ in write_tasks]
    all_write_tasks = [t for _, t in write_tasks]

    slug_list = "\n".join(f"  - \'{m['slug']}\'" for m in topics_meta)
    publisher = Agent(
        role="Operations Agent",
        goal="Upload each topic report to Google Drive and send one email with all links",
        backstory="You are an automation agent handling publishing.",
        tools=[upload_draft_tool, send_email_notification_tool],
        llm=crew_llm,
        verbose=True,
        allow_delegation=False,
    )
    publish_task = Task(
        description=(
            f"Draft files have been saved to disk. Upload each to Google Drive:\n{slug_list}\n\n"
            "Call Upload Draft tool once per slug. Collect all links.\n"
            "Then send one email listing all Drive links."
        ),
        expected_output="Confirmation with all Drive links.",
        agent=publisher,
        context=all_write_tasks,
    )

    return Crew(
        agents=agents + all_writers + [publisher],
        tasks=research_tasks + all_write_tasks + [publish_task],
        process=Process.sequential,
    )




# ─── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    log.debug(f"sys.argv: {sys.argv}")
    if len(sys.argv) >= 4 and sys.argv[1] == "--revise":
        idx = int(sys.argv[2])
        instruction = " ".join(sys.argv[3:])
        log.info(revise_topic(idx, instruction))
    else:
        all_topics = load_topics(TOPICS_FILE)

        # --topics 1,3,4 → run only those indices
        topics = all_topics
        if "--topics" in sys.argv:
            ti = sys.argv.index("--topics")
            indices = [int(x) for x in sys.argv[ti+1].split(",")]
            topics = [all_topics[i-1] for i in indices if 1 <= i <= len(all_topics)]

        log.info(f"Agent starting — {len(topics)} topic(s)")
        for t in topics:
            log.info(f"  Topic: {t}")
        
        crew = build_crew(topics)
        log.info("Crew kickoff starting...")
        result = crew.kickoff()
        log.info("Pipeline complete.")
        log.info(f"Result: {result}")
