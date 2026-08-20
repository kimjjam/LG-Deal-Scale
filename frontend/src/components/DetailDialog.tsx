import { MouseEvent, ReactNode, useEffect, useRef } from "react";

export default function DetailDialog({
  labelledBy,
  onClose,
  children
}: {
  labelledBy: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeHandler = useRef(onClose);

  useEffect(() => {
    closeHandler.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const dialog = dialogRef.current;
    dialog?.showModal();
    return () => {
      if (dialog?.open) dialog.open = false;
    };
  }, []);

  function close() {
    dialogRef.current?.close();
  }

  function closeFromBackdrop(event: MouseEvent<HTMLDialogElement>) {
    if (event.target === event.currentTarget) close();
  }

  return (
    <dialog
      ref={dialogRef}
      className="detail-panel"
      aria-labelledby={labelledBy}
      onClose={() => closeHandler.current()}
      onClick={closeFromBackdrop}
    >
      <div className="detail-panel-inner">
        {children}
        <button className="sr-only" type="button" onClick={close}>패널 닫기</button>
      </div>
    </dialog>
  );
}
