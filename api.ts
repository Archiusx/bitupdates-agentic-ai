/**
 * BIT Updates v2 - API Client
 * Connects React frontend to Python FastAPI backend.
 * All data now flows through MongoDB via the AI agent layer.
 */

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

class APIError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "APIError";
  }
}

async function request<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
    ...options,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new APIError(res.status, err.detail || "Request failed");
  }

  return res.json() as Promise<T>;
}

// ── Complaints ────────────────────────────────────────────────────────────────

export interface Complaint {
  _id?: string;
  title: string;
  description: string;
  department: string;
  user_id: string;
  user_name: string;
  user_email: string;
  category?: string;
  priority?: string;
  status?: string;
  created_at?: string;
  updated_at?: string;
  admin_reply?: string;
  ai_category?: string;
  ai_priority?: string;
  ai_sentiment?: string;
  ai_summary?: string;
  urgency_score?: number;
  agent_processed?: boolean;
  escalated?: boolean;
}

export interface ComplaintListResponse {
  complaints: Complaint[];
  total: number;
  limit: number;
  skip: number;
}

export const complaintsAPI = {
  create: (data: Omit<Complaint, "_id">) =>
    request<{ complaint_id: string; status: string; message: string }>("/api/complaints", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  list: (params?: { user_id?: string; department?: string; status?: string; limit?: number; skip?: number }) => {
    const qs = new URLSearchParams(
      Object.fromEntries(
        Object.entries(params || {})
          .filter(([, v]) => v !== undefined && v !== null)
          .map(([k, v]) => [k, String(v)]),
      ),
    ).toString();
    return request<ComplaintListResponse>(`/api/complaints${qs ? `?${qs}` : ""}`);
  },

  get: (id: string) => request<Complaint>(`/api/complaints/${id}`),

  update: (id: string, data: { status: string; admin_reply?: string; admin_id?: string }) =>
    request<{ message: string; status: string }>(`/api/complaints/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  delete: (id: string, user_id: string) =>
    request<{ message: string }>(`/api/complaints/${id}?user_id=${user_id}`, {
      method: "DELETE",
    }),
};

// ── Chat / AI ─────────────────────────────────────────────────────────────────

export interface ChatResponse {
  response: string;
  confidence: number;
  timestamp: string;
}

export const chatAPI = {
  send: (question: string, user_id?: string, student_name?: string) =>
    request<ChatResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ question, user_id, student_name }),
    }),
};

// ── Analytics ─────────────────────────────────────────────────────────────────

export interface AnalyticsData {
  stats: {
    total_complaints: number;
    resolution_rate: number;
    by_status: Record<string, number>;
    by_department: Array<{ department: string; total: number; resolved: number }>;
    by_category: Array<{ category: string; count: number }>;
    daily_volume: Array<{ date: string; count: number }>;
  };
  insights: {
    executive_summary: string;
    campus_health_score: number;
    top_issues: string[];
    department_alerts: Array<{ department: string; alert: string; severity: string }>;
    recommended_actions: string[];
  };
  period_days: number;
}

export const analyticsAPI = {
  get: (days = 30) => request<AnalyticsData>(`/api/analytics?days=${days}`),
  getDepartment: (dept: string, days = 30) =>
    request(`/api/analytics/department/${dept}?days=${days}`),
};

// ── Users ─────────────────────────────────────────────────────────────────────

export const usersAPI = {
  upsert: (data: {
    email: string;
    name: string;
    role?: string;
    department?: string;
    firebase_uid?: string;
    photo_url?: string;
  }) =>
    request("/api/users", { method: "POST", body: JSON.stringify(data) }),

  get: (email: string) => request(`/api/users/${encodeURIComponent(email)}`),
};

// ── Announcements ─────────────────────────────────────────────────────────────

export interface Announcement {
  _id?: string;
  title: string;
  content: string;
  type: "info" | "warning" | "emergency" | "event";
  departments: string[];
  created_at?: string;
}

export const announcementsAPI = {
  list: (limit = 10, type?: string) => {
    const qs = new URLSearchParams({ limit: String(limit), ...(type ? { type } : {}) });
    return request<{ announcements: Announcement[] }>(`/api/announcements?${qs}`);
  },

  create: (data: { title: string; content: string; type: string; admin_id: string }) =>
    request("/api/announcements", { method: "POST", body: JSON.stringify(data) }),
};

// ── MCP Gateway ───────────────────────────────────────────────────────────────

export const mcpAPI = {
  listTools: () => request<{ tools: Array<{ name: string; description: string }> }>("/api/mcp/tools"),
  execute: (tool: string, params: Record<string, unknown>) =>
    request("/api/mcp/execute", { method: "POST", body: JSON.stringify({ tool, params }) }),
};

// ── Health ────────────────────────────────────────────────────────────────────

export const healthAPI = {
  check: () => request<{ status: string; mongodb: { status: string }; agents: string }>("/health"),
};

export { APIError };
