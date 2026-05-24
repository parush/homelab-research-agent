"""
Run: python test_image.py
Tests Gemini Imagen API directly and shows the exact error.
"""
import os, requests
from dotenv import load_dotenv
load_dotenv()

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("❌ GEMINI_API_KEY not set")
    exit(1)

print(f"Using key: {api_key[:12]}...")

resp = requests.post(
    f"https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-002:predict?key={api_key}",
    json={
        "instances": [{"prompt": "A clean minimalist illustration of a graph database, no text"}],
        "parameters": {"sampleCount": 1, "aspectRatio": "16:9"},
    },
    timeout=45,
)

print(f"Status: {resp.status_code}")
print(f"Response: {resp.text[:800]}")

if resp.status_code == 200:
    b64 = resp.json()["predictions"][0]["bytesBase64Encoded"]
    # Save as PNG to verify
    import base64
    with open("test_image.png", "wb") as f:
        f.write(base64.b64decode(b64))
    print("✅ Image saved as test_image.png")
