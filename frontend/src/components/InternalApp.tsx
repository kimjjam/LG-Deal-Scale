import { FormEvent, useEffect, useState } from "react";

import { api, loadSession, saveSession } from "../api";
import type { SearchResult, Session } from "../types";
import Accounts from "./Accounts";
import ApiManagement from "./ApiManagement";
import Dashboard from "./Dashboard";
import Inbox from "./Inbox";
import Login from "./Login";
import Outbound from "./Outbound";
import Pipeline from "./Pipeline";
import PartnersRegions from "./PartnersRegions";
import StaffManagement from "./StaffManagement";
import Tasks from "./Tasks";
import { EmptyState } from "./States";

type View = "inbox" | "accounts" | "pipeline" | "tasks" | "search" | "leads" | "dashboard" | "partners" | "staff" | "admin";

const NAV_ITEMS: Array<{ key: View; label: string; description: string }> = [
  { key: "inbox", label: "문의 인박스", description: "우선순위 문의" },
  { key: "accounts", label: "고객사", description: "계정 정보" },
  { key: "pipeline", label: "영업기회", description: "딜 파이프라인" },
  { key: "tasks", label: "할 일", description: "후속 업무" },
  { key: "search", label: "AI 검색", description: "데이터 질문" },
  { key: "leads", label: "잠재고객", description: "아웃바운드" },
  { key: "dashboard", label: "성과", description: "파이프라인" },
  { key: "partners", label: "파트너·지역", description: "총판과 지역 담당" },
  { key: "staff", label: "계정 관리", description: "직원 계정" },
  { key: "admin", label: "API 관리", description: "연동 상태" }
];

const SEARCH_EXAMPLES = [
  "우선순위가 높은 문의 5건을 보여줘",
  "등록된 전체 고객 수를 알려줘",
  "업종별 잠재고객 수를 보여줘"
];

function initialView(): View {
  const hash = window.location.hash.slice(1);
  return NAV_ITEMS.some((item) => item.key === hash) ? hash as View : "inbox";
}

export default function InternalApp() {
  const [session, setSession] = useState<Session | null>(() => loadSession());
  const [view, setView] = useState<View>(initialView);

  useEffect(() => {
    const expire = () => setSession(null);
    window.addEventListener("directdesk:session-expired", expire);
    return () => window.removeEventListener("directdesk:session-expired", expire);
  }, []);

  function login(nextSession: Session) {
    saveSession(nextSession);
    setSession(nextSession);
  }

  function changeView(nextView: View) {
    window.history.replaceState(null, "", `#${nextView}`);
    setView(nextView);
  }

  function logout() {
    saveSession(null);
    setSession(null);
  }

  if (!session) return <Login onLogin={login} />;
  const visibleItems = session.role === "owner"
    ? NAV_ITEMS
    : NAV_ITEMS.filter((item) => item.key !== "staff" && item.key !== "admin");
  const currentView = (view === "staff" || view === "admin") && session.role !== "owner" ? "inbox" : view;
  const activeItem = visibleItems.find((item) => item.key === currentView) ?? visibleItems[0];

  return (
    <div className="internal-shell">
      <nav className="sidebar" aria-label="주요 메뉴">
        <a className="internal-brand" href="/" aria-label="LG Deal Scale 홈">
          <span className="brand-symbol small" aria-hidden="true">LG</span>
          <span className="brand-copy"><strong>LG Deal Scale</strong><small>SALES WORKSPACE</small></span>
        </a>

        <div className="nav-group">
          <span className="nav-label">WORKSPACE</span>
          {visibleItems.map((item) => (
            <button
              key={item.key}
              className={currentView === item.key ? "active" : ""}
              onClick={() => changeView(item.key)}
              aria-current={currentView === item.key ? "page" : undefined}
              title={item.label}
            >
              <NavIcon name={item.key} />
              <span className="nav-copy"><strong>{item.label}</strong><small>{item.description}</small></span>
            </button>
          ))}
        </div>

        <a className="public-link" href="/inquiry">
          <span className="nav-external" aria-hidden="true">↗</span>
          <span><strong>공개 문의 페이지</strong><small>고객 화면 열기</small></span>
        </a>
      </nav>

      <div className="internal-main">
        <header className="topbar">
          <div className="topbar-context">
            <span className="mobile-brand-symbol" aria-hidden="true">LG</span>
            <div><strong>{activeItem.label}</strong><span>{activeItem.description}</span></div>
          </div>
          <div className="user-menu">
            <span className="user-avatar" aria-hidden="true">{session.name.slice(0, 1)}</span>
            <span className="user-copy"><strong>{session.name}</strong><small>{session.role === "owner" ? "총관리자" : session.role === "manager" ? "관리자" : "영업 담당자"}</small></span>
            <button className="ghost-button" onClick={logout}>로그아웃</button>
          </div>
        </header>

        {currentView === "inbox" ? <Inbox session={session} /> : null}
        {currentView === "accounts" ? <Accounts session={session} /> : null}
        {currentView === "pipeline" ? <Pipeline session={session} /> : null}
        {currentView === "tasks" ? <Tasks session={session} /> : null}
        {currentView === "search" ? <Search session={session} /> : null}
        {currentView === "leads" ? <Outbound session={session} /> : null}
        {currentView === "dashboard" ? <Dashboard session={session} /> : null}
        {currentView === "partners" ? <PartnersRegions session={session} /> : null}
        {currentView === "staff" && session.role === "owner" ? <StaffManagement session={session} /> : null}
        {currentView === "admin" && session.role === "owner" ? <ApiManagement session={session} /> : null}
      </div>
    </div>
  );
}

function NavIcon({ name }: { name: View }) {
  const paths: Record<View, string> = {
    inbox: "M3.5 5.5h17v13h-17z M3.5 6l8.5 7 8.5-7",
    accounts: "M4 20V7l8-4 8 4v13 M8 10h2 M14 10h2 M8 14h2 M14 14h2 M10 20v-3h4v3",
    pipeline: "M4 18V9h4v9z M10 18V5h4v13z M16 18v-6h4v6z",
    tasks: "M5 5h14v14H5z M8 10l2 2 5-5 M8 16h8",
    search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z M16 16l4 4",
    leads: "M12 3l2.2 4.7L19 10l-4.8 2.3L12 17l-2.2-4.7L5 10l4.8-2.3z M5 17l-2 4 M19 17l2 4",
    dashboard: "M4 20V11h3v9z M10.5 20V4h3v16z M17 20v-6h3v6z",
    partners: "M4 20V8h7v12 M13 20V4h7v16 M7 11h1 M7 15h1 M16 8h1 M16 12h1 M16 16h1",
    staff: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M4 21a8 8 0 0 1 16 0 M19 8v6 M16 11h6",
    admin: "M5 4h14v16H5z M8 8h8 M8 12h8 M8 16h5"
  };
  return (
    <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d={paths[name]} />
    </svg>
  );
}

function Search({ session }: { session: Session }) {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<SearchResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      setResult(await api<SearchResult>("/search", {
        method: "POST",
        body: JSON.stringify({ question })
      }, session));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "검색하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  const columns = result?.rows[0] ? Object.keys(result.rows[0]) : [];
  return (
    <section className="workspace" aria-labelledby="search-title">
      <div className="commandbar">
        <div><h1 id="search-title">자연어 데이터 검색</h1><p>일상적인 질문으로 CRM 데이터를 안전하게 조회하세요.</p></div>
        <span className="safety-chip"><span aria-hidden="true">●</span> 읽기 전용</span>
      </div>

      <section className="search-card" aria-busy={busy}>
        <form className="search-form" onSubmit={submit}>
          <label htmlFor="question">무엇을 확인할까요?</label>
          <div className="search-input-row">
            <input
              id="question"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="예: 이번 달 구매 문의를 우선순위순으로 보여줘"
              minLength={2}
              required
            />
            <button className="primary" disabled={busy || question.trim().length < 2}>
              {busy ? <><span className="spinner" aria-hidden="true" />조회 중</> : "검색"}
            </button>
          </div>
        </form>
        <div className="query-suggestions" aria-label="예시 질문">
          <span>예시</span>
          {SEARCH_EXAMPLES.map((example) => <button type="button" key={example} onClick={() => setQuestion(example)}>{example}</button>)}
        </div>
      </section>

      {error ? <p className="error notice" role="alert">{error}</p> : null}
      {result ? (
        <section className="search-results" aria-live="polite">
          <div className="section-heading"><div><h2>조회 결과</h2></div><span className="count-chip">{result.rows.length}개 행</span></div>
          {result.rows.length ? (
            <div className="data-grid-wrap" role="region" aria-label="자연어 검색 결과 표, 가로 스크롤 가능" tabIndex={0}>
              <table className="data-grid">
                <caption className="sr-only">자연어 검색 결과</caption>
                <thead><tr>{columns.map((column) => <th scope="col" key={column}>{column}</th>)}</tr></thead>
                <tbody>{result.rows.map((row, index) => <tr key={index}>{columns.map((column) => <td key={column}>{formatCell(row[column])}</td>)}</tr>)}</tbody>
              </table>
            </div>
          ) : <EmptyState title="조건에 맞는 데이터가 없습니다" description="질문의 기간이나 조건을 바꿔 다시 검색해보세요." />}
          <details className="sql-details">
            <summary>실행된 SQL 보기</summary>
            <pre className="sql-preview">{result.sql}</pre>
          </details>
        </section>
      ) : !busy ? <div className="search-placeholder"><span aria-hidden="true">⌕</span><p>질문을 입력하면 결과가 여기에 표시됩니다.</p></div> : null}
    </section>
  );
}

function formatCell(value: unknown) {
  if (value === null || value === undefined) return "-";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
