"""Standard newline-delimited MCP stdio bridge to the read-only HTTP endpoint.

Configuration comes from environment variables, not source code or CLI arguments.
"""
import json, os, sys
import httpx

def main():
    base = os.environ.get("ECHOLEDGER_URL", "http://127.0.0.1:8000").rstrip("/")
    token = os.environ.get("MCP_TOKEN", "")
    if not token:
        print("MCP_TOKEN is required", file=sys.stderr)
        raise SystemExit(2)
    with httpx.Client(timeout=30) as client:
        for line in sys.stdin.buffer:
            req = None
            if len(line) > 128000:
                continue
            try:
                req = json.loads(line)
                response = client.post(base + "/mcp", headers={"Authorization": "Bearer " + token,
                    "Accept": "application/json, text/event-stream"}, json=req)
                if response.status_code == 202:
                    continue
                response.raise_for_status()
                sys.stdout.write(json.dumps(response.json(), ensure_ascii=False) + "\n")
                sys.stdout.flush()
            except Exception:
                # Keep stdout protocol-only and keep credentials out of stderr.
                if isinstance(req, dict) and "id" in req:
                    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "error": {
                        "code": -32603, "message": "EchoLedger transport error"}}) + "\n")
                    sys.stdout.flush()

if __name__ == "__main__":
    main()
