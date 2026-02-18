from flask import Flask, request, Response, jsonify
import os
import json
import time
import requests
from flask_cors import CORS
from dotenv import load_dotenv
from datetime import datetime

# =========================
# Load env
# =========================
load_dotenv()

ARCGIS_URL = os.getenv("ARCGIS_URL", "").strip()
MEMORIAL_LAYER_URL = os.getenv("MEMORIAL_LAYER_URL", "").strip()

ARCGIS_USERNAME = os.getenv("ARCGIS_USERNAME", "").strip()
ARCGIS_PASSWORD = os.getenv("ARCGIS_PASSWORD", "").strip()


def parse_date_to_ms(date_str):
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


app = Flask(__name__)
CORS(app)

# =========================
# ArcGIS Token (cached)
# =========================
_TOKEN_CACHE = {"token": None, "expires": 0}


def get_arcgis_token():
    if not ARCGIS_USERNAME or not ARCGIS_PASSWORD:
        raise RuntimeError("Missing ARCGIS_USERNAME or ARCGIS_PASSWORD in .env")

    now = time.time()
    if _TOKEN_CACHE["token"] and now < (_TOKEN_CACHE["expires"] - 60):
        return _TOKEN_CACHE["token"]

    url = "https://www.arcgis.com/sharing/rest/generateToken"
    payload = {
        "username": ARCGIS_USERNAME,
        "password": ARCGIS_PASSWORD,
        "client": "referer",
        "referer": "https://api.jewishatlas.org",
        "expiration": 60,
        "f": "json",
    }

    r = requests.post(url, data=payload, timeout=30)
    data = r.json()

    if "token" not in data:
        raise RuntimeError(f"Token error: {data}")

    token = data["token"]
    expires_ms = data.get("expires", 0)
    expires_sec = int(expires_ms / 1000) if expires_ms else int(now + 55 * 60)

    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expires"] = expires_sec
    return token


# =========================
# Health check
# =========================
@app.route("/")
def health():
    return "OK", 200


# =========================
# Map Proxy
# =========================
@app.route("/api/landmarks", defaults={"subpath": None}, methods=["GET", "POST"])
@app.route("/api/landmarks/<path:subpath>", methods=["GET", "POST"])
def proxy_landmarks(subpath):
    if not ARCGIS_URL:
        return Response(
            json.dumps({"error": "ARCGIS_URL not set"}),
            status=500,
            content_type="application/json",
        )

    is_query = request.method == "POST" or "where" in request.args

    if is_query:
        endpoint = f"{ARCGIS_URL}/query"
        if request.method == "GET":
            upstream = requests.get(endpoint, params=request.args)
        else:
            upstream = requests.post(
                endpoint,
                data=request.get_data(),
                headers={"Content-Type": request.headers.get("Content-Type")},
            )
    else:
        upstream = requests.get(ARCGIS_URL, params=request.args)

    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json"),
    )


# =========================
# Test token
# =========================
@app.route("/api/test-token", methods=["GET"])
def api_test_token():
    try:
        token = get_arcgis_token()
        return jsonify({"ok": True, "token_preview": token[:10]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# =========================
# Wall list (published only)
# =========================
@app.route("/api/wall", methods=["GET"])
def api_wall():
    if not MEMORIAL_LAYER_URL:
        return jsonify({"error": "MEMORIAL_LAYER_URL not set"}), 500

    try:
        token = get_arcgis_token()

        params = {
            "where": "1=1",
            "outFields": "slug,he_name,eng_name,born_str,death_str,born_display,death_display,origin,tier",
            "orderByFields": "updated_at DESC",
            "f": "json",
            "token": token,
        }

        upstream = requests.get(f"{MEMORIAL_LAYER_URL}/query", params=params, timeout=30)
        return Response(upstream.content, status=upstream.status_code, content_type="application/json")

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# Debug fields (TEMP)
# =========================
@app.route("/api/debug-fields", methods=["GET"])
def debug_fields():
    try:
        token = get_arcgis_token()
        res = requests.get(
            MEMORIAL_LAYER_URL,
            params={"f": "json", "token": token},
            timeout=30
        )
        data = res.json()
        fields = [{"name": f["name"], "type": f.get("type")} for f in data.get("fields", [])]
        return jsonify({"fields": fields, "raw_keys": [f["name"] for f in data.get("fields", [])]})
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# =========================
# Debug row (TEMP)
# =========================
@app.route("/api/debug-row/<int:oid>", methods=["GET"])
def debug_row(oid):
    try:
        token = get_arcgis_token()
        params = {
            "where": f"OBJECTID = {oid}",
            "outFields": "*",
            "f": "json",
            "token": token,
        }
        res = requests.get(f"{MEMORIAL_LAYER_URL}/query", params=params, timeout=30)
        return Response(res.content, content_type="application/json")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# Dedicate (insert draft)
# =========================
@app.route("/api/dedicate", methods=["POST"])
def api_dedicate():
    if not MEMORIAL_LAYER_URL:
        return jsonify({"error": "MEMORIAL_LAYER_URL not set"}), 500

    try:
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        token = get_arcgis_token()

        # Read form fields
        data = request.form

        slug = data.get("slug")
        he_name = data.get("he_name")
        eng_name = data.get("eng_name")

        if not slug:
            return jsonify({"error": "Missing slug"}), 400

        if not (he_name or eng_name):
            return jsonify({"error": "Name required"}), 400

        # Build attributes
        attrs = {
            "slug": slug,
            "he_name": he_name,
            "eng_name": eng_name,
            "born_str": data.get("born_str"),
            "death_str": data.get("death_str"),
            "born_display": data.get("born_date"),
            "death_display": data.get("death_date"),
            "origin": data.get("origin"),
            "full_bio": data.get("full_bio"),
            "tier": data.get("tier") or "brick",
            "memorial_type": "memory",
            "is_published": 0,
            "payment_status": "pending",
            "dedicator_email": data.get("dedicator_email"),
            "created": now_str,
            "updated": now_str,
            "created_at": int(time.time() * 1000),
            "updated_at": int(time.time() * 1000),
        }

        # Remove None values — ArcGIS can silently drop all attrs if any are None
        attrs = {k: v for k, v in attrs.items() if v is not None}

        feature = {"attributes": attrs}

        # TEMP DEBUG
        print("SENT ATTRS:", json.dumps(attrs, ensure_ascii=False))

        # Insert feature
        insert_res = requests.post(
            f"{MEMORIAL_LAYER_URL}/applyEdits",
            data={
                "f": "json",
                "token": token,
                "adds": json.dumps([feature]),
            },
            timeout=30
        )

        insert_json = insert_res.json()

        # TEMP DEBUG
        print("ARCGIS RESPONSE:", json.dumps(insert_json))

        add_results = insert_json.get("addResults", [])
        if not add_results:
            return jsonify({"error": insert_json}), 500

        add_result = add_results[0]
        if not add_result.get("success"):
            return jsonify({"error": add_result}), 500

        object_id = add_result["objectId"]

        # Upload image (if exists)
        if "image" in request.files:
            file = request.files["image"]

            if file and file.filename:
                files = {
                    "attachment": (file.filename, file.stream, file.mimetype)
                }

                attach_payload = {
                    "f": "json",
                    "token": token
                }

                attach_res = requests.post(
                    f"{MEMORIAL_LAYER_URL}/{object_id}/addAttachment",
                    data=attach_payload,
                    files=files,
                    timeout=30
                )

                attach_json = attach_res.json()

                if not attach_json.get("addAttachmentResult", {}).get("success"):
                    return jsonify({"error": "Image upload failed", "details": attach_json}), 500

        return jsonify({
            "success": True,
            "objectId": object_id
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# Run
# =========================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)