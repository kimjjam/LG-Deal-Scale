import { useEffect, useState } from "react";

import { api } from "../api";
import type { Lead, OutboundDashboard, OutboundDraft, Session } from "../types";
import DetailDialog from "./DetailDialog";
import CsvControls from "./CsvControls";
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

const PAGE_SIZE = 25;
const REGIONS = [
  ["11", "서울"], ["26", "부산"], ["27", "대구"], ["28", "인천"],
  ["29", "광주"], ["30", "대전"], ["31", "울산"], ["36", "세종"],
  ["41", "경기"], ["43", "충북"], ["44", "충남"], ["46", "전남"],
  ["47", "경북"], ["48", "경남"], ["50", "제주"],
  ["51", "강원특별자치도"], ["52", "전북특별자치도"]
] as const;

const NEXT_STAGES: Record<string, string[]> = {
  discovered: ["draft_generated", "dropped"],
  draft_generated: ["dropped"],
  approved: ["contacted", "follow_up_due", "dropped"],
  contacted: ["follow_up_due", "dropped"],
  follow_up_due: ["contacted", "dropped"]
};

interface SendResponse {
  id: number;
  mode: "dry_run" | "test_override";
  sent_at: string;
}

interface SbizSyncResponse {
  fetched_count: number;
  created_count: number;
  updated_count: number;
  total_count: number;
}

export default function Outbound({ session }: { session: Session }) {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [dashboard, setDashboard] = useState<OutboundDashboard | null>(null);
  const [selected, setSelected] = useState<Lead | null>(null);
  const [drafts, setDrafts] = useState<OutboundDraft[]>([]);
  const [activeDraftId, setActiveDraftId] = useState<number | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [editSubject, setEditSubject] = useState("");
  const [editBody, setEditBody] = useState("");
  const [contactChannel, setContactChannel] = useState("phone");
  const [contactNote, setContactNote] = useState("");
  const [busyAction, setBusyAction] = useState("");
  const [loading, setLoading] = useState(true);
  const [draftLoading, setDraftLoading] = useState(false);
  const [error, setError] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [stage, setStage] = useState("");
  const [offset, setOffset] = useState(0);
  const [hasNext, setHasNext] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [syncRegion, setSyncRegion] = useState("11");
  const [syncPage, setSyncPage] = useState(1);
  const [syncBusy, setSyncBusy] = useState(false);
  const [syncMessage, setSyncMessage] = useState("");
  const canManage = session.role !== "rep";
  const selectedId = selected?.id;

  useEffect(() => {
    let active = true;
    const query = new URLSearchParams({ limit: String(PAGE_SIZE + 1), offset: String(offset) });
    if (search) query.set("q", search);
    if (stage) query.set("status", stage);
    Promise.all([
      api<Lead[]>(`/outbound/leads?${query}`, {}, session),
      api<OutboundDashboard>("/outbound/dashboard", {}, session)
    ]).then(([leadRows, metrics]) => {
      if (active) {
        setHasNext(leadRows.length > PAGE_SIZE);
        setLeads(leadRows.slice(0, PAGE_SIZE));
        setDashboard(metrics);
      }
    }).catch((requestError: unknown) => {
      if (active) setError(requestError instanceof Error ? requestError.message : "데이터를 불러오지 못했습니다.");
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [session, search, stage, offset, refreshKey]);

  useEffect(() => {
    if (!selectedId) return;
    let active = true;
    void api<OutboundDraft[]>(`/outbound/leads/${selectedId}/drafts`, {}, session)
      .then((rows) => {
        if (active) {
          setDrafts(rows);
          setActiveDraftId(rows[0]?.id ?? null);
          setEditSubject(rows[0]?.subject ?? "");
          setEditBody(rows[0]?.body ?? "");
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

  async function syncPublicLeads() {
    setSyncBusy(true);
    setSyncMessage("");
    setError("");
    try {
      const result = await api<SbizSyncResponse>("/outbound/leads/sync-sbiz", {
        method: "POST",
        body: JSON.stringify({ region_code: syncRegion, page: syncPage, rows: 100 })
      }, session);
      setSyncMessage(`숙박업소 ${result.fetched_count}건 확인 · 신규 ${result.created_count}건 · 갱신 ${result.updated_count}건 (지역 전체 ${result.total_count}건)`);
      setOffset(0);
      setRefreshKey((value) => value + 1);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "공공데이터를 가져오지 못했습니다.");
    } finally {
      setSyncBusy(false);
    }
  }

  async function generateDraft(leadId: number) {
    setBusyAction("generate");
    setError("");
    try {
      const draft = await api<OutboundDraft>(`/outbound/leads/${leadId}/drafts`, { method: "POST" }, session);
      setDrafts((rows) => [draft, ...rows.filter((row) => row.id !== draft.id)]);
      setActiveDraftId(draft.id);
      setEditSubject(draft.subject);
      setEditBody(draft.body);
      setConfirmed(false);
      if (selected?.pipeline_stage === "discovered") updateLeadStage(leadId, "draft_generated");
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
      if (selected?.pipeline_stage === "draft_generated") updateLeadStage(draft.lead_id, "approved");
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
      await refreshDashboard();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "안전 발송을 완료하지 못했습니다.");
    } finally {
      setBusyAction("");
    }
  }

  async function saveDraft(draft: OutboundDraft) {
    setBusyAction("edit");
    setError("");
    try {
      const updated = await api<OutboundDraft>(`/outbound/drafts/${draft.id}`, { method: "PATCH", body: JSON.stringify({ subject: editSubject, body: editBody }) }, session);
      setDrafts((rows) => rows.map((row) => row.id === updated.id ? updated : row));
      setConfirmed(false);
      if (selected?.pipeline_stage === "approved") updateLeadStage(draft.lead_id, "draft_generated");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "초안을 수정하지 못했습니다.");
    } finally {
      setBusyAction("");
    }
  }

  async function changeStage(leadId: number, pipelineStage: string) {
    setBusyAction("stage");
    setError("");
    try {
      await api(`/outbound/leads/${leadId}/stage`, { method: "PUT", body: JSON.stringify({ pipeline_stage: pipelineStage }) }, session);
      updateLeadStage(leadId, pipelineStage);
      await refreshDashboard();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "단계를 변경하지 못했습니다.");
    } finally {
      setBusyAction("");
    }
  }

  async function recordContact(leadId: number) {
    setBusyAction("contact");
    setError("");
    try {
      await api(`/outbound/leads/${leadId}/actual-contact`, { method: "POST", body: JSON.stringify({ channel: contactChannel, note: contactNote || null }) }, session);
      updateLeadStage(leadId, "contacted");
      setContactNote("");
      await refreshDashboard();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "실제 접촉을 기록하지 못했습니다.");
    } finally {
      setBusyAction("");
    }
  }

  async function stopSequence(leadId: number) {
    setBusyAction("stop");
    setError("");
    try {
      await api(`/outbound/leads/${leadId}/stop`, { method: "POST" }, session);
      updateLeadStage(leadId, "dropped");
      await refreshDashboard();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "시퀀스를 중단하지 못했습니다.");
    } finally {
      setBusyAction("");
    }
  }

  const activeDraft = drafts.find((draft) => draft.id === activeDraftId) ?? drafts[0] ?? null;
  const latestDraft = drafts[0] ?? null;
  const canGenerateDraft = !latestDraft || Boolean(latestDraft.reviewed && latestDraft.sent_at);
  const nextStages = selected ? (NEXT_STAGES[selected.pipeline_stage] ?? []).filter((nextStage) => nextStage !== "contacted" && nextStage !== "dropped") : [];
  return (
    <section className="workspace" aria-labelledby="leads-title" aria-busy={loading}>
      <div className="commandbar">
        <div><h1 id="leads-title">잠재고객</h1><p>리드 근거를 확인하고 검토된 메시지만 안전하게 다음 단계로 보냅니다.</p></div>
        <div className="command-actions">{canManage ? <CsvControls session={session} importPath="/outbound/leads/import" exportPath="/outbound/leads/export.csv" filename="leads.csv" onImported={() => { setOffset(0); setRefreshKey((value) => value + 1); }} /> : null}{dashboard ? <ModeChip mode={dashboard.outbound_email_mode} /> : null}<span className="count-chip">현재 페이지 {leads.length}개</span></div>
      </div>
      {canManage ? <form className="compact-filter" onSubmit={(event) => { event.preventDefault(); void syncPublicLeads(); }}>
        <label>공공데이터 지역<select value={syncRegion} onChange={(event) => setSyncRegion(event.target.value)}>{REGIONS.map(([code, name]) => <option key={code} value={code}>{name}</option>)}</select></label>
        <label>페이지<input type="number" min="1" max="10000" value={syncPage} onChange={(event) => setSyncPage(Number(event.target.value))} /></label>
        <button className="secondary-button" disabled={syncBusy}>{syncBusy ? "가져오는 중…" : "숙박업소 100건 가져오기"}</button>
      </form> : null}
      {syncMessage ? <p className="success notice" role="status">{syncMessage}</p> : null}
      <form className="compact-filter" onSubmit={(event) => { event.preventDefault(); setOffset(0); setSearch(searchInput.trim()); }}>
        <label>리드 검색<input value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder="업체명 또는 주소" /></label>
        <label>단계<select value={stage} onChange={(event) => { setStage(event.target.value); setOffset(0); }}><option value="">전체</option>{Object.entries(STAGE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <button className="secondary-button" type="submit">검색</button>
      </form>
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
      <div className="pager"><button className="secondary-button" type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>이전</button><span>{Math.floor(offset / PAGE_SIZE) + 1}페이지</span><button className="secondary-button" type="button" disabled={!hasNext} onClick={() => setOffset(offset + PAGE_SIZE)}>다음</button></div>

      {selected ? (
        <DetailDialog labelledBy="lead-detail-title" onClose={closeLead}>
          <div className="panel-heading">
            <div><span>잠재고객 #{selected.id}</span><h2 id="lead-detail-title">{selected.name}</h2></div>
            <button className="icon-button" type="button" onClick={closeLead} aria-label="잠재고객 상세 닫기">×</button>
          </div>
          <div className="panel-content">
            <div className="detail-meta"><span className={`status-badge stage-${selected.pipeline_stage}`}>{STAGE_LABELS[selected.pipeline_stage] ?? selected.pipeline_stage}</span><span>{selected.business_type ?? "업종 미상"}</span><span>{selected.address ?? "주소 미상"}</span></div>
            {canManage && !["converted", "dropped"].includes(selected.pipeline_stage) ? <section className="lead-actions" aria-labelledby="lead-stage-title"><h3 id="lead-stage-title">진행 관리</h3>{nextStages.length ? <label>다음 단계<select defaultValue="" disabled={Boolean(busyAction)} onChange={(event) => { if (event.target.value) void changeStage(selected.id, event.target.value); event.target.value = ""; }}><option value="">선택</option>{nextStages.map((nextStage) => <option key={nextStage} value={nextStage}>{STAGE_LABELS[nextStage]}</option>)}</select></label> : null}<details><summary>실제 접촉 기록</summary><div className="contact-form"><label>채널<select value={contactChannel} onChange={(event) => setContactChannel(event.target.value)}><option value="phone">전화</option><option value="email">이메일</option><option value="meeting">미팅</option><option value="other">기타</option></select></label><label>메모<textarea value={contactNote} onChange={(event) => setContactNote(event.target.value)} maxLength={1000} /></label><button className="primary" type="button" disabled={Boolean(busyAction)} onClick={() => void recordContact(selected.id)}>접촉 기록</button></div></details><button className="danger-button" type="button" disabled={Boolean(busyAction)} onClick={() => { if (window.confirm("이 리드의 아웃바운드 시퀀스를 종결할까요?")) void stopSequence(selected.id); }}>시퀀스 중단</button></section> : null}
            <section className="lead-score-card" aria-labelledby="lead-score-title">
              <div><h3 id="lead-score-title">발굴 근거</h3></div>
              <strong>{selected.lead_score}<small>/100</small></strong>
            </section>
            <div className="lead-reasons">
              {Object.entries(selected.reasoning).map(([axis, reason]) => <div key={axis}><strong>{humanizeAxis(axis)}</strong><p>{reason}</p></div>)}
            </div>

            <section className="draft-workflow" aria-labelledby="draft-title">
              <div className="section-heading compact">
                <div><h3 id="draft-title">아웃바운드 메시지</h3><span>최대 3단계 시퀀스</span></div>
                {canManage && drafts.length < 3 ? <button className="secondary-button" type="button" disabled={Boolean(busyAction) || !canGenerateDraft} onClick={() => void generateDraft(selected.id)}>{busyAction === "generate" ? "AI 작성 중…" : drafts.length ? "후속 초안 생성" : "첫 초안 생성"}</button> : null}
              </div>
              {canManage && latestDraft && drafts.length < 3 && !canGenerateDraft ? <p className="notice">후속 초안은 최신 초안을 검토하고 안전 발송 처리한 뒤 생성할 수 있습니다.</p> : null}
              <ModeNotice mode={dashboard?.outbound_email_mode ?? "dry_run"} />
              {draftLoading ? <LoadingState label="저장된 초안을 불러오는 중" /> : drafts.length ? (
                <>
                  <div className="draft-tabs" aria-label="시퀀스 초안 선택">
                    {drafts.map((draft) => <button key={draft.id} type="button" aria-pressed={activeDraft?.id === draft.id} className={activeDraft?.id === draft.id ? "active" : ""} onClick={() => { setActiveDraftId(draft.id); setEditSubject(draft.subject); setEditBody(draft.body); setConfirmed(false); }}>{draft.sequence_step}단계 {draft.sent_at ? "완료" : draft.reviewed ? "검토됨" : "초안"}</button>)}
                  </div>
                  {activeDraft ? (
                    <article className="draft-card">
                      <div className="draft-meta"><span>{activeDraft.sequence_step}단계</span><span>{new Date(activeDraft.generated_at).toLocaleString("ko-KR")}</span></div>
                      {activeDraft.sent_at || !canManage ? <><h4>{activeDraft.subject}</h4><div className="draft-body">{activeDraft.body}</div></> : <div className="draft-editor"><label>제목<input value={editSubject} onChange={(event) => setEditSubject(event.target.value)} maxLength={300} /></label><label>본문<textarea value={editBody} onChange={(event) => setEditBody(event.target.value)} maxLength={20000} /></label><button className="secondary-button" type="button" disabled={Boolean(busyAction) || !editSubject.trim() || !editBody.trim() || (editSubject === activeDraft.subject && editBody === activeDraft.body)} onClick={() => void saveDraft(activeDraft)}>{busyAction === "edit" ? "저장 중…" : "수정 저장"}</button><p>수정하면 기존 검토 완료 상태가 해제되어 다시 검토해야 합니다.</p></div>}
                      {activeDraft.sent_at ? (
                        <p className="success notice" role="status">{activeDraft.send_mode === "dry_run" ? "드라이런 기록이 완료됐습니다." : "설정된 테스트 주소로 발송됐습니다."}</p>
                      ) : !canManage ? (
                        <p className="notice" role="status">영업 담당자는 아웃바운드 기록을 조회만 할 수 있습니다.</p>
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

function humanizeAxis(axis: string) {
  const labels: Record<string, string> = {
    years_in_business: "업력",
    business_type: "업종 적합도"
  };
  return labels[axis] ?? axis.replaceAll("_", " ");
}
