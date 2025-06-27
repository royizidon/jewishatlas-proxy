from flask import Flask, request, Response
import os, requests
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()
# load and clean the URL exactly once
raw_url = os.getenv("ARCGIS_URL", "")
ARCGIS_URL = raw_url.strip().replace("\n", "").replace("\r", "")
print(f"[DEBUG] Cleaned ARCGIS_URL: {ARCGIS_URL}", flush=True)

app = Flask(__name__)
CORS(app)

@app.route("/api/landmarks")
def proxy_landmarks():
    # Let requests handle encoding
    upstream = requests.get(ARCGIS_URL, params=request.args)
    # Log the fully-encoded URL
    print(f"[DEBUG] Upstream URL: {upstream.url}", flush=True)

    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json")
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
