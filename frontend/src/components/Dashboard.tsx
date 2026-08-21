import { useEffect, useState } from "react";

import { api } from "../api";
import type { CrmDashboard, OpportunityStage, Session } from "../types";
import { EmptyState, LoadingState } from "./States";

const STAGES: OpportunityStage[] = ["qualify", "develop", "propose", "won", "lost"];
const LABELS: Record<OpportunityStage, string> = { qualify: "검토", develop: "개발", propose: "제안", won: "수주", lost: "실주" };
const money = new Intl.NumberFormat("ko-KR", { style: "currency", currency: "KRW", maximumFractionDigits: 0 });

export default function Dashboard({ session }: { session: Session }) {
  const [metrics, setMetrics] = useState<CrmDashboard | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void api<CrmDashboard>("/crm/dashboard", {}, session)
      .then((data) => { if (active) setMetrics(data); })
      .catch((requestError: unknown) => { if (active) setError(requestError instanceof Error ? requestError.message : "성과를 불러오지 못했습니다."); });
    return () => { active = false; };
  }, [session]);

  return (
    <section className="workspace" aria-labelledby="dashboard-title">
      <div className="commandbar"><div><h1 id="dashboard-title">CRM 성과</h1><p>등록된 영업기회와 활동을 규칙 기반으로 집계한 업무 지표입니다.</p></div><span className="safety-chip">업무 기록 기준</span></div>
      {error ? <p className="error notice" role="alert">{error}</p> : null}
      {!metrics && !error ? <LoadingState label="CRM 성과를 계산하는 중" /> : null}
      {metrics ? <DashboardContent metrics={metrics} /> : error ? <EmptyState title="성과를 표시할 수 없습니다" description="잠시 후 다시 시도해주세요." /> : null}
    </section>
  );
}

function DashboardContent({ metrics }: { metrics: CrmDashboard }) {
  const totalAmount = STAGES.reduce((sum, stage) => sum + metrics.pipeline[stage].amount, 0);
  return <>
    <p className="metric-definition">아래 수치는 AI 정확도가 아니라 CRM에 저장된 금액·단계·완료 기록의 결정론적 집계입니다.</p>
    <div className="summary-cards four">
      <section><span>전체 파이프라인 금액</span><strong>{money.format(totalAmount)}</strong><small>모든 영업기회 합계</small></section>
      <section><span>가중 파이프라인</span><strong>{money.format(metrics.weighted_amount)}</strong><small>영업기회별 확률 적용</small></section>
      <section><span>종결 건 전환율</span><strong>{metrics.closed_conversion.rate === null ? "-" : `${Math.round(metrics.closed_conversion.rate * 100)}%`}</strong><small>수주 / (수주 + 실주)</small></section>
      <section><span>기한 지난 할 일</span><strong>{metrics.tasks.overdue}</strong><small>진행 중 {metrics.tasks.open}건</small></section>
    </div>
    <div className="metrics-layout dashboard-grid">
      <section className="metric-block"><div className="section-heading compact"><div><h2>영업 파이프라인</h2><span>건수 · 금액 · 고정 가중치</span></div></div><div className="metric-table-wrap"><table className="metric-table"><thead><tr><th>단계</th><th>건수</th><th>금액</th><th>가중치</th></tr></thead><tbody>{STAGES.map((stage) => <tr key={stage}><th>{LABELS[stage]}</th><td>{metrics.pipeline[stage].count}</td><td>{money.format(metrics.pipeline[stage].amount)}</td><td>{Math.round(metrics.stage_probabilities[stage] * 100)}%</td></tr>)}</tbody></table></div></section>
      <section className="metric-block"><div className="section-heading compact"><div><h2>평균 단계 체류시간</h2><span>다음 단계로 이동한 기록만 계산</span></div></div><div className="sequence-list">{STAGES.map((stage) => <div key={stage}><span>{LABELS[stage]}</span><strong>{metrics.average_stage_hours[stage] === null ? "-" : `${metrics.average_stage_hours[stage]}시간`}</strong></div>)}</div></section>
      <section className="metric-block"><div className="section-heading compact"><div><h2>담당자 활동</h2><span>현재 조회 권한 범위</span></div></div><div className="metric-table-wrap"><table className="metric-table"><thead><tr><th>담당자</th><th>활동</th><th>기회</th><th>수주</th><th>수주액</th></tr></thead><tbody>{metrics.rep_stats.map((rep) => <tr key={rep.staff_id}><th>{rep.name}</th><td>{rep.activity_count}</td><td>{rep.opportunity_count}</td><td>{rep.won_count}</td><td>{money.format(rep.won_amount)}</td></tr>)}</tbody></table></div></section>
      <section className="metric-block"><div className="section-heading compact"><div><h2>점수 구간별 수주 결과</h2><span>종결 영업기회 기준 관찰값</span></div></div><div className="metric-table-wrap"><table className="metric-table"><thead><tr><th>점수</th><th>문의</th><th>종결</th><th>수주</th><th>전환</th></tr></thead><tbody>{metrics.ai_score_buckets.map((bucket) => <tr key={bucket.range}><th>{bucket.range}</th><td>{bucket.scored_inquiries}</td><td>{bucket.closed_opportunities}</td><td>{bucket.won_opportunities}</td><td>{bucket.won_conversion === null ? "-" : `${Math.round(bucket.won_conversion * 100)}%`}</td></tr>)}</tbody></table></div></section>
    </div>
  </>;
}
