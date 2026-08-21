import { FormEvent, useEffect, useState } from "react";

import { api } from "../api";
import type { Role, Session, StaffMember } from "../types";
import { EmptyState, LoadingState } from "./States";

type EditableRole = Exclude<Role, "owner">;

export default function StaffManagement({ session }: { session: Session }) {
  const [staff, setStaff] = useState<StaffMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<EditableRole>("manager");
  const [password, setPassword] = useState("");
  const [resetTarget, setResetTarget] = useState<StaffMember | null>(null);
  const [resetPassword, setResetPassword] = useState("");

  async function loadStaff() {
    setStaff(await api<StaffMember[]>("/staff", {}, session));
  }

  useEffect(() => {
    let active = true;
    void api<StaffMember[]>("/staff", {}, session)
      .then((rows) => { if (active) setStaff(rows); })
      .catch((requestError: unknown) => {
        if (active) setError(requestError instanceof Error ? requestError.message : "계정을 불러오지 못했습니다.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [session]);

  async function createAccount(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      await api("/staff", {
        method: "POST",
        body: JSON.stringify({ name, email, role, password })
      }, session);
      await loadStaff();
      setName("");
      setEmail("");
      setPassword("");
      setSuccess("직원 계정을 생성했습니다.");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "계정을 생성하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function changeRole(member: StaffMember, nextRole: EditableRole) {
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      await api(`/staff/${member.id}/role`, {
        method: "PATCH",
        body: JSON.stringify({ role: nextRole })
      }, session);
      await loadStaff();
      setSuccess(`${member.name}님의 역할을 변경했습니다.`);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "역할을 변경하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function changeActive(member: StaffMember) {
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      await api(`/staff/${member.id}/active`, { method: "PATCH", body: JSON.stringify({ is_active: !member.is_active }) }, session);
      await loadStaff();
      setSuccess(`${member.name}님 계정을 ${member.is_active ? "비활성화" : "활성화"}했습니다.`);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "계정 상태를 변경하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function resetAccountPassword(event: FormEvent) {
    event.preventDefault();
    if (!resetTarget) return;
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      await api(`/staff/${resetTarget.id}/reset-password`, {
        method: "POST",
        body: JSON.stringify({ password: resetPassword })
      }, session);
      setSuccess(`${resetTarget.name}님의 비밀번호를 재설정했습니다.`);
      setResetTarget(null);
      setResetPassword("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "비밀번호를 재설정하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="workspace" aria-labelledby="staff-title" aria-busy={loading || busy}>
      <div className="commandbar">
        <div><h1 id="staff-title">계정 관리</h1><p>직원 계정을 만들고 역할과 비밀번호를 관리합니다.</p></div>
        <span className="count-chip">{staff.length.toLocaleString("ko-KR")}개 계정</span>
      </div>

      <form className="staff-create-form" onSubmit={createAccount}>
        <div className="field-group"><label htmlFor="staff-name">이름</label><input id="staff-name" value={name} onChange={(event) => setName(event.target.value)} maxLength={100} required /></div>
        <div className="field-group"><label htmlFor="staff-email">이메일</label><input id="staff-email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} required /></div>
        <div className="field-group"><label htmlFor="staff-role">역할</label><select id="staff-role" value={role} onChange={(event) => setRole(event.target.value as EditableRole)}><option value="manager">관리자</option><option value="rep">영업 담당자</option></select></div>
        <div className="field-group"><label htmlFor="staff-password">초기 비밀번호</label><input id="staff-password" type="password" autoComplete="new-password" minLength={12} maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} required /></div>
        <button className="primary" disabled={busy}>계정 생성</button>
      </form>

      {error ? <p className="error notice" role="alert">{error}</p> : null}
      {success ? <p className="success notice" role="status">{success}</p> : null}

      {resetTarget ? (
        <form className="password-reset-form" onSubmit={resetAccountPassword}>
          <div><strong>{resetTarget.name} 비밀번호 재설정</strong><span>12자 이상의 새 임시 비밀번호를 입력하세요.</span></div>
          <input type="password" aria-label={`${resetTarget.name} 새 비밀번호`} autoComplete="new-password" minLength={12} maxLength={128} value={resetPassword} onChange={(event) => setResetPassword(event.target.value)} required autoFocus />
          <button className="primary" disabled={busy}>저장</button>
          <button className="secondary-button" type="button" onClick={() => { setResetTarget(null); setResetPassword(""); }}>취소</button>
        </form>
      ) : null}

      {loading ? <LoadingState label="직원 계정을 불러오는 중" /> : (
        <div className="data-grid-wrap" role="region" aria-label="직원 계정 표, 가로 스크롤 가능" tabIndex={0}>
          <table className="data-grid staff-grid">
            <caption className="sr-only">직원 계정 목록</caption>
            <thead><tr><th scope="col">이름</th><th scope="col">이메일</th><th scope="col">역할</th><th scope="col">상태</th><th scope="col">비밀번호</th></tr></thead>
            <tbody>{staff.length ? staff.map((member) => (
              <tr key={member.id}>
                <td><strong>{member.name}</strong></td>
                <td>{member.email}</td>
                <td>{member.role === "owner" ? "총관리자" : <select aria-label={`${member.name} 역할`} value={member.role} disabled={busy} onChange={(event) => void changeRole(member, event.target.value as EditableRole)}><option value="manager">관리자</option><option value="rep">영업 담당자</option></select>}</td>
                <td>{member.role === "owner" ? <span className="status-badge">활성</span> : <button className="text-button" type="button" disabled={busy} onClick={() => void changeActive(member)}>{member.is_active ? "활성 · 비활성화" : "비활성 · 활성화"}</button>}</td>
                <td>{member.role === "owner" ? "-" : <button className="secondary-button" type="button" onClick={() => { setResetTarget(member); setResetPassword(""); }}>재설정</button>}</td>
              </tr>
            )) : <tr><td colSpan={5}><EmptyState title="등록된 직원이 없습니다" description="위 양식에서 첫 직원 계정을 생성하세요." /></td></tr>}</tbody>
          </table>
        </div>
      )}
    </section>
  );
}
