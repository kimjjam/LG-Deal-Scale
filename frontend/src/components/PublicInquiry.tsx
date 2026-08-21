import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";

import { api } from "../api";
import type { ChatMessage, ChatTurn, IntakeFields, PublicResult } from "../types";

const INITIAL_MESSAGES: ChatMessage[] = [
  {
    role: "assistant",
    content: "안녕하세요. 다온비즈 AI 상담입니다. 업체명과 필요한 가전을 편하게 말씀해주세요."
  }
];

const FIELD_LABELS: Array<{ key: keyof IntakeFields; label: string; suffix?: string }> = [
  { key: "business_name", label: "업체명" },
  { key: "business_type", label: "업종" },
  { key: "room_count", label: "객실", suffix: "개" },
  { key: "inquiry", label: "문의 요약" },
  { key: "product", label: "관심 제품" },
  { key: "quantity", label: "수량", suffix: "대" },
  { key: "location", label: "지역" },
  { key: "phone", label: "연락처" }
];

export default function PublicInquiry() {
  const [messages, setMessages] = useState<ChatMessage[]>(INITIAL_MESSAGES);
  const [fields, setFields] = useState<IntakeFields>({});
  const [input, setInput] = useState("");
  const [ready, setReady] = useState(false);
  const [returningCustomer, setReturningCustomer] = useState(false);
  const [result, setResult] = useState<PublicResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const messagesRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const resultRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const container = messagesRef.current;
    container?.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
  }, [messages.length, busy, ready, result]);

  useEffect(() => {
    if (!busy && !result) inputRef.current?.focus();
  }, [busy, result]);

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    const content = input.trim();
    if (!content || busy) return;
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
    if (busy) return;
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
    setMessages(INITIAL_MESSAGES);
    setFields({});
    setInput("");
    setReady(false);
    setReturningCustomer(false);
    setResult(null);
    setError("");
  }

  const summary = FIELD_LABELS.flatMap(({ key, label, suffix }) => {
    const value = fields[key];
    return value === null || value === undefined || value === "" ? [] : [{ key, label, value: `${String(value)}${suffix ?? ""}` }];
  });

  return (
    <main className="public-page">
      <section className="chat-shell" aria-labelledby="public-title">
        <header className="chat-header">
          <div className="public-brand">
            <span className="brand-symbol public" aria-hidden="true">D</span>
            <div><span className="header-kicker">DAONBIZ B2B CARE</span><h1 id="public-title">가전 도입 상담</h1></div>
          </div>
          <span className="chat-status"><span aria-hidden="true" />상담 가능</span>
        </header>

        <div className="chat-subheader">
          <span><strong>1</strong> 상황 확인</span><i aria-hidden="true" />
          <span className={ready ? "complete" : ""}><strong>2</strong> 정보 정리</span><i aria-hidden="true" />
          <span className={result ? "complete" : ""}><strong>3</strong> 맞춤 안내</span>
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
            <div><strong>복잡한 양식 없이 시작하세요</strong><p>업체 상황과 필요한 제품을 대화로 알려주시면 꼭 필요한 내용만 정리합니다.</p></div>
          </div>
          {returningCustomer ? <p className="returning">이전에 상담하신 연락처로 확인됐어요.</p> : null}
          {messages.map((message, index) => (
            <div className={`message-row ${message.role}`} key={`${message.role}-${index}`}>
              {message.role === "assistant" ? <span className="message-avatar" aria-hidden="true">D</span> : null}
              <p className={`bubble ${message.role}`}>{message.content}</p>
            </div>
          ))}
          {busy ? (
            <div className="message-row assistant" role="status">
              <span className="message-avatar" aria-hidden="true">D</span>
              <p className="bubble assistant typing"><span aria-hidden="true" /><span aria-hidden="true" /><span aria-hidden="true" /><span className="sr-only">내용을 확인하고 있어요.</span></p>
            </div>
          ) : null}
        </div>

        {error ? <p className="error notice chat-notice" role="alert">{error}</p> : null}
        {ready && !result ? (
          <section className="intake-review" aria-labelledby="review-title">
            <div className="section-heading compact">
              <div><span className="eyebrow">READY TO SUBMIT</span><h2 id="review-title">이렇게 이해했어요</h2></div>
              <span className="status-badge routed">정보 확인</span>
            </div>
            <dl>
              {summary.map((item) => <div key={item.key}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}
            </dl>
            <p className="review-help">내용을 바꾸려면 아래 대화창에 수정할 정보를 말씀해주세요.</p>
            <button className="primary analysis-button" onClick={submitInquiry} disabled={busy}>
              {busy ? <><span className="spinner" aria-hidden="true" />접수 중…</> : "문의 접수하고 맞춤 분석 받기"}
            </button>
            <p className="submit-note">버튼을 누르면 상담 요청이 접수되고 담당자에게 전달됩니다.</p>
          </section>
        ) : null}

        {result ? (
          <section className="analysis-result" aria-labelledby="analysis-title" ref={resultRef} tabIndex={-1}>
            <div className="result-confirmation">
              <span className="result-check" aria-hidden="true">✓</span>
              <div><span className="eyebrow">REQUEST RECEIVED</span><h2 id="analysis-title">상담이 접수됐습니다</h2><p>{result.confirmation}</p></div>
            </div>

            <section className="analysis-copy">
              <div className="section-heading compact"><div><h3>AI 맞춤 분석</h3><span>입력한 정보를 기준으로 정리했어요.</span></div>{result.analysis_error ? <span className="status-badge warning">분석 지연</span> : null}</div>
              <p>{result.analysis ?? "분석 생성은 지연됐지만 문의는 정상 접수되었습니다. 담당자가 내용을 확인해 안내드리겠습니다."}</p>
            </section>

            <section className="result-section">
              <div className="section-heading compact"><div><h3>추천 제품</h3><span>공식 제품 정보와 현재 표시 가격 기준</span></div></div>
              {result.products.length ? <div className="product-list">{result.products.map((product) => (
                <article className="product-card" key={product.product_url}>
                  <div><span>{product.brand}</span><strong>{product.name}</strong></div>
                  <strong className="product-price">{product.price.toLocaleString("ko-KR")}원</strong>
                  <a href={product.product_url} target="_blank" rel="noreferrer">제품 정보 보기<span className="sr-only"> (새 창)</span><span aria-hidden="true">↗</span></a>
                </article>
              ))}</div> : <EmptyResult text="현재 조건에 맞는 추천 제품을 준비 중입니다." />}
            </section>

            <section className="result-section">
              <div className="section-heading compact"><div><h3>가까운 전문점</h3><span>방문 전 영업 여부를 확인해주세요.</span></div></div>
              {result.stores.length ? <div className="store-list">{result.stores.map((store) => (
                <article className="store-card" key={`${store.name}-${store.address}`}>
                  <div><strong>{store.name}</strong><p>{store.address}</p></div>
                  {store.phone ? <a href={`tel:${store.phone.replace(/[^\d+]/g, "")}`}>{store.phone}</a> : null}
                </article>
              ))}</div> : <EmptyResult text="지금은 근처 매장 정보를 불러올 수 없습니다." />}
            </section>

            <div className="result-actions">
              <a className="primary button-link" href="https://www.lgb2bonlinestore.com" target="_blank" rel="noreferrer">온라인몰에서 더 알아보기<span className="sr-only"> (새 창)</span><span aria-hidden="true">↗</span></a>
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
                onChange={(event) => setInput(event.target.value)}
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
        <footer className="chat-footer"><span aria-hidden="true">●</span> 문의 내용은 상담 목적으로만 안전하게 처리됩니다.</footer>
      </section>
    </main>
  );
}

function EmptyResult({ text }: { text: string }) {
  return <p className="inline-empty">{text}</p>;
}
