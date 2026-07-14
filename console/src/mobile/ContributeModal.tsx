import { CheckCircle2, ImagePlus, Info, Loader2, RotateCcw, X, XCircle } from "lucide-react";
import { useRef, useState } from "react";
import { type LibraryUploadOutcome, uploadToLibrary } from "../lib/api";
import { fileToBase64 } from "../lib/files";

/** 貢獻梗圖到共用圖庫（登入者）：選圖 → 上傳 → 後端標註＋嚴格 NSFW 把關 → 乾淨即自動上架。
 * 入口藏在設定頁；主畫面不受影響。伺服器端已強制把關，客戶端 NSFW 預擋為後續成本優化。 */
export default function ContributeModal({ onClose }: { onClose: () => void }) {
  const [preview, setPreview] = useState<string | null>(null);
  const [b64, setB64] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<LibraryUploadOutcome | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const pick = async (file: File | undefined) => {
    if (!file) return;
    setResult(null);
    const data = await fileToBase64(file);
    setB64(data);
    setPreview(`data:${file.type};base64,${data}`);
  };

  const reset = () => {
    setPreview(null);
    setB64(null);
    setResult(null);
  };

  const upload = async () => {
    if (!b64) return;
    setBusy(true);
    setResult(null);
    const outcome = await uploadToLibrary(b64);
    setBusy(false);
    setResult(outcome);
    if (outcome.kind === "published") {
      setPreview(null);
      setB64(null);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex flex-col bg-ink">
      <div className="mx-auto flex min-h-[100dvh] w-full max-w-md flex-col">
        <header className="flex items-center justify-between px-4 pb-2 pt-[max(0.75rem,env(safe-area-inset-top))]">
          <h1 className="text-sm font-semibold">貢獻梗圖</h1>
          <button
            onClick={onClose}
            className="grid size-8 place-items-center rounded-full text-muted active:bg-panel"
            aria-label="關閉"
          >
            <X className="size-5" />
          </button>
        </header>

        <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-5 pb-6">
          <p className="text-xs leading-relaxed text-muted">
            上傳你的梗圖，讓它進大家的<span className="text-fg">共用圖庫</span>，所有人都能拿來回話。
            系統會自動辨識並過濾不宜內容，乾淨的圖會立刻上架。
          </p>

          {preview ? (
            <div className="overflow-hidden rounded-2xl border border-line bg-panel">
              <img src={preview} alt="預覽" className="max-h-[46vh] w-full object-contain" />
            </div>
          ) : (
            <button
              onClick={() => fileRef.current?.click()}
              disabled={busy}
              className="flex aspect-square w-full flex-col items-center justify-center gap-3
                         rounded-2xl border-2 border-dashed border-line text-muted active:bg-panel
                         disabled:opacity-50"
            >
              <ImagePlus className="size-10" strokeWidth={1.5} />
              <span className="text-sm">選一張梗圖</span>
            </button>
          )}

          {result && <ResultBanner result={result} />}

          {preview && (
            <div className="flex flex-col gap-2">
              <button
                onClick={upload}
                disabled={busy}
                className="flex items-center justify-center gap-2 rounded-full bg-amber py-3 text-sm
                           font-semibold text-ink active:opacity-80 disabled:opacity-50"
              >
                {busy ? (
                  <>
                    <Loader2 className="size-4 animate-spin" /> 辨識中……（約 10 秒）
                  </>
                ) : (
                  "上傳到共用圖庫"
                )}
              </button>
              {!busy && (
                <button
                  onClick={reset}
                  className="flex items-center justify-center gap-1.5 rounded-full border border-line
                             py-2.5 text-xs text-muted active:bg-panel"
                >
                  <RotateCcw className="size-3.5" /> 換一張
                </button>
              )}
            </div>
          )}

          {!preview && result?.kind === "published" && (
            <button
              onClick={() => fileRef.current?.click()}
              className="rounded-full bg-amber py-3 text-sm font-semibold text-ink active:opacity-80"
            >
              再傳一張
            </button>
          )}
        </div>

        <input
          ref={fileRef}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          className="hidden"
          onChange={(e) => {
            void pick(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
      </div>
    </div>
  );
}

function ResultBanner({ result }: { result: LibraryUploadOutcome }) {
  if (result.kind === "published") {
    return (
      <div className="flex items-start gap-2.5 rounded-2xl border border-signal/40 bg-signal/10 px-4 py-3">
        <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-signal" />
        <div className="text-sm">
          <p className="font-semibold text-fg">上架成功，謝謝你！</p>
          <p className="mt-0.5 text-xs text-muted">
            已辨識：{result.ocr?.trim() || result.franchise || "梗圖"}
            {result.franchise ? `（${result.franchise}）` : ""}。大家現在都能用了。
          </p>
        </div>
      </div>
    );
  }

  const isInfo = result.kind === "duplicate" || result.kind === "quota";
  const Icon = isInfo ? Info : XCircle;
  const tone = isInfo ? "border-amber/40 bg-amber-soft text-amber" : "border-danger/40 bg-danger/10 text-danger";
  return (
    <div className={`flex items-start gap-2.5 rounded-2xl border px-4 py-3 ${tone}`}>
      <Icon className="mt-0.5 size-5 shrink-0" />
      <p className="text-sm text-fg">{result.message}</p>
    </div>
  );
}
