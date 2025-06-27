from flask import Flask, request, Response
import os, requests
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()    # load ARCGIS_URL from .env

app = Flask(__name__)
CORS(app)

ARCGIS_URL = os.getenv("ARCGIS_URL")

@app.route("/api/landmarks")
def proxy_landmarks():
    # forward all query params to the real service
    upstream = requests.get(ARCGIS_URL, params=request.args, stream=True)
    
    # grab upstream content type (likely application/json)
    content_type = upstream.headers.get("Content-Type", "application/json")
    
    # return a Flask Response with the raw body, code, and content-type
    return Response(
        upstream.raw.read(), 
        status=upstream.status_code, 
        content_type=content_type
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
