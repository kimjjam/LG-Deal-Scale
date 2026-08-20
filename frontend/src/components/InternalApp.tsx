import { FormEvent, useEffect, useState } from "react";

import { api, loadSession, saveSession } from "../api";
import type { Account, SearchResult, Session } from "../types";
import Inbox from "./Inbox";
import Login from "./Login";
import Outbound from "./Outbound";
import { EmptyState, LoadingState } from "./States";

type View = "inbox" | "accounts" | "search" | "leads" | "dashboard";

const NAV_ITEMS: Array<{ key: View; label: string; description: string }> = [
  { key: "inbox", label: "문의 인박스", description: "우선순위 문의" },
  { key: "accounts", label: "고객사", description: "계정 정보" },
  { key: "search", label: "AI 검색", description: "데이터 질문" },
  { key: "leads", label: "잠재고객", description: "아웃바운드" },
  { key: "dashboard", label: "성과", description: "파이프라인" }
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
  const activeItem = NAV_ITEMS.find((item) => item.key === view) ?? NAV_ITEMS[0];

  return (
    <div className="internal-shell">
      <nav className="sidebar" aria-label="주요 메뉴">
        <a className="internal-brand" href="/" aria-label="DirectDesk 홈">
          <span className="brand-symbol small" aria-hidden="true">D</span>
          <span className="brand-copy"><strong>DirectDesk</strong><small>DAONBIZ SALES</small></span>
        </a>

        <div className="nav-group">
          <span className="nav-label">WORKSPACE</span>
          {NAV_ITEMS.map((item) => (
            <button
              key={item.key}
              className={view === item.key ? "active" : ""}
              onClick={() => changeView(item.key)}
              aria-current={view === item.key ? "page" : undefined}
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
            <span className="mobile-brand-symbol" aria-hidden="true">D</span>
            <div><strong>{activeItem.label}</strong><span>{activeItem.description}</span></div>
          </div>
          <div className="user-menu">
            <span className="user-avatar" aria-hidden="true">{session.name.slice(0, 1)}</span>
            <span className="user-copy"><strong>{session.name}</strong><small>{session.role === "manager" ? "관리자" : "영업 담당자"}</small></span>
            <button className="ghost-button" onClick={logout}>로그아웃</button>
          </div>
        </header>

        {view === "inbox" ? <Inbox session={session} /> : null}
        {view === "accounts" ? <Accounts session={session} /> : null}
        {view === "search" ? <Search session={session} /> : null}
        {view === "leads" ? <Outbound session={session} /> : null}
        {view === "dashboard" ? <Outbound session={session} dashboardOnly /> : null}
      </div>
    </div>
  );
}

function NavIcon({ name }: { name: View }) {
  const paths: Record<View, string> = {
    inbox: "M3.5 5.5h17v13h-17z M3.5 6l8.5 7 8.5-7",
    accounts: "M4 20V7l8-4 8 4v13 M8 10h2 M14 10h2 M8 14h2 M14 14h2 M10 20v-3h4v3",
    search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z M16 16l4 4",
    leads: "M12 3l2.2 4.7L19 10l-4.8 2.3L12 17l-2.2-4.7L5 10l4.8-2.3z M5 17l-2 4 M19 17l2 4",
    dashboard: "M4 20V11h3v9z M10.5 20V4h3v16z M17 20v-6h3v6z"
  };
  return (
    <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d={paths[name]} />
    </svg>
  );
}

function Accounts({ session }: { session: Session }) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void api<Account[]>("/accounts", {}, session)
      .then((rows) => { if (active) setAccounts(rows); })
      .catch((requestError: unknown) => {
        if (active) setError(requestError instanceof Error ? requestError.message : "고객사를 불러오지 못했습니다.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [session]);

  return (
    <section className="workspace" aria-labelledby="accounts-title" aria-busy={loading}>
      <div className="commandbar">
        <div><span className="eyebrow">ACCOUNTS</span><h1 id="accounts-title">고객사</h1><p>문의와 거래의 기준이 되는 업체 정보입니다.</p></div>
        <span className="count-chip">{accounts.length.toLocaleString("ko-KR")}개 업체</span>
      </div>
      {error ? <p className="error notice" role="alert">{error}</p> : null}
      {loading ? <LoadingState label="고객사를 불러오는 중" /> : (
        <div className="data-grid-wrap" role="region" aria-label="고객사 표, 가로 스크롤 가능" tabIndex={0}>
          <table className="data-grid">
            <caption className="sr-only">등록된 고객사 목록</caption>
            <thead><tr><th scope="col">업체명</th><th scope="col">연락처</th><th scope="col">업종</th><th scope="col">객실 수</th><th scope="col">등록일</th></tr></thead>
            <tbody>
              {accounts.length ? accounts.map((account) => (
                <tr key={account.id}>
                  <td><span className="company-cell"><span className="company-avatar" aria-hidden="true">{account.name.slice(0, 1)}</span><strong>{account.name}</strong></span></td>
                  <td>{account.phone}</td>
                  <td>{String(account.attributes.business_type ?? "-")}</td>
                  <td>{String(account.attributes.room_count ?? "-")}</td>
                  <td>{new Date(account.created_at).toLocaleDateString("ko-KR")}</td>
                </tr>
              )) : <tr><td colSpan={5}><EmptyState title="등록된 고객사가 없습니다" description="새 문의가 접수되면 고객사가 자동으로 생성됩니다." /></td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </section>
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
        <div><span className="eyebrow">READ-ONLY AI SEARCH</span><h1 id="search-title">자연어 데이터 검색</h1><p>일상적인 질문으로 CRM 데이터를 안전하게 조회하세요.</p></div>
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
          <div className="section-heading"><div><span className="eyebrow">RESULT</span><h2>조회 결과</h2></div><span className="count-chip">{result.rows.length}개 행</span></div>
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
