import { useEffect, useState } from "react";

import { api, sessionStaffId } from "../api";
import type { Account, Opportunity, OpportunityStage, Session, StaffMember } from "../types";
import DetailDialog from "./DetailDialog";
import { EmptyState, LoadingState } from "./States";

const STAGES: OpportunityStage[] = ["qualify", "develop", "propose", "won", "lost"];
const STAGE_LABELS: Record<OpportunityStage, string> = { qualify: "검증", develop: "개발", propose: "제안", won: "수주", lost: "실주" };
const NEXT_STAGES: Record<OpportunityStage, OpportunityStage[]> = { qualify: ["develop", "propose", "won", "lost"], develop: ["propose", "won", "lost"], propose: ["won", "lost"], won: [], lost: [] };
const PAGE_SIZE = 25;
const MAX_AMOUNT = 999999999999.99;

function opportunityPath(q: string, stage: OpportunityStage | "", scope: "mine" | "all", ownId: string | null, offset: number) {
  const query = new URLSearchParams({ limit: String(PAGE_SIZE + 1), offset: String(offset) });
  if (q) query.set("q", q);
  if (stage) query.set("stage", stage);
  if (scope === "mine" && ownId) query.set("assignee_id", ownId);
  return `/crm/opportunities?${query}`;
}

export default function Pipeline({ session }: { session: Session }) {
  const canManage = session.role !== "rep";
  const ownId = sessionStaffId(session);
  const [items, setItems] = useState<Opportunity[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [accountSearch, setAccountSearch] = useState("");
  const [staff, setStaff] = useState<StaffMember[]>([]);
  const [selected, setSelected] = useState<Opportunity | null>(null);
  const [scope, setScope] = useState<"mine" | "all">(canManage ? "all" : "mine");
  const [stage, setStage] = useState<OpportunityStage | "">("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [hasNext, setHasNext] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    const rows = await api<Opportunity[]>(opportunityPath(q, stage, scope, ownId, offset), {}, session);
    setHasNext(rows.length > PAGE_SIZE);
    const page = rows.slice(0, PAGE_SIZE);
    setItems(page);
    setSelected((current) => page.find((item) => item.id === current?.id) ?? current);
  }

  useEffect(() => {
    let active = true;
    Promise.all([
      api<Opportunity[]>(opportunityPath(q, stage, scope, ownId, offset), {}, session),
      canManage ? api<StaffMember[]>("/staff?role=rep", {}, session) : Promise.resolve([])
    ]).then(([rows, members]) => {
      if (active) { setHasNext(rows.length > PAGE_SIZE); setItems(rows.slice(0, PAGE_SIZE)); setStaff(members.filter((member) => member.is_active)); }
    }).catch((requestError: unknown) => { if (active) setError(message(requestError)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [canManage, offset, ownId, q, scope, session, stage]);

  useEffect(() => {
    if (!canManage) return;
    let active = true;
    const query = new URLSearchParams({ limit: "50", offset: "0" });
    if (accountSearch.trim()) query.set("q", accountSearch.trim());
    void api<Account[]>(`/accounts?${query}`, {}, session).then((rows) => { if (active) setAccounts(rows); }).catch((requestError: unknown) => { if (active) setError(message(requestError)); });
    return () => { active = false; };
  }, [accountSearch, canManage, session]);

  async function save(id: number, payload: Record<string, string | number | null>) {
    setBusy(true); setError("");
    try { const updated = await api<Opportunity>(`/crm/opportunities/${id}`, { method: "PATCH", body: JSON.stringify(payload) }, session); setSelected(updated); await load(); return true; }
    catch (requestError) { setError(message(requestError)); }
    finally { setBusy(false); }
    return false;
  }

  return <section className="workspace" aria-labelledby="pipeline-title" aria-busy={loading || busy}>
    <div className="commandbar"><div><h1 id="pipeline-title">영업 파이프라인</h1><p>예상 금액과 다음 단계를 기준으로 딜을 관리합니다.</p></div><div className="command-actions">
      <label>범위<select value={scope} onChange={(event) => { setOffset(0); setScope(event.target.value as "mine" | "all"); }}><option value="mine">내 영업기회</option>{canManage ? <option value="all">전체</option> : null}</select></label>
      <label>단계<select value={stage} onChange={(event) => { setOffset(0); setStage(event.target.value as OpportunityStage | ""); }}><option value="">전체 단계</option>{STAGES.map((value) => <option key={value} value={value}>{STAGE_LABELS[value]}</option>)}</select></label>
      <label>검색<input value={q} onChange={(event) => { setOffset(0); setQ(event.target.value); }} placeholder="딜 또는 고객사" /></label>
    </div></div>
    {canManage ? <CreateOpportunity accounts={accounts} staff={staff} busy={busy} onSearch={setAccountSearch} onCreate={async (payload) => { setBusy(true); try { await api("/crm/opportunities", { method: "POST", body: JSON.stringify(payload) }, session); await load(); return true; } catch (requestError) { setError(message(requestError)); return false; } finally { setBusy(false); } }} /> : null}
    {error ? <p className="error notice" role="alert">{error}</p> : null}
    {loading ? <LoadingState label="영업기회를 불러오는 중" /> : <div className="data-grid-wrap" role="region" aria-label="영업기회 목록" tabIndex={0}><table className="data-grid"><caption className="sr-only">영업 파이프라인</caption><thead><tr><th>영업기회</th><th>단계</th><th>예상 금액</th><th>확률</th><th>예상 계약일</th><th>수정일</th></tr></thead><tbody>{items.length ? items.map((item) => <tr key={item.id}><td><button className="inquiry-link" onClick={() => setSelected(item)}>{item.title}</button></td><td><span className={`status-badge ${item.stage}`}>{STAGE_LABELS[item.stage]}</span></td><td>{money(item.amount)}</td><td>{item.probability}%</td><td>{item.expected_close_date ?? "-"}</td><td>{date(item.updated_at)}</td></tr>) : <tr><td colSpan={6}><EmptyState title="영업기회가 없습니다" description="문의나 잠재고객을 영업기회로 전환하세요." /></td></tr>}</tbody></table></div>}
    <div className="pager"><button className="secondary-button" type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>이전</button><span>{Math.floor(offset / PAGE_SIZE) + 1}페이지</span><button className="secondary-button" type="button" disabled={!hasNext} onClick={() => setOffset(offset + PAGE_SIZE)}>다음</button></div>
    {selected ? <EditOpportunity item={selected} staff={staff} canManage={canManage} busy={busy} onClose={() => setSelected(null)} onSave={save} /> : null}
  </section>;
}

function CreateOpportunity({ accounts, staff, busy, onSearch, onCreate }: { accounts: Account[]; staff: StaffMember[]; busy: boolean; onSearch: (value: string) => void; onCreate: (payload: Record<string, string | number | null>) => Promise<boolean> }) {
  const [accountId, setAccountId] = useState(""); const [assigneeId, setAssigneeId] = useState(""); const [title, setTitle] = useState("");
  return <details className="create-strip"><summary>새 영업기회</summary><form onSubmit={async (event) => { event.preventDefault(); if (await onCreate({ account_id: Number(accountId), assignee_id: assigneeId, title })) { setTitle(""); setAccountId(""); setAssigneeId(""); } }}><label>고객사 검색<input onChange={(event) => onSearch(event.target.value)} placeholder="업체명 또는 연락처" /></label><label>고객사<select value={accountId} onChange={(event) => setAccountId(event.target.value)} required><option value="">선택</option>{accounts.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>제목<input value={title} onChange={(event) => setTitle(event.target.value)} required /></label><label>담당자<select value={assigneeId} onChange={(event) => setAssigneeId(event.target.value)} required><option value="">선택</option>{staff.map((member) => <option key={member.id} value={member.id}>{member.name}</option>)}</select></label><button className="primary" disabled={busy}>생성</button></form></details>;
}

function EditOpportunity({ item, staff, canManage, busy, onClose, onSave }: { item: Opportunity; staff: StaffMember[]; canManage: boolean; busy: boolean; onClose: () => void; onSave: (id: number, payload: Record<string, string | number | null>) => Promise<boolean> }) {
  const [title, setTitle] = useState(item.title); const [amount, setAmount] = useState(item.amount == null ? "" : String(item.amount)); const [probability, setProbability] = useState(String(item.probability)); const [closeDate, setCloseDate] = useState(item.expected_close_date ?? ""); const [stage, setStage] = useState<OpportunityStage>(item.stage); const [lossReason, setLossReason] = useState(item.loss_reason ?? ""); const [assignee, setAssignee] = useState(item.assignee_id);
  const allowedStages = [item.stage, ...NEXT_STAGES[item.stage]];
  return <DetailDialog labelledBy="opportunity-title" onClose={onClose}><div className="panel-heading"><h2 id="opportunity-title">영업기회 수정</h2><button className="icon-button" type="button" onClick={onClose} aria-label="영업기회 닫기">×</button></div><form className="panel-content form-grid" onSubmit={(event) => { event.preventDefault(); void onSave(item.id, { title, amount: amount ? Number(amount) : null, probability: Number(probability), expected_close_date: closeDate || null, stage, loss_reason: stage === "lost" ? lossReason : null, ...(canManage && assignee !== item.assignee_id ? { assignee_id: assignee } : {}) }); }}>
    <label>제목<input value={title} onChange={(event) => setTitle(event.target.value)} required /></label><label>예상 금액<input type="number" min="0" max={MAX_AMOUNT} step="0.01" value={amount} onChange={(event) => setAmount(event.target.value)} /></label><label>확률<input type="number" min="0" max="100" value={probability} onChange={(event) => setProbability(event.target.value)} required /></label><label>예상 계약일<input type="date" value={closeDate} onChange={(event) => setCloseDate(event.target.value)} /></label><label>단계<select value={stage} onChange={(event) => setStage(event.target.value as OpportunityStage)}>{allowedStages.map((value) => <option key={value} value={value}>{STAGE_LABELS[value]}</option>)}</select></label>{stage === "lost" ? <label className="wide-field">실주 사유<textarea value={lossReason} onChange={(event) => setLossReason(event.target.value)} required maxLength={500} /></label> : null}{canManage ? <label>담당자<select value={assignee} onChange={(event) => setAssignee(event.target.value)}>{staff.map((member) => <option key={member.id} value={member.id}>{member.name}</option>)}</select></label> : null}<button className="primary wide-field" disabled={busy}>저장</button></form></DetailDialog>;
}

function message(error: unknown) { return error instanceof Error ? error.message : "영업기회를 처리하지 못했습니다."; }
function money(value: string | number | null) { return value == null ? "-" : `${Number(value).toLocaleString("ko-KR")}원`; }
function date(value: string) { return new Date(value).toLocaleDateString("ko-KR"); }
