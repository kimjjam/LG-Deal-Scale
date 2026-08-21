import { FormEvent, useState } from "react";

import { api } from "../api";
import type { Role, Session } from "../types";

interface LoginResponse {
  access_token: string;
  role: Role;
  name: string;
}

export default function Login({
  onLogin,
}: {
  onLogin: (session: Session) => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await api<LoginResponse>(
        "/auth/login",
        { method: "POST", body: JSON.stringify({ email, password }) },
        null,
      );
      onLogin({
        accessToken: response.access_token,
        role: response.role,
        name: response.name,
      });
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "로그인하지 못했습니다.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-hero" aria-label="LG Deal Scale 소개">
        <a className="login-brand" href="/">
          <span className="brand-symbol" aria-hidden="true">
            L
          </span>
          <span>LG Deal Scale</span>
        </a>
        <div className="login-hero-copy">
          <span className="eyebrow light">LG Deal Scale 영업 워크스페이스</span>
          <h1>
            문의부터 후속 영업까지
            <br />
            한곳에서 선명하게.
          </h1>
          <p>
            고객의 신호를 놓치지 않도록 우선순위를 정리하고, 담당자가 다음
            행동에 집중할 수 있게 돕습니다.
          </p>
          <ul className="hero-points">
            <li>
              <span aria-hidden="true">✓</span> AI 문의 분류와 점수 근거
            </li>
            <li>
              <span aria-hidden="true">✓</span> 담당자 자동 배정과 진행 현황
            </li>
            <li>
              <span aria-hidden="true">✓</span> 읽기 전용 자연어 데이터 검색
            </li>
          </ul>
        </div>
        <p className="login-hero-foot">
          LG Deal Scale · Sales intelligence platform
        </p>
      </section>

      <section className="login-form-side">
        <form className="login-panel" onSubmit={submit} aria-busy={busy}>
          <div className="mobile-login-brand">
            <span className="brand-symbol" aria-hidden="true">
              L
            </span>
            <strong>LG Deal Scale</strong>
          </div>
          <div className="login-heading">
            <span className="eyebrow">WELCOME BACK</span>
            <h2>영업 워크스페이스 로그인</h2>
            <p>등록된 LG Deal Scale 계정으로 계속하세요.</p>
          </div>

          <div className="field-group">
            <label htmlFor="email">이메일</label>
            <input
              id="email"
              type="email"
              autoComplete="username"
              placeholder="name@company.com"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoFocus
              required
            />
          </div>

          <div className="field-group">
            <label htmlFor="password">비밀번호</label>
            <div className="password-field">
              <input
                id="password"
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                minLength={8}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
              <button
                type="button"
                className="password-toggle"
                onClick={() => setShowPassword((visible) => !visible)}
                aria-label={showPassword ? "비밀번호 숨기기" : "비밀번호 보기"}
                aria-pressed={showPassword}
              >
                {showPassword ? "숨김" : "보기"}
              </button>
            </div>
          </div>

          {error ? (
            <p className="error notice" role="alert">
              {error}
            </p>
          ) : null}
          <button
            className="primary login-submit"
            type="submit"
            disabled={busy}
          >
            {busy ? (
              <>
                <span className="spinner" aria-hidden="true" />
                확인 중…
              </>
            ) : (
              "로그인"
            )}
          </button>
          <p className="secure-note">
            <span aria-hidden="true">●</span> 인증 정보는 암호화된 연결로
            전송됩니다.
          </p>
        </form>
      </section>
    </main>
  );
}
