from flask import Flask, request, Response, jsonify
import os, requests
from flask_cors import CORS
from dotenv import load_dotenv
import json
import random

# 1. Load & clean your layer URL (no /query at the end)
load_dotenv()
ARCGIS_URL = os.getenv("ARCGIS_URL", "").strip()
app = Flask(__name__)
CORS(app)

import math
from collections import defaultdict

def create_clusters(features, zoom_level, cluster_distance=50):
    """Create clusters from features based on geographic proximity"""
    if zoom_level >= 12:
        # At high zoom, return individual points
        return features
    
    # Group features into clusters based on geographic proximity
    clusters = []
    unclustered = features[:]
    
    # Calculate cluster distance based on zoom (smaller distance = more clusters at higher zoom)
    # At zoom 3: ~200km apart, at zoom 11: ~5km apart
    cluster_radius_degrees = cluster_distance / (111000 * (2 ** (zoom_level - 3)))
    
    while unclustered:
        # Start a new cluster with the first unclustered point
        seed_feature = unclustered.pop(0)
        seed_coords = seed_feature.get('geometry', {}).get('coordinates', [0, 0])
        
        if not seed_coords or len(seed_coords) < 2:
            continue
            
        cluster_features = [seed_feature]
        cluster_center_lon = seed_coords[0]
        cluster_center_lat = seed_coords[1]
        
        # Find all features within cluster radius
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
                    # Update cluster center (simple average)
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
            # Single point - return as individual feature (but limit details for security)
            feature = cluster_features[0]
            feature['properties'] = {
                'id': feature.get('properties', {}).get('OBJECTID', 0),
                'main_category': feature.get('properties', {}).get('main_category', 'Unknown'),
                'cluster_count': 1,
                'is_cluster': False
            }
            clusters.append(feature)
        else:
            # Create cluster feature
            # Count categories in cluster
            categories = defaultdict(int)
            total_count = len(cluster_features)
            
            for feature in cluster_features:
                category = feature.get('properties', {}).get('main_category', 'Unknown')
                categories[category] += 1
            
            # Find dominant category
            dominant_category = max(categories.items(), key=lambda x: x[1])[0]
            
            cluster_feature = {
                'type': 'Feature',
                'geometry': {
                    'type': 'Point',
                    'coordinates': [cluster_center_lon, cluster_center_lat]
                },
                'properties': {
                    'id': f"cluster_{len(clusters)}",
                    'main_category': dominant_category,
                    'cluster_count': total_count,
                    'is_cluster': True,
                    'categories': dict(categories),
                    'Name': f"Cluster of {total_count} Jewish Sites",
                    'Address': f"{total_count} sites in this area"
                }
            }
            clusters.append(cluster_feature)
    
    return clusters

def limit_features_randomly(response_data, viewport_bounds=None, zoom_level=10, max_features=1000):
    """Smart sampling with server-side clustering for security"""
    try:
        data = json.loads(response_data)
        
        if 'features' not in data:
            return response_data
        
        features = data['features']
        
        # Create consistent seed from viewport bounds for same area = same points
        if viewport_bounds:
            seed_str = f"{viewport_bounds['west']:.3f},{viewport_bounds['south']:.3f},{viewport_bounds['east']:.3f},{viewport_bounds['north']:.3f}"
            seed = hash(seed_str) % (2**32)
            random.seed(seed)
        
        # Smart zoom-based strategy with clustering
        if zoom_level >= 12:
            # High zoom: Individual points with sampling
            adjusted_max = 1500  # Reasonable number of individual points
            
            # Prioritize Featured, then sample others
            featured_features = []
            other_features = []
            
            for feature in features:
                category = feature.get('properties', {}).get('main_category', '')
                if category == 'Featured':
                    featured_features.append(feature)
                else:
                    other_features.append(feature)
            
            sampled_features = featured_features[:]
            remaining_slots = adjusted_max - len(sampled_features)
            
            if remaining_slots > 0 and other_features:
                if len(other_features) <= remaining_slots:
                    sampled_features.extend(other_features)
                else:
                    random_others = random.sample(other_features, remaining_slots)
                    sampled_features.extend(random_others)
        
        else:
            # Low zoom: Use clustering for security
            # First sample a larger set for clustering
            sample_size = min(3000, len(features))  # Get enough points for good clustering
            
            # Prioritize Featured sites in the sample
            featured_features = [f for f in features if f.get('properties', {}).get('main_category') == 'Featured']
            other_features = [f for f in features if f.get('properties', {}).get('main_category') != 'Featured']
            
            # Always include all Featured sites
            sample_features = featured_features[:]
            remaining_slots = sample_size - len(sample_features)
            
            if remaining_slots > 0 and other_features:
                if len(other_features) <= remaining_slots:
                    sample_features.extend(other_features)
                else:
                    random_others = random.sample(other_features, remaining_slots)
                    sample_features.extend(random_others)
            
            # Create clusters from the sampled features
            sampled_features = create_clusters(sample_features, zoom_level)
        
        # Update data with processed features
        data['features'] = sampled_features
        
        # Add metadata about processing
        if 'metadata' not in data:
            data['metadata'] = {}
        data['metadata'].update({
            'processed': True,
            'original_count': len(features),
            'returned_count': len(sampled_features),
            'zoom_level': zoom_level,
            'display_mode': 'clusters' if zoom_level < 12 else 'points',
            'cluster_count': len([f for f in sampled_features if f.get('properties', {}).get('is_cluster', False)]) if zoom_level < 12 else 0
        })
        
        return json.dumps(data)
        
    except Exception as e:
        # If anything goes wrong, return original data
        print(f"Processing error: {e}")
        return response_data

# 2. Catch both the base path and ANY sub-path under /api/landmarks
@app.route("/api/landmarks", defaults={"subpath": None}, methods=["GET", "POST"])
@app.route("/api/landmarks/<path:subpath>",                methods=["GET", "POST"])
def proxy_landmarks(subpath):
    """
    - metadata requests (no 'where' + GET) → ARCGIS_URL?f=json
    - feature queries (has 'where' or it's a POST) → ARCGIS_URL/query
    """
    is_query = request.method == "POST" or "where" in request.args
    
    if is_query:
        endpoint = f"{ARCGIS_URL}/query"
        
        # Modify parameters to add spatial filter if viewport bounds provided
        if request.method == "GET":
            params = dict(request.args)
            
            # Add spatial filter if viewport bounds are provided
            if all(param in params for param in ['xmin', 'ymin', 'xmax', 'ymax']):
                # Create envelope geometry for spatial filter
                envelope = f"{params['xmin']},{params['ymin']},{params['xmax']},{params['ymax']}"
                params['geometry'] = envelope
                params['geometryType'] = 'esriGeometryEnvelope'
                params['spatialRel'] = 'esriSpatialRelIntersects'
                params['inSR'] = '3857'  # Changed from 4326 to 3857 (Web Mercator)
                
                # Remove the individual bound parameters since we've used them
                for bound_param in ['xmin', 'ymin', 'xmax', 'ymax']:
                    params.pop(bound_param, None)
            
            # Ensure we're getting GeoJSON format
            if 'f' not in params:
                params['f'] = 'geojson'
            
            # Increase the result count limit to get more features before sampling
            if 'resultRecordCount' not in params:
                params['resultRecordCount'] = '5000'  # Get more, then sample
            
            upstream = requests.get(endpoint, params=params)
        else:
            upstream = requests.post(
                endpoint,
                data=request.get_data(),
                headers={"Content-Type": request.headers.get("Content-Type")}
            )
    else:
        # layer metadata
        upstream = requests.get(ARCGIS_URL, params=request.args)
    
    # If this is a feature query response, apply smart sampling
    if is_query and upstream.status_code == 200:
        # Check if we should apply sampling (only for viewport queries)
        should_sample = False
        viewport_bounds = None
        zoom_level = 10  # default zoom
        
        if request.method == "GET":
            # Sample if spatial bounds were provided
            should_sample = any(param in request.args for param in ['xmin', 'ymin', 'xmax', 'ymax'])
            
            if should_sample:
                # Extract viewport bounds and zoom level
                viewport_bounds = {
                    'west': float(request.args.get('xmin', 0)),
                    'south': float(request.args.get('ymin', 0)),
                    'east': float(request.args.get('xmax', 0)),
                    'north': float(request.args.get('ymax', 0))
                }
                zoom_level = int(request.args.get('zoom', 10))
                
                # Add more debug info
                print(f"DEBUG: Viewport bounds: {viewport_bounds}")
                print(f"DEBUG: Zoom level: {zoom_level}")
                print(f"DEBUG: Should sample: {should_sample}")
        
        if should_sample:
            # Apply smart sampling based on zoom and viewport
            modified_content = limit_features_randomly(
                upstream.content.decode('utf-8'), 
                viewport_bounds=viewport_bounds,
                zoom_level=zoom_level,
                max_features=1000
            )
            return Response(
                modified_content,
                status=upstream.status_code,
                content_type="application/json"
            )
    
    # Return original response for metadata or non-sampled queries
    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json")
    )

# 3. Add a new endpoint for viewport-based queries
@app.route("/api/landmarks/viewport", methods=["POST"])
def get_viewport_landmarks():
    """Get landmarks for specific viewport with automatic random sampling"""
    try:
        data = request.get_json()
        bounds = data.get('bounds', {})
        
        if not all(k in bounds for k in ['west', 'south', 'east', 'north']):
            return jsonify({"error": "Missing viewport bounds"}), 400
        
        # Create spatial query parameters
        envelope = f"{bounds['west']},{bounds['south']},{bounds['east']},{bounds['north']}"
        
        params = {
            'where': '1=1',  # Get all features in area
            'geometry': envelope,
            'geometryType': 'esriGeometryEnvelope',
            'spatialRel': 'esriSpatialRelIntersects',
            'inSR': '4326',
            'outSR': '4326',
            'f': 'geojson',
            'outFields': '*',
            'resultRecordCount': '5000'  # Get more features before sampling
        }
        
        # Add category filter if provided
        if 'category' in data and data['category']:
            params['where'] = f"main_category = '{data['category']}'"
        
        # Query ArcGIS
        response = requests.get(f"{ARCGIS_URL}/query", params=params)
        
        if response.status_code == 200:
            # Get zoom level from request
            zoom_level = data.get('zoom', 12)
            
            # Create viewport bounds for consistent sampling
            viewport_bounds = bounds
            
            # Apply smart sampling
            sampled_content = limit_features_randomly(
                response.content.decode('utf-8'), 
                viewport_bounds=viewport_bounds,
                zoom_level=zoom_level,
                max_features=1000
            )
            return Response(sampled_content, content_type="application/json")
        else:
            return jsonify({"error": "ArcGIS query failed"}), response.status_code
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)