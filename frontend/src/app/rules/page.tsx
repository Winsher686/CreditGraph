"use client";

import { useEffect, useState } from "react";

import { type RulesInfo, getRules } from "@/lib/api";
import { Card, Loading, Notice, Page } from "@/components/ui";

const THRESHOLD_LABEL: Record<string, string> = {
  auto_approve_max_amount: "自动通过金额上限（元）",
  senior_review_min_amount: "高级审批金额下限（元）",
  aml_amount_threshold: "反洗钱上报金额（元）",
  max_dti: "负债率上限",
  min_monthly_income: "最低月收入（元）",
  min_credit_score: "自动通过最低信用评分",
  approval_timeout_minutes: "审批超时（分钟）",
};

export default function RulesPage() {
  const [rules, setRules] = useState<RulesInfo | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getRules()
      .then(setRules)
      .catch((err) => setError(String(err)));
  }, []);

  if (error) {
    return (
      <Page title="规则与审批矩阵">
        <Notice kind="error">{error}</Notice>
      </Page>
    );
  }
  if (!rules) {
    return (
      <Page title="规则与审批矩阵">
        <Loading />
      </Page>
    );
  }

  return (
    <Page
      wide
      title="规则与审批矩阵"
      subtitle="阈值可通过环境变量 / .env 配置（CREDITGRAPH_*），改配置不用改代码"
    >
      <Card title="审批矩阵">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-black/[0.06] text-left text-xs text-zinc-500">
              <th className="py-2 pr-3 font-medium">条件</th>
              <th className="py-2 font-medium">审批路径</th>
            </tr>
          </thead>
          <tbody>
            {rules.matrix.map((row) => (
              <tr key={row.level} className="border-b border-black/[0.04] last:border-0">
                <td className="py-2.5 pr-3 text-zinc-700">{row.condition}</td>
                <td className="py-2.5 font-medium text-teal-800">{row.route}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card title="当前阈值">
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {Object.entries(rules.thresholds).map(([key, value]) => (
            <div
              key={key}
              className="flex items-center justify-between rounded-lg border border-black/[0.06] px-3 py-2 text-sm"
            >
              <span className="text-zinc-600">{THRESHOLD_LABEL[key] ?? key}</span>
              <span className="font-medium text-zinc-900">{String(value)}</span>
            </div>
          ))}
        </dl>
      </Card>

      <Card title="规则说明">
        <pre className="whitespace-pre-wrap text-sm leading-relaxed text-zinc-700">
          {rules.text}
        </pre>
      </Card>
    </Page>
  );
}
