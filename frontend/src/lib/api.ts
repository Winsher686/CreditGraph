// 信贷审批前端 API 客户端。
// 与后端 creditgraph.interfaces.api 一一对应；SSE 用 fetch + ReadableStream 手写解析。

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8011";

export type Applicant = {
  name: string;
  id_number?: string;
  age?: number;
  occupation?: string;
  industry?: string;
  monthly_income?: number;
  monthly_debt?: number;
  credit_score?: number;
  blacklisted?: boolean;
  related_party?: boolean;
};

export type Loan = {
  amount: number;
  term_months?: number;
  purpose?: string;
  annual_rate?: number;
};

export type Material = { name: string; text?: string; kind?: string };

export type Citation = {
  library: string;
  title: string;
  chunk_id: number;
  snippet: string;
  score: number;
};

export type RiskAssessment = {
  id?: number;
  credit_score: number;
  debt_to_income: number;
  risk_level: string;
  repayment_capacity: string;
  summary: string;
  recommendation: string;
  risk_factors: string[];
  citations: Citation[];
  created_at?: number;
};

export type ApprovalRecord = {
  id?: number;
  approver: string;
  level: string;
  decision: string;
  comment: string;
  modified: boolean;
  created_at: number;
};

export type ChainEntry = {
  id?: number;
  seq: number;
  actor: string;
  action: string;
  from_level: string;
  to_level: string;
  detail: string;
  created_at: number;
};

export type AuditLog = {
  id: number;
  actor: string;
  action: string;
  detail: string;
  created_at: number;
};

export type Application = {
  id: number;
  applicant: Applicant;
  loan: Loan;
  materials?: Material[];
  status: string;
  decision: string | null;
  decision_reason: string;
  risk_level: string | null;
  review_type: string;
  created_at: number;
  updated_at: number;
  risk_assessments?: RiskAssessment[];
  approval_records?: ApprovalRecord[];
  approval_chain?: ChainEntry[];
};

export type RulesInfo = {
  text: string;
  matrix: { condition: string; route: string; level: string }[];
  thresholds: Record<string, number>;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export const getHealth = () =>
  request<{ status: string; provider: string; rules: Record<string, number> }>(
    "/api/health",
  );

export const getRules = () => request<RulesInfo>("/api/rules");

export const listApplications = (status?: string) =>
  request<{ applications: Application[] }>(
    status
      ? `/api/applications?status=${encodeURIComponent(status)}`
      : "/api/applications",
  );

export const getApplication = (id: number) =>
  request<{ application: Application }>(`/api/applications/${id}`);

export const getAuditTrail = (id: number) =>
  request<{
    application_id: number;
    chain: ChainEntry[];
    records: ApprovalRecord[];
    audits: AuditLog[];
  }>(`/api/applications/${id}/audit-trail`);

export const submitApplication = (payload: {
  applicant: Applicant;
  loan: Loan;
  materials?: Material[];
  operator?: string;
}) =>
  request<{ application: Application }>("/api/applications", {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const listPolicyLibraries = () =>
  request<{ libraries: { name: string; document_count: number }[] }>(
    "/api/policies/libraries",
  );

export const listPolicyDocuments = (library: string) =>
  request<{
    documents: { id: number; title: string; chars: number; created_at: number }[];
  }>(`/api/policies/documents?library=${encodeURIComponent(library)}`);

export const addPolicy = (library: string, title: string, content: string) =>
  request<{ id: number }>("/api/policies", {
    method: "POST",
    body: JSON.stringify({ library, title, content }),
  });

// ---------------------------------------------------------------------------
// SSE
// ---------------------------------------------------------------------------

export type StreamEvent = { event: string; data: Record<string, unknown> };

export type ApprovalOpinion = {
  approver?: string;
  comment?: string;
  modified_assessment?: Record<string, unknown>;
};

/** 把 SSE 原始文本块解析成事件；返回 [事件列表, 剩余未闭合的缓冲]。 */
function parseSSE(buffer: string): [StreamEvent[], string] {
  const events: StreamEvent[] = [];
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";
  for (const block of blocks) {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) continue;
    try {
      events.push({ event, data: JSON.parse(dataLines.join("\n")) });
    } catch {
      /* 忽略无法解析的帧 */
    }
  }
  return [events, rest];
}

/** 以 SSE 方式调用工作流接口，逐事件回调。 */
export async function streamPost(
  path: string,
  body: ApprovalOpinion | null,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok || !response.body) {
    throw new Error(`流式请求失败：${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const [events, rest] = parseSSE(buffer);
    buffer = rest;
    for (const event of events) onEvent(event);
  }
}

export const RISK_LABEL: Record<string, string> = {
  low: "低风险",
  medium: "中风险",
  high: "高风险",
};

export const STATUS_LABEL: Record<string, string> = {
  submitted: "已提交",
  in_review: "审批中",
  pending_review: "待人工审批",
  approved: "已通过",
  rejected: "已拒绝",
  conditional: "有条件通过",
};

export const LEVEL_LABEL: Record<string, string> = {
  auto: "自动审批",
  junior: "初级审批人",
  senior: "高级审批人",
  compliance: "合规部门",
};

export const DECISION_LABEL: Record<string, string> = {
  auto_approved: "自动通过",
  auto_rejected: "自动拒绝",
  approved: "审批通过",
  rejected: "审批拒绝",
  conditional: "有条件通过",
  pending_review: "待审批",
};
