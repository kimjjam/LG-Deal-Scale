import { useEffect, useState } from "react";

import { api } from "../api";
import type { Inquiry, Session, StaffMember } from "../types";
import DetailDialog from "./DetailDialog";
import { EmptyState, LoadingState } from "./States";

const STATUS_LABELS: Record<string, string> = {
  open: "접수",
  routed: "배정 완료",
  resolved: "처리 완료"
};

export default function Inbox({ session }: { session: Session }) {
  const [inquiries, setInquiries] = useState<Inquiry[]>([]);
  const [staff, setStaff] = useState<StaffMember[]>([]);
  const [scope, setScope] = useState<"mine" | "all">(session.role === "manager" ? "all" : "mine");
  const [sort, setSort] = useState<"priority" | "latest">("priority");
  const [selected, setSelected] = useState<Inquiry | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    Promise.all([
      api<Inquiry[]>(`/inquiries?scope=${scope}&sort_by=${sort}`, {}, session),
      session.role === "manager" ? api<StaffMember[]>("/staff?role=rep", {}, session) : Promise.resolve([])
    ]).then(([inquiryRows, staffRows]) => {
      if (active) {
        setInquiries(inquiryRows);
        setStaff(staffRows);
        setSelected((current) => inquiryRows.find((row) => row.id === current?.id) ?? null);
      }
    }).catch((requestError: unknown) => {
      if (active) setError(requestError instanceof Error ? requestError.message : "문의함을 불러오지 못했습니다.");
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [scope, sort, session]);

  function changeScope(nextScope: "mine" | "all") {
    setLoading(true);
    setError("");
    setScope(nextScope);
  }

  function changeSort(nextSort: "priority" | "latest") {
    setLoading(true);
    setError("");
    setSort(nextSort);
  }

  async function retry(inquiryId: number) {
    setBusyId(inquiryId);
    setError("");
    try {
      await api(`/inquiries/${inquiryId}/score`, { method: "POST" }, session);
      const rows = await api<Inquiry[]>(`/inquiries?scope=${scope}&sort_by=${sort}`, {}, session);
      setInquiries(rows);
      setSelected(rows.find((row) => row.id === inquiryId) ?? null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "스코어링을 다시 실행하지 못했습니다.");
    } finally {
      setBusyId(null);
    }
  }

  async function assign(inquiryId: number, assigneeId: string) {
    if (!assigneeId) return;
    setBusyId(inquiryId);
    setError("");
    try {
      await api(`/inquiries/${inquiryId}/assign`, {
        method: "POST",
        body: JSON.stringify({ assignee_id: assigneeId })
      }, session);
      const assigneeName = staff.find((member) => member.id === assigneeId)?.name ?? null;
      setInquiries((rows) => rows.map((row) => row.id === inquiryId ? {
        ...row,
        assignee_id: assigneeId,
        assignee_name: assigneeName
      } : row));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "담당자를 변경하지 못했습니다.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="workspace" aria-labelledby="inbox-title" aria-busy={loading}>
      <div className="commandbar">
        <div>
          <span className="eyebrow">INQUIRY QUEUE</span>
          <h1 id="inbox-title">문의 인박스</h1>
          <p>구매 가능성이 높은 문의부터 확인하고 다음 행동을 결정하세요.</p>
        </div>
        <div className="command-actions">
          <label>범위
            <select value={scope} onChange={(event) => changeScope(event.target.value as "mine" | "all")}>
              <option value="mine">내 문의함</option>
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
            <thead><tr><th scope="col">고객사</th><th scope="col">문의 내용</th><th scope="col">구매 의도</th><th scope="col">우선순위</th><th scope="col">상태</th><th scope="col">접수일</th><th scope="col">담당자</th></tr></thead>
            <tbody>
              {inquiries.length ? inquiries.map((inquiry) => (
                <tr key={inquiry.id} className={selected?.id === inquiry.id ? "selected-row" : ""}>
                  <td><button className="company-link" type="button" onClick={() => setSelected(inquiry)}><span className="company-avatar" aria-hidden="true">{(inquiry.account_name ?? "?").slice(0, 1)}</span><span>{inquiry.account_name ?? `고객사 #${inquiry.account_id}`}</span></button></td>
                  <td><button className="inquiry-link" type="button" onClick={() => setSelected(inquiry)}>{inquiry.content}</button></td>
                  <td>{inquiry.score ? <div className="intent-cell"><strong>{inquiry.score.category}</strong>{inquiry.score.confidence < 0.6 ? <span className="status-badge warning">확신 낮음</span> : null}</div> : <button className="text-button" disabled={busyId === inquiry.id} onClick={() => void retry(inquiry.id)}>{busyId === inquiry.id ? "처리 중…" : "스코어링 재시도"}</button>}</td>
                  <td>{inquiry.score ? <span className={`score-pill ${scoreTone(inquiry.score.total)}`}><strong>{Math.round(inquiry.score.total)}</strong><small>/100</small></span> : <span className="muted">대기 중</span>}</td>
                  <td><span className={`status-badge ${inquiry.status}`}><span className="status-dot" aria-hidden="true" />{STATUS_LABELS[inquiry.status] ?? inquiry.status}</span></td>
                  <td className="nowrap">{new Date(inquiry.created_at).toLocaleDateString("ko-KR")}</td>
                  <td>
                    {session.role === "manager" ? (
                      <select
                        aria-label={`문의 ${inquiry.id} 담당자 재배정`}
                        value={inquiry.assignee_id ?? ""}
                        disabled={busyId === inquiry.id}
                        onChange={(event) => void assign(inquiry.id, event.target.value)}
                      >
                        <option value="">담당자 선택</option>
                        {staff.map((member) => <option key={member.id} value={member.id}>{member.name}</option>)}
                      </select>
                    ) : <span>{inquiry.assignee_name ?? "미배정"}</span>}
                  </td>
                </tr>
              )) : <tr><td colSpan={7}><EmptyState title="표시할 문의가 없습니다" description={scope === "mine" ? "아직 배정된 문의가 없습니다." : "새 문의가 접수되면 여기에 표시됩니다."} /></td></tr>}
            </tbody>
          </table>
        </div>
      )}

      {selected ? (
        <DetailDialog labelledBy="inquiry-detail-title" onClose={() => setSelected(null)}>
          <div className="panel-heading">
            <div><span className="eyebrow">INQUIRY #{selected.id}</span><h2 id="inquiry-detail-title">{selected.account_name ?? `고객사 #${selected.account_id}`}</h2></div>
            <button className="icon-button" type="button" onClick={() => setSelected(null)} aria-label="문의 상세 닫기">×</button>
          </div>
          <div className="panel-content">
            <div className="detail-meta">
              <span className={`status-badge ${selected.status}`}>{STATUS_LABELS[selected.status] ?? selected.status}</span>
              <span>{new Date(selected.created_at).toLocaleString("ko-KR")}</span>
              <span>담당 {selected.assignee_name ?? "미배정"}</span>
            </div>
            <section className="inquiry-copy" aria-labelledby="inquiry-copy-title">
              <h3 id="inquiry-copy-title">문의 내용</h3>
              <p>{selected.content}</p>
            </section>
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
