"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { getHealth } from "@/lib/api";
import useMediaQuery, { useMounted } from "./useMediaQuery";

const SIDEBAR_WIDTH_KEY = "creditgraph-sidebar-width";
const SIDEBAR_MIN_WIDTH = 180;
const SIDEBAR_MAX_WIDTH = 340;
const SIDEBAR_DEFAULT_WIDTH = 224;

function NavIcon({ name }: { name: string }) {
  const common = {
    width: 16,
    height: 16,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  switch (name) {
    case "dashboard":
      return (
        <svg {...common}>
          <rect x="3" y="3" width="7" height="9" rx="1.5" />
          <rect x="14" y="3" width="7" height="5" rx="1.5" />
          <rect x="14" y="12" width="7" height="9" rx="1.5" />
          <rect x="3" y="16" width="7" height="5" rx="1.5" />
        </svg>
      );
    case "library":
      return (
        <svg {...common}>
          <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
          <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z" />
        </svg>
      );
    case "rules":
      return (
        <svg {...common}>
          <path d="M9 3v18M4 7h10M4 17h10" />
          <path d="M15 7l5 10M20 7l-5 10" />
        </svg>
      );
    case "audit":
      return (
        <svg {...common}>
          <path d="M12 3l7 3v6c0 4.5-3 7.6-7 9-4-1.4-7-4.5-7-9V6Z" />
          <path d="m9 12 2 2 4-4" />
        </svg>
      );
    default:
      return null;
  }
}

const NAV_ITEMS = [
  { href: "/", icon: "dashboard", label: "审批总览" },
  { href: "/policies", icon: "library", label: "政策知识库" },
  { href: "/rules", icon: "rules", label: "规则与审批矩阵" },
];

export default function AppShell({ children }: { children: ReactNode }) {
  // 动态路由（如 /applications/[id]）预渲染时 usePathname 不可用，
  // 用 Suspense 包住整个外壳，让预渲染先出骨架、真实导航在请求时注入。
  return (
    <Suspense fallback={<div className="h-dvh bg-white" />}>
      <AppShellInner>{children}</AppShellInner>
    </Suspense>
  );
}

function AppShellInner({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [provider, setProvider] = useState<string>("");
  const mounted = useMounted();
  const isLg = useMediaQuery("(min-width: 1024px)");
  const [sidebarWidth, setSidebarWidth] = useState<number | null>(() => {
    try {
      const saved = Number(localStorage.getItem(SIDEBAR_WIDTH_KEY));
      return saved >= SIDEBAR_MIN_WIDTH && saved <= SIDEBAR_MAX_WIDTH
        ? saved
        : null;
    } catch {
      return null;
    }
  });
  const [isSidebarDragging, setIsSidebarDragging] = useState(false);
  const latestWidth = useRef<number>(SIDEBAR_DEFAULT_WIDTH);

  useEffect(() => {
    getHealth()
      .then((h) => setProvider(h.provider))
      .catch(() => setProvider(""));
  }, []);

  const startSidebarDrag = useCallback((event: React.MouseEvent) => {
    event.preventDefault();
    setIsSidebarDragging(true);
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";

    const onMove = (move: MouseEvent) => {
      const next = Math.max(
        SIDEBAR_MIN_WIDTH,
        Math.min(SIDEBAR_MAX_WIDTH, move.clientX),
      );
      latestWidth.current = next;
      setSidebarWidth(next);
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      setIsSidebarDragging(false);
      try {
        localStorage.setItem(SIDEBAR_WIDTH_KEY, String(latestWidth.current));
      } catch {
        /* ignore */
      }
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }, []);

  const showResizeHandle = mounted && isLg;

  return (
    <div className="flex h-dvh bg-white text-zinc-900">
      <aside
        className="relative flex w-14 shrink-0 flex-col border-r border-black/[0.06] bg-white lg:w-56"
        style={
          showResizeHandle && sidebarWidth ? { width: sidebarWidth } : undefined
        }
      >
        {showResizeHandle && (
          <div
            role="separator"
            aria-orientation="vertical"
            onMouseDown={startSidebarDrag}
            onDoubleClick={() => {
              setSidebarWidth(null);
              try {
                localStorage.removeItem(SIDEBAR_WIDTH_KEY);
              } catch {
                /* ignore */
              }
            }}
            title="拖拽调节侧栏宽度（双击恢复默认）"
            className="group absolute -right-1 top-0 z-30 h-full w-2 cursor-col-resize"
          >
            <div
              className={`absolute right-0 top-0 h-full transition-all duration-150 ${
                isSidebarDragging
                  ? "w-[3px] bg-teal-600"
                  : "w-px bg-transparent group-hover:w-[3px] group-hover:bg-teal-600"
              }`}
            />
          </div>
        )}
        <div className="flex items-start justify-center gap-2 px-2 pt-5 pb-4 lg:justify-between lg:px-4">
          <div className="hidden lg:block">
            <div className="text-[15px] font-bold leading-tight tracking-tight">
              CreditGraph
            </div>
            <div className="text-[11px] leading-tight text-zinc-400">
              信贷审批工作流 · 1.0.0
            </div>
          </div>
          {provider && (
            <span
              title={
                provider === "mock"
                  ? "当前为确定性 Mock 模型，零 API 消耗"
                  : `已接入真实模型（${provider}）`
              }
              className="hidden items-center gap-1 rounded-full border border-black/[0.08] px-2 py-0.5 text-[10px] font-medium text-zinc-500 lg:inline-flex"
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  provider === "mock" ? "bg-zinc-300" : "bg-accent"
                }`}
              />
              {provider === "mock" ? "Mock" : "真实模型"}
            </span>
          )}
          <span className="flex h-7 w-7 shrink-0 items-center justify-center lg:hidden">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0f766e" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <circle cx="5" cy="19" r="2.2" />
              <circle cx="12" cy="5" r="2.2" />
              <circle cx="19" cy="19" r="2.2" />
              <path d="M6.5 17.2 10.6 7.4" />
              <path d="m13.4 7.4 4.1 9.8" />
              <path d="M7.2 19h9.6" />
            </svg>
          </span>
        </div>

        <nav className="px-2.5 pb-2 pt-1">
          <div className="flex flex-col gap-0.5">
            {NAV_ITEMS.map((item) => {
              const active =
                item.href === "/"
                  ? pathname === "/"
                  : pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  title={item.label}
                  className={`relative flex items-center justify-center gap-2.5 rounded-lg px-2.5 py-[6px] text-[13px] transition focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:outline-none lg:justify-start ${
                    active
                      ? "bg-teal-600/[0.08] font-medium text-teal-900"
                      : "text-zinc-700 hover:bg-black/[0.04]"
                  }`}
                >
                  {active && (
                    <span
                      aria-hidden
                      className="absolute left-0 top-1/2 h-4 w-[2px] -translate-y-1/2 rounded-full bg-teal-600"
                    />
                  )}
                  <span className={active ? "text-teal-700" : "text-zinc-500"}>
                    <NavIcon name={item.icon} />
                  </span>
                  <span className="hidden lg:inline">{item.label}</span>
                </Link>
              );
            })}
          </div>
        </nav>

        <div className="min-h-0 flex-1" />

        <div className="hidden border-t border-black/[0.06] px-4 py-3 lg:block">
          <div className="flex items-center gap-2.5">
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-zinc-100 text-[11px] font-medium text-zinc-600 ring-1 ring-black/10">
              审
            </div>
            <div className="text-[13px] font-medium text-zinc-700">
              本地审批员
            </div>
          </div>
        </div>
        <div className="border-t border-black/[0.06] px-4 py-3 lg:hidden">
          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-zinc-100 text-[11px] font-medium text-zinc-600 ring-1 ring-black/10">
            审
          </div>
        </div>
      </aside>
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {children}
      </main>
    </div>
  );
}
