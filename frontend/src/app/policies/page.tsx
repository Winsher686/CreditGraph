"use client";

import { useCallback, useEffect, useState } from "react";

import {
  addPolicy,
  listPolicyDocuments,
  listPolicyLibraries,
} from "@/lib/api";
import {
  Card,
  Field,
  Notice,
  Page,
  buttonClass,
  inputClass,
} from "@/components/ui";

type Library = { name: string; document_count: number };
type Doc = { id: number; title: string; chars: number; created_at: number };

export default function PoliciesPage() {
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [active, setActive] = useState("");
  const [documents, setDocuments] = useState<Doc[]>([]);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ library: "信贷政策", title: "", content: "" });

  const loadLibraries = useCallback(async () => {
    try {
      const data = await listPolicyLibraries();
      setLibraries(data.libraries);
      setActive((prev) => prev || data.libraries[0]?.name || "");
    } catch (err) {
      setError(String(err));
    }
  }, []);

  const loadDocuments = useCallback(async (library: string) => {
    if (!library) return;
    try {
      const data = await listPolicyDocuments(library);
      setDocuments(data.documents);
    } catch (err) {
      setError(String(err));
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    void (async () => {
      try {
        const data = await listPolicyLibraries();
        if (!mounted) return;
        setLibraries(data.libraries);
        setActive((prev) => prev || data.libraries[0]?.name || "");
      } catch (err) {
        if (mounted) setError(String(err));
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (!active) return;
    let mounted = true;
    void (async () => {
      try {
        const data = await listPolicyDocuments(active);
        if (mounted) setDocuments(data.documents);
      } catch (err) {
        if (mounted) setError(String(err));
      }
    })();
    return () => {
      mounted = false;
    };
  }, [active]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!form.title.trim() || !form.content.trim()) return;
    try {
      await addPolicy(form.library.trim(), form.title.trim(), form.content.trim());
      setForm({ library: form.library, title: "", content: "" });
      await loadLibraries();
      await loadDocuments(active);
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <Page
      wide
      title="政策知识库"
      subtitle="信贷政策 / 审批规则 / 风险案例 / 监管要求 / 反洗钱规则——风险评估时检索并标注出处"
    >
      {error && <Notice kind="error">{error}</Notice>}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[260px_1fr]">
        <Card title="知识库">
          <ul className="space-y-1">
            {libraries.map((library) => (
              <li key={library.name}>
                <button
                  type="button"
                  onClick={() => setActive(library.name)}
                  className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm transition ${
                    active === library.name
                      ? "bg-teal-600/10 font-medium text-teal-900"
                      : "text-zinc-700 hover:bg-black/[0.04]"
                  }`}
                >
                  <span>{library.name}</span>
                  <span className="text-xs text-zinc-400">
                    {library.document_count}
                  </span>
                </button>
              </li>
            ))}
            {libraries.length === 0 && (
              <li className="text-sm text-zinc-500">
                暂无知识库，先运行 <code>uv run creditgraph-demo</code> 播种。
              </li>
            )}
          </ul>
        </Card>

        <Card title={`文档（${active || "—"}）`}>
          {documents.length === 0 ? (
            <p className="text-sm text-zinc-500">该库暂无文档。</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {documents.map((doc) => (
                <li
                  key={doc.id}
                  className="flex items-center justify-between rounded-lg border border-black/[0.06] px-3 py-2"
                >
                  <span className="text-zinc-800">{doc.title}</span>
                  <span className="text-xs text-zinc-400">{doc.chars} 字</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card title="新增政策 / 案例">
        <form className="space-y-3" onSubmit={handleSubmit}>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="知识库">
              <input
                className={inputClass}
                value={form.library}
                onChange={(e) => setForm({ ...form, library: e.target.value })}
              />
            </Field>
            <Field label="标题">
              <input
                className={inputClass}
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
                placeholder="如：消费贷款首付比例要求"
              />
            </Field>
          </div>
          <Field label="正文">
            <textarea
              className={`${inputClass} min-h-28`}
              value={form.content}
              onChange={(e) => setForm({ ...form, content: e.target.value })}
              placeholder="粘贴政策条款 / 规则 / 案例正文"
            />
          </Field>
          <button type="submit" className={buttonClass}>
            保存入库
          </button>
        </form>
      </Card>
    </Page>
  );
}
