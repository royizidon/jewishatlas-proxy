from flask import Flask, request, Response
import os, requests, urllib.parse
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)
CORS(app)
ARCGIS_URL = os.getenv("ARCGIS_URL")

@app.route("/api/landmarks")
def proxy_landmarks():
    # Build full URL including querystring
    qs = urllib.parse.urlencode(request.args)
    full_url = f"{ARCGIS_URL}?{qs}"
    app.logger.info(f"[DEBUG] Upstream URL: {full_url}")
    
    upstream = requests.get(full_url)
    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json")
    )
