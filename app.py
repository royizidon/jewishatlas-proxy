from flask import Flask, request, Response, jsonify
import os, requests
from flask_cors import CORS
from dotenv import load_dotenv
import json
import random
import math
from collections import defaultdict

# Load environment and setup Flask
load_dotenv()
ARCGIS_URL = os.getenv("ARCGIS_URL", "").strip()
app = Flask(__name__)
CORS(app)

def create_clusters(features, zoom_level):
    """Create clusters from features based on geographic proximity and zoom level"""
    
    print(f"🔍 CLUSTER DEBUG: Input - zoom={zoom_level}, features={len(features)}")
    
    # At zoom 12+, return individual points with is_cluster=False
    if zoom_level >= 12:
        print("✅ HIGH ZOOM: Returning individual points")
        for feature in features:
            if 'properties' not in feature:
                feature['properties'] = {}
            feature['properties']['is_cluster'] = False
            feature['properties']['cluster_count'] = 1
            # Ensure we have required fields for the frontend
            if 'Name' not in feature['properties']:
                feature['properties']['Name'] = feature['properties'].get('eng_name', 'Unknown Site')
        print(f"✅ HIGH ZOOM: Returning {len(features)} individual points")
        return features
    
    # At lower zoom levels, create clusters
    print("🔄 LOW ZOOM: Creating clusters...")
    
    # Dynamic cluster distance based on zoom level
    if zoom_level <= 6:
        cluster_distance_km = 100
    elif zoom_level <= 8:
        cluster_distance_km = 50
    elif zoom_level <= 10:
        cluster_distance_km = 20
    else:
        cluster_distance_km = 10
    
    cluster_radius_degrees = cluster_distance_km / 111.0
    print(f"🎯 Cluster radius: {cluster_distance_km}km ({cluster_radius_degrees:.4f}°)")
    
    clusters = []
    unclustered = features[:]
    cluster_id = 0
    
    while unclustered:
        seed_feature = unclustered.pop(0)
        seed_coords = seed_feature.get('geometry', {}).get('coordinates', [0, 0])
        
        if not seed_coords or len(seed_coords) < 2:
            continue
            
        cluster_features = [seed_feature]
        cluster_center_lon = seed_coords[0] 
        cluster_center_lat = seed_coords[1]
        
        remaining = []
        for feature in unclustered:
            coords = feature.get('geometry', {}).get('coordinates', [0, 0])
            if len(coords) >= 2:
                distance = math.sqrt(
                    (coords[0] - cluster_center_lon) ** 2 + 
                    (coords[1] - cluster_center_lat) ** 2
                )
                
                if distance <= cluster_radius_degrees:
                    cluster_features.append(feature)
                    cluster_center_lon = sum(f.get('geometry', {}).get('coordinates', [0, 0])[0] 
                                           for f in cluster_features) / len(cluster_features)
                    cluster_center_lat = sum(f.get('geometry', {}).get('coordinates', [0, 0])[1] 
                                           for f in cluster_features) / len(cluster_features)
                else:
                    remaining.append(feature)
            else:
                remaining.append(feature)
        
        unclustered = remaining
        
        if len(cluster_features) == 1:
            # Single point
            feature = cluster_features[0]
            if 'properties' not in feature:
                feature['properties'] = {}
            feature['properties']['is_cluster'] = False
            feature['properties']['cluster_count'] = 1
            if 'Name' not in feature['properties']:
                feature['properties']['Name'] = feature['properties'].get('eng_name', 'Unknown Site')
            clusters.append(feature)
        else:
            # Create cluster
            categories = defaultdict(int)
            total_count = len(cluster_features)
            
            for feature in cluster_features:
                category = feature.get('properties', {}).get('main_category', 'Unknown')
                categories[category] += 1
            
            dominant_category = max(categories.items(), key=lambda x: x[1])[0] if categories else 'Unknown'
            
            cluster_feature = {
                'type': 'Feature',
                'geometry': {
                    'type': 'Point',
                    'coordinates': [cluster_center_lon, cluster_center_lat]
                },
                'properties': {
                    'OBJECTID': f"cluster_{cluster_id}",
                    'main_category': dominant_category,
                    'cluster_count': total_count,
                    'is_cluster': True,
                    'categories': dict(categories),
                    'Name': f"Cluster of {total_count} Jewish Sites",
                    'eng_name': f"Cluster ({total_count})",
                    'Address': f"{total_count} sites in this area"
                }
            }
            clusters.append(cluster_feature)
            cluster_id += 1
    
    cluster_count = len([c for c in clusters if c.get('properties', {}).get('is_cluster', False)])
    point_count = len([c for c in clusters if not c.get('properties', {}).get('is_cluster', False)])
    print(f"🎉 CLUSTER RESULT: {cluster_count} clusters + {point_count} individual points = {len(clusters)} total")
    
    return clusters

def process_features(response_data, zoom_level=10, viewport_bounds=None):
    """Process features with clustering based on zoom level"""
    try:
        print(f"📊 PROCESSING: Starting with zoom={zoom_level}")
        
        data = json.loads(response_data)
        
        if 'features' not in data:
            print("❌ ERROR: No 'features' key in response")
            return response_data
        
        features = data['features']
        original_count = len(features)
        
        print(f"📊 PROCESSING: {original_count} features from ArcGIS")
        
        if original_count == 0:
            print("⚠️  WARNING: Zero features to process")
            return response_data
        
        # Print sample feature to debug structure
        if features:
            sample_feature = features[0]
            print(f"📋 SAMPLE FEATURE: {json.dumps(sample_feature, indent=2)[:500]}...")
        
        # Create consistent seed for viewport
        if viewport_bounds:
            seed_str = f"{viewport_bounds.get('west', 0):.3f},{viewport_bounds.get('south', 0):.3f},{viewport_bounds.get('east', 0):.3f},{viewport_bounds.get('north', 0):.3f}"
            seed = hash(seed_str) % (2**32)
            random.seed(seed)
        
        # Sample features if too many
        max_features_for_processing = 2000
        if len(features) > max_features_for_processing:
            print(f"✂️  SAMPLING: Reducing from {len(features)} to {max_features_for_processing}")
            
            featured = [f for f in features if f.get('properties', {}).get('main_category') == 'Featured']
            others = [f for f in features if f.get('properties', {}).get('main_category') != 'Featured']
            
            print(f"📊 FEATURED: {len(featured)}, OTHERS: {len(others)}")
            
            remaining_slots = max_features_for_processing - len(featured)
            if remaining_slots > 0 and others:
                if len(others) <= remaining_slots:
                    sampled_others = others
                else:
                    sampled_others = random.sample(others, remaining_slots)
                features = featured + sampled_others
            else:
                features = featured[:max_features_for_processing]
            
            print(f"✂️  SAMPLING: Final feature count: {len(features)}")
        
        # Apply clustering logic
        print(f"🔄 CLUSTERING: Calling create_clusters with {len(features)} features at zoom {zoom_level}")
        processed_features = create_clusters(features, zoom_level)
        
        # Update the response
        data['features'] = processed_features
        
        # Add detailed metadata
        cluster_count = len([f for f in processed_features if f.get('properties', {}).get('is_cluster', False)])
        point_count = len([f for f in processed_features if not f.get('properties', {}).get('is_cluster', False)])
        
        data['metadata'] = {
            'processed': True,
            'original_count': original_count,
            'returned_count': len(processed_features),
            'cluster_count': cluster_count,
            'point_count': point_count, 
            'zoom_level': zoom_level,
            'display_mode': 'clusters' if zoom_level < 12 else 'points',
            'viewport_bounds': viewport_bounds,
            'timestamp': f"{__import__('datetime').datetime.now()}"
        }
        
        print(f"✅ FINAL RESULT: {cluster_count} clusters, {point_count} points (zoom: {zoom_level})")
        
        # Print a few sample processed features
        if processed_features:
            for i, feature in enumerate(processed_features[:3]):
                is_cluster = feature.get('properties', {}).get('is_cluster', False)
                name = feature.get('properties', {}).get('Name', 'Unknown')
                count = feature.get('properties', {}).get('cluster_count', 1)
                print(f"  📍 Feature {i+1}: {'🔗' if is_cluster else '📌'} {name} (count: {count})")
        
        return json.dumps(data)
        
    except Exception as e:
        print(f"💥 ERROR: Processing failed: {e}")
        import traceback
        traceback.print_exc()
        return response_data

# Main proxy endpoint
@app.route("/api/landmarks", defaults={"subpath": None}, methods=["GET", "POST"])
@app.route("/api/landmarks/<path:subpath>", methods=["GET", "POST"])
def proxy_landmarks(subpath):
    """Proxy requests to ArcGIS with smart clustering"""
    
    print(f"\n🌐 NEW REQUEST: {request.method} {request.path}")
    print(f"🔗 Request URL: {request.url}")
    
    # Determine if this is a feature query
    is_query = request.method == "POST" or "where" in request.args
    
    if is_query:
        endpoint = f"{ARCGIS_URL}/query"
        print(f"📡 QUERY REQUEST to: {endpoint}")
        
        if request.method == "GET":
            params = dict(request.args)
            zoom_level = int(params.get('zoom', 10))
            
            print(f"🔍 GET PARAMS: {dict(params)}")
            print(f"🎯 ZOOM LEVEL: {zoom_level}")
            
            # Handle spatial filtering
            viewport_bounds = None
            if all(param in params for param in ['xmin', 'ymin', 'xmax', 'ymax']):
                xmin = float(params['xmin'])
                ymin = float(params['ymin'])
                xmax = float(params['xmax'])
                ymax = float(params['ymax'])
                
                print(f"🗺️  RAW VIEWPORT: {xmin:.4f},{ymin:.4f} to {xmax:.4f},{ymax:.4f}")
                
                # Check if coordinates are in Web Mercator (large numbers) or WGS84 (small numbers)
                if abs(xmin) > 180 or abs(ymin) > 90:
                    print("🔄 Converting from Web Mercator (3857) to WGS84 (4326)")
                    # These are Web Mercator coordinates - convert to WGS84
                    import math
                    
                    def webmercator_to_wgs84(x, y):
                        # Convert Web Mercator to WGS84
                        lon = x / 20037508.34 * 180
                        lat = y / 20037508.34 * 180
                        lat = 180 / math.pi * (2 * math.atan(math.exp(lat * math.pi / 180)) - math.pi / 2)
                        return lon, lat
                    
                    xmin_wgs84, ymin_wgs84 = webmercator_to_wgs84(xmin, ymin)
                    xmax_wgs84, ymax_wgs84 = webmercator_to_wgs84(xmax, ymax)
                    
                    print(f"🌍 CONVERTED VIEWPORT: {xmin_wgs84:.4f},{ymin_wgs84:.4f} to {xmax_wgs84:.4f},{ymax_wgs84:.4f}")
                    
                    envelope = f"{xmin_wgs84},{ymin_wgs84},{xmax_wgs84},{ymax_wgs84}"
                    params['inSR'] = '3857'  # Input is Web Mercator
                    params['outSR'] = '4326'  # Output should be WGS84
                    
                    viewport_bounds = {
                        'west': xmin_wgs84, 'south': ymin_wgs84, 
                        'east': xmax_wgs84, 'north': ymax_wgs84
                    }
                else:
                    print("✅ Coordinates already in WGS84")
                    # Already in WGS84
                    envelope = f"{xmin},{ymin},{xmax},{ymax}"
                    params['inSR'] = '4326'
                    params['outSR'] = '4326'
                    
                    viewport_bounds = {
                        'west': xmin, 'south': ymin, 
                        'east': xmax, 'north': ymax
                    }
                
                params['geometry'] = envelope
                params['geometryType'] = 'esriGeometryEnvelope'
                params['spatialRel'] = 'esriSpatialRelIntersects'
                
                # Remove viewport params
                for bound_param in ['xmin', 'ymin', 'xmax', 'ymax']:
                    params.pop(bound_param, None)
            
            # Remove zoom parameter
            params.pop('zoom', None)
            
            # Ensure good settings
            params['resultRecordCount'] = '5000'
            params['f'] = 'geojson'
            params['outFields'] = '*'
            
            print(f"📡 ARCGIS PARAMS: {list(params.keys())}")
            
            try:
                print(f"🚀 SENDING REQUEST to ArcGIS...")
                upstream = requests.get(endpoint, params=params, timeout=30)
                print(f"📨 ARCGIS RESPONSE: {upstream.status_code} ({len(upstream.content)} bytes)")
            except Exception as e:
                print(f"💥 ARCGIS REQUEST FAILED: {e}")
                return jsonify({"error": "ArcGIS request failed"}), 500
            
        else:
            # POST request
            upstream = requests.post(
                endpoint,
                data=request.get_data(),
                headers={"Content-Type": request.headers.get("Content-Type")},
                timeout=30
            )
            zoom_level = 10
            viewport_bounds = None
    
    else:
        # Metadata request
        print("📋 METADATA REQUEST")
        upstream = requests.get(ARCGIS_URL, params=request.args, timeout=30)
        return Response(
            upstream.content,
            status=upstream.status_code,
            content_type=upstream.headers.get("Content-Type", "application/json")
        )
    
    # Process successful feature queries
    if upstream.status_code == 200 and is_query:
        try:
            response_text = upstream.content.decode('utf-8')
            print(f"📊 ARCGIS DATA: {len(response_text)} characters")
            
            # Quick feature count check
            if '"features"' in response_text:
                feature_count = response_text.count('"type":"Feature"')
                print(f"📍 RAW FEATURE COUNT: {feature_count}")
                
                processed_content = process_features(
                    response_text,
                    zoom_level=zoom_level,
                    viewport_bounds=viewport_bounds
                )
                
                print(f"✅ SENDING PROCESSED RESPONSE")
                return Response(
                    processed_content,
                    status=200,
                    content_type="application/json",
                    headers={
                        'Access-Control-Allow-Origin': '*',
                        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                        'Access-Control-Allow-Headers': 'Content-Type'
                    }
                )
            else:
                print("⚠️  NO FEATURES in ArcGIS response")
                return Response(response_text, status=200, content_type="application/json")
            
        except Exception as e:
            print(f"💥 PROCESSING ERROR: {e}")
            import traceback
            traceback.print_exc()
            return Response(
                upstream.content,
                status=upstream.status_code,
                content_type=upstream.headers.get("Content-Type", "application/json")
            )
    else:
        print(f"❌ NON-SUCCESS RESPONSE: {upstream.status_code}")
    
    # Default response
    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json")
    )

@app.route("/health", methods=["GET"])
def health_check():
    """Health check with debug info"""
    return jsonify({
        "status": "healthy", 
        "arcgis_url": ARCGIS_URL,
        "timestamp": f"{__import__('datetime').datetime.now()}"
    }), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"🚀 Starting DEBUG server on port {port}")
    print(f"🔗 ArcGIS URL: {ARCGIS_URL}")
    app.run(host="0.0.0.0", port=port, debug=True)