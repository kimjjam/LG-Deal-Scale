import { FormEvent, useEffect, useState } from "react";

import { api, sessionStaffId } from "../api";
import type { Account, Session, StaffMember, Task, TaskStatus } from "../types";
import DetailDialog from "./DetailDialog";
import { EmptyState, LoadingState } from "./States";

const PAGE_SIZE = 25;

function taskPath(scope: "mine" | "all", status: TaskStatus | "", overdue: "" | "true", q: string, offset: number) {
  const query = new URLSearchParams({ scope, limit: String(PAGE_SIZE + 1), offset: String(offset) });
  if (status) query.set("status", status);
  if (overdue) query.set("overdue", overdue);
  if (q) query.set("q", q);
  return `/crm/tasks?${query}`;
}

export default function Tasks({ session }: { session: Session }) {
  const canManage = session.role !== "rep";
  const ownId = sessionStaffId(session);
  const [items, setItems] = useState<Task[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [accountSearch, setAccountSearch] = useState("");
  const [staff, setStaff] = useState<StaffMember[]>([]);
  const [selected, setSelected] = useState<Task | null>(null);
  const [scope, setScope] = useState<"mine" | "all">("mine");
  const [status, setStatus] = useState<TaskStatus | "">("pending");
  const [overdue, setOverdue] = useState<"" | "true">("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [hasNext, setHasNext] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    const rows = await api<Task[]>(taskPath(scope, status, overdue, q, offset), {}, session);
    setHasNext(rows.length > PAGE_SIZE);
    const page = rows.slice(0, PAGE_SIZE);
    setItems(page); setSelected((current) => page.find((item) => item.id === current?.id) ?? null);
  }

  useEffect(() => {
    let active = true;
    Promise.all([api<Task[]>(taskPath(scope, status, overdue, q, offset), {}, session), canManage ? api<StaffMember[]>("/staff?role=rep", {}, session) : Promise.resolve([])])
      .then(([rows, members]) => { if (active) { setHasNext(rows.length > PAGE_SIZE); setItems(rows.slice(0, PAGE_SIZE)); setStaff(members.filter((member) => member.is_active)); } })
      .catch((requestError: unknown) => { if (active) setError(message(requestError)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [canManage, offset, overdue, q, scope, session, status]);

  useEffect(() => {
    let active = true;
    const query = new URLSearchParams({ limit: "50", offset: "0" });
    if (accountSearch.trim()) query.set("q", accountSearch.trim());
    void api<Account[]>(`/accounts?${query}`, {}, session).then((rows) => { if (active) setAccounts(rows); }).catch((requestError: unknown) => { if (active) setError(message(requestError)); });
    return () => { active = false; };
  }, [accountSearch, session]);

  async function action(request: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await request(); await load(); return true; }
    catch (requestError) { setError(message(requestError)); }
    finally { setBusy(false); }
    return false;
  }

  return <section className="workspace" aria-labelledby="tasks-title" aria-busy={loading || busy}>
    <div className="commandbar"><div><h1 id="tasks-title">할 일</h1><p>후속 연락과 마감일을 놓치지 않도록 관리합니다.</p></div><div className="command-actions"><label>범위<select value={scope} onChange={(event) => { setOffset(0); setScope(event.target.value as "mine" | "all"); }}><option value="mine">내 할 일</option>{canManage ? <option value="all">전체</option> : null}</select></label><label>상태<select value={status} onChange={(event) => { setOffset(0); setStatus(event.target.value as TaskStatus | ""); }}><option value="">전체</option><option value="pending">미완료</option><option value="completed">완료</option></select></label><label>기한<select value={overdue} onChange={(event) => { setOffset(0); setOverdue(event.target.value as "" | "true"); }}><option value="">전체</option><option value="true">기한 초과</option></select></label><label>검색<input value={q} onChange={(event) => { setOffset(0); setQ(event.target.value); }} /></label></div></div>
    {ownId ? <CreateTask accounts={accounts} staff={staff} ownId={ownId} canManage={canManage} busy={busy} onSearch={setAccountSearch} onCreate={(payload) => action(() => api("/crm/tasks", { method: "POST", body: JSON.stringify(payload) }, session))} /> : <p className="error notice" role="alert">로그인 토큰에서 담당자 정보를 확인할 수 없습니다.</p>}
    {error ? <p className="error notice" role="alert">{error}</p> : null}
    {loading ? <LoadingState label="할 일을 불러오는 중" /> : <div className="data-grid-wrap" role="region" aria-label="할 일 목록" tabIndex={0}><table className="data-grid"><caption className="sr-only">할 일 목록</caption><thead><tr><th>할 일</th><th>상태</th><th>마감</th><th>고객사</th><th>작업</th></tr></thead><tbody>{items.length ? items.map((item) => <tr key={item.id}><td><button className="inquiry-link" onClick={() => setSelected(item)}>{item.title}</button></td><td><span className={`status-badge ${item.status}`}>{item.status === "completed" ? "완료" : "미완료"}</span></td><td className={item.status === "pending" && new Date(item.due_at) < new Date() ? "danger-text" : ""}>{dateTime(item.due_at)}</td><td>#{item.account_id}</td><td>{item.status === "pending" ? <button className="text-button" disabled={busy} onClick={() => void action(() => api(`/crm/tasks/${item.id}/complete`, { method: "POST" }, session))}>완료</button> : "-"}</td></tr>) : <tr><td colSpan={5}><EmptyState title="할 일이 없습니다" description="필터를 바꾸거나 새 할 일을 등록하세요." /></td></tr>}</tbody></table></div>}
    <div className="pager"><button className="secondary-button" type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>이전</button><span>{Math.floor(offset / PAGE_SIZE) + 1}페이지</span><button className="secondary-button" type="button" disabled={!hasNext} onClick={() => setOffset(offset + PAGE_SIZE)}>다음</button></div>
    {selected ? <EditTask item={selected} staff={staff} canManage={canManage} busy={busy} onClose={() => setSelected(null)} onSave={(payload) => action(() => api(`/crm/tasks/${selected.id}`, { method: "PATCH", body: JSON.stringify(payload) }, session))} /> : null}
  </section>;
}

function CreateTask({ accounts, staff, ownId, canManage, busy, onSearch, onCreate }: { accounts: Account[]; staff: StaffMember[]; ownId: string; canManage: boolean; busy: boolean; onSearch: (value: string) => void; onCreate: (payload: Record<string, string | number>) => Promise<boolean> }) {
  const [accountId, setAccountId] = useState(""); const [title, setTitle] = useState(""); const [dueAt, setDueAt] = useState(""); const [assignee, setAssignee] = useState(canManage ? "" : ownId);
  return <details className="create-strip"><summary>새 할 일</summary><form onSubmit={async (event: FormEvent) => { event.preventDefault(); if (await onCreate({ account_id: Number(accountId), assignee_id: canManage ? assignee : ownId, title, due_at: new Date(dueAt).toISOString() })) { setAccountId(""); setTitle(""); setDueAt(""); setAssignee(canManage ? "" : ownId); } }}><label>고객사 검색<input onChange={(event) => onSearch(event.target.value)} placeholder="업체명 또는 연락처" /></label><label>고객사<select value={accountId} onChange={(event) => setAccountId(event.target.value)} required><option value="">선택</option>{accounts.map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}</select></label><label>할 일<input value={title} onChange={(event) => setTitle(event.target.value)} required /></label><label>마감<input type="datetime-local" value={dueAt} onChange={(event) => setDueAt(event.target.value)} required /></label>{canManage ? <label>담당자<select value={assignee} onChange={(event) => setAssignee(event.target.value)} required><option value="">선택</option>{staff.map((member) => <option key={member.id} value={member.id}>{member.name}</option>)}</select></label> : null}<button className="primary" disabled={busy}>등록</button></form></details>;
}

function EditTask({ item, staff, canManage, busy, onClose, onSave }: { item: Task; staff: StaffMember[]; canManage: boolean; busy: boolean; onClose: () => void; onSave: (payload: Record<string, string>) => Promise<boolean> }) {
  const [title, setTitle] = useState(item.title); const [dueAt, setDueAt] = useState(toLocalInput(item.due_at)); const [status, setStatus] = useState<TaskStatus>(item.status); const [assignee, setAssignee] = useState(item.assignee_id);
  return <DetailDialog labelledBy="task-edit-title" onClose={onClose}><div className="panel-heading"><h2 id="task-edit-title">할 일 수정</h2><button className="icon-button" type="button" onClick={onClose} aria-label="할 일 닫기">×</button></div><form className="panel-content form-grid" onSubmit={(event) => { event.preventDefault(); void onSave({ title, due_at: new Date(dueAt).toISOString(), status, ...(canManage && assignee !== item.assignee_id ? { assignee_id: assignee } : {}) }); }}><label>할 일<input value={title} onChange={(event) => setTitle(event.target.value)} required /></label><label>마감<input type="datetime-local" value={dueAt} onChange={(event) => setDueAt(event.target.value)} required /></label><label>상태<select value={status} onChange={(event) => setStatus(event.target.value as TaskStatus)}><option value="pending">미완료</option><option value="completed">완료</option></select></label>{canManage ? <label>담당자<select value={assignee} onChange={(event) => setAssignee(event.target.value)}>{staff.map((member) => <option key={member.id} value={member.id}>{member.name}</option>)}</select></label> : null}<button className="primary wide-field" disabled={busy}>저장</button></form></DetailDialog>;
}

function toLocalInput(value: string) { const date = new Date(value); return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16); }
function dateTime(value: string) { return new Date(value).toLocaleString("ko-KR"); }
function message(error: unknown) { return error instanceof Error ? error.message : "할 일을 처리하지 못했습니다."; }
