import { useRef, useState } from "react";

import { api, downloadCsv } from "../api";
import type { CsvImportResult, Session } from "../types";

export default function CsvControls({ session, importPath, exportPath, filename, onImported }: {
  session: Session;
  importPath: string;
  exportPath: string;
  filename: string;
  onImported: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [result, setResult] = useState<CsvImportResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function importFile(file: File) {
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const response = await api<CsvImportResult>(importPath, {
        method: "POST",
        body: JSON.stringify({ csv_text: await file.text() })
      }, session);
      setResult(response);
      if (response.imported_count) onImported();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "CSV를 가져오지 못했습니다.");
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <div className="csv-controls">
      <input ref={inputRef} className="sr-only" type="file" accept=".csv,text/csv" aria-label={`${filename} CSV 파일 선택`} onChange={(event) => { const file = event.target.files?.[0]; if (file) void importFile(file); }} />
      <button className="secondary-button" type="button" disabled={busy} onClick={() => inputRef.current?.click()}>{busy ? "가져오는 중…" : "CSV 가져오기"}</button>
      <button className="secondary-button" type="button" disabled={busy} onClick={() => void downloadCsv(exportPath, filename, session).catch((requestError: unknown) => setError(requestError instanceof Error ? requestError.message : "CSV를 내보내지 못했습니다."))}>CSV 내보내기</button>
      {error ? <p className="error notice" role="alert">{error}</p> : null}
      {result?.imported_count ? <p className="success notice" role="status">{result.imported_count}행을 가져왔습니다.</p> : null}
      {result?.errors.length ? <ul className="csv-errors" role="alert">{result.errors.map((item, index) => <li key={`${item.row}-${index}`}>{item.row ? `${item.row}행: ` : ""}{item.error}</li>)}</ul> : null}
    </div>
  );
}
