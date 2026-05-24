"""
Headless OAuth flow for servers with no browser.
Requires: pip install google-auth-oauthlib requests
"""
from google_auth_oauthlib.flow import Flow
import json, os

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/gmail.send",
]

if not os.path.exists("credentials.json"):
    print("""
❌ credentials.json not found. Do this first:
1. https://console.cloud.google.com/
2. Create/select a project
3. Enable: Google Drive API + Gmail API
4. APIs & Services > Credentials > Create Credentials > OAuth 2.0 Client ID
5. Application type: Desktop App
6. Download JSON → save as 'credentials.json' here
""")
    exit(1)

flow = Flow.from_client_secrets_file(
    "credentials.json",
    scopes=SCOPES,
    redirect_uri="urn:ietf:wg:oauth:2.0:oob",  # out-of-band, no redirect server needed
)

auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

print("\n👉 Open this URL in your browser:\n")
print(auth_url)
print("\nAfter signing in, Google will show you a code. Paste it here:")
code = input("Code: ").strip()

flow.fetch_token(code=code)
creds = flow.credentials

with open("google_creds.json", "w") as f:
    json.dump({
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "token_uri": creds.token_uri,
        "token_expiry": creds.expiry.isoformat() if creds.expiry else None,
    }, f, indent=2)

print("\n✅ google_creds.json saved. You're good to run agent.py.")
