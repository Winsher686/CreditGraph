"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  type Application,
  type ChainEntry,
  type RiskAssessment,
  type StreamEvent,
  DECISION_LABEL,
  LEVEL_LABEL,
  RISK_LABEL,
  STATUS_LABEL,
  getApplication,
  getAuditTrail,
  streamPost,
} from "@/lib/api";
import {
  Card,
  Field,
  Loading,
  Notice,
  Page,
  buttonClass,
  ghostButtonClass,
  inputClass,
  selectClass,
} from "@/components/ui";

type TraceLine = { kind: "status" | "token" | "step" | "info"; text: string };

const LEVEL_OPTIONS = ["low", "medium", "high"];

export default function ApplicationPage() {
  const params = useParams<{ id: string }>();
  const applicationId = Number(params?.id);

  const [application, setApplication] = useState<Application | null>(null);
  const [chain, setChain] = useState<ChainEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [interrupted, setInterrupted] = useState<Record<string, unknown> | null>(
    null,
  );
  const [trace, setTrace] = useState<TraceLine[]>([]);
  const [report, setReport] = useState("");

  const [approver, setApprover] = useState("审批员甲");
  const [comment, setComment] = useState("");
  const [modifiedRisk, setModifiedRisk] = useState("");
  const [modifiedRecommendation, setModifiedRecommendation] = useState("");

  const traceRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    if (!Number.isFinite(applicationId)) return;
    try {
      const [{ application: record }, trail] = await Promise.all([
        getApplication(applicationId),
        getAuditTrail(applicationId),
      ]);
      setApplication(record);
      setChain(trail.chain);
      setError("");
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, [applicationId]);

  useEffect(() => {
    let mounted = true;
    void (async () => {
      if (!Number.isFinite(applicationId)) return;
      try {
        const [{ application: record }, trail] = await Promise.all([
          getApplication(applicationId),
          getAuditTrail(applicationId),
        ]);
        if (!mounted) return;
        setApplication(record);
        setChain(trail.chain);
        setError("");
      } catch (err) {
        if (mounted) setError(String(err));
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, [applicationId]);

  useEffect(() => {
    traceRef.current?.scrollTo({ top: traceRef.current.scrollHeight });
  }, [trace]);

  const handleEvent = useCallback((event: StreamEvent) => {
    if (event.event === "status") {
      setTrace((prev) => [
        ...prev,
        { kind: "status", text: `● ${String(event.data.label ?? "")}` },
      ]);
    } else if (event.event === "step") {
      setTrace((prev) => [
        ...prev,
        {
          kind: "step",
          text: `▸ ${String(event.data.title ?? "")}：${String(event.data.detail ?? "")}`,
        },
      ]);
    } else if (event.event === "token") {
      setReport((prev) => prev + String(event.data.content ?? ""));
    } else if (event.event === "interrupt") {
      setInterrupted(event.data);
      setTrace((prev) => [
        ...prev,
        { kind: "info", text: "⏸ 触发人工审批（HITL 中断），请选择审批动作。" },
      ]);
    } else if (event.event === "decision") {
      setTrace((prev) => [
        ...prev,
        {
          kind: "info",
          text: `✔ 最终决策：${DECISION_LABEL[String(event.data.decision)] ?? event.data.decision}`,
        },
      ]);
    } else if (event.event === "error") {
      setTrace((prev) => [...prev, { kind: "info", text: `✖ ${event.data.message}` }]);
    }
  }, []);

  async function run(path: string, body: Record<string, unknown> | null) {
    setRunning(true);
    setError("");
    if (!body) {
      setReport("");
      setTrace([{ kind: "info", text: "▶ 启动审批工作流…" }]);
      setInterrupted(null);
    }
    try {
      await streamPost(path, body, handleEvent);
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setRunning(false);
      setInterrupted(null);
    }
  }

  function opinion(action: string, withModification: boolean) {
    return {
      approver,
      comment,
      action,
      modified_assessment: withModification
        ? {
            risk_level: modifiedRisk || undefined,
            recommendation: modifiedRecommendation || undefined,
          }
        : undefined,
    };
  }

  if (loading) {
    return (
      <Page title="审批台">
        <Loading />
      </Page>
    );
  }
  if (!application) {
    return (
      <Page title="审批台">
        <Notice kind="error">{error || "申请不存在"}</Notice>
      </Page>
    );
  }

  const latest: RiskAssessment | undefined = application.risk_assessments?.at(-1);
  const isApproved = application.status === "approved";

  return (
    <Page
      wide
      title={`审批台 · #${application.id} ${application.applicant?.name}`}
      subtitle={`金额 ¥${(application.loan?.amount ?? 0).toLocaleString()} · 期限 ${application.loan?.term_months} 个月 · 用途 ${application.loan?.purpose || "—"}`}
      action={
        <Link href="/" className={ghostButtonClass}>
          ← 返回总览
        </Link>
      }
    >
      {error && <Notice kind="error">{error}</Notice>}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
        <Card title="状态">
          <p className="text-lg font-semibold">
            {STATUS_LABEL[application.status] ?? application.status}
          </p>
        </Card>
        <Card title="风险等级">
          <p className="text-lg font-semibold">
            {application.risk_level
              ? RISK_LABEL[application.risk_level] ?? application.risk_level
              : "—"}
          </p>
        </Card>
        <Card title="最终决策">
          <p className="text-lg font-semibold">
            {application.decision
              ? DECISION_LABEL[application.decision] ?? application.decision
              : "—"}
          </p>
        </Card>
        <Card title="复审类型">
          <p className="text-lg font-semibold">
            {application.review_type === "recheck" ? "贷后复审" : "新申请"}
          </p>
        </Card>
      </div>

      <Card
        title="审批工作流"
        action={
          <div className="flex gap-2">
            <button
              type="button"
              className={buttonClass}
              disabled={running}
              onClick={() => run(`/api/applications/${applicationId}/stream`, null)}
            >
              {running ? "运行中…" : "▶ 运行审批工作流"}
            </button>
            <button
              type="button"
              className={ghostButtonClass}
              disabled={running || !isApproved}
              title={isApproved ? "" : "只有已通过的贷款才能复审"}
              onClick={() =>
                run(`/api/applications/${applicationId}/recheck`, null)
              }
            >
              发起贷后复审
            </button>
          </div>
        }
      >
        <div
          ref={traceRef}
          className="max-h-52 space-y-1 overflow-y-auto rounded-lg bg-zinc-50 p-3 font-mono text-xs text-zinc-600"
        >
          {trace.length === 0 ? (
            <p className="text-zinc-400">点击「运行审批工作流」开始；节点进展与报告会实时推送。</p>
          ) : (
            trace.map((line, index) => (
              <p key={index} className={line.kind === "info" ? "text-teal-700" : ""}>
                {line.text}
              </p>
            ))
          )}
        </div>

        {report && (
          <div className="mt-3 whitespace-pre-wrap rounded-lg bg-white p-4 text-sm leading-relaxed ring-1 ring-black/[0.06]">
            {report}
          </div>
        )}

        {interrupted && (
          <div className="mt-4 rounded-xl bg-amber-50 p-4 ring-1 ring-amber-600/20">
            <p className="text-sm font-medium text-amber-900">
              ⏸ 等待
              {LEVEL_LABEL[String(interrupted.level)] ?? String(interrupted.level)}
              审批 · 原因：{String(interrupted.route_reason ?? "")}
            </p>
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="审批人">
                <input
                  className={inputClass}
                  value={approver}
                  onChange={(e) => setApprover(e.target.value)}
                />
              </Field>
              <Field label="审批意见">
                <input
                  className={inputClass}
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  placeholder="同意 / 拒绝原因 / 修改说明"
                />
              </Field>
              <Field label="修改风险等级（可选，用于「修改后提交」）">
                <select
                  className={selectClass}
                  value={modifiedRisk}
                  onChange={(e) => setModifiedRisk(e.target.value)}
                >
                  <option value="">不修改</option>
                  {LEVEL_OPTIONS.map((level) => (
                    <option key={level} value={level}>
                      {RISK_LABEL[level]}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="修改放款建议（可选）">
                <input
                  className={inputClass}
                  value={modifiedRecommendation}
                  onChange={(e) => setModifiedRecommendation(e.target.value)}
                  placeholder="如：额度下调至 20 万后放款"
                />
              </Field>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                className={buttonClass}
                disabled={running}
                onClick={() =>
                  run(`/api/applications/${applicationId}/approve`, opinion("approve", false))
                }
              >
                审批通过
              </button>
              <button
                type="button"
                className={ghostButtonClass}
                disabled={running}
                onClick={() =>
                  run(`/api/applications/${applicationId}/reject`, opinion("reject", false))
                }
              >
                审批拒绝
              </button>
              <button
                type="button"
                className={ghostButtonClass}
                disabled={running}
                onClick={() =>
                  run(`/api/applications/${applicationId}/modify`, opinion("modify", true))
                }
              >
                修改风险评估后提交
              </button>
              <button
                type="button"
                className={ghostButtonClass}
                disabled={running}
                onClick={() =>
                  run(
                    `/api/applications/${applicationId}/escalate`,
                    opinion("escalate", false),
                  )
                }
              >
                升级到高级审批人
              </button>
            </div>
          </div>
        )}
      </Card>

      <Card title="AI 风险评估报告">
        {latest ? (
          <div className="space-y-2 text-sm">
            <p className="text-zinc-700">
              信用评分 <strong>{latest.credit_score}</strong> · 负债率{" "}
              <strong>{(latest.debt_to_income * 100).toFixed(0)}%</strong> · 风险等级{" "}
              <strong>{RISK_LABEL[latest.risk_level] ?? latest.risk_level}</strong>
            </p>
            <p className="text-zinc-600">{latest.repayment_capacity}</p>
            <p className="text-zinc-800">{latest.summary}</p>
            <p className="text-zinc-600">建议：{latest.recommendation}</p>
            {latest.risk_factors?.length > 0 && (
              <ul className="list-disc pl-5 text-zinc-600">
                {latest.risk_factors.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            )}
            {latest.citations?.length > 0 && (
              <div>
                <p className="mt-2 text-xs font-medium text-zinc-500">政策依据</p>
                <ul className="mt-1 space-y-1 text-xs text-zinc-600">
                  {latest.citations.map((cite, index) => (
                    <li key={index}>
                      【{cite.library} / {cite.title}】{cite.snippet}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ) : (
          <p className="text-sm text-zinc-500">
            尚未生成风险评估报告，请先运行审批工作流。
          </p>
        )}
      </Card>

      <Card title="审批链 / 审计日志">
        {chain.length === 0 ? (
          <p className="text-sm text-zinc-500">暂无流转记录。</p>
        ) : (
          <ol className="space-y-2 text-sm">
            {chain.map((entry, index) => (
              <li key={entry.id ?? index} className="flex gap-3">
                <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-teal-600/10 text-xs font-medium text-teal-800">
                  {entry.seq}
                </span>
                <div>
                  <p className="text-zinc-800">
                    <strong>{entry.actor}</strong> · {entry.action}
                    {entry.from_level &&
                      ` （${LEVEL_LABEL[entry.from_level] ?? entry.from_level} → ${
                        LEVEL_LABEL[entry.to_level] ?? entry.to_level
                      }）`}
                  </p>
                  {entry.detail && (
                    <p className="text-xs text-zinc-500">{entry.detail}</p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </Card>
    </Page>
  );
}
