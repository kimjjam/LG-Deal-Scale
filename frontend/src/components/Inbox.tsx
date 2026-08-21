import { FormEvent, useEffect, useState } from "react";

import { api, sessionStaffId } from "../api";
import type { ActivityType, AssignmentResult, Inquiry, InquiryStatus, IntentCategory, NearbyStoreStatus, Partner, Session, StaffMember } from "../types";
import DetailDialog from "./DetailDialog";
import { EmptyState, LoadingState } from "./States";

const STATUS_LABELS: Record<string, string> = {
  open: "접수",
  routed: "배정 완료",
  resolved: "처리 완료"
};
const NEARBY_STATUS_LABELS: Record<NearbyStoreStatus, string> = {
  location_missing: "지역 정보 없음",
  not_configured: "검색 이용 불가",
  failed: "검색 실패",
  no_results: "검색 결과 없음",
  success: "검색 완료"
};
const PAGE_SIZE = 25;
const MAX_AMOUNT = 999999999999.99;
type InboxScope = "unassigned" | "mine" | "my_region" | "all";

export default function Inbox({ session }: { session: Session }) {
  const isManager = session.role !== "rep";
  const ownId = sessionStaffId(session);
  const [inquiries, setInquiries] = useState<Inquiry[]>([]);
  const [staff, setStaff] = useState<StaffMember[]>([]);
  const [partners, setPartners] = useState<Partner[]>([]);
  const [scope, setScope] = useState<InboxScope>(isManager ? "all" : "unassigned");
  const [sort, setSort] = useState<"priority" | "latest">("priority");
  const [offset, setOffset] = useState(0);
  const [hasNext, setHasNext] = useState(false);
  const [selected, setSelected] = useState<Inquiry | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [intent, setIntent] = useState<IntentCategory>("구매임박");
  const [intentReason, setIntentReason] = useState("");
  const [activityType, setActivityType] = useState<ActivityType>("call");
  const [activityContent, setActivityContent] = useState("");
  const [taskTitle, setTaskTitle] = useState("");
  const [taskDueAt, setTaskDueAt] = useState("");
  const [dealTitle, setDealTitle] = useState("");
  const [dealAmount, setDealAmount] = useState("");
  const nearbyStores = selected?.nearby_store_search?.stores ?? [];
  const canMutateSelected = isManager || selected?.assignee_id === ownId;

  useEffect(() => {
    let active = true;
    Promise.all([
      api<Inquiry[]>(`/inquiries?scope=${scope}&sort_by=${sort}&limit=${PAGE_SIZE + 1}&offset=${offset}`, {}, session),
      isManager ? api<StaffMember[]>("/staff?role=rep", {}, session) : Promise.resolve([]),
      isManager ? api<Partner[]>("/partners-regions/partners", {}, session) : Promise.resolve([])
    ]).then(([inquiryRows, staffRows, partnerRows]) => {
      if (active) {
        setHasNext(inquiryRows.length > PAGE_SIZE);
        const page = inquiryRows.slice(0, PAGE_SIZE);
        setInquiries(page);
        setStaff(staffRows.filter((member) => member.is_active));
        setPartners(partnerRows.filter((partner) => partner.is_active));
        setSelected((current) => page.find((row) => row.id === current?.id) ?? null);
      }
    }).catch((requestError: unknown) => {
      if (active) setError(requestError instanceof Error ? requestError.message : "문의함을 불러오지 못했습니다.");
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [isManager, offset, scope, sort, session]);

  function changeScope(nextScope: InboxScope) {
    setLoading(true);
    setError("");
    setOffset(0);
    setScope(nextScope);
  }

  function changeSort(nextSort: "priority" | "latest") {
    setLoading(true);
    setError("");
    setOffset(0);
    setSort(nextSort);
  }

  async function retry(inquiryId: number) {
    await perform(inquiryId, () => api(`/inquiries/${inquiryId}/score`, { method: "POST" }, session), "스코어링을 다시 실행하지 못했습니다.");
  }

  async function assign(inquiryId: number, assigneeId: string) {
    if (!assigneeId) return;
    setBusyId(inquiryId);
    setError("");
    try {
      const result = await api<AssignmentResult>(`/inquiries/${inquiryId}/assign`, {
        method: "POST",
        body: JSON.stringify({ assignee_id: assigneeId })
      }, session);
      const assigneeName = staff.find((member) => member.id === assigneeId)?.name ?? null;
      setInquiries((rows) => rows.map((row) => row.id === inquiryId ? {
        ...row,
        assignee_id: assigneeId,
        assignee_name: assigneeName,
        status: result.status
      } : row));
      setSelected((current) => current?.id === inquiryId ? {
        ...current,
        assignee_id: assigneeId,
        assignee_name: assigneeName,
        status: result.status
      } : current);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "담당자를 변경하지 못했습니다.");
    } finally {
      setBusyId(null);
    }
  }

  async function claim(inquiryId: number) {
    await perform(inquiryId, () => api(`/inquiries/${inquiryId}/claim`, { method: "POST" }, session), "문의를 담당하지 못했습니다.");
  }

  async function linkPartner(inquiryId: number, partnerId: string) {
    await perform(inquiryId, () => api(`/inquiries/${inquiryId}/partner`, {
      method: "PATCH",
      body: JSON.stringify({ partner_id: partnerId ? Number(partnerId) : null })
    }, session), "검증 파트너 연결을 변경하지 못했습니다.");
  }

  async function refresh(inquiryId: number) {
    const rows = await api<Inquiry[]>(`/inquiries?scope=${scope}&sort_by=${sort}&limit=${PAGE_SIZE + 1}&offset=${offset}`, {}, session);
    setHasNext(rows.length > PAGE_SIZE);
    const page = rows.slice(0, PAGE_SIZE);
    setInquiries(page);
    setSelected(page.find((row) => row.id === inquiryId) ?? null);
  }

  async function perform(inquiryId: number, request: () => Promise<unknown>, fallback: string) {
    setBusyId(inquiryId);
    setError("");
    try {
      await request();
      await refresh(inquiryId);
      return true;
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : fallback);
      return false;
    } finally {
      setBusyId(null);
    }
  }

  async function submitIntent(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    if (await perform(selected.id, () => api(`/inquiries/${selected.id}/intent`, { method: "PATCH", body: JSON.stringify({ category: intent, confidence: 1, reasoning: intentReason }) }, session), "구매 의도를 수정하지 못했습니다.")) setIntentReason("");
  }

  async function submitActivity(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    if (await perform(selected.id, () => api("/crm/activities", { method: "POST", body: JSON.stringify({ account_id: selected.account_id, inquiry_id: selected.id, type: activityType, content: activityContent || null }) }, session), "활동을 기록하지 못했습니다.")) setActivityContent("");
  }

  async function submitTask(event: FormEvent) {
    event.preventDefault();
    if (!selected?.assignee_id) { setError("활성 담당자를 먼저 배정해주세요."); return; }
    if (await perform(selected.id, () => api("/crm/tasks", { method: "POST", body: JSON.stringify({ account_id: selected.account_id, inquiry_id: selected.id, assignee_id: selected.assignee_id, title: taskTitle, due_at: new Date(taskDueAt).toISOString() }) }, session), "할 일을 만들지 못했습니다.")) { setTaskTitle(""); setTaskDueAt(""); }
  }

  async function submitConversion(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    if (await perform(selected.id, () => api(`/inquiries/${selected.id}/opportunity`, { method: "POST", body: JSON.stringify({ title: dealTitle, amount: dealAmount ? Number(dealAmount) : null, probability: 10, expected_close_date: null }) }, session), "영업기회로 전환하지 못했습니다.")) { setDealTitle(""); setDealAmount(""); }
  }

  function selectInquiry(inquiry: Inquiry) {
    setSelected(inquiry);
    setIntent(inquiry.score?.category ?? "구매임박");
    setIntentReason("");
    setActivityType("call");
    setActivityContent("");
    setTaskTitle("");
    setTaskDueAt("");
    setDealTitle("");
    setDealAmount("");
  }

  return (
    <section className="workspace" aria-labelledby="inbox-title" aria-busy={loading}>
      <div className="commandbar">
        <div>
          <h1 id="inbox-title">문의 인박스</h1>
          <p>구매 가능성이 높은 문의부터 확인하고 다음 행동을 결정하세요.</p>
        </div>
        <div className="command-actions">
          <label>범위
            <select value={scope} onChange={(event) => changeScope(event.target.value as InboxScope)}>
              <option value="unassigned">미배정 문의</option>
              <option value="mine">내 문의함</option>
              {session.role === "manager" ? <option value="my_region">내 지역 문의</option> : null}
              <option value="all">전체 문의</option>
            </select>
          </label>
          <label>정렬
            <select value={sort} onChange={(event) => changeSort(event.target.value as "priority" | "latest")}>
              <option value="priority">영업 우선순위순</option>
              <option value="latest">최신순</option>
            </select>
          </label>
          <span className="count-chip">{inquiries.length}건</span>
        </div>
      </div>

      {error ? <p className="error notice" role="alert">{error}</p> : null}
      {loading ? <LoadingState label="문의 우선순위를 정리하는 중" /> : (
        <div className="data-grid-wrap" role="region" aria-label="문의 인박스 표, 가로 스크롤 가능" tabIndex={0}>
          <table className="data-grid inbox-grid">
            <caption className="sr-only">문의 우선순위와 담당자 목록</caption>
            <thead><tr><th scope="col">고객사</th><th scope="col">문의 내용</th><th scope="col">구매 의도</th><th scope="col">우선순위</th><th scope="col">상태</th><th scope="col">접수일</th><th scope="col">지역 매니저</th><th scope="col">담당자</th></tr></thead>
            <tbody>
              {inquiries.length ? inquiries.map((inquiry) => (
                <tr key={inquiry.id} className={selected?.id === inquiry.id ? "selected-row" : ""}>
                  <td><button className="company-link" type="button" onClick={() => selectInquiry(inquiry)}><span className="company-avatar" aria-hidden="true">{(inquiry.account_name ?? "?").slice(0, 1)}</span><span>{inquiry.account_name ?? `고객사 #${inquiry.account_id}`}</span></button></td>
                  <td><button className="inquiry-link" type="button" onClick={() => selectInquiry(inquiry)}>{inquiry.content}</button></td>
                  <td>{inquiry.score ? <div className="intent-cell"><strong>{inquiry.score.category}</strong>{inquiry.score.confidence < 0.6 ? <span className="status-badge warning">확신 낮음</span> : null}</div> : isManager || inquiry.assignee_id === ownId ? <button className="text-button" disabled={busyId === inquiry.id} onClick={() => void retry(inquiry.id)}>{busyId === inquiry.id ? "처리 중…" : "스코어링 재시도"}</button> : <span className="muted">담당 후 재시도</span>}</td>
                  <td>{inquiry.score ? <span className={`score-pill ${scoreTone(inquiry.score.total)}`}><strong>{Math.round(inquiry.score.total)}</strong><small>/100</small></span> : <span className="muted">대기 중</span>}</td>
                  <td><span className={`status-badge ${inquiry.status}`}><span className="status-dot" aria-hidden="true" />{STATUS_LABELS[inquiry.status] ?? inquiry.status}</span></td>
                  <td className="nowrap">{new Date(inquiry.created_at).toLocaleDateString("ko-KR")}</td>
                  <td>{inquiry.routing_manager_name ?? "공용"}</td>
                  <td>
                    {isManager ? (
                      <select
                        aria-label={`문의 ${inquiry.id} 담당자 재배정`}
                        value={inquiry.assignee_id ?? ""}
                        disabled={busyId === inquiry.id}
                        onChange={(event) => void assign(inquiry.id, event.target.value)}
                      >
                        <option value="">담당자 선택</option>
                        {staff.map((member) => <option key={member.id} value={member.id}>{member.name}</option>)}
                      </select>
                    ) : inquiry.assignee_id ? <span>{inquiry.assignee_name}</span> : <button className="text-button" disabled={busyId === inquiry.id} onClick={() => void claim(inquiry.id)}>담당하기</button>}
                  </td>
                </tr>
              )) : <tr><td colSpan={8}><EmptyState title="표시할 문의가 없습니다" description={scope === "mine" ? "아직 배정된 문의가 없습니다." : "새 문의가 접수되면 여기에 표시됩니다."} /></td></tr>}
            </tbody>
          </table>
        </div>
      )}
      <div className="pager"><button className="secondary-button" type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>이전</button><span>{Math.floor(offset / PAGE_SIZE) + 1}페이지</span><button className="secondary-button" type="button" disabled={!hasNext} onClick={() => setOffset(offset + PAGE_SIZE)}>다음</button></div>

      {selected ? (
        <DetailDialog labelledBy="inquiry-detail-title" onClose={() => setSelected(null)}>
          <div className="panel-heading">
            <div><span>문의 #{selected.id}</span><h2 id="inquiry-detail-title">{selected.account_name ?? `고객사 #${selected.account_id}`}</h2></div>
            <button className="icon-button" type="button" onClick={() => setSelected(null)} aria-label="문의 상세 닫기">×</button>
          </div>
          <div className="panel-content">
            <div className="detail-meta">
              <span className={`status-badge ${selected.status}`}>{STATUS_LABELS[selected.status] ?? selected.status}</span>
              <span>{new Date(selected.created_at).toLocaleString("ko-KR")}</span>
              <span>담당 {selected.assignee_name ?? "미배정"}</span>
              <span>지역 매니저 {selected.routing_manager_name ?? "공용"}</span>
            </div>
            <section className="inquiry-copy" aria-labelledby="inquiry-copy-title">
              <h3 id="inquiry-copy-title">문의 내용</h3>
              <p>{selected.content}</p>
            </section>
            <section className="inquiry-copy" aria-labelledby="curated-partner-title">
              <h3 id="curated-partner-title">{selected.partner?.is_active === false ? "과거 연결 파트너" : "연결된 검증 파트너"}</h3>
              {selected.partner ? <div className="partner-detail"><strong>{selected.partner.name}</strong>{selected.partner.is_active ? null : <span className="status-badge warning">비활성 · 과거 연결</span>}<p>{selected.partner.address}</p><p>{selected.partner.phone ?? "연락처 미등록"}</p><small>{selected.partner.verification_source} · {selected.partner.verified_at} 확인 기록</small></div> : <p className="inline-empty">연결된 검증 파트너가 없습니다.</p>}
              {isManager ? <label>검증 파트너 연결<select value={selected.partner?.is_active ? selected.partner.id : ""} disabled={busyId === selected.id} onChange={(event) => void linkPartner(selected.id, event.target.value)}><option value="">연결 해제</option>{partners.map((partner) => <option key={partner.id} value={partner.id}>{partner.name} · {partner.region}</option>)}</select></label> : null}
            </section>
            {selected.nearby_store_search ? (
              <section className="inquiry-copy" aria-labelledby="nearby-stores-title">
                <h3 id="nearby-stores-title">접수 당시 네이버 검색 후보</h3>
                <p>공식 인증 또는 연결된 총판 정보가 아닙니다.</p>
                <span className="status-badge">{NEARBY_STATUS_LABELS[selected.nearby_store_search.status]}</span>
                <p>{selected.nearby_store_search.message}</p>
                {nearbyStores.length ? <div className="store-list">{nearbyStores.map((store) => (
                  <article className="store-card" key={`${store.name}-${store.address}`}>
                    <div><strong>{store.name}</strong><p>{store.address}</p></div>
                    {store.phone ? <a aria-label={`${store.name} 전화 ${store.phone}`} href={`tel:${store.phone.replace(/[^\d+]/g, "")}`}>{store.phone}</a> : null}
                  </article>
                ))}</div> : <p className="inline-empty">저장된 주변 전문점 후보가 없습니다.</p>}
              </section>
            ) : null}
            {(!isManager && !selected.assignee_id) || canMutateSelected ? <div className="action-row" aria-label="문의 상태 변경">
              {!isManager && !selected.assignee_id ? <button className="primary" disabled={busyId === selected.id} onClick={() => void claim(selected.id)}>담당하기</button> : null}
              {canMutateSelected ? <button className="secondary-button" disabled={busyId === selected.id} onClick={() => void perform(selected.id, () => api(`/inquiries/${selected.id}/status`, { method: "PATCH", body: JSON.stringify({ status: (selected.status === "resolved" ? "open" : "resolved") satisfies InquiryStatus }) }, session), "문의 상태를 변경하지 못했습니다.")}>{selected.status === "resolved" ? "문의 다시 열기" : "처리 완료"}</button> : null}
            </div> : null}
            {selected.score ? (
              <>
                <div className="score-summary" aria-label="문의 점수 요약">
                  <div className="total-score-card"><span>영업 우선순위</span><strong>{Math.round(selected.score.total)}</strong><small>/ 100</small></div>
                  <div><span>구매 의도</span><strong>{selected.score.category}</strong><small>확신도 {Math.round(selected.score.confidence * 100)}%</small></div>
                </div>
                <section className="reasoning-section" aria-labelledby="reasoning-title">
                  <div className="section-heading compact"><h3 id="reasoning-title">점수 산정 근거</h3><span>3가지 신호</span></div>
                  {(["fit", "intent", "recency"] as const).map((axis) => (
                    <div className="reasoning-row" key={axis}>
                      <div className="reasoning-score"><span>{axis === "fit" ? "적합도" : axis === "intent" ? "구매 의도" : "최근 활동"}</span><strong>{selected.score?.[axis]}</strong></div>
                      <p>{selected.score?.reasoning[axis]}</p>
                    </div>
                  ))}
                </section>
              </>
            ) : <EmptyState title="아직 점수가 없습니다" description="스코어링 재시도를 실행해 우선순위를 계산하세요." />}
            {canMutateSelected && selected.score ? <details className="inline-form"><summary>구매 의도 수정</summary><form onSubmit={submitIntent}><label>분류<select value={intent} onChange={(event) => setIntent(event.target.value as IntentCategory)}><option value="구매임박">구매임박</option><option value="정보탐색">정보탐색</option><option value="AS·불만">AS·불만</option></select></label><label>수정 근거<textarea value={intentReason} onChange={(event) => setIntentReason(event.target.value)} required maxLength={1000} /></label><button className="primary" disabled={busyId === selected.id}>수정</button></form></details> : null}
            {canMutateSelected ? <><details className="inline-form"><summary>활동 기록</summary><form onSubmit={submitActivity}><label>유형<select value={activityType} onChange={(event) => setActivityType(event.target.value as ActivityType)}><option value="call">통화</option><option value="email">이메일</option><option value="meeting">미팅</option><option value="note">메모</option><option value="purchase">구매</option></select></label><label>내용<textarea value={activityContent} onChange={(event) => setActivityContent(event.target.value)} maxLength={10000} /></label><button className="primary" disabled={busyId === selected.id}>기록</button></form></details>
            <details className="inline-form"><summary>후속 할 일 생성</summary><form onSubmit={submitTask}><label>할 일<input value={taskTitle} onChange={(event) => setTaskTitle(event.target.value)} required /></label><label>마감<input type="datetime-local" value={taskDueAt} onChange={(event) => setTaskDueAt(event.target.value)} required /></label><button className="primary" disabled={busyId === selected.id || !selected.assignee_id}>생성</button></form></details>
            <details className="inline-form"><summary>영업기회로 전환</summary><form onSubmit={submitConversion}><label>영업기회 제목<input value={dealTitle} onChange={(event) => setDealTitle(event.target.value)} required /></label><label>예상 금액<input type="number" min="0" max={MAX_AMOUNT} step="0.01" value={dealAmount} onChange={(event) => setDealAmount(event.target.value)} /></label><button className="primary" disabled={busyId === selected.id || selected.status === "resolved"}>전환</button></form></details></> : null}
          </div>
        </DetailDialog>
      ) : null}
    </section>
  );
}

function scoreTone(score: number) {
  if (score >= 75) return "hot";
  if (score >= 50) return "warm";
  return "cool";
}
