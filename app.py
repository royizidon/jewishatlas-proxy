from flask import Flask, request, Response
import os, requests
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
CORS(app)
ARCGIS_URL = os.getenv("ARCGIS_URL")

@app.route("/api/landmarks")
def proxy_landmarks():
    # Let requests build & percent-encode the query string for us
    upstream = requests.get(ARCGIS_URL, params=request.args)
    # Print the exact URL so you can confirm encoding (e.g. where=1%3D1)
    print(f"[DEBUG] Upstream URL: {upstream.url}", flush=True)

    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json")
    )

if __name__ == "__main__":
    # For local testing
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
