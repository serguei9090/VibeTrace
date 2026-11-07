import json
import sys
import urllib.error
import urllib.request

try:
    print("Testing GET http://localhost:8000/graph ...")
    with urllib.request.urlopen("http://localhost:8000/graph", timeout=10) as response:
        if response.status != 200:
            print("Status Code:", response.status)
            sys.exit(1)

        data = json.loads(response.read().decode())

    if "nodes" not in data or "edges" not in data:
        print("ERROR: Response missing 'nodes' or 'edges' keys.")
        print("Keys found:", data.keys())
        sys.exit(1)

    nodes = data["nodes"]
    edges = data["edges"]

    print(f"Nodes count: {len(nodes)}")
    print(f"Edges count: {len(edges)}")

    if len(nodes) > 0:
        print("\nSample Node:")
        print(json.dumps(nodes[0], indent=2))

        # Check for required fields expected by React Flow
        required_node_keys = ["id", "data", "position"]
        sample = nodes[0]
        missing = [k for k in required_node_keys if k not in sample]
        if missing:
            print(f"WARNING: Sample node missing keys expected by React Flow: {missing}")
    else:
        print("\nWARNING: No nodes returned. Database might be empty.")

except (urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError, TimeoutError) as e:
    print(f"Request failed: {e}")
    sys.exit(1)
