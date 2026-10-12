export type ResponseStatus =
  | "answered" | "fallback" | "needs_confirmation" | "completed" | "escalated" | "clarification" | "error";

export interface Citation {
  index: number; filename: string; page: number | null; section: string | null;
  label: string; score: number; snippet: string;
}
export interface ToolCall {
  tool: string; arguments: Record<string, unknown>; success: boolean;
  error?: string | null; error_code?: string | null; duration_ms: number;
}
export interface TraceStep { step: string; detail: string }

export interface ChatMessage {
  id: number | string;
  role: "user" | "assistant" | "agent";
  content: string;
  intent?: string | null;
  status?: ResponseStatus | null;
  citations?: Citation[];
  tool_calls?: ToolCall[];
  trace?: TraceStep[];
  metrics?: Record<string, number | string | boolean | null>;
  validation?: { passed: boolean; grounding_ratio?: number | null; relevance_score?: number | null; reasons: string[] } | null;
}

export interface ConversationSummary {
  id: string; customer_id: string | null; status: "open" | "resolved" | "escalated";
  title: string | null; message_count: number; updated_at: string;
}
export interface ConversationDetail {
  id: string; status: ConversationSummary["status"]; memory: { last_order_id?: string; ticket_ref?: string; pending_action?: unknown };
  messages: (Omit<ChatMessage, "id"> & { id: number; latency_ms?: number })[];
}
export interface Customer { id: string; name: string; tier: string }

export interface Metrics {
  window_days: number;
  conversations: { total: number; resolved: number; escalated: number; open: number };
  latency_ms: { avg: number | null; p50: number | null; p95: number | null; avg_llm: number | null; avg_retrieval: number | null; avg_tool: number | null };
  tools: { total_calls: number; success_rate: number | null; by_tool: { tool: string; calls: number; success_rate: number; avg_ms: number }[] };
  rag: { queries: number; avg_retrieved_chunks: number | null; avg_top_score: number | null; no_evidence_rate: number | null };
  intents: Record<string, number>;
  response_status: Record<string, number>;
  failed_queries: { question: string; status: string; at: string }[];
  frequent_questions: { question: string; count: number }[];
  conversations_per_day: { date: string; conversations: number }[];
  tokens: { prompt: number; completion: number };
}

export interface KbDocument {
  id: string; filename: string; source: string; pages: number | null; chunks: number;
  size_bytes: number; embedding_model: string; uploaded_at: string;
}

export interface EvalReport {
  meta: { generated_at: string; llm_provider: string; llm_model: string; embedding_provider: string; embedding_model: string; top_k: number; min_score: number };
  warnings: string[];
  metrics: {
    counts: { total: number; knowledge: number; unanswerable: number; tool: number };
    retrieval: { precision_at_k: number | null; source_recall: number | null; evidence_hit_rate: number | null };
    answers: Record<string, number | null>;
    routing_accuracy: number | null; tool_call_accuracy: number | null;
    latency_ms: { mean: number; p50: number | null; p95: number | null };
  };
  failures: { id: string; question: string; problems: string[] }[];
}

export interface TicketSummary {
  reference: string; subject: string; category: string; priority: "low" | "normal" | "high";
  status: "open" | "in_progress" | "resolved"; customer_id: string | null; customer_name: string | null;
  order_id: string | null; conversation_id: string | null; assignee: string | null; created_at: string;
}
export interface TicketQueue { counts: Record<"open" | "in_progress" | "resolved", number>; items: TicketSummary[] }
export interface TicketDetail extends TicketSummary {
  description: string;
  customer: { id: string; name: string; email: string; tier: string } | null;
  order: { id: string; status: string; total: number; payment_status: string } | null;
  transcript: { role: string; content: string; status: string | null; intent: string | null; created_at: string }[];
  activity: { id: number; author: string; kind: "note" | "reply" | "status" | "assign"; body: string; created_at: string }[];
}

export type Role = "customer" | "agent" | "admin";
export interface AuthUser { id: number | null; email: string | null; name: string; role: Role; customer_id: string | null; is_active: boolean }
export interface LoginResponse { access_token: string; expires_in: number; user: AuthUser }
export interface DemoAccount { email: string; name: string; role: Role; password: string }

export interface TestResult { ok: boolean; error?: string | null; message?: string; model?: string; sample?: string; dimension?: number; latency_ms?: number; server?: string; state?: string }
export interface SettingsInfo {
  editable: boolean;
  llm: { provider: string; ollama_base_url: string; ollama_model: string; ollama_num_ctx: number; openai_base_url: string; openai_api_key: string; openai_model: string; health: { ok: boolean; error?: string | null } };
  embedding: { provider: string; model: string };
  database: { url: string; dialect: string };
  knowledge_base: { documents: number; chunks: number; indexed: number; needs_reindex: boolean };
  overridden_keys: string[];
}
export interface ApplyResult { applied: boolean; relogin_required: boolean; database_changed: boolean; tests: Record<string, TestResult>; reindexed?: { chunks: number }; reindex_error?: string }
export interface ManagedUser { id: number; email: string; name: string; role: Role; customer_id: string | null; is_active: boolean; last_login_at: string | null }
