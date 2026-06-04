from flask import Flask, request, Response, jsonify
import os
import re
import json
import time
import unicodedata
import requests
from flask_cors import CORS
from dotenv import load_dotenv
from datetime import datetime

# =========================
# Load env
# =========================
load_dotenv()

ARCGIS_URL         = os.getenv("ARCGIS_URL", "").strip()
MEMORIAL_LAYER_URL = os.getenv("MEMORIAL_LAYER_URL", "").strip()
MEMORY_MAP_LAYER_URL = os.getenv("MEMORY_MAP_LAYER_URL", "").strip()
ARCGIS_USERNAME    = os.getenv("ARCGIS_USERNAME", "").strip()
ARCGIS_PASSWORD    = os.getenv("ARCGIS_PASSWORD", "").strip()
ADMIN_SECRET       = os.getenv("ADMIN_SECRET", "").strip()  # protect debug endpoints


def require_admin(req):
    """Returns True only if the request carries the correct admin secret header."""
    return bool(ADMIN_SECRET) and req.headers.get("X-Admin-Secret") == ADMIN_SECRET


# =========================
# Slug generation (server-side only)
# =========================
def generate_slug(eng_name: str = "", he_name: str = "") -> str:
    """
    Always generated server-side — never trust the client.
    - Prefers eng_name (Latin, URL-safe)
    - Falls back to 'memorial' if empty or fully non-Latin after stripping
    - Normalises accented chars (é→e, ü→u) via NFKD decomposition
    - Strips everything except a-z, 0-9, and hyphens
    - Appends a hex ms-timestamp suffix for uniqueness
    """
    base = (eng_name or "").strip()

    if base:
        base = unicodedata.normalize("NFKD", base)
        base = base.encode("ascii", "ignore").decode("ascii")

    if not base:
        base = "memorial"

    suffix = format(int(time.time() * 1000), "x")

    slug = base.lower().strip().replace(" ", "-").replace("_", "-")
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")

    if not slug:
        slug = "memorial"

    return f"{slug}-{suffix}"


def sanitize_slug_param(raw: str) -> str:
    """
    Whitelist-only filter for slug values coming in via URL.
    Allows only a-z, 0-9, and hyphens.
    Raises ValueError if empty or suspiciously long.
    """
    cleaned = re.sub(r"[^a-z0-9\-]", "", raw.lower())
    if not cleaned or len(cleaned) > 200:
        raise ValueError(f"Invalid slug: {repr(raw)}")
    return cleaned


app = Flask(__name__)
CORS(app, origins=[
    "https://jewishatlas.org",
    "https://www.jewishatlas.org",
    "http://localhost:3000",
    "http://localhost:5500",
])

# =========================
# ArcGIS Token (cached)
# =========================
_TOKEN_CACHE = {"token": None, "expires": 0}

_WALL_CACHE = {"data": None, "expires": 0}
WALL_CACHE_TTL = 7 * 24 * 3600  # 1 week — cache is busted manually via /api/wall/refresh after publishing

def get_arcgis_token():
    if not ARCGIS_USERNAME or not ARCGIS_PASSWORD:
        raise RuntimeError("Missing ARCGIS_USERNAME or ARCGIS_PASSWORD in .env")

    now = time.time()
    if _TOKEN_CACHE["token"] and now < (_TOKEN_CACHE["expires"] - 60):
        return _TOKEN_CACHE["token"]

    url = "https://www.arcgis.com/sharing/rest/generateToken"
    payload = {
        "username":   ARCGIS_USERNAME,
        "password":   ARCGIS_PASSWORD,
        "client":     "referer",
        "referer":    "https://api.jewishatlas.org",
        "expiration": 60,
        "f":          "json",
    }

    r = requests.post(url, data=payload, timeout=30)
    data = r.json()

    if "token" not in data:
        raise RuntimeError(f"Token error: {data}")

    token      = data["token"]
    expires_ms = data.get("expires", 0)
    expires_sec = int(expires_ms / 1000) if expires_ms else int(now + 55 * 60)

    _TOKEN_CACHE["token"]   = token
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
            upstream = requests.get(endpoint, params=request.args, timeout=30)
        else:
            upstream = requests.post(
                endpoint,
                data=request.get_data(),
                headers={"Content-Type": request.headers.get("Content-Type")},
                timeout=30,
            )
    else:
        upstream = requests.get(ARCGIS_URL, params=request.args, timeout=30)

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

    now = time.time()
    if _WALL_CACHE["data"] and now < _WALL_CACHE["expires"]:
        return Response(_WALL_CACHE["data"], status=200, content_type="application/json")

    try:
        token = get_arcgis_token()
        params = {
            "where":         "is_published = 1",
            "outFields":     "slug,he_name,eng_name,born_str,death_str,born_display,death_display,origin,tier",
            "orderByFields": "OBJECTID DESC",
            "f":             "json",
            "token":         token,
        }
        upstream = requests.get(f"{MEMORIAL_LAYER_URL}/query", params=params, timeout=30)
        _WALL_CACHE["data"]    = upstream.content
        _WALL_CACHE["expires"] = now + WALL_CACHE_TTL
        return Response(upstream.content, status=upstream.status_code, content_type="application/json")

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/wall/refresh", methods=["POST"])
def refresh_wall_cache():
    if not require_admin(request):
         return jsonify({"error": "Unauthorized"}), 401
    _WALL_CACHE["data"]    = None
    _WALL_CACHE["expires"] = 0
    return jsonify({"ok": True})


# =========================
# Debug fields (protected)
# =========================
@app.route("/api/debug-fields", methods=["GET"])
def debug_fields():
    if not require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        token = get_arcgis_token()
        res = requests.get(
            MEMORIAL_LAYER_URL,
            params={"f": "json", "token": token},
            timeout=30,
        )
        data = res.json()
        fields = [{"name": f["name"], "type": f.get("type")} for f in data.get("fields", [])]
        return jsonify({"fields": fields, "raw_keys": [f["name"] for f in data.get("fields", [])]})
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# =========================
# Debug row (protected)
# =========================
@app.route("/api/debug-row/<int:oid>", methods=["GET"])
def debug_row(oid):
    if not require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        token = get_arcgis_token()
        params = {
            "where":     f"OBJECTID = {oid}",  # safe — Flask types oid as int
            "outFields": "*",
            "f":         "json",
            "token":     token,
        }
        res = requests.get(f"{MEMORIAL_LAYER_URL}/query", params=params, timeout=30)
        return Response(res.content, content_type="application/json")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# Dedicate (insert)
# =========================
@app.route("/api/dedicate", methods=["POST"])
def api_dedicate():
    if not MEMORIAL_LAYER_URL:
        return jsonify({"error": "MEMORIAL_LAYER_URL not set"}), 500

    try:
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        token   = get_arcgis_token()

        data = request.form

        he_name  = (data.get("he_name")  or "").strip()
        eng_name = (data.get("eng_name") or "").strip()

        if not he_name and not eng_name:
            return jsonify({"error": "Name required"}), 400

        # Email validation
        email = (data.get("dedicator_email") or "").strip()
        if not email:
            return jsonify({"error": "Email required"}), 400
        if "@" not in email:
            return jsonify({"error": "Invalid email — must contain @"}), 400
        if len(email) > 256:
            return jsonify({"error": "Email must be 256 characters or fewer"}), 400

        # Slug — always generated server-side
        slug = generate_slug(eng_name=eng_name, he_name=he_name)

        # Tier whitelist
        tier = data.get("tier") or "brick"
        if tier not in ("brick", "page"):
            tier = "brick"

        attrs = {
            "slug":            slug,
            "he_name":         he_name  or None,
            "eng_name":        eng_name or None,
            "born_str":        data.get("born_str")  or None,
            "death_str":       data.get("death_str") or None,
            "born_display":    data.get("born_date") or None,
            "death_display":   data.get("death_date") or None,
            "origin":          data.get("origin")    or None,
            # BUG FIX: operator precedence — must use ternary, not "x or None if cond else None"
            "full_bio":        data.get("full_bio") if tier == "page" else None,
            "tier":            tier,
            "memorial_type":   "memory",
            "is_published":    0,          # BUG FIX: was missing entirely — defaulted to null
            "payment_status":  "pending",
            "dedicator_email": email,
            "created":         now_str,
            "updated":         now_str,
        }

        # Remove None values — ArcGIS silently drops all attrs if any are None
        attrs = {k: v for k, v in attrs.items() if v is not None}

        feature = {"attributes": attrs}

        print("SENT ATTRS:", json.dumps(attrs, ensure_ascii=False))

        insert_res = requests.post(
            f"{MEMORIAL_LAYER_URL}/applyEdits",
            data={
                "f":     "json",
                "token": token,
                "adds":  json.dumps([feature]),
            },
            timeout=30,
        )

        insert_json = insert_res.json()
        print("ARCGIS RESPONSE:", json.dumps(insert_json))

        add_results = insert_json.get("addResults", [])
        if not add_results:
            return jsonify({"error": insert_json}), 500

        add_result = add_results[0]
        if not add_result.get("success"):
            return jsonify({"error": add_result}), 500

        object_id = add_result["objectId"]

        # Upload image (page tier only)
        if tier == "page" and "image" in request.files:
            file = request.files["image"]
            if file and file.filename:
                attach_res = requests.post(
                    f"{MEMORIAL_LAYER_URL}/{object_id}/addAttachment",
                    data={"f": "json", "token": token},
                    files={"attachment": (file.filename, file.stream, file.mimetype)},
                    timeout=30,
                )
                attach_json = attach_res.json()
                if not attach_json.get("addAttachmentResult", {}).get("success"):
                    # Log but do not abort — wall record already inserted, orphaning it would cause duplicates on retry
                    print("IMAGE UPLOAD FAILED (non-fatal):", json.dumps(attach_json))

        # =========================
        # Map layer insert (second table)
        # =========================
        map_insert_status = "skipped"

        has_map_place = data.get("has_map_place") == "1"
        lat_raw  = data.get("latitude")
        lng_raw  = data.get("longitude")

        if has_map_place and lat_raw and lng_raw and MEMORY_MAP_LAYER_URL:
            try:
                latitude  = float(lat_raw)
                longitude = float(lng_raw)

                if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
                    raise ValueError(f"Coordinates out of range: {latitude}, {longitude}")

                raw_precision  = (data.get("location_precision") or "").strip()
                raw_connection = (data.get("connection_type")    or "").strip()

                VALID_PRECISION       = {"Exact", "Approximate", "City level"}
                VALID_CONNECTION_TYPES = {
                    "Birthplace", "Hometown", "Family origin",
                    "Home", "Resting place", "Meaningful place", "Other"
                }

                map_attrs = {
                    "slug":               slug,
                    "why_this_place":     (data.get("why_this_place")  or "").strip() or None,
                    "connection_type":    raw_connection if raw_connection in VALID_CONNECTION_TYPES else None,
                    "location_label":     (data.get("location_label")  or "").strip() or None,
                    "location_precision": raw_precision if raw_precision in VALID_PRECISION else None,
                    "show_on_map":        0,
                }
                map_attrs = {k: v for k, v in map_attrs.items() if v is not None}

                map_feature = {
                    "attributes": map_attrs,
                    "geometry": {
                        "x": longitude,
                        "y": latitude,
                        "spatialReference": {"wkid": 4326},
                    },
                }

                print("MAP INSERT ATTRS:", json.dumps(map_attrs, ensure_ascii=False))

                map_res  = requests.post(
                    f"{MEMORY_MAP_LAYER_URL}/applyEdits",
                    data={
                        "f":     "json",
                        "token": token,
                        "adds":  json.dumps([map_feature], ensure_ascii=False),
                    },
                    timeout=30,
                )
                map_json = map_res.json()
                map_add  = map_json.get("addResults", [{}])[0]

                if map_add.get("success"):
                    map_insert_status = "success"
                else:
                    map_insert_status = "error"
                    print("MAP INSERT FAILED:", json.dumps(map_add, ensure_ascii=False))
                    print("MAP INSERT FULL RESPONSE:", json.dumps(map_json, ensure_ascii=False))

            except Exception as map_err:
                map_insert_status = "error"
                print("MAP INSERT EXCEPTION:", str(map_err))

        return jsonify({
            "success":    True,
            "objectId":   object_id,
            "slug":       slug,
            "map_insert": map_insert_status,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# Image proxy
# Avoids embedding a short-lived ArcGIS token in the image URL.
# BUG FIX: direct token URLs break after 60 minutes — this fetches a fresh token per request.
# =========================
@app.route("/api/image/<int:object_id>/<int:attachment_id>", methods=["GET"])
def proxy_image(object_id, attachment_id):
    try:
        token = get_arcgis_token()
        img_res = requests.get(
            f"{MEMORIAL_LAYER_URL}/{object_id}/attachments/{attachment_id}",
            params={"token": token},
            timeout=15,
            stream=True,
        )
        return Response(
            img_res.content,
            status=img_res.status_code,
            content_type=img_res.headers.get("Content-Type", "image/jpeg"),
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# Memory page
# =========================
@app.route("/api/memory/<slug>", methods=["GET"])
def api_memory(slug):
    try:
        slug = sanitize_slug_param(slug)
    except ValueError:
        return jsonify({"error": "Invalid slug"}), 400

    try:
        token = get_arcgis_token()

        params = {
            "where":          f"slug = '{slug}'",  # safe — slug is a-z0-9- only after sanitize
            "outFields":      "*",
            "returnGeometry": "false",
            "f":              "json",
            "token":          token,
        }

        res  = requests.get(f"{MEMORIAL_LAYER_URL}/query", params=params, timeout=15)
        data = res.json()

        features = data.get("features", [])
        if not features:
            return jsonify({"error": "Not found"}), 404

        feature   = features[0]
        object_id = feature["attributes"]["OBJECTID"]

        att_res     = requests.get(
            f"{MEMORIAL_LAYER_URL}/{object_id}/attachments",
            params={"f": "json", "token": token},
            timeout=15,
        )
        attachments = att_res.json().get("attachmentInfos", [])

        # BUG FIX: use proxy URL instead of direct token URL — tokens expire in 60 min
        image_url = None
        if attachments:
            attachment_id = attachments[0]["id"]
            image_url = f"https://api.jewishatlas.org/api/image/{object_id}/{attachment_id}"

        return jsonify({
            "attributes": feature["attributes"],
            "image_url":  image_url,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# Run
# =========================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG", "0") == "1")