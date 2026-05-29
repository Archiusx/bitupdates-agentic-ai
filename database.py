"""
BIT Updates v2 - MongoDB Database Layer
Primary backbone for all complaint, user, analytics, and route data.
Uses Motor (async PyMongo) for non-blocking operations.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import IndexModel, ASCENDING, DESCENDING, TEXT
from pymongo.errors import CollectionInvalid
import structlog

from config import settings

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# MongoDB Collections
# ---------------------------------------------------------------------------
COLLECTIONS = {
    "complaints": "complaints",
    "users": "users",
    "departments": "departments",
    "announcements": "announcements",
    "analytics": "analytics_events",
    "agent_logs": "agent_logs",
    "embeddings": "complaint_embeddings",
    "notifications": "notifications",
    "resources": "resources",
    "sessions": "sessions",
    "feedback": "feedback",
}


class MongoDBClient:
    """Singleton async MongoDB client for BIT Updates."""

    _instance: Optional["MongoDBClient"] = None
    client: Optional[AsyncIOMotorClient] = None
    db: Optional[AsyncIOMotorDatabase] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def connect(self):
        """Initialize MongoDB connection and create indexes."""
        if self.client is not None:
            return

        log.info("Connecting to MongoDB Atlas...")
        self.client = AsyncIOMotorClient(
            settings.MONGODB_URI,
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
            socketTimeoutMS=10000,
            maxPoolSize=5,
            minPoolSize=0,
            tls=True,
            tlsInsecure=True,
        )
        self.db = self.client[settings.MONGODB_DB_NAME]

        # Verify connection
        await self.client.admin.command("ping")
        log.info("✅ MongoDB connected", db=settings.MONGODB_DB_NAME)

        # Create collections and indexes
        await self._setup_collections()
        await self._create_indexes()

    async def disconnect(self):
        if self.client:
            self.client.close()
            self.client = None
            self.db = None
            log.info("MongoDB disconnected")

    async def _setup_collections(self):
        """Create collections with validators if they don't exist."""
        existing = await self.db.list_collection_names()

        # Complaints collection with JSON schema validation
        if COLLECTIONS["complaints"] not in existing:
            try:
                await self.db.create_collection(
                    COLLECTIONS["complaints"],
                    validator={
                        "$jsonSchema": {
                            "bsonType": "object",
                            "required": ["title", "description", "department", "user_id", "status"],
                            "properties": {
                                "title": {"bsonType": "string", "minLength": 5},
                                "description": {"bsonType": "string", "minLength": 20},
                                "department": {"bsonType": "string"},
                                "user_id": {"bsonType": "string"},
                                "status": {
                                    "bsonType": "string",
                                    "enum": ["open", "in_progress", "resolved", "closed", "escalated"],
                                },
                            },
                        }
                    },
                )
                log.info("Created complaints collection")
            except CollectionInvalid:
                pass

    async def _create_indexes(self):
        """Create optimized indexes for all collections."""
        log.info("Creating MongoDB indexes...")

        # Complaints indexes
        await self.db[COLLECTIONS["complaints"]].create_indexes([
            IndexModel([("user_id", ASCENDING)]),
            IndexModel([("department", ASCENDING)]),
            IndexModel([("status", ASCENDING)]),
            IndexModel([("priority", ASCENDING)]),
            IndexModel([("created_at", DESCENDING)]),
            IndexModel([("title", TEXT), ("description", TEXT)]),  # Full-text search
            IndexModel([("ai_category", ASCENDING)]),
            IndexModel([("assigned_to", ASCENDING)]),
        ])

        # Users indexes
        await self.db[COLLECTIONS["users"]].create_indexes([
            IndexModel([("email", ASCENDING)], unique=True),
            IndexModel([("role", ASCENDING)]),
            IndexModel([("department", ASCENDING)]),
        ])

        # Analytics indexes
        await self.db[COLLECTIONS["analytics"]].create_indexes([
            IndexModel([("event_type", ASCENDING)]),
            IndexModel([("timestamp", DESCENDING)]),
            IndexModel([("department", ASCENDING), ("timestamp", DESCENDING)]),
        ])

        # Agent logs indexes
        await self.db[COLLECTIONS["agent_logs"]].create_indexes([
            IndexModel([("complaint_id", ASCENDING)]),
            IndexModel([("agent_name", ASCENDING)]),
            IndexModel([("timestamp", DESCENDING)]),
        ])

        # Embeddings TTL index (auto-expire stale vectors after 30 days)
        await self.db[COLLECTIONS["embeddings"]].create_indexes([
            IndexModel([("complaint_id", ASCENDING)], unique=True),
            IndexModel([("created_at", ASCENDING)], expireAfterSeconds=2592000),
        ])

        log.info("✅ MongoDB indexes created")

    def get_collection(self, name: str):
        """Get a collection by logical name. Auto-connects if not yet connected (serverless-safe)."""
        if name not in COLLECTIONS:
            raise ValueError(f"Unknown collection: {name}. Valid: {list(COLLECTIONS.keys())}")
        # Lazy init: if client was never connected (lifespan skipped on Vercel cold start)
        if self.client is None:
            self.client = AsyncIOMotorClient(
                settings.MONGODB_URI,
                serverSelectionTimeoutMS=10000,
                connectTimeoutMS=10000,
                socketTimeoutMS=10000,
                maxPoolSize=5,
                minPoolSize=0,
                tls=True,
                tlsInsecure=True,
            )
            self.db = self.client[settings.MONGODB_DB_NAME]
        return self.db[COLLECTIONS[name]]

    async def health_check(self) -> dict:
        try:
            # Auto-connect if not connected yet (Vercel serverless cold start)
            if self.client is None:
                await self.connect()
            result = await self.client.admin.command("ping")
            stats = await self.db.command("dbStats")
            return {
                "status": "healthy",
                "collections": stats.get("collections", 0),
                "data_size_mb": round(stats.get("dataSize", 0) / 1024 / 1024, 2),
                "indexes": stats.get("indexes", 0),
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}


# Global singleton
mongo = MongoDBClient()


# ---------------------------------------------------------------------------
# Data Models / Document Schemas
# ---------------------------------------------------------------------------

def complaint_document(
    title: str,
    description: str,
    department: str,
    user_id: str,
    user_name: str,
    user_email: str,
    category: str = "general",
    priority: str = "medium",
    attachments: list = None,
) -> dict:
    """Build a new complaint document."""
    now = datetime.now(timezone.utc)
    return {
        "title": title,
        "description": description,
        "department": department,
        "user_id": user_id,
        "user_name": user_name,
        "user_email": user_email,
        "category": category,
        "priority": priority,
        "status": "open",
        "created_at": now,
        "updated_at": now,
        "resolved_at": None,
        "assigned_to": None,
        "admin_reply": None,
        "tags": [],
        "attachments": attachments or [],
        # AI-enriched fields (populated by agents)
        "ai_category": None,
        "ai_priority": None,
        "ai_sentiment": None,
        "ai_summary": None,
        "ai_suggested_department": None,
        "urgency_score": 0.5,
        "similar_complaints": [],
        "agent_processed": False,
        "agent_log_ids": [],
        "escalated": False,
        "escalation_reason": None,
        "resolution_time_hours": None,
        "satisfaction_rating": None,
        "view_count": 0,
        "upvotes": 0,
    }


def user_document(
    email: str,
    name: str,
    role: str = "student",
    department: str = None,
    photo_url: str = None,
    firebase_uid: str = None,
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "email": email,
        "name": name,
        "role": role,  # student | admin | faculty | department_head
        "department": department,
        "photo_url": photo_url,
        "firebase_uid": firebase_uid,
        "created_at": now,
        "last_active": now,
        "is_active": True,
        "complaint_count": 0,
        "resolved_count": 0,
        "notification_preferences": {
            "email": True,
            "whatsapp": False,
            "slack": False,
        },
        "ai_profile": {
            "most_common_category": None,
            "avg_urgency": 0.5,
            "satisfaction_score": None,
        },
    }


def agent_log_document(
    agent_name: str,
    complaint_id: str,
    input_data: dict,
    output_data: dict,
    execution_time_ms: float,
    success: bool,
    error: str = None,
) -> dict:
    return {
        "agent_name": agent_name,
        "complaint_id": complaint_id,
        "input_data": input_data,
        "output_data": output_data,
        "execution_time_ms": execution_time_ms,
        "success": success,
        "error": error,
        "timestamp": datetime.now(timezone.utc),
        "model_used": "gemini-2.0-flash",
        "tokens_used": output_data.get("tokens_used", 0),
    }
