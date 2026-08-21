import { FormEvent, useEffect, useRef, useState } from "react";

import { api } from "../api";
import type {
  Account,
  AccountOverview,
  ActivityType,
  Session,
  StaffMember,
} from "../types";
import DetailDialog from "./DetailDialog";
import CsvControls from "./CsvControls";
import { EmptyState, LoadingState } from "./States";

const PAGE_SIZE = 25;

function accountListPath(search: string, offset: number) {
  const query = new URLSearchParams({
    limit: String(PAGE_SIZE + 1),
    offset: String(offset),
  });
  if (search) query.set("q", search);
  return `/accounts?${query}`;
}

export default function Accounts({ session }: { session: Session }) {
  const canAssign = session.role !== "rep";
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [staff, setStaff] = useState<StaffMember[]>([]);
  const [overview, setOverview] = useState<AccountOverview | null>(null);
  const [loadingAccount, setLoadingAccount] = useState<Account | null>(null);
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [hasNext, setHasNext] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const overviewRequest = useRef(0);

  async function loadAccounts() {
    const rows = await api<Account[]>(
      accountListPath(search, offset),
      {},
      session,
    );
    setHasNext(rows.length > PAGE_SIZE);
    setAccounts(rows.slice(0, PAGE_SIZE));
  }

  async function loadOverview(accountId: number) {
    const request = ++overviewRequest.current;
    setError("");
    try {
      const result = await api<AccountOverview>(
        `/accounts/${accountId}/overview`,
        {},
        session,
      );
      if (request === overviewRequest.current) {
        setOverview(result);
        setLoadingAccount(null);
      }
    } catch (requestError) {
      if (request === overviewRequest.current) {
        setError(message(requestError, "고객사 상세를 불러오지 못했습니다."));
        setLoadingAccount(null);
      }
    }
  }

  useEffect(() => {
    let active = true;
    Promise.all([
      api<Account[]>(accountListPath(search, offset), {}, session),
      canAssign
        ? api<StaffMember[]>("/staff?role=rep", {}, session)
        : Promise.resolve([]),
    ])
      .then(([rows, members]) => {
        if (active) {
          setHasNext(rows.length > PAGE_SIZE);
          setAccounts(rows.slice(0, PAGE_SIZE));
          setStaff(members.filter((member) => member.is_active));
        }
      })
      .catch((requestError: unknown) => {
        if (active)
          setError(message(requestError, "고객사를 불러오지 못했습니다."));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [canAssign, offset, search, session]);

  async function submitSearch(event: FormEvent) {
    event.preventDefault();
    setOffset(0);
    setSearch(q.trim());
  }

  async function perform(action: () => Promise<unknown>) {
    if (!overview) return false;
    setBusy(true);
    setError("");
    try {
      await action();
      await Promise.all([loadOverview(overview.account.id), loadAccounts()]);
      return true;
    } catch (requestError) {
      setError(message(requestError, "요청을 처리하지 못했습니다."));
      return false;
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="workspace"
      aria-labelledby="accounts-title"
      aria-busy={loading || busy}
    >
      <div className="commandbar">
        <div>
          <h1 id="accounts-title">고객사</h1>
          <p>연락처와 영업 이력을 한곳에서 확인합니다.</p>
        </div>
        <div className="command-actions">
          {canAssign ? (
            <CsvControls
              session={session}
              importPath="/accounts/import"
              exportPath="/accounts/export.csv"
              filename="accounts.csv"
              onImported={() => void loadAccounts()}
            />
          ) : null}
          <form
            className="compact-filter"
            onSubmit={submitSearch}
            role="search"
          >
            <label htmlFor="account-search">고객사 검색</label>
            <input
              id="account-search"
              value={q}
              onChange={(event) => setQ(event.target.value)}
              placeholder="업체명 또는 연락처"
            />
            <button className="secondary-button">검색</button>
          </form>
        </div>
      </div>
      {error ? (
        <p className="error notice" role="alert">
          {error}
        </p>
      ) : null}
      {loading ? (
        <LoadingState label="고객사를 불러오는 중" />
      ) : (
        <>
          <div
            className="data-grid-wrap"
            role="region"
            aria-label="고객사 목록"
            tabIndex={0}
          >
            <table className="data-grid">
              <caption className="sr-only">등록된 고객사</caption>
              <thead>
                <tr>
                  <th>업체명</th>
                  <th>연락처</th>
                  <th>업종</th>
                  <th>객실 수</th>
                  <th>등록일</th>
                </tr>
              </thead>
              <tbody>
                {accounts.length ? (
                  accounts.map((account) => (
                    <tr key={account.id}>
                      <td>
                        <button
                          className="company-link"
                          onClick={() => {
                            setLoadingAccount(account);
                            void loadOverview(account.id);
                          }}
                        >
                          <span className="company-avatar" aria-hidden="true">
                            {account.name.slice(0, 1)}
                          </span>
                          <strong>{account.name}</strong>
                        </button>
                      </td>
                      <td>{account.phone}</td>
                      <td>{String(account.attributes.business_type ?? "-")}</td>
                      <td>{String(account.attributes.room_count ?? "-")}</td>
                      <td>{date(account.created_at)}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={5}>
                      <EmptyState
                        title="고객사가 없습니다"
                        description="검색 조건을 바꾸거나 새 문의를 접수하세요."
                      />
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <button
              className="secondary-button"
              type="button"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              이전
            </button>
            <span>{Math.floor(offset / PAGE_SIZE) + 1}페이지</span>
            <button
              className="secondary-button"
              type="button"
              disabled={!hasNext}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              다음
            </button>
          </div>
        </>
      )}
      {overview ? (
        <AccountDetail
          overview={overview}
          staff={staff}
          canAssign={canAssign}
          busy={busy}
          onClose={() => setOverview(null)}
          perform={perform}
        />
      ) : loadingAccount ? (
        <AccountDetailShell
          account={loadingAccount}
          onClose={() => setLoadingAccount(null)}
        />
      ) : null}
    </section>
  );
}

function AccountDetailShell({
  account,
  onClose,
}: {
  account: Account;
  onClose: () => void;
}) {
  return (
    <DetailDialog labelledBy="account-detail-title" onClose={onClose}>
      <div className="panel-heading">
        <div>
          <h2 id="account-detail-title">{account.name}</h2>
          <span>{account.phone}</span>
        </div>
        <button
          className="icon-button"
          onClick={onClose}
          aria-label="고객사 상세 닫기"
        >
          ×
        </button>
      </div>
      <div className="panel-content crm-detail">
        <LoadingState label="상세 정보를 불러오는 중" />
      </div>
    </DetailDialog>
  );
}

function AccountDetail({
  overview,
  staff,
  canAssign,
  busy,
  onClose,
  perform,
}: {
  overview: AccountOverview;
  staff: StaffMember[];
  canAssign: boolean;
  busy: boolean;
  onClose: () => void;
  perform: (action: () => Promise<unknown>) => Promise<boolean>;
}) {
  const account = overview.account;
  return (
    <DetailDialog labelledBy="account-detail-title" onClose={onClose}>
      <div className="panel-heading">
        <div>
          <h2 id="account-detail-title">{account.name}</h2>
          <span>{account.phone}</span>
        </div>
        <button
          className="icon-button"
          onClick={onClose}
          aria-label="고객사 상세 닫기"
        >
          ×
        </button>
      </div>
      <div className="panel-content crm-detail">
        <section>
          <div className="section-heading compact">
            <h3>담당자</h3>
            <span>{overview.contacts.length}명</span>
          </div>
          {overview.contacts.length ? (
            <ul className="plain-list">
              {overview.contacts.map((contact) => (
                <li key={contact.id}>
                  <strong>{contact.name}</strong>
                  <span>
                    {contact.role ?? "역할 미등록"} ·{" "}
                    {contact.phone ?? contact.email ?? "연락처 없음"}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="inline-empty">등록된 담당자가 없습니다.</p>
          )}
          <ContactForm accountId={account.id} busy={busy} perform={perform} />
        </section>
        <section>
          <div className="section-heading compact">
            <h3>통합 타임라인</h3>
            <span>{overview.timeline.length}건</span>
          </div>
          {overview.timeline.length ? (
            <ol className="timeline-list">
              {overview.timeline.map((item) => (
                <li key={`${item.kind}-${item.id}`}>
                  <time>{dateTime(item.at)}</time>
                  <strong>{kindLabel(item.kind)}</strong>
                  <p>{item.text}</p>
                </li>
              ))}
            </ol>
          ) : (
            <p className="inline-empty">아직 기록이 없습니다.</p>
          )}
          <ActivityForm accountId={account.id} busy={busy} perform={perform} />
        </section>
        <section>
          <div className="section-heading compact">
            <h3>영업기회</h3>
            <span>{overview.opportunities.length}건</span>
          </div>
          <ul className="plain-list">
            {overview.opportunities.map((item) => (
              <li key={item.id}>
                <strong>{item.title}</strong>
                <span>
                  {stageLabel(item.stage)} · {money(item.amount)} ·{" "}
                  {item.probability}%
                </span>
              </li>
            ))}
          </ul>
          {canAssign ? (
            <OpportunityForm
              accountId={account.id}
              staff={staff}
              busy={busy}
              perform={perform}
            />
          ) : null}
        </section>
        <section>
          <div className="section-heading compact">
            <h3>할 일</h3>
            <span>
              {
                overview.tasks.filter((item) => item.status === "pending")
                  .length
              }
              개 미완료
            </span>
          </div>
          <ul className="plain-list">
            {overview.tasks.map((item) => (
              <li key={item.id}>
                <strong>{item.title}</strong>
                <span>
                  {item.status === "completed" ? "완료" : dateTime(item.due_at)}
                </span>
              </li>
            ))}
          </ul>
          {canAssign ? (
            <TaskForm
              accountId={account.id}
              staff={staff}
              busy={busy}
              perform={perform}
            />
          ) : null}
        </section>
        <section>
          <div className="section-heading compact">
            <h3>문의 이력</h3>
            <span>{overview.inquiries.length}건</span>
          </div>
          <ul className="plain-list">
            {overview.inquiries.map((item) => (
              <li key={item.id}>
                <strong>
                  {item.status === "resolved" ? "처리 완료" : "처리 중"}
                </strong>
                <span>{dateTime(item.created_at)}</span>
                <p>{item.content}</p>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </DetailDialog>
  );
}

function ContactForm({ accountId, busy, perform }: FormProps) {
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  return (
    <details className="inline-form">
      <summary>담당자 추가</summary>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (
            await perform(() =>
              api(`/accounts/${accountId}/contacts`, {
                method: "POST",
                body: JSON.stringify({
                  name,
                  role: null,
                  phone: phone || null,
                  email: null,
                }),
              }),
            )
          ) {
            setName("");
            setPhone("");
          }
        }}
      >
        <label>
          이름
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
          />
        </label>
        <label>
          연락처
          <input
            value={phone}
            onChange={(event) => setPhone(event.target.value)}
            inputMode="tel"
          />
        </label>
        <button className="primary" disabled={busy}>
          추가
        </button>
      </form>
    </details>
  );
}

function ActivityForm({ accountId, busy, perform }: FormProps) {
  const [type, setType] = useState<ActivityType>("call");
  const [content, setContent] = useState("");
  return (
    <details className="inline-form">
      <summary>활동 기록</summary>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (
            await perform(() =>
              api("/crm/activities", {
                method: "POST",
                body: JSON.stringify({
                  account_id: accountId,
                  type,
                  content: content || null,
                }),
              }),
            )
          )
            setContent("");
        }}
      >
        <label>
          유형
          <select
            value={type}
            onChange={(event) => setType(event.target.value as ActivityType)}
          >
            <option value="call">통화</option>
            <option value="email">이메일</option>
            <option value="meeting">미팅</option>
            <option value="note">메모</option>
            <option value="purchase">구매</option>
          </select>
        </label>
        <label>
          내용
          <textarea
            value={content}
            onChange={(event) => setContent(event.target.value)}
            maxLength={10000}
          />
        </label>
        <button className="primary" disabled={busy}>
          기록
        </button>
      </form>
    </details>
  );
}

function OpportunityForm({ accountId, staff, busy, perform }: AssignFormProps) {
  const [title, setTitle] = useState("");
  const [assignee, setAssignee] = useState("");
  return (
    <details className="inline-form">
      <summary>영업기회 추가</summary>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (
            await perform(() =>
              api("/crm/opportunities", {
                method: "POST",
                body: JSON.stringify({
                  account_id: accountId,
                  assignee_id: assignee,
                  title,
                }),
              }),
            )
          ) {
            setTitle("");
            setAssignee("");
          }
        }}
      >
        <label>
          제목
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            required
          />
        </label>
        <label>
          담당자
          <select
            value={assignee}
            onChange={(event) => setAssignee(event.target.value)}
            required
          >
            <option value="">선택</option>
            {staff.map((member) => (
              <option key={member.id} value={member.id}>
                {member.name}
              </option>
            ))}
          </select>
        </label>
        <button className="primary" disabled={busy}>
          추가
        </button>
      </form>
    </details>
  );
}

function TaskForm({ accountId, staff, busy, perform }: AssignFormProps) {
  const [title, setTitle] = useState("");
  const [assignee, setAssignee] = useState("");
  const [dueAt, setDueAt] = useState("");
  return (
    <details className="inline-form">
      <summary>할 일 추가</summary>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (
            await perform(() =>
              api("/crm/tasks", {
                method: "POST",
                body: JSON.stringify({
                  account_id: accountId,
                  assignee_id: assignee,
                  title,
                  due_at: new Date(dueAt).toISOString(),
                }),
              }),
            )
          ) {
            setTitle("");
            setAssignee("");
            setDueAt("");
          }
        }}
      >
        <label>
          제목
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            required
          />
        </label>
        <label>
          마감
          <input
            type="datetime-local"
            value={dueAt}
            onChange={(event) => setDueAt(event.target.value)}
            required
          />
        </label>
        <label>
          담당자
          <select
            value={assignee}
            onChange={(event) => setAssignee(event.target.value)}
            required
          >
            <option value="">선택</option>
            {staff.map((member) => (
              <option key={member.id} value={member.id}>
                {member.name}
              </option>
            ))}
          </select>
        </label>
        <button className="primary" disabled={busy}>
          추가
        </button>
      </form>
    </details>
  );
}

type FormProps = {
  accountId: number;
  busy: boolean;
  perform: (action: () => Promise<unknown>) => Promise<boolean>;
};
type AssignFormProps = FormProps & { staff: StaffMember[] };
function message(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}
function date(value: string) {
  return new Date(value).toLocaleDateString("ko-KR");
}
function dateTime(value: string) {
  return new Date(value).toLocaleString("ko-KR");
}
function money(value: string | number | null) {
  return value == null
    ? "금액 미정"
    : `${Number(value).toLocaleString("ko-KR")}원`;
}
function kindLabel(kind: AccountOverview["timeline"][number]["kind"]) {
  return {
    inquiry: "문의",
    activity: "활동",
    opportunity: "영업기회",
    task: "할 일",
  }[kind];
}
function stageLabel(stage: AccountOverview["opportunities"][number]["stage"]) {
  return {
    qualify: "검증",
    develop: "개발",
    propose: "제안",
    won: "수주",
    lost: "실주",
  }[stage];
}
