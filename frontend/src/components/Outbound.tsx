import { useEffect, useState } from "react";

import { api } from "../api";
import type { Lead, OutboundDashboard, OutboundDraft, Session } from "../types";
import DetailDialog from "./DetailDialog";
import { EmptyState, LoadingState } from "./States";

const STAGE_LABELS: Record<string, string> = {
  discovered: "발굴",
  draft_generated: "초안 생성",
  approved: "검토 완료",
  contacted: "접촉",
  follow_up_due: "후속 필요",
  converted: "전환",
  dropped: "종결"
};

const PIPELINE_ORDER = [
  "discovered",
  "draft_generated",
  "approved",
  "contacted",
  "follow_up_due",
  "converted",
  "dropped"
];

interface SendResponse {
  id: number;
  mode: "dry_run" | "test_override";
  sent_at: string;
}

export default function Outbound({ session, dashboardOnly = false }: { session: Session; dashboardOnly?: boolean }) {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [dashboard, setDashboard] = useState<OutboundDashboard | null>(null);
  const [selected, setSelected] = useState<Lead | null>(null);
  const [drafts, setDrafts] = useState<OutboundDraft[]>([]);
  const [activeDraftId, setActiveDraftId] = useState<number | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busyAction, setBusyAction] = useState("");
  const [loading, setLoading] = useState(true);
  const [draftLoading, setDraftLoading] = useState(false);
  const [error, setError] = useState("");
  const selectedId = selected?.id;

  useEffect(() => {
    let active = true;
    Promise.all([
      dashboardOnly ? Promise.resolve([]) : api<Lead[]>("/outbound/leads", {}, session),
      api<OutboundDashboard>("/outbound/dashboard", {}, session)
    ]).then(([leadRows, metrics]) => {
      if (active) {
        setLeads(leadRows);
        setDashboard(metrics);
      }
    }).catch((requestError: unknown) => {
      if (active) setError(requestError instanceof Error ? requestError.message : "데이터를 불러오지 못했습니다.");
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [dashboardOnly, session]);

  useEffect(() => {
    if (!selectedId) return;
    let active = true;
    void api<OutboundDraft[]>(`/outbound/leads/${selectedId}/drafts`, {}, session)
      .then((rows) => {
        if (active) {
          setDrafts(rows);
          setActiveDraftId(rows[0]?.id ?? null);
        }
      })
      .catch((requestError: unknown) => {
        if (active) setError(requestError instanceof Error ? requestError.message : "초안을 불러오지 못했습니다.");
      })
      .finally(() => { if (active) setDraftLoading(false); });
    return () => { active = false; };
  }, [selectedId, session]);

  function openLead(lead: Lead) {
    setDrafts([]);
    setActiveDraftId(null);
    setConfirmed(false);
    setDraftLoading(true);
    setError("");
    setSelected(lead);
  }

  function closeLead() {
    setSelected(null);
    setDrafts([]);
    setActiveDraftId(null);
  }

  function updateLeadStage(leadId: number, pipelineStage: string) {
    setLeads((rows) => rows.map((lead) => lead.id === leadId ? { ...lead, pipeline_stage: pipelineStage } : lead));
    setSelected((lead) => lead?.id === leadId ? { ...lead, pipeline_stage: pipelineStage } : lead);
  }

  async function refreshDashboard() {
    setDashboard(await api<OutboundDashboard>("/outbound/dashboard", {}, session));
  }

  async function generateDraft(leadId: number) {
    setBusyAction("generate");
    setError("");
    try {
      const draft = await api<OutboundDraft>(`/outbound/leads/${leadId}/drafts`, { method: "POST" }, session);
      setDrafts((rows) => [draft, ...rows.filter((row) => row.id !== draft.id)]);
      setActiveDraftId(draft.id);
      setConfirmed(false);
      updateLeadStage(leadId, "draft_generated");
      await refreshDashboard();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "초안을 만들지 못했습니다.");
    } finally {
      setBusyAction("");
    }
  }

  async function reviewDraft(draft: OutboundDraft) {
    setBusyAction("review");
    setError("");
    try {
      await api(`/outbound/drafts/${draft.id}/review`, { method: "POST" }, session);
      setDrafts((rows) => rows.map((row) => row.id === draft.id ? { ...row, reviewed: true } : row));
      updateLeadStage(draft.lead_id, "approved");
      await refreshDashboard();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "검토 상태를 저장하지 못했습니다.");
    } finally {
      setBusyAction("");
    }
  }

  async function sendDraft(draft: OutboundDraft) {
    setBusyAction("send");
    setError("");
    try {
      const response = await api<SendResponse>(`/outbound/drafts/${draft.id}/send`, { method: "POST" }, session);
      setDrafts((rows) => rows.map((row) => row.id === draft.id ? {
        ...row,
        sent_at: response.sent_at,
        send_mode: response.mode
      } : row));
      updateLeadStage(draft.lead_id, "contacted");
      await refreshDashboard();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "안전 발송을 완료하지 못했습니다.");
    } finally {
      setBusyAction("");
    }
  }

  if (dashboardOnly) {
    return (
      <section className="workspace" aria-labelledby="dashboard-title" aria-busy={loading}>
        <div className="commandbar">
          <div><span className="eyebrow">OUTBOUND PERFORMANCE</span><h1 id="dashboard-title">아웃바운드 성과</h1><p>시스템에 기록된 이벤트를 기준으로 파이프라인을 살펴봅니다.</p></div>
          {dashboard ? <ModeChip mode={dashboard.outbound_email_mode} /> : null}
        </div>
        {error ? <p className="error notice" role="alert">{error}</p> : null}
        {loading ? <LoadingState label="성과 지표를 계산하는 중" /> : dashboard ? <Dashboard metrics={dashboard} /> : <EmptyState title="표시할 성과가 없습니다" description="아웃바운드 활동이 시작되면 지표가 표시됩니다." />}
      </section>
    );
  }

  const activeDraft = drafts.find((draft) => draft.id === activeDraftId) ?? drafts[0] ?? null;
  return (
    <section className="workspace" aria-labelledby="leads-title" aria-busy={loading}>
      <div className="commandbar">
        <div><span className="eyebrow">OUTBOUND PIPELINE</span><h1 id="leads-title">잠재고객</h1><p>리드 근거를 확인하고 검토된 메시지만 안전하게 다음 단계로 보냅니다.</p></div>
        <div className="command-actions">{dashboard ? <ModeChip mode={dashboard.outbound_email_mode} /> : null}<span className="count-chip">{leads.length}개 리드</span></div>
      </div>
      {error ? <p className="error notice" role="alert">{error}</p> : null}
      {loading ? <LoadingState label="잠재고객을 불러오는 중" /> : (
        <div className="data-grid-wrap" role="region" aria-label="잠재고객 표, 가로 스크롤 가능" tabIndex={0}>
          <table className="data-grid leads-grid">
            <caption className="sr-only">리드 점수와 아웃바운드 진행 단계</caption>
            <thead><tr><th scope="col">업체명</th><th scope="col">업종</th><th scope="col">주소</th><th scope="col">리드 점수</th><th scope="col">단계</th><th scope="col">다음 작업</th></tr></thead>
            <tbody>{leads.length ? leads.map((lead) => (
              <tr key={lead.id}>
                <td><button className="company-link" type="button" onClick={() => openLead(lead)}><span className="company-avatar lead" aria-hidden="true">{lead.name.slice(0, 1)}</span><span>{lead.name}</span></button></td>
                <td>{lead.business_type ?? "-"}</td>
                <td className="address-cell">{lead.address ?? "-"}</td>
                <td><span className={`score-pill ${lead.lead_score >= 80 ? "hot" : lead.lead_score >= 60 ? "warm" : "cool"}`}><strong>{lead.lead_score}</strong><small>/100</small></span></td>
                <td><span className={`status-badge stage-${lead.pipeline_stage}`}><span className="status-dot" aria-hidden="true" />{STAGE_LABELS[lead.pipeline_stage] ?? lead.pipeline_stage}</span></td>
                <td><button className="text-button" type="button" onClick={() => openLead(lead)}>{lead.pipeline_stage === "discovered" ? "근거 확인 및 초안 생성" : "진행 내용 보기"}</button></td>
              </tr>
            )) : <tr><td colSpan={6}><EmptyState title="등록된 잠재고객이 없습니다" description="수집된 리드가 생기면 점수순으로 표시됩니다." /></td></tr>}</tbody>
          </table>
        </div>
      )}

      {selected ? (
        <DetailDialog labelledBy="lead-detail-title" onClose={closeLead}>
          <div className="panel-heading">
            <div><span className="eyebrow">LEAD #{selected.id}</span><h2 id="lead-detail-title">{selected.name}</h2></div>
            <button className="icon-button" type="button" onClick={closeLead} aria-label="잠재고객 상세 닫기">×</button>
          </div>
          <div className="panel-content">
            <div className="detail-meta"><span className={`status-badge stage-${selected.pipeline_stage}`}>{STAGE_LABELS[selected.pipeline_stage] ?? selected.pipeline_stage}</span><span>{selected.business_type ?? "업종 미상"}</span><span>{selected.address ?? "주소 미상"}</span></div>
            <section className="lead-score-card" aria-labelledby="lead-score-title">
              <div><span className="eyebrow">LEAD SCORE</span><h3 id="lead-score-title">발굴 근거</h3></div>
              <strong>{selected.lead_score}<small>/100</small></strong>
            </section>
            <div className="lead-reasons">
              {Object.entries(selected.reasoning).map(([axis, reason]) => <div key={axis}><strong>{humanizeAxis(axis)}</strong><p>{reason}</p></div>)}
            </div>

            <section className="draft-workflow" aria-labelledby="draft-title">
              <div className="section-heading compact">
                <div><h3 id="draft-title">아웃바운드 메시지</h3><span>최대 3단계 시퀀스</span></div>
                {drafts.length < 3 ? <button className="secondary-button" type="button" disabled={Boolean(busyAction)} onClick={() => void generateDraft(selected.id)}>{busyAction === "generate" ? "AI 작성 중…" : drafts.length ? "후속 초안 생성" : "첫 초안 생성"}</button> : null}
              </div>
              <ModeNotice mode={dashboard?.outbound_email_mode ?? "dry_run"} />
              {draftLoading ? <LoadingState label="저장된 초안을 불러오는 중" /> : drafts.length ? (
                <>
                  <div className="draft-tabs" role="tablist" aria-label="시퀀스 초안">
                    {drafts.map((draft) => <button key={draft.id} role="tab" aria-selected={activeDraft?.id === draft.id} className={activeDraft?.id === draft.id ? "active" : ""} onClick={() => { setActiveDraftId(draft.id); setConfirmed(false); }}>{draft.sequence_step}단계 {draft.sent_at ? "완료" : draft.reviewed ? "검토됨" : "초안"}</button>)}
                  </div>
                  {activeDraft ? (
                    <article className="draft-card">
                      <div className="draft-meta"><span>{activeDraft.sequence_step}단계</span><span>{new Date(activeDraft.generated_at).toLocaleString("ko-KR")}</span></div>
                      <h4>{activeDraft.subject}</h4>
                      <div className="draft-body">{activeDraft.body}</div>
                      {activeDraft.sent_at ? (
                        <p className="success notice" role="status">{activeDraft.send_mode === "dry_run" ? "드라이런 기록이 완료됐습니다." : "설정된 테스트 주소로 발송됐습니다."}</p>
                      ) : !activeDraft.reviewed ? (
                        <div className="draft-actions"><p>제목과 본문을 직접 읽고 문제없을 때만 검토를 완료하세요.</p><button className="primary" disabled={Boolean(busyAction)} onClick={() => void reviewDraft(activeDraft)}>{busyAction === "review" ? "저장 중…" : "내용 검토 완료"}</button></div>
                      ) : (
                        <div className="draft-actions">
                          <label className="confirm-check"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />{dashboard?.outbound_email_mode === "test_override" ? "설정된 테스트 주소로만 발송되는 것을 확인했습니다." : "실제 메일 없이 드라이런 기록만 남는 것을 확인했습니다."}</label>
                          <button className="primary" disabled={!confirmed || Boolean(busyAction)} onClick={() => void sendDraft(activeDraft)}>{busyAction === "send" ? "처리 중…" : dashboard?.outbound_email_mode === "test_override" ? "테스트 주소로 발송" : "드라이런 완료 기록"}</button>
                        </div>
                      )}
                    </article>
                  ) : null}
                </>
              ) : <EmptyState title="아직 생성된 초안이 없습니다" description="리드 근거를 확인한 뒤 첫 초안을 생성하세요." />}
            </section>
          </div>
        </DetailDialog>
      ) : null}
    </section>
  );
}

function ModeChip({ mode }: { mode: "dry_run" | "test_override" }) {
  return <span className={`mode-chip ${mode}`}><span aria-hidden="true">●</span>{mode === "dry_run" ? "드라이런" : "테스트 발송"}</span>;
}

function ModeNotice({ mode }: { mode: "dry_run" | "test_override" }) {
  return <p className={`mode-notice ${mode}`}><strong>{mode === "dry_run" ? "안전한 연습 모드" : "테스트 주소 제한 모드"}</strong><span>{mode === "dry_run" ? "실제 이메일은 전송되지 않고 내부 기록만 남습니다." : "실제 고객 주소가 아닌 설정된 테스트 주소로만 전송됩니다."}</span></p>;
}

function Dashboard({ metrics }: { metrics: OutboundDashboard }) {
  const max = Math.max(1, ...Object.values(metrics.pipeline));
  const total = Object.values(metrics.pipeline).reduce((sum, count) => sum + count, 0);
  const contacted = (metrics.pipeline.contacted ?? 0) + (metrics.pipeline.converted ?? 0);
  return (
    <>
      <div className="summary-cards">
        <section><span>전체 잠재고객</span><strong>{total}</strong><small>현재 파이프라인</small></section>
        <section><span>초안 승인률</span><strong>{Math.round(metrics.draft_approval_rate * 100)}%</strong><small>검토 완료 / 생성 초안</small></section>
        <section><span>접촉 완료</span><strong>{contacted}</strong><small>접촉 및 전환</small></section>
      </div>
      <div className="metrics-layout">
        <section className="metric-block pipeline-block">
          <div className="section-heading compact"><div><h2>파이프라인</h2><span>단계별 리드 분포</span></div></div>
          {PIPELINE_ORDER.filter((stage) => metrics.pipeline[stage]).map((stage) => {
            const count = metrics.pipeline[stage];
            return <div className="bar-row" key={stage}><span>{STAGE_LABELS[stage] ?? stage}</span><div className="bar-track" aria-hidden="true"><span style={{ width: `${(count / max) * 100}%` }} /></div><strong>{count}</strong></div>;
          })}
        </section>
        <section className="metric-block">
          <div className="section-heading compact"><div><h2>시퀀스 분포</h2><span>생성된 메시지 단계</span></div></div>
          <div className="sequence-list">{[1, 2, 3].map((step) => <div key={step}><span>{step}단계</span><strong>{metrics.sequence_distribution[String(step)] ?? metrics.sequence_distribution[step] ?? 0}</strong></div>)}</div>
        </section>
      </div>
    </>
  );
}

function humanizeAxis(axis: string) {
  const labels: Record<string, string> = {
    years_in_business: "업력",
    business_type: "업종 적합도"
  };
  return labels[axis] ?? axis.replaceAll("_", " ");
}
