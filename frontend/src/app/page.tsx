"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  type Application,
  DECISION_LABEL,
  RISK_LABEL,
  STATUS_LABEL,
  listApplications,
  submitApplication,
} from "@/lib/api";
import {
  Card,
  Field,
  Notice,
  Page,
  buttonClass,
  ghostButtonClass,
  inputClass,
} from "@/components/ui";

const EMPTY_FORM = {
  name: "",
  age: "30",
  occupation: "",
  industry: "",
  monthly_income: "20000",
  monthly_debt: "3000",
  credit_score: "720",
  amount: "300000",
  term_months: "36",
  purpose: "装修",
  blacklisted: false,
  related_party: false,
};

const STATUS_STYLE: Record<string, string> = {
  approved: "bg-emerald-50 text-emerald-700 ring-emerald-600/15",
  conditional: "bg-amber-50 text-amber-700 ring-amber-600/15",
  rejected: "bg-red-50 text-red-700 ring-red-600/15",
  pending_review: "bg-sky-50 text-sky-700 ring-sky-600/15",
  in_review: "bg-sky-50 text-sky-700 ring-sky-600/15",
  submitted: "bg-zinc-100 text-zinc-600 ring-black/10",
};

export default function HomePage() {
  const [applications, setApplications] = useState<Application[]>([]);
  const [error, setError] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);

  const refresh = useCallback(async () => {
    try {
      const data = await listApplications();
      setApplications(data.applications);
      setError("");
    } catch (err) {
      setError(String(err));
    }
  }, []);

  // 初次加载走 effect 内的异步 IIFE（避免在 effect 体里同步 setState）。
  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const data = await listApplications();
        if (active) {
          setApplications(data.applications);
          setError("");
        }
      } catch (err) {
        if (active) setError(String(err));
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  const set = (key: keyof typeof EMPTY_FORM) => (value: string | boolean) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const income = Number(form.monthly_income) || 0;
      const debt = Number(form.monthly_debt) || 0;
      await submitApplication({
        applicant: {
          name: form.name.trim() || "未署名",
          age: Number(form.age) || 0,
          occupation: form.occupation,
          industry: form.industry,
          monthly_income: income,
          monthly_debt: debt,
          credit_score: Number(form.credit_score) || 0,
          blacklisted: form.blacklisted,
          related_party: form.related_party,
        },
        loan: {
          amount: Number(form.amount) || 0,
          term_months: Number(form.term_months) || 12,
          purpose: form.purpose,
        },
        materials: [
          { name: "收入证明", text: `月收入 ${income} 元`, kind: "text" },
          { name: "征信报告", text: `信用评分 ${form.credit_score}`, kind: "text" },
          { name: "资产负债表", text: `月负债 ${debt} 元`, kind: "text" },
        ],
        operator: "applicant",
      });
      setForm(EMPTY_FORM);
      setShowForm(false);
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page
      wide
      title="信贷审批总览"
      subtitle="提交贷款申请，触发材料解析 → 风险评估 → 规则引擎 → 审批矩阵 → 人工审批(HITL) 的完整工作流"
      action={
        <button
          type="button"
          className={buttonClass}
          onClick={() => setShowForm((prev) => !prev)}
        >
          {showForm ? "收起表单" : "＋ 新建申请"}
        </button>
      }
    >
      {error && <Notice kind="error">{error}</Notice>}

      {showForm && (
        <Card title="提交贷款申请">
          <form
            className="grid grid-cols-1 gap-4 sm:grid-cols-2"
            onSubmit={handleSubmit}
          >
            <Field label="申请人姓名">
              <input
                className={inputClass}
                value={form.name}
                onChange={(e) => set("name")(e.target.value)}
                placeholder="张三"
                required
              />
            </Field>
            <Field label="年龄">
              <input
                className={inputClass}
                value={form.age}
                onChange={(e) => set("age")(e.target.value)}
              />
            </Field>
            <Field label="职业">
              <input
                className={inputClass}
                value={form.occupation}
                onChange={(e) => set("occupation")(e.target.value)}
                placeholder="软件工程师"
              />
            </Field>
            <Field label="所属行业">
              <input
                className={inputClass}
                value={form.industry}
                onChange={(e) => set("industry")(e.target.value)}
                placeholder="信息技术"
              />
            </Field>
            <Field label="月收入（元）">
              <input
                className={inputClass}
                value={form.monthly_income}
                onChange={(e) => set("monthly_income")(e.target.value)}
              />
            </Field>
            <Field label="月负债（元）">
              <input
                className={inputClass}
                value={form.monthly_debt}
                onChange={(e) => set("monthly_debt")(e.target.value)}
              />
            </Field>
            <Field label="征信评分">
              <input
                className={inputClass}
                value={form.credit_score}
                onChange={(e) => set("credit_score")(e.target.value)}
              />
            </Field>
            <Field label="贷款金额（元）">
              <input
                className={inputClass}
                value={form.amount}
                onChange={(e) => set("amount")(e.target.value)}
              />
            </Field>
            <Field label="期限（月）">
              <input
                className={inputClass}
                value={form.term_months}
                onChange={(e) => set("term_months")(e.target.value)}
              />
            </Field>
            <Field label="用途">
              <input
                className={inputClass}
                value={form.purpose}
                onChange={(e) => set("purpose")(e.target.value)}
              />
            </Field>
            <div className="flex items-center gap-6 sm:col-span-2">
              <label className="flex items-center gap-2 text-sm text-zinc-700">
                <input
                  type="checkbox"
                  checked={form.blacklisted}
                  onChange={(e) => set("blacklisted")(e.target.checked)}
                />
                命中征信黑名单
              </label>
              <label className="flex items-center gap-2 text-sm text-zinc-700">
                <input
                  type="checkbox"
                  checked={form.related_party}
                  onChange={(e) => set("related_party")(e.target.checked)}
                />
                关联交易
              </label>
            </div>
            <div className="sm:col-span-2">
              <button type="submit" className={buttonClass} disabled={busy}>
                {busy ? "提交中…" : "提交申请"}
              </button>
            </div>
          </form>
        </Card>
      )}

      <Card title={`申请列表（${applications.length}）`}>
        {applications.length === 0 ? (
          <p className="text-sm text-zinc-500">
            还没有申请。点击右上角「新建申请」，或先运行
            <code className="mx-1 rounded bg-black/[0.05] px-1.5 py-0.5 text-xs">
              uv run creditgraph-demo
            </code>
            播种示例数据。
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-black/[0.06] text-left text-xs text-zinc-500">
                  <th className="py-2 pr-3 font-medium">申请</th>
                  <th className="py-2 pr-3 font-medium">金额</th>
                  <th className="py-2 pr-3 font-medium">状态</th>
                  <th className="py-2 pr-3 font-medium">风险</th>
                  <th className="py-2 pr-3 font-medium">决策</th>
                  <th className="py-2 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {applications.map((app) => (
                  <tr
                    key={app.id}
                    className="border-b border-black/[0.04] last:border-0"
                  >
                    <td className="py-2.5 pr-3">
                      <Link
                        href={`/applications/${app.id}`}
                        className="font-medium text-teal-800 hover:underline"
                      >
                        #{app.id} {app.applicant?.name}
                      </Link>
                    </td>
                    <td className="py-2.5 pr-3 text-zinc-700">
                      ¥{(app.loan?.amount ?? 0).toLocaleString()}
                    </td>
                    <td className="py-2.5 pr-3">
                      <span
                        className={`rounded-full px-2 py-0.5 text-xs ring-1 ${
                          STATUS_STYLE[app.status] ??
                          "bg-zinc-100 text-zinc-600 ring-black/10"
                        }`}
                      >
                        {STATUS_LABEL[app.status] ?? app.status}
                      </span>
                    </td>
                    <td className="py-2.5 pr-3 text-zinc-600">
                      {app.risk_level
                        ? RISK_LABEL[app.risk_level] ?? app.risk_level
                        : "—"}
                    </td>
                    <td className="py-2.5 pr-3 text-zinc-600">
                      {app.decision
                        ? DECISION_LABEL[app.decision] ?? app.decision
                        : "—"}
                    </td>
                    <td className="py-2.5">
                      <Link
                        href={`/applications/${app.id}`}
                        className={ghostButtonClass}
                      >
                        进入审批台
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </Page>
  );
}
