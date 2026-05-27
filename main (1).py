"""
BIT Updates v2 - FastAPI Main Application
Cloud Run / API Gateway layer with auth, rate limiting, and all endpoints.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional
from bson import ObjectId

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
import structlog

from backend.utils.config import settings
from backend.utils.database import mongo, complaint_document, COLLECTIONS
from backend.agents.orchestrator import orchestrator
from backend.mcp.gateway import mcp_gateway, serialize_doc

log = structlog.get_logger(__name__)


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    log.info("🚀 BIT Updates v2 starting...")
    await mongo.connect()
    log.info("✅ All services initialized")
    yield
    log.info("Shutting down...")
    await mongo.disconnect()


# ── App Setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="BIT Updates v2 - AI Campus Support Platform",
    description="Multi-agent AI system for campus complaint management powered by Gemini 2.0 + MongoDB",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic Models ──────────────────────────────────────────────────────────

class ComplaintCreate(BaseModel):
    title: str = Field(..., min_length=5, max_length=200)
    description: str = Field(..., min_length=20, max_length=5000)
    department: str
    user_id: str
    user_name: str
    user_email: str
    category: str = "general"
    priority: str = "medium"
    attachments: list = []


class ComplaintUpdate(BaseModel):
    status: str
    admin_reply: Optional[str] = None
    admin_id: Optional[str] = None
    priority: Optional[str] = None


class ChatMessage(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)
    user_id: Optional[str] = None
    student_name: Optional[str] = "Student"
    session_id: Optional[str] = None


class UserUpsert(BaseModel):
    email: str
    name: str
    role: str = "student"
    department: Optional[str] = None
    firebase_uid: Optional[str] = None
    photo_url: Optional[str] = None


class AnnouncementCreate(BaseModel):
    title: str
    content: str
    type: str = "info"
    admin_id: str
    departments: list = ["all"]


class MCPToolCall(BaseModel):
    tool: str
    params: dict = {}


# ── Helper ───────────────────────────────────────────────────────────────────

def object_id_or_error(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid ID format: {id_str}")


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
async def root():
    return {
        "service": "BIT Updates v2",
        "version": "2.0.0",
        "status": "operational",
        "architecture": "Multi-Agent AI + MongoDB Atlas",
        "agents": ["Orchestrator", "Triage", "Sentiment", "Routing", "Chatbot", "Analytics", "Notification"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health", tags=["Health"])
async def health():
    db_health = await mongo.health_check()
    return {
        "status": "healthy" if db_health["status"] == "healthy" else "degraded",
        "mongodb": db_health,
        "agents": "operational",
        "mcp_tools": len(mcp_gateway.list_tools()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Complaints ────────────────────────────────────────────────────────────────

@app.post("/api/complaints", tags=["Complaints"])
async def create_complaint(data: ComplaintCreate, background_tasks: BackgroundTasks):
    """Create a new complaint and trigger AI agent pipeline."""

    # 1. Save to MongoDB first
    doc = complaint_document(
        title=data.title,
        description=data.description,
        department=data.department,
        user_id=data.user_id,
        user_name=data.user_name,
        user_email=data.user_email,
        category=data.category,
        priority=data.priority,
        attachments=data.attachments,
    )
    col = mongo.get_collection("complaints")
    result = await col.insert_one(doc)
    complaint_id = str(result.inserted_id)

    # 2. Trigger AI pipeline in background (non-blocking)
    background_tasks.add_task(
        orchestrator.process_new_complaint,
        complaint_id,
        data.model_dump(),
    )

    # 3. Update user complaint count
    background_tasks.add_task(_increment_user_complaint_count, data.user_id)

    return {
        "complaint_id": complaint_id,
        "status": "open",
        "message": "Complaint submitted successfully. AI agents are processing your request.",
        "ai_processing": True,
    }


@app.get("/api/complaints", tags=["Complaints"])
async def list_complaints(
    user_id: Optional[str] = None,
    department: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
    skip: int = 0,
):
    """List complaints with optional filters."""
    col = mongo.get_collection("complaints")
    query = {}
    if user_id:
        query["user_id"] = user_id
    if department:
        query["department"] = department
    if status:
        query["status"] = status

    docs = await col.find(query).sort("created_at", -1).skip(skip).limit(min(limit, 100)).to_list(limit)
    total = await col.count_documents(query)

    return {
        "complaints": [serialize_doc(d) for d in docs],
        "total": total,
        "limit": limit,
        "skip": skip,
    }


@app.get("/api/complaints/{complaint_id}", tags=["Complaints"])
async def get_complaint(complaint_id: str):
    """Get a single complaint by ID."""
    col = mongo.get_collection("complaints")
    doc = await col.find_one({"_id": object_id_or_error(complaint_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Complaint not found")

    # Increment view count
    await col.update_one({"_id": doc["_id"]}, {"$inc": {"view_count": 1}})
    return serialize_doc(doc)


@app.patch("/api/complaints/{complaint_id}", tags=["Complaints"])
async def update_complaint(complaint_id: str, data: ComplaintUpdate, background_tasks: BackgroundTasks):
    """Update complaint status (admin action)."""
    col = mongo.get_collection("complaints")
    old_doc = await col.find_one({"_id": object_id_or_error(complaint_id)})
    if not old_doc:
        raise HTTPException(status_code=404, detail="Complaint not found")

    update = {
        "$set": {
            "status": data.status,
            "updated_at": datetime.now(timezone.utc),
        }
    }
    if data.admin_reply:
        update["$set"]["admin_reply"] = data.admin_reply
    if data.priority:
        update["$set"]["priority"] = data.priority
    if data.status == "resolved":
        update["$set"]["resolved_at"] = datetime.now(timezone.utc)
    if data.admin_id:
        update["$set"]["assigned_to"] = data.admin_id

    await col.update_one({"_id": object_id_or_error(complaint_id)}, update)

    # Background: send notification to student
    old_doc_serialized = serialize_doc(old_doc)
    notification_data = {
        **old_doc_serialized,
        "complaint_id": complaint_id,
        "old_status": old_doc.get("status"),
        "new_status": data.status,
        "admin_reply": data.admin_reply,
        "event_type": "status_update",
    }
    background_tasks.add_task(orchestrator.process_complaint_update, complaint_id, notification_data)

    return {"message": "Complaint updated", "status": data.status}


@app.delete("/api/complaints/{complaint_id}", tags=["Complaints"])
async def delete_complaint(complaint_id: str, user_id: str):
    """Delete a complaint (owner only)."""
    col = mongo.get_collection("complaints")
    result = await col.delete_one(
        {"_id": object_id_or_error(complaint_id), "user_id": user_id}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Complaint not found or not authorized")
    return {"message": "Complaint deleted"}


# ── Chat / AI Assistant ───────────────────────────────────────────────────────

@app.post("/api/chat", tags=["AI Assistant"])
async def chat(message: ChatMessage):
    """Chat with BITS AI assistant (RAG-powered FAQ)."""
    result = await orchestrator.chatbot.run(message.model_dump())
    return {
        "response": result.get("response", "I couldn't process your request."),
        "confidence": result.get("confidence", 0.0),
        "timestamp": result.get("timestamp"),
    }


# ── Analytics ─────────────────────────────────────────────────────────────────

@app.get("/api/analytics", tags=["Analytics"])
async def get_analytics(days: int = 30, department: Optional[str] = None):
    """Get AI-powered analytics insights."""
    result = await orchestrator.analytics.run({"days": days, "department": department})
    return result


@app.get("/api/analytics/department/{dept}", tags=["Analytics"])
async def get_department_analytics(dept: str, days: int = 30):
    """Get department-specific analytics."""
    result = await orchestrator.analytics.get_department_report(dept, days)
    return result


# ── Users ─────────────────────────────────────────────────────────────────────

@app.post("/api/users", tags=["Users"])
async def upsert_user(data: UserUpsert):
    """Create or update user profile in MongoDB."""
    result = await mcp_gateway.execute("upsert_user", data.model_dump())
    return result


@app.get("/api/users/{email}", tags=["Users"])
async def get_user(email: str):
    """Get user by email."""
    result = await mcp_gateway.execute("get_user", {"email": email})
    if "error" in result:
        raise HTTPException(status_code=404, detail="User not found")
    return result


# ── Announcements ─────────────────────────────────────────────────────────────

@app.post("/api/announcements", tags=["Announcements"])
async def create_announcement(data: AnnouncementCreate):
    result = await mcp_gateway.execute("create_announcement", data.model_dump())
    return result


@app.get("/api/announcements", tags=["Announcements"])
async def list_announcements(limit: int = 10, type: Optional[str] = None):
    col = mongo.get_collection("announcements")
    query = {"is_active": True}
    if type:
        query["type"] = type
    docs = await col.find(query).sort("created_at", -1).limit(limit).to_list(limit)
    return {"announcements": [serialize_doc(d) for d in docs]}


# ── MCP Gateway ───────────────────────────────────────────────────────────────

@app.get("/api/mcp/tools", tags=["MCP Gateway"])
async def list_mcp_tools():
    """List all available MCP tools."""
    return {"tools": mcp_gateway.list_tools(), "count": len(mcp_gateway.list_tools())}


@app.post("/api/mcp/execute", tags=["MCP Gateway"])
async def execute_mcp_tool(call: MCPToolCall):
    """Execute an MCP tool directly."""
    result = await mcp_gateway.execute(call.tool, call.params)
    if not result.get("success") and "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ── Agent Direct Access ───────────────────────────────────────────────────────

@app.post("/api/agents/triage", tags=["Agents"])
async def run_triage(data: dict):
    return await orchestrator.triage.run(data)


@app.post("/api/agents/sentiment", tags=["Agents"])
async def run_sentiment(data: dict):
    return await orchestrator.sentiment.run(data)


@app.post("/api/agents/route", tags=["Agents"])
async def run_routing(data: dict):
    return await orchestrator.routing.run(data)


@app.get("/api/agents/logs/{complaint_id}", tags=["Agents"])
async def get_agent_logs(complaint_id: str):
    """Get all agent execution logs for a complaint."""
    col = mongo.get_collection("agent_logs")
    logs = await col.find({"complaint_id": complaint_id}).sort("timestamp", 1).to_list(50)
    return {"logs": [serialize_doc(l) for l in logs]}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _increment_user_complaint_count(user_id: str):
    try:
        col = mongo.get_collection("users")
        await col.update_one(
            {"firebase_uid": user_id},
            {"$inc": {"complaint_count": 1}},
        )
    except Exception:
        pass
