import { useRef, useState } from "react";

import { api, downloadCsv } from "../api";
import type { CsvImportResult, Session } from "../types";

export default function CsvControls({ session, importPath, exportPath, filename, onImported }: {
  session: Session;
  importPath: string;
  exportPath?: string;
  filename: string;
  onImported: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [result, setResult] = useState<CsvImportResult | null>(null);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [busyAction, setBusyAction] = useState<"import" | "export" | null>(null);

  async function importFile(file: File) {
    if (busyAction) return;
    setBusyAction("import");
    setError("");
    setStatus("");
    setResult(null);
    try {
      const response = await api<CsvImportResult>(importPath, {
        method: "POST",
        body: JSON.stringify({ csv_text: await file.text() })
      }, session);
      setError("");
      setResult(response);
      if (!response.errors.length) setStatus(`${response.imported_count}행을 가져왔습니다.`);
      if (response.imported_count) onImported();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "CSV를 가져오지 못했습니다.");
    } finally {
      setBusyAction(null);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function exportFile() {
    if (!exportPath || busyAction) return;
    setBusyAction("export");
    setError("");
    setStatus("");
    setResult(null);
    try {
      await downloadCsv(exportPath, filename, session);
      setError("");
      setStatus("CSV 내보내기가 완료됐습니다.");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "CSV를 내보내지 못했습니다.");
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <div className="csv-controls">
      <input ref={inputRef} className="sr-only" type="file" accept=".csv,text/csv" aria-label={`${filename} CSV 파일 선택`} onChange={(event) => { const file = event.target.files?.[0]; if (file) void importFile(file); }} />
      <button className="secondary-button" type="button" disabled={Boolean(busyAction)} onClick={() => inputRef.current?.click()}>{busyAction === "import" ? "가져오는 중…" : "CSV 가져오기"}</button>
      {exportPath ? <button className="secondary-button" type="button" disabled={Boolean(busyAction)} onClick={() => void exportFile()}>{busyAction === "export" ? "내보내는 중…" : "CSV 내보내기"}</button> : null}
      {error ? <p className="error notice" role="alert">{error}</p> : null}
      {status ? <p className="success notice" role="status">{status}</p> : null}
      {result?.errors.length ? <ul className="csv-errors" role="alert">{result.errors.map((item, index) => <li key={`${item.row}-${index}`}>{item.row ? `${item.row}행: ` : ""}{item.error}</li>)}</ul> : null}
    </div>
  );
}
