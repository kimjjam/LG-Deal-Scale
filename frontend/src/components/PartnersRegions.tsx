import { FormEvent, useEffect, useState } from "react";

import { api } from "../api";
import type { Partner, SalesRegion, Session, StaffMember } from "../types";
import { EmptyState, LoadingState } from "./States";

function fetchData(session: Session, canManage: boolean) {
  return Promise.all([
    api<Partner[]>("/partners-regions/partners", {}, session),
    api<SalesRegion[]>("/partners-regions/regions", {}, session),
    canManage ? api<StaffMember[]>("/staff?role=manager", {}, session) : Promise.resolve([])
  ]);
}

export default function PartnersRegions({ session }: { session: Session }) {
  const canManage = session.role !== "rep";
  const [tab, setTab] = useState<"partners" | "regions">("partners");
  const [partners, setPartners] = useState<Partner[]>([]);
  const [regions, setRegions] = useState<SalesRegion[]>([]);
  const [managers, setManagers] = useState<StaffMember[]>([]);
  const [editPartner, setEditPartner] = useState<Partner | null>(null);
  const [editRegion, setEditRegion] = useState<SalesRegion | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [partnerRows, regionRows, managerRows] = await fetchData(session, canManage);
      setPartners(partnerRows);
      setRegions(regionRows);
      setManagers(managerRows.filter((manager) => manager.is_active));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "파트너·지역 정보를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let active = true;
    fetchData(session, canManage).then(([partnerRows, regionRows, managerRows]) => {
      if (!active) return;
      setPartners(partnerRows);
      setRegions(regionRows);
      setManagers(managerRows.filter((manager) => manager.is_active));
    }).catch((requestError: unknown) => {
      if (active) setError(requestError instanceof Error ? requestError.message : "파트너·지역 정보를 불러오지 못했습니다.");
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [canManage, session]);

  async function update(path: string, body: object) {
    setError("");
    try {
      await api(path, { method: "PATCH", body: JSON.stringify(body) }, session);
      await load();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "정보를 수정하지 못했습니다.");
    }
  }

  return <section className="workspace" aria-labelledby="partners-title">
    <div className="commandbar"><div><h1 id="partners-title">파트너·지역</h1><p>검증된 파트너와 지역별 문의 라우팅을 관리합니다.</p></div></div>
    <div className="draft-tabs" role="tablist" aria-label="파트너와 지역 선택">
      <button className={tab === "partners" ? "active" : ""} onClick={() => setTab("partners")}>총판·전문점</button>
      <button className={tab === "regions" ? "active" : ""} onClick={() => setTab("regions")}>지역 담당</button>
    </div>
    {error ? <p className="error notice" role="alert">{error}</p> : null}
    {loading ? <LoadingState label="파트너·지역 정보를 불러오는 중" /> : tab === "partners" ? <>
      {canManage ? <PartnerForm session={session} partner={editPartner} onSaved={async () => { setEditPartner(null); await load(); }} onError={setError} /> : null}
      <div className="data-grid-wrap" role="region" aria-label="검증된 파트너 목록" tabIndex={0}><table className="data-grid"><thead><tr><th>파트너</th><th>구분</th><th>매칭 지역 키워드</th><th>연락처</th><th>검증</th><th>상태</th></tr></thead><tbody>{partners.length ? partners.map((partner) => <tr key={partner.id}><td><strong>{partner.name}</strong><br /><small>{partner.address}</small></td><td>{partner.partner_type}</td><td>{partner.region}</td><td>{partner.phone ?? "-"}</td><td>{partner.verification_source}<br /><small>{partner.verified_at}</small></td><td>{canManage ? <><button className="text-button" onClick={() => setEditPartner(partner)}>수정</button> · <button className="text-button" onClick={() => void update(`/partners-regions/partners/${partner.id}`, { is_active: !partner.is_active })}>{partner.is_active ? "활성" : "비활성"}</button></> : partner.is_active ? "활성" : "비활성"}</td></tr>) : <tr><td colSpan={6}><EmptyState title="등록된 파트너가 없습니다" description="수동 검증한 파트너만 등록하세요." /></td></tr>}</tbody></table></div>
    </> : <>
      {canManage ? <RegionForm session={session} managers={managers} region={editRegion} onSaved={async () => { setEditRegion(null); await load(); }} onError={setError} /> : null}
      <div className="data-grid-wrap" role="region" aria-label="지역 담당 매핑 목록" tabIndex={0}><table className="data-grid"><thead><tr><th>지역</th><th>매칭 키워드</th><th>지역 매니저</th><th>상태</th></tr></thead><tbody>{regions.length ? regions.map((region) => <tr key={region.id}><td>{region.region_name}</td><td>{region.match_keyword}</td><td>{region.manager_name}</td><td>{canManage ? <><button className="text-button" onClick={() => setEditRegion(region)}>수정</button> · <button className="text-button" onClick={() => void update(`/partners-regions/regions/${region.id}`, { is_active: !region.is_active })}>{region.is_active ? "활성" : "비활성"}</button></> : region.is_active ? "활성" : "비활성"}</td></tr>) : <tr><td colSpan={4}><EmptyState title="등록된 지역 매핑이 없습니다" description="위치가 매칭되지 않으면 공용 미배정함에 남습니다." /></td></tr>}</tbody></table></div>
    </>}
  </section>;
}

function PartnerForm({ session, partner, onSaved, onError }: { session: Session; partner: Partner | null; onSaved: () => Promise<void>; onError: (message: string) => void }) {
  const [busy, setBusy] = useState(false);
  return <details className="create-strip" open={Boolean(partner)}><summary>{partner ? "검증 파트너 수정" : "검증 파트너 등록"}</summary><form key={partner?.id ?? "new"} className="form-grid" onSubmit={async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); setBusy(true); const data = new FormData(event.currentTarget); try { await api(partner ? `/partners-regions/partners/${partner.id}` : "/partners-regions/partners", { method: partner ? "PATCH" : "POST", body: JSON.stringify(Object.fromEntries(data)) }, session); event.currentTarget.reset(); await onSaved(); } catch (error) { onError(error instanceof Error ? error.message : "파트너를 저장하지 못했습니다."); } finally { setBusy(false); } }}><label>상호명<input name="name" defaultValue={partner?.name} required maxLength={200} /></label><label>구분<select name="partner_type" defaultValue={partner?.partner_type ?? "전문점"}><option value="총판">총판</option><option value="전문점">전문점</option><option value="기타">기타</option></select></label><label>매칭 지역 키워드<input name="region" defaultValue={partner?.region} placeholder="예: 서울 강남구" required maxLength={100} /><small>고객 위치에 포함될 행정구역만 입력하세요.</small></label><label>연락처<input name="phone" defaultValue={partner?.phone ?? ""} maxLength={30} /></label><label className="wide-field">주소<input name="address" defaultValue={partner?.address} required maxLength={500} /></label><label>검증 출처<input name="verification_source" defaultValue={partner?.verification_source} required maxLength={200} /></label><label>검증일<input name="verified_at" type="date" defaultValue={partner?.verified_at} required /></label><button className="primary wide-field" disabled={busy}>{partner ? "수정 저장" : "등록"}</button></form></details>;
}

function RegionForm({ session, managers, region, onSaved, onError }: { session: Session; managers: StaffMember[]; region: SalesRegion | null; onSaved: () => Promise<void>; onError: (message: string) => void }) {
  const [busy, setBusy] = useState(false);
  return <details className="create-strip" open={Boolean(region)}><summary>{region ? "지역 담당 수정" : "지역 담당 등록"}</summary><form key={region?.id ?? "new"} className="form-grid" onSubmit={async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); setBusy(true); const data = new FormData(event.currentTarget); try { await api(region ? `/partners-regions/regions/${region.id}` : "/partners-regions/regions", { method: region ? "PATCH" : "POST", body: JSON.stringify(Object.fromEntries(data)) }, session); event.currentTarget.reset(); await onSaved(); } catch (error) { onError(error instanceof Error ? error.message : "지역 담당을 저장하지 못했습니다."); } finally { setBusy(false); } }}><label>지역명<input name="region_name" defaultValue={region?.region_name} required maxLength={100} /></label><label>매칭 키워드<input name="match_keyword" defaultValue={region?.match_keyword} required maxLength={100} /></label><label>지역 매니저<select name="manager_id" defaultValue={region?.manager_id ?? ""} required><option value="">선택</option>{managers.map((manager) => <option key={manager.id} value={manager.id}>{manager.name}</option>)}</select></label><button className="primary" disabled={busy || managers.length === 0}>{region ? "수정 저장" : "등록"}</button></form></details>;
}
