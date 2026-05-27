"""
BIT Updates v2 - MCP Gateway (Model Context Protocol)
Tool Registration & Execution layer.
Exposes MongoDB operations as MCP tools for Gemini agents.
Follows the MCP architecture from the system design.
"""

import json
from datetime import datetime, timezone
from typing import Any, Callable
from bson import ObjectId
import structlog

from database import mongo

log = structlog.get_logger(__name__)


def serialize_doc(doc: dict) -> dict:
    """Convert MongoDB document to JSON-serializable format."""
    if doc is None:
        return {}
    result = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            result[k] = str(v)
        elif isinstance(v, datetime):
            result[k] = v.isoformat()
        elif isinstance(v, dict):
            result[k] = serialize_doc(v)
        elif isinstance(v, list):
            result[k] = [serialize_doc(i) if isinstance(i, dict) else (str(i) if isinstance(i, ObjectId) else i) for i in v]
        else:
            result[k] = v
    return result


class MCPGateway:
    """
    Model Context Protocol Gateway
    Registers MongoDB operations as tools callable by AI agents.
    This is the central execution layer between agents and data stores.
    """

    def __init__(self):
        self._tools: dict[str, dict] = {}
        self._register_all_tools()
        log.info(f"MCP Gateway initialized with {len(self._tools)} tools")

    def _register_all_tools(self):
        """Register all available MCP tools."""

        # ── Complaint Tools ──────────────────────────────────────
        self.register(
            name="create_complaint",
            description="Create a new complaint in MongoDB",
            parameters=["title", "description", "department", "user_id", "user_name", "user_email"],
            handler=self._create_complaint,
        )
        self.register(
            name="get_complaint",
            description="Retrieve a complaint by ID",
            parameters=["complaint_id"],
            handler=self._get_complaint,
        )
        self.register(
            name="update_complaint_status",
            description="Update complaint status and optionally add admin reply",
            parameters=["complaint_id", "status", "admin_reply", "admin_id"],
            handler=self._update_complaint_status,
        )
        self.register(
            name="list_complaints",
            description="List complaints with optional filters",
            parameters=["user_id", "department", "status", "limit", "skip"],
            handler=self._list_complaints,
        )
        self.register(
            name="search_complaints",
            description="Full-text search across complaints",
            parameters=["query", "limit"],
            handler=self._search_complaints,
        )
        self.register(
            name="get_similar_complaints",
            description="Find similar complaints using text similarity",
            parameters=["title", "description", "limit"],
            handler=self._get_similar_complaints,
        )

        # ── User Tools ───────────────────────────────────────────
        self.register(
            name="get_user",
            description="Get user by email or Firebase UID",
            parameters=["email", "firebase_uid"],
            handler=self._get_user,
        )
        self.register(
            name="upsert_user",
            description="Create or update user profile",
            parameters=["email", "name", "role", "department", "firebase_uid"],
            handler=self._upsert_user,
        )

        # ── Analytics Tools ──────────────────────────────────────
        self.register(
            name="get_complaint_stats",
            description="Get aggregated complaint statistics",
            parameters=["days", "department"],
            handler=self._get_complaint_stats,
        )
        self.register(
            name="log_analytics_event",
            description="Log an analytics event to MongoDB",
            parameters=["event_type", "data"],
            handler=self._log_event,
        )

        # ── Announcement Tools ───────────────────────────────────
        self.register(
            name="create_announcement",
            description="Create a campus announcement",
            parameters=["title", "content", "type", "admin_id", "departments"],
            handler=self._create_announcement,
        )
        self.register(
            name="list_announcements",
            description="List recent announcements",
            parameters=["limit", "type"],
            handler=self._list_announcements,
        )

    def register(self, name: str, description: str, parameters: list, handler: Callable):
        """Register a new MCP tool."""
        self._tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "handler": handler,
        }

    async def execute(self, tool_name: str, params: dict) -> dict:
        """Execute an MCP tool by name."""
        if tool_name not in self._tools:
            return {"error": f"Tool '{tool_name}' not found", "available": list(self._tools.keys())}

        try:
            tool = self._tools[tool_name]
            result = await tool["handler"](**params)
            return {"success": True, "result": result}
        except TypeError as e:
            return {"error": f"Invalid parameters: {e}"}
        except Exception as e:
            log.error("MCP tool execution failed", tool=tool_name, error=str(e))
            return {"error": str(e)}

    def list_tools(self) -> list[dict]:
        """List all registered tools (for MCP discovery)."""
        return [
            {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
            for t in self._tools.values()
        ]

    # ── Tool Handlers ────────────────────────────────────────────

    async def _create_complaint(self, title: str, description: str, department: str,
                                 user_id: str, user_name: str, user_email: str, **kwargs) -> dict:
        from backend.utils.database import complaint_document
        doc = complaint_document(title, description, department, user_id, user_name, user_email)
        col = mongo.get_collection("complaints")
        result = await col.insert_one(doc)
        return {"complaint_id": str(result.inserted_id), "status": "created"}

    async def _get_complaint(self, complaint_id: str, **kwargs) -> dict:
        col = mongo.get_collection("complaints")
        doc = await col.find_one({"_id": ObjectId(complaint_id)})
        return serialize_doc(doc) if doc else {"error": "Not found"}

    async def _update_complaint_status(self, complaint_id: str, status: str,
                                        admin_reply: str = None, admin_id: str = None, **kwargs) -> dict:
        col = mongo.get_collection("complaints")
        update = {
            "$set": {
                "status": status,
                "updated_at": datetime.now(timezone.utc),
            }
        }
        if admin_reply:
            update["$set"]["admin_reply"] = admin_reply
        if status == "resolved":
            update["$set"]["resolved_at"] = datetime.now(timezone.utc)
        if admin_id:
            update["$set"]["assigned_to"] = admin_id

        result = await col.update_one({"_id": ObjectId(complaint_id)}, update)
        return {"modified": result.modified_count, "status": status}

    async def _list_complaints(self, user_id: str = None, department: str = None,
                                status: str = None, limit: int = 20, skip: int = 0, **kwargs) -> list:
        col = mongo.get_collection("complaints")
        query = {}
        if user_id:
            query["user_id"] = user_id
        if department:
            query["department"] = department
        if status:
            query["status"] = status

        docs = await col.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
        return [serialize_doc(d) for d in docs]

    async def _search_complaints(self, query: str, limit: int = 10, **kwargs) -> list:
        col = mongo.get_collection("complaints")
        docs = await col.find(
            {"$text": {"$search": query}},
            {"score": {"$meta": "textScore"}},
        ).sort([("score", {"$meta": "textScore"})]).limit(limit).to_list(limit)
        return [serialize_doc(d) for d in docs]

    async def _get_similar_complaints(self, title: str, description: str = "", limit: int = 3, **kwargs) -> list:
        search_text = f"{title} {description}"
        return await self._search_complaints(search_text[:200], limit)

    async def _get_user(self, email: str = None, firebase_uid: str = None, **kwargs) -> dict:
        col = mongo.get_collection("users")
        query = {}
        if email:
            query["email"] = email
        elif firebase_uid:
            query["firebase_uid"] = firebase_uid
        else:
            return {"error": "email or firebase_uid required"}
        doc = await col.find_one(query)
        return serialize_doc(doc) if doc else {}

    async def _upsert_user(self, email: str, name: str, role: str = "student",
                            department: str = None, firebase_uid: str = None, **kwargs) -> dict:
        from backend.utils.database import user_document
        col = mongo.get_collection("users")
        doc = user_document(email, name, role, department, firebase_uid=firebase_uid)
        result = await col.update_one(
            {"email": email},
            {"$set": doc, "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        return {"upserted": result.upserted_id is not None, "modified": result.modified_count}

    async def _get_complaint_stats(self, days: int = 30, department: str = None, **kwargs) -> dict:
        from backend.agents.analytics_agent import AnalyticsAgent
        agent = AnalyticsAgent()
        result = await agent._aggregate_stats(days)
        if department:
            dept_result = await agent.get_department_report(department, days)
            result["department_report"] = dept_result
        return result

    async def _log_event(self, event_type: str, data: dict = None, **kwargs) -> dict:
        col = mongo.get_collection("analytics")
        await col.insert_one({
            "event_type": event_type,
            "data": data or {},
            "timestamp": datetime.now(timezone.utc),
        })
        return {"logged": True}

    async def _create_announcement(self, title: str, content: str, type: str = "info",
                                    admin_id: str = None, departments: list = None, **kwargs) -> dict:
        col = mongo.get_collection("announcements")
        doc = {
            "title": title,
            "content": content,
            "type": type,  # info | warning | emergency | event
            "admin_id": admin_id,
            "departments": departments or ["all"],
            "created_at": datetime.now(timezone.utc),
            "is_active": True,
            "views": 0,
        }
        result = await col.insert_one(doc)
        return {"announcement_id": str(result.inserted_id)}

    async def _list_announcements(self, limit: int = 10, type: str = None, **kwargs) -> list:
        col = mongo.get_collection("announcements")
        query = {"is_active": True}
        if type:
            query["type"] = type
        docs = await col.find(query).sort("created_at", -1).limit(limit).to_list(limit)
        return [serialize_doc(d) for d in docs]


# Global MCP gateway instance
mcp_gateway = MCPGateway()
