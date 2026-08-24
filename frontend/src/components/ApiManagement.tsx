import { useEffect, useState } from "react";

import { api } from "../api";
import type { Session } from "../types";
import { LoadingState } from "./States";

type ApiStatus = "available" | "configured" | "not_configured" | "incomplete" | "degraded";

interface ApiStatusResponse {
  checked_at: string;
  services: Array<{ name: string; status: ApiStatus; detail: string }>;
}

const STATUS_LABELS: Record<ApiStatus, string> = {
  available: "정상",
  configured: "설정됨",
  not_configured: "미설정",
  incomplete: "설정 불완전",
  degraded: "확인 필요"
};

export default function ApiManagement({ session }: { session: Session }) {
  const [data, setData] = useState<ApiStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      setData(await api<ApiStatusResponse>("/admin/api-status", {}, session));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "API 상태를 확인하지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let active = true;
    void api<ApiStatusResponse>("/admin/api-status", {}, session)
      .then((response) => { if (active) setData(response); })
      .catch((requestError: unknown) => {
        if (active) setError(requestError instanceof Error ? requestError.message : "API 상태를 확인하지 못했습니다.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [session]);

  return (
    <section className="workspace" aria-labelledby="api-management-title" aria-busy={loading}>
      <div className="commandbar">
        <div><h1 id="api-management-title">API 관리</h1><p>서버 연결과 외부 연동 설정 상태를 확인합니다.</p></div>
        <button className="secondary-button" type="button" disabled={loading} onClick={() => void refresh()}>{loading ? "확인 중…" : "상태 새로고침"}</button>
      </div>
      <p className="notice">API 키는 서버 환경변수에서만 관리하며 이 화면에는 표시하지 않습니다.</p>
      {error ? <p className="error notice" role="alert">{error}</p> : null}
      {loading && !data ? <LoadingState label="API 상태를 확인하는 중" /> : data ? <>
        <div className="data-grid-wrap" role="region" aria-label="API 상태 표" tabIndex={0}>
          <table className="data-grid">
            <caption className="sr-only">서버와 외부 API 연결 상태</caption>
            <thead><tr><th scope="col">서비스</th><th scope="col">상태</th><th scope="col">확인 내용</th></tr></thead>
            <tbody>{data.services.map((service) => <tr key={service.name}>
              <td><strong>{service.name}</strong></td>
              <td><span className={`status-badge ${service.status === "available" || service.status === "configured" ? "routed" : "warning"}`}><span className="status-dot" aria-hidden="true" />{STATUS_LABELS[service.status]}</span></td>
              <td>{service.detail}</td>
            </tr>)}</tbody>
          </table>
        </div>
        <p className="muted">마지막 확인: {new Date(data.checked_at).toLocaleString("ko-KR")}</p>
      </> : null}
    </section>
  );
}
