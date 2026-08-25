import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";

import { api } from "../api";
import type { ChatMessage, ChatTurn, IntakeFields, PublicResult } from "../types";

const INITIAL_MESSAGES: ChatMessage[] = [
  {
    role: "assistant",
    content: "안녕하세요. LG Deal Scale 제품 비교 안내입니다. 업체 상황과 필요한 가전제품을 말씀해주세요."
  }
];

const FIELD_LABELS: Array<{ key: keyof IntakeFields; label: string; suffix?: string }> = [
  { key: "business_name", label: "업체명" },
  { key: "business_type", label: "업종" },
  { key: "room_count", label: "객실", suffix: "개" },
  { key: "seat_count", label: "좌석", suffix: "석" },
  { key: "employee_count", label: "직원", suffix: "명" },
  { key: "store_count", label: "매장", suffix: "개" },
  { key: "inquiry", label: "문의 요약" },
  { key: "product", label: "관심 제품" },
  { key: "quantity", label: "수량", suffix: "대" },
  { key: "location", label: "지역" },
  { key: "purchase_stage", label: "구매 단계" },
  { key: "purchase_timing", label: "구매 시기" },
  { key: "phone", label: "연락처" }
];

export default function PublicInquiry() {
  const [started, setStarted] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>(INITIAL_MESSAGES);
  const [fields, setFields] = useState<IntakeFields>({});
  const [input, setInput] = useState("");
  const [ready, setReady] = useState(false);
  const [returningCustomer, setReturningCustomer] = useState(false);
  const [result, setResult] = useState<PublicResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const messagesRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const resultRef = useRef<HTMLElement>(null);
  const startButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const container = messagesRef.current;
    container?.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
  }, [messages.length, busy, ready, result]);

  useEffect(() => {
    if (started && !busy && !result) inputRef.current?.focus();
  }, [started, busy, result]);

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    const content = input.trim();
    if (!content || busy) return;
    setConfirmed(false);
    setReady(false);
    const nextMessages = [...messages, { role: "user" as const, content }];
    setMessages(nextMessages);
    setInput("");
    setBusy(true);
    setError("");
    try {
      const turn = await api<ChatTurn>("/public/chat", {
        method: "POST",
        body: JSON.stringify({ messages: nextMessages, fields })
      }, null);
      setConfirmed(false);
      setFields(turn.fields);
      setReady(turn.ready_for_analysis);
      setReturningCustomer(turn.returning_customer);
      setMessages((current) => [...current, { role: "assistant", content: turn.message }]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "다시 시도해주세요.");
    } finally {
      setBusy(false);
    }
  }

  function submitFromKeyboard(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  async function submitInquiry() {
    if (busy || !confirmed) return;
    setBusy(true);
    setError("");
    try {
      const submission = await api<PublicResult>("/public/submit", {
        method: "POST",
        body: JSON.stringify({ messages, fields })
      }, null);
      setResult(submission);
      window.setTimeout(() => resultRef.current?.focus(), 0);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "접수하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    setStarted(false);
    setMessages(INITIAL_MESSAGES);
    setFields({});
    setInput("");
    setReady(false);
    setReturningCustomer(false);
    setResult(null);
    setError("");
    setConfirmed(false);
    window.setTimeout(() => startButtonRef.current?.focus(), 0);
  }

  const summary = FIELD_LABELS.flatMap(({ key, label, suffix }) => {
    const value = fields[key];
    return value === null || value === undefined || value === "" ? [] : [{ key, label, value: `${String(value)}${suffix ?? ""}` }];
  });

  if (!started) return (
    <main className="public-page">
      <section className="chat-shell public-landing" aria-labelledby="public-intro-title">
        <header className="chat-header">
          <div className="public-brand">
            <span className="brand-symbol public" aria-hidden="true">LG</span>
            <div><h1>LG Deal Scale | LG전자 등 제품 정보 비교</h1><small>제품 비교 안내 서비스</small></div>
          </div>
        </header>
        <div className="public-intro">
          <div>
            <h2 id="public-intro-title">사업장용 가전제품 정보와 공식몰 참고 가격을 한눈에 확인하세요</h2>
            <p>업종·규모·필요 수량을 입력하면 등록된 제품 정보, 공식몰 참고 가격, 공식 링크와 상담 경로를 안내합니다.</p>
            <p className="service-disclaimer">LG Deal Scale 제품 비교 안내 서비스입니다.</p>
          </div>
          <ol>
            <li><strong>상황 확인</strong><span>업종, 규모, 제품, 수량과 구매 시기를 물어봅니다.</span></li>
            <li><strong>내용 확인</strong><span>정리된 내용을 직접 확인하고 수정할 수 있습니다.</span></li>
            <li><strong>제품 안내</strong><span>확정 버튼을 누르면 상담이 접수되고 등록된 제품 정보가 표시됩니다.</span></li>
          </ol>
          <section className="public-intro-notice" aria-labelledby="public-notice-title">
            <h3 id="public-notice-title">안내사항</h3>
            <dl>
              <div><dt>가격</dt><dd>표시 단가는 검증된 사업자 가격 또는 공식몰 참고가입니다. 실제 B2B 견적, 설치비와 할인은 상담 조건에 따라 달라집니다.</dd></div>
              <div><dt>접수</dt><dd>대화만으로는 접수되지 않으며, 마지막 확인 버튼을 눌러야 담당팀에 전달됩니다.</dd></div>
              <div><dt>개인정보</dt><dd>입력 정보는 상담 접수, 제품 안내와 영업 우선순위 산정에 사용됩니다.</dd></div>
            </dl>
          </section>
          <button ref={startButtonRef} className="primary public-start-button" type="button" onClick={() => setStarted(true)}>제품 비교 시작하기</button>
        </div>
        <footer className="chat-footer"><span aria-hidden="true">●</span> LG Deal Scale · 입력 정보는 상담과 제품 안내에 사용됩니다.</footer>
      </section>
    </main>
  );

  return (
    <main className="public-page">
      <section className="chat-shell" aria-labelledby="public-title">
        <header className="chat-header">
          <div className="public-brand">
            <span className="brand-symbol public" aria-hidden="true">LG</span>
            <div><h1 id="public-title">LG Deal Scale | LG전자 등 제품 정보 비교</h1><small>제품 비교 안내 서비스</small></div>
          </div>
          <span className="chat-status"><span aria-hidden="true" />상담 가능</span>
        </header>

        <div className="chat-subheader">
          <span className={ready || result ? "complete" : ""}><strong>1</strong> 상황 확인</span><i aria-hidden="true" />
          <span className={ready || result ? "complete" : ""}><strong>2</strong> 정보 정리</span><i aria-hidden="true" />
          <span className={result ? "complete" : ""}><strong>3</strong> 제품 안내</span>
        </div>

        <div
          className={`messages${result ? " completed" : ""}`}
          ref={messagesRef}
          role="log"
          aria-live="polite"
          aria-relevant="additions"
          aria-busy={busy}
        >
          <div className="welcome-card">
            <span aria-hidden="true">AI</span>
            <div><strong>제품 비교 정보를 준비하겠습니다</strong><p>업체 상황, 필요한 제품과 수량을 알려주시면 제품 정보·공식몰 참고 가격·공식 링크와 상담 경로를 정리합니다.</p></div>
          </div>
          {returningCustomer ? <p className="returning">이전에 상담하신 연락처로 확인됐어요.</p> : null}
          {messages.map((message, index) => (
            <div className={`message-row ${message.role}`} key={`${message.role}-${index}`}>
              {message.role === "assistant" ? <span className="message-avatar" aria-hidden="true">LG</span> : null}
              <p className={`bubble ${message.role}`}>{message.content}</p>
            </div>
          ))}
          {ready && !result ? (
            <div className="message-row assistant">
              <span className="message-avatar" aria-hidden="true">LG</span>
              <p className="bubble assistant submission-guidance">수정하거나 추가할 내용이 있다면 지금 말씀해주세요. 아래 버튼을 누르면 상담 요청이 접수되고 담당자에게 전달됩니다.</p>
            </div>
          ) : null}
          {busy ? (
            <div className="message-row assistant" role="status">
              <span className="message-avatar" aria-hidden="true">LG</span>
              <p className="bubble assistant typing"><span aria-hidden="true" /><span aria-hidden="true" /><span aria-hidden="true" /><span className="sr-only">내용을 확인하고 있어요.</span></p>
            </div>
          ) : null}
        </div>

        {error ? <p className="error notice chat-notice" role="alert">{error}</p> : null}
        {ready && !result ? (
          <section className="intake-review" aria-labelledby="review-title">
            <div className="section-heading compact">
              <div><h2 id="review-title">이렇게 이해했어요</h2></div>
              <span className="status-badge routed">정보 확인</span>
            </div>
            <dl>
              {summary.map((item) => <div key={item.key}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}
            </dl>
            <p className="review-help">내용을 바꾸려면 아래 대화창에 수정할 정보를 말씀해주세요.</p>
            <p className="submit-note"><strong>접수 전 확인</strong> 아래 버튼을 누르는 즉시 상담 요청이 접수되고 담당자에게 전달됩니다.</p>
            <label className="confirm-check intake-confirm">
              <input type="checkbox" checked={confirmed} disabled={busy} onChange={(event) => setConfirmed(event.target.checked)} />
              <span><strong>[필수] 개인정보 수집·이용 동의</strong><br />수집 항목: 업체명, 연락처, 업종·규모, 관심 제품·수량, 지역, 상담 내용. 이용 목적: 제품 정보·공식몰 참고 가격 안내, 상담 접수·담당자 연결, 영업 우선순위 산정. 보유 기간: 계정 및 상담 기록 삭제 시까지. 동의를 거부할 수 있으나, 거부 시 상담 접수와 제품 안내를 이용할 수 없습니다.</span>
            </label>
            <button className="primary analysis-button" type="button" onClick={submitInquiry} disabled={busy || !confirmed}>
              {busy ? <><span className="spinner" aria-hidden="true" />접수 중…</> : "문의 접수하고 제품 정보 보기"}
            </button>
          </section>
        ) : null}

        {result ? (
          <section className="analysis-result" aria-labelledby="analysis-title" ref={resultRef} tabIndex={-1}>
            <div className="result-confirmation">
              <span className="result-check" aria-hidden="true">✓</span>
              <div><h2 id="analysis-title">상담이 접수됐습니다</h2><p>{result.confirmation}</p></div>
            </div>

            <section className="analysis-copy">
              <div className="section-heading compact"><div><h3>요청 정보 안내</h3><span>확인된 입력값과 등록 제품을 기준으로 정리했습니다.</span></div></div>
              <p>{result.analysis}</p>
            </section>

            <section className="result-section">
              <div className="section-heading compact"><div><h3>추천 제품</h3><span>제조사 제품 페이지에 등록된 정보 기준</span></div></div>
              {result.products.length ? <div className="product-list">{result.products.map((product) => (
                <article className="product-card" key={product.product_url}>
                  <div><span>{product.brand}</span><strong>{product.name}</strong></div>
                  <div className="product-price"><strong>{product.price_label}</strong>{product.price_source_url && product.price_verified_at ? <small><a href={product.price_source_url} target="_blank" rel="noreferrer">가격 출처<span className="sr-only"> (새 창)</span></a> · {product.price_verified_at} 확인</small> : null}</div>
                  <a href={product.product_url} target="_blank" rel="noreferrer">제품 정보 보기<span className="sr-only"> (새 창)</span><span aria-hidden="true">↗</span></a>
                </article>
              ))}</div> : <EmptyResult text="현재 조건에 맞는 추천 제품을 준비 중입니다." />}
              {result.products.some((product) => product.price !== null) ? <p className="price-disclaimer">표시 가격은 제품별 단가입니다. 최종 수량별 견적, 설치·배송·할인 조건은 상담에서 확정됩니다.</p> : null}
            </section>

            <section className="result-section">
              <div className="section-heading compact"><div><h3>지역 상담 연결</h3><span>입력한 지역을 기준으로 등록된 담당팀과 파트너를 확인합니다.</span></div></div>
              <p className="regional-team-status">{result.regional_team_connected ? "해당 지역 담당팀에 상담이 전달되었습니다." : "지역 담당팀을 확인한 후 안내드리겠습니다."}</p>
              {result.partner ? <article className="store-card"><div><strong>{result.partner.name} · {result.partner.partner_type}</strong><p>{result.partner.address}</p><small>{result.partner.verified_at} 기준 등록 정보</small></div>{result.partner.phone ? <a aria-label={`${result.partner.name} 전화 ${result.partner.phone}`} href={`tel:${result.partner.phone.replace(/[^\d+]/g, "")}`}>{result.partner.phone}</a> : null}</article> : <EmptyResult text="현재 지역에 매칭된 검증 파트너가 없어 담당팀이 확인 후 안내드립니다." />}
            </section>

            <section className="result-section">
              <div className="section-heading compact"><div><h3>구매 경로 비교</h3><span>계약과 지원 방식이 다르므로 운영 상황에 맞춰 확인하세요.</span></div></div>
              <div className="purchase-comparison-wrap" role="region" aria-label="사업자 구매 경로 비교" tabIndex={0}>
                <table className="purchase-comparison">
                  <thead><tr><th>구분</th><th>가격 기준</th><th>소유·계약</th><th>설치·지원</th><th>적합한 상황·유의사항</th></tr></thead>
                  <tbody>
                    <tr><th>지역 등록 파트너 상담</th><td>상담 후 견적</td><td>판매 또는 계약 조건 확인</td><td>지역별 설치·지원 범위 확인</td><td>현장 조건을 함께 확인하려는 경우. 등록 정보와 계약 조건을 먼저 확인하세요.</td></tr>
                    <tr><th>LG전자 B2B 온라인스토어</th><td>온라인스토어 표시·견적 기준</td><td>제품 직접 구매 후 소유</td><td>상품별 배송·설치 조건 확인</td><td>제품과 수량을 정해 직접 주문하려는 경우. 설치 포함 여부를 확인하세요.</td></tr>
                    <tr><th>사업자용 구독 서비스</th><td>월 이용료·계약 조건 기준</td><td>계약 기간 동안 이용</td><td>계약에 포함된 관리 범위 확인</td><td>초기 일시 지출을 나누려는 경우. 총 계약기간과 해지 조건을 확인하세요.</td></tr>
                  </tbody>
                </table>
              </div>
            </section>

            <section className="result-section">
              <div className="section-heading compact"><div><h3>주변 전문점 검색 후보</h3><span>네이버 지역 검색 결과이며 공식 총판 인증 정보가 아닙니다.</span></div></div>
              {result.stores.length ? <div className="store-list">{result.stores.map((store) => (
                <article className="store-card" key={`${store.name}-${store.address}`}>
                  <div><strong>{store.name}</strong><p>{store.address}</p></div>
                  {store.phone ? <a aria-label={`${store.name} 전화 ${store.phone}`} href={`tel:${store.phone.replace(/[^\d+]/g, "")}`}>{store.phone}</a> : null}
                </article>
              ))}</div> : <EmptyResult text={result.nearby_store_message} />}
            </section>

            <div className="result-actions">
              <a className="primary button-link" href="https://www.lgb2bonlinestore.com" target="_blank" rel="noreferrer">LG전자 B2B 온라인스토어<span className="sr-only"> (새 창)</span><span aria-hidden="true">↗</span></a>
              <button className="secondary-button" type="button" onClick={reset}>새 상담 시작</button>
            </div>
          </section>
        ) : (
          <form className="chat-input" onSubmit={sendMessage}>
            <label className="sr-only" htmlFor="message">상담 내용</label>
            <div className="composer">
              <textarea
                id="message"
                ref={inputRef}
                value={input}
                onChange={(event) => {
                  setConfirmed(false);
                  setInput(event.target.value);
                }}
                onKeyDown={submitFromKeyboard}
                placeholder="예: 객실 12개 펜션인데 냉장고가 필요해요"
                maxLength={4000}
                rows={1}
                disabled={busy}
              />
              <span>Enter 전송 · Shift+Enter 줄바꿈</span>
            </div>
            <button type="submit" className="send-button" disabled={busy || !input.trim()} aria-label="메시지 보내기">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 11.5L21 3l-8.5 18-2-7.5z M10.5 13.5L21 3" /></svg>
            </button>
          </form>
        )}
        <footer className="chat-footer"><span aria-hidden="true">●</span> LG Deal Scale · 입력 정보는 상담과 제품 안내에 사용됩니다.</footer>
      </section>
    </main>
  );
}

function EmptyResult({ text }: { text: string }) {
  return <p className="inline-empty">{text}</p>;
}
