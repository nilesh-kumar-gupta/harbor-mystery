"""Sequential newline-delimited JSON-RPC transport for the tool registry."""
import json
import sys

from harbor.tools import Context, REGISTRY


def rpc_error(ident, code, message):
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}


def handle(ctx, request):
    ctx.require_turn = True
    if not isinstance(request, dict):
        return rpc_error(None, -32600, "Invalid Request")
    ident = request.get("id")
    method = request.get("method")
    if request.get("jsonrpc") != "2.0" or not isinstance(method, str) or (
            "id" in request and (isinstance(ident, (bool, list, dict)))):
        return rpc_error(None, -32600, "Invalid Request")
    notification = "id" not in request
    params = request.get("params", {})
    if not isinstance(params, dict):
        return None if notification else rpc_error(ident, -32602, "Invalid params")
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "harbor", "version": "0.1.0"}}
    elif method == "ping":
        result = {}
    elif method == "notifications/initialized":
        return None
    elif method == "tools/list":
        result = {"tools": [
            {"name": tool["name"], "description": tool["description"],
             "inputSchema": tool["input_schema"], "_meta": {"anthropic/alwaysLoad": True}}
            for audience in ("gm", "npc", "referee") for tool in REGISTRY.schemas(audience)]}
    elif method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or not isinstance(params.get("arguments", {}), dict):
            return None if notification else rpc_error(ident, -32602, "Invalid params")
        meta = params.get("_meta", request.get("_meta", {}))
        if not isinstance(meta, dict):
            return None if notification else rpc_error(ident, -32602, "Invalid metadata")
        tool = REGISTRY.get(name)
        audience = tool.audience if tool and tool.audience != "engine" else "gm"
        value = REGISTRY.call(ctx, name, params.get("arguments", {}), audience, meta=meta)
        result = {"content": [{"type": "text", "text": json.dumps(value)}], "isError": False}
    else:
        return None if notification else rpc_error(ident, -32601, "Method not found")
    return None if notification else {"jsonrpc": "2.0", "id": ident, "result": result}


def main():
    ctx = Context()
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
            except Exception:
                response = rpc_error(None, -32700, "Parse error")
            else:
                try:
                    response = handle(ctx, request)
                except Exception:
                    response = rpc_error(request.get("id") if isinstance(request, dict) else None,
                                         -32603, "Internal error")
            if response is not None:
                print(json.dumps(response), flush=True)
    finally:
        from harbor.tools.session_tools import release_lease
        if ctx.game is not None:
            ctx.tracer.close_open()
            ctx.game.conn.close()
        release_lease(ctx)


if __name__ == "__main__":
    main()
