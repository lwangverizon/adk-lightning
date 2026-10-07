"""Internal method/path dispatch for the local console API.

Routing does not acquire locks, read bodies, import factories or own workspace
state. Handler._guard runs first; endpoint methods retain validation and policy.
"""
import re
import urllib.parse


GET_ROUTES = {
    "/api/health": "_health",
    "/api/agents": "_agents",
    "/api/project": "_project_status",
    "/api/project/artifact": "_project_artifact",
    "/api/audit": "_audit",
    "/api/catalog": "_catalog_route",
    "/api/workflows": "_workflow_list",
    "/api/execution": "_execution_status",
    "/api/chat": "_chat_status",
}
GET_ENV_ROUTES = {
    "/api/agent": "_agent",
    "/api/findings": "_findings",
    "/api/skills": "_skill_list",
}
POST_ROUTES = {
    "/api/execution/select": "_execution_select",
    "/api/chat": "_chat_send",
    "/api/chat/clear": "_chat_clear",
    "/api/agents/select": "_agents_select",
}
PROJECT_ROUTES = frozenset({
    "/api/agents", "/api/project/prepare", "/api/project/release",
    "/api/project/inspect", "/api/project/credentials", "/api/project/baseline",
    "/api/project/candidate", "/api/project/doctor",
})


def get(handler):
    path = urllib.parse.urlparse(handler.path).path
    env = handler._query().get("env") or None
    if path in GET_ROUTES:
        return getattr(handler, GET_ROUTES[path])()
    if path in GET_ENV_ROUTES:
        return getattr(handler, GET_ENV_ROUTES[path])(env)
    match = re.match(r"^/api/skills/([a-z0-9-]+)$", path)
    if match:
        return handler._skill_get(match.group(1), env)
    return handler._static(path)


def put(handler):
    path = urllib.parse.urlparse(handler.path).path
    if path == "/api/features":
        return handler._features_put()
    match = re.match(r"^/api/skills/([a-z0-9-]+)$", path)
    if not match:
        return handler._send(404, {"error": "not found"})
    return handler._skill_put(match.group(1), handler._query().get("env") or None)


def post(handler):
    path = urllib.parse.urlparse(handler.path).path
    if path in PROJECT_ROUTES:
        return handler._project_post(path)
    if path in POST_ROUTES:
        return getattr(handler, POST_ROUTES[path])()
    match = re.match(r"^/api/skills/([a-z0-9-]+)/lint$", path)
    if not match:
        return handler._send(404, {"error": "not found"})
    return handler._skill_lint_dry(match.group(1), handler._query().get("env") or None)
