"""Optional FastAPI mount for embedded inspection; imports deferred until use.

The host supplies authentication AND per-agent grants as a FastAPI dependency.
No anonymous/default-VPC access policy, login flow or serving lifecycle is added.
"""
import json
from pathlib import Path
import re
from datetime import datetime, timezone

from adkl.embedded import InspectionRegistry, InspectionUnavailable


def mount_inspection(app, registry: InspectionRegistry, *, authorize,
                     enabled: bool = False, prefix: str = "/adkl",
                     portal_directory=None) -> bool:
    """Mount GET-only APIs and optionally a separately built connected Flutter UI.

    authorize is a required FastAPI dependency returning a set/frozenset of
    allowed agent IDs, or raising HTTPException. It must validate identity on
    every request, including static assets. Empty grants are denied. SDK install
    alone starts nothing; enabled=False adds no routes and opens no files.
    """
    if type(enabled) is not bool:
        raise TypeError("enabled must be bool")
    if not enabled:
        return False
    if not callable(authorize):
        raise TypeError("An authorization dependency is required")
    if not isinstance(registry, InspectionRegistry):
        raise TypeError("Expected InspectionRegistry")
    if not re.fullmatch(r"(?:/[a-zA-Z0-9_-]+)+", prefix):
        raise ValueError("Invalid inspection prefix")
    # Reserve prefixes across SDK mounts (including FastAPI's lazy routers).
    mounted = getattr(app.state, "_adkl_inspection_prefixes", set())
    if any(prefix == other or prefix.startswith(other + "/")
           or other.startswith(prefix + "/") for other in mounted):
        raise ValueError("Inspection prefix already registered")
    # Refuse common ambiguous overlap rather than hide a host route/mount.
    for route in app.routes:
        path = getattr(route, "path", "")
        if path == prefix or path.startswith(prefix + "/"):
            raise ValueError("Inspection prefix already registered")

    from fastapi import APIRouter, Depends, HTTPException, Request
    from fastapi.responses import FileResponse, RedirectResponse, Response

    files = _portal_files(portal_directory) if portal_directory is not None else {}

    async def grants(request: Request, allowed=Depends(authorize)):
        if request.query_params or "range" in request.headers:
            raise HTTPException(400, "Invalid inspection request")
        if (type(allowed) not in (set, frozenset) or not allowed
                or len(allowed) > 32 or any(type(key) is not str or not re.fullmatch(
                    r"[a-z0-9][a-z0-9-]{0,63}", key) for key in allowed)):
            raise HTTPException(403, "Inspection access denied")
        return frozenset(allowed)

    router = APIRouter(prefix=prefix, include_in_schema=False)
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
               "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
               "Content-Security-Policy": (
                   "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; "
                   "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                   "font-src 'self'; connect-src 'self'; worker-src 'self' blob:; "
                   "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
               )}

    def response(data, *, digest=None):
        body = data if type(data) is bytes else json.dumps(
            data, ensure_ascii=True, allow_nan=False).encode()
        return Response(body, media_type="application/json",
                        headers={**headers, **({"ETag": f'"{digest}"'} if digest else {})})

    def read(agent_id, allowed):
        if agent_id not in allowed:
            raise HTTPException(404, "Not found")
        try:
            return registry.snapshot(agent_id)
        except InspectionUnavailable:
            raise HTTPException(503, "Inspection unavailable") from None

    @router.get("/api/v1/agents")
    def agents(allowed=Depends(grants)):
        return response({"schemaVersion": 1, "mode": "connected",
                         "agents": registry.agents(allowed)})

    @router.get("/api/v1/agents/{agent_id}/inspection")
    def inspection(agent_id: str, allowed=Depends(grants)):
        body, digest = read(agent_id, allowed)
        # A recent read observes publication, not fresh collection. capturedAt
        # and generation remain unchanged until the host explicitly republishes.
        return response({
            "schemaVersion": 1, "mode": "connected", "snapshot": json.loads(body),
            "gatewayReceivedAt": datetime.now(timezone.utc).isoformat(),
            "observationValidForSeconds": 15, "snapshotDigest": digest,
        })

    @router.get("/api/v1/agents/{agent_id}/declarations")
    def declarations(agent_id: str, allowed=Depends(grants)):
        body, digest = read(agent_id, allowed)
        return response(body, digest=digest)

    if portal_directory is not None:
        @router.get("")
        def redirect(allowed=Depends(grants)):
            # Relative Location preserves a reverse proxy's external root_path.
            return RedirectResponse(prefix.rsplit("/", 1)[-1] + "/", headers=headers)

        @router.get("/{asset_path:path}")
        def asset(asset_path: str, allowed=Depends(grants)):
            path = files.get(asset_path or "index.html")
            if path is None:
                raise HTTPException(404, "Not found")
            return FileResponse(path, headers=headers)

    app.include_router(router)
    app.state._adkl_inspection_prefixes = {*mounted, prefix}
    return True


def _portal_files(directory):
    """Inventory a trusted immutable web build once, never export host content.

    Runtime mutation/symlinks are unsupported: mount the directory read-only.
    No SPA fallback, source maps, hidden files or service worker are published.
    """
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Expected immutable connected portal web directory")
    root = root.resolve()
    files = {}
    total = 0
    count = 0
    for path in root.rglob("*"):
        count += 1
        if count > 1024 or path.is_symlink():
            raise ValueError("Unsupported portal inventory")
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix()
        if (any(part.startswith(".") for part in path.relative_to(root).parts)
                or path.suffix not in {".html", ".js", ".json", ".bin", ".wasm",
                                       ".css", ".png", ".ico", ".svg", ".woff", ".woff2", ".ttf", ".otf", ".frag", ".txt"}
                or path.name == "flutter_service_worker.js"):
            continue
        size = path.stat().st_size
        total += size
        if size > 16 * 1024 * 1024 or total > 96 * 1024 * 1024 or len(files) >= 512:
            raise ValueError("Portal artifact exceeds bounds")
        files[name] = path
    if not {"index.html", "flutter_bootstrap.js", "main.dart.js"} <= files.keys():
        raise ValueError("Incomplete connected Flutter web build")
    return files
