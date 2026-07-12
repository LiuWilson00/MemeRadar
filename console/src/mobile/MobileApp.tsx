import {
  AlertTriangle,
  Camera,
  RotateCcw,
  SearchX,
  Swords,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import { useCallback, useRef, useState } from "react";
import MemeImage from "../components/MemeImage";
import {
  DEFAULT_FILTERS,
  DEFAULT_PARAMS,
  recommend,
  recommendByMemeBattle,
  recommendByScreenshot,
  sendFeedback,
} from "../lib/api";
import { fileToBase64 } from "../lib/files";
import type { RecommendResponse, ResultItem } from "../types";

type Phase = "idle" | "loading" | "results" | "error";
type Mode = "screenshot" | "battle";

export default function MobileApp() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [response, setResponse] = useState<RecommendResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState("");
  const [battleImage, setBattleImage] = useState<string | null>(null);
  const modeRef = useRef<Mode>("screenshot");
  const fileRef = useRef<HTMLInputElement>(null);

  const run = useCallback(async (task: () => Promise<RecommendResponse>) => {
    setPhase("loading");
    setError(null);
    setResponse(null);
    try {
      setResponse(await task());
      setPhase("results");
    } catch (e) {
      setError(e instanceof Error ? e.message : "出了點問題，請再試一次");
      setPhase("error");
    }
  }, []);

  const pick = (mode: Mode) => {
    modeRef.current = mode;
    fileRef.current?.click();
  };

  const onFile = useCallback(
    async (file: File | undefined) => {
      if (!file) return;
      const b64 = await fileToBase64(file);
      if (modeRef.current === "battle") {
        setBattleImage(`data:${file.type};base64,${b64}`);
        void run(() => recommendByMemeBattle(b64));
      } else {
        setBattleImage(null);
        void run(() => recommendByScreenshot(b64));
      }
    },
    [run],
  );

  const onText = useCallback(() => {
    const t = text.trim();
    if (!t) return;
    setBattleImage(null);
    void run(() => recommend([{ speaker: "other", text: t }], DEFAULT_FILTERS, DEFAULT_PARAMS));
  }, [text, run]);

  const reset = () => {
    setPhase("idle");
    setResponse(null);
    setError(null);
    setText("");
    setTyping(false);
    setBattleImage(null);
  };

  return (
    <div className="mx-auto flex min-h-[100dvh] max-w-md flex-col">
      <header className="flex items-center justify-center gap-2 px-4 pb-2 pt-[max(0.75rem,env(safe-area-inset-top))]">
        <span className="radar h-5 w-5 shrink-0" aria-hidden />
        <h1 className="font-mono text-sm font-semibold tracking-[0.35em]">
          MEME<span className="text-amber">RADAR</span>
        </h1>
      </header>

      {phase === "idle" && (
        <IdleScreen
          typing={typing}
          text={text}
          onText={setText}
          onToggleTyping={() => setTyping((v) => !v)}
          onSubmitText={onText}
          onPick={pick}
        />
      )}

      {phase === "loading" && <LoadingScreen mode={modeRef.current} />}

      {phase === "error" && (
        <div className="flex flex-1 flex-col items-center justify-center gap-4 px-8 text-center">
          <p className="text-sm text-danger">{error}</p>
          <button
            onClick={reset}
            className="rounded-full border border-line px-5 py-2 text-sm text-fg active:bg-panel"
          >
            重新開始
          </button>
        </div>
      )}

      {phase === "results" && response && (
        <ResultsScreen response={response} battleImage={battleImage} onReset={reset} />
      )}

      <input
        ref={fileRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        className="hidden"
        onChange={(e) => {
          void onFile(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
    </div>
  );
}

/** 首頁：兩個主入口（截圖 / 對方梗圖）+ 手動輸入為輔。 */
function IdleScreen({
  typing,
  text,
  onText,
  onToggleTyping,
  onSubmitText,
  onPick,
}: {
  typing: boolean;
  text: string;
  onText: (v: string) => void;
  onToggleTyping: () => void;
  onSubmitText: () => void;
  onPick: (mode: Mode) => void;
}) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 px-6 pb-[max(1.5rem,env(safe-area-inset-bottom))]">
      <div className="text-center">
        <p className="text-lg font-semibold leading-relaxed">
          不知道怎麼回？
          <br />
          <span className="text-amber">丟給我，我幫你想梗圖。</span>
        </p>
      </div>

      <button
        onClick={() => onPick("screenshot")}
        className="flex w-full items-center gap-4 rounded-2xl border border-line bg-panel px-5 py-5
                   text-left active:scale-[0.99] active:bg-raised"
      >
        <Camera className="size-7 shrink-0 text-amber" strokeWidth={1.75} />
        <span>
          <span className="block text-base font-semibold">上傳對話截圖</span>
          <span className="block text-xs text-muted">看對話內容，推薦怎麼回</span>
        </span>
      </button>

      <button
        onClick={() => onPick("battle")}
        className="flex w-full items-center gap-4 rounded-2xl border border-amber/50 bg-amber-soft
                   px-5 py-5 text-left active:scale-[0.99] active:bg-amber/20"
      >
        <Swords className="size-7 shrink-0 text-amber" strokeWidth={1.75} />
        <span>
          <span className="block text-base font-semibold text-amber">對方丟了梗圖</span>
          <span className="block text-xs text-muted">梗圖大戰——上傳對方的圖，挑一張回敬</span>
        </span>
      </button>

      <div className="w-full">
        <button
          onClick={onToggleTyping}
          className="mx-auto block text-xs text-muted underline underline-offset-4 active:text-fg"
        >
          {typing ? "收起" : "或手動輸入對方說的話"}
        </button>
        {typing && (
          <div className="mt-3 flex flex-col gap-2">
            <textarea
              value={text}
              onChange={(e) => onText(e.target.value)}
              rows={2}
              placeholder="例如：你報告怎麼又遲交了"
              className="w-full resize-none rounded-2xl border border-line bg-panel px-4 py-3
                         text-sm outline-none focus:border-amber"
            />
            <button
              onClick={onSubmitText}
              disabled={!text.trim()}
              className="rounded-full bg-amber py-3 text-sm font-semibold text-ink
                         disabled:opacity-40 active:opacity-80"
            >
              幫我想回應
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function LoadingScreen({ mode }: { mode: Mode }) {
  return (
    <div
      className="flex flex-1 flex-col items-center justify-center gap-6"
      role="status"
      aria-live="polite"
    >
      <div className="radar h-36 w-36">
        <span className="radar-blip" style={{ left: "62%", top: "30%" }} />
        <span className="radar-blip" style={{ left: "30%", top: "58%", animationDelay: "0.9s" }} />
        <span className="radar-blip" style={{ left: "52%", top: "70%", animationDelay: "1.6s" }} />
      </div>
      <p className="text-sm text-muted">
        {mode === "battle" ? "解讀對方的梗，想怎麼回敬……" : "掃描梗圖庫中……"}（約 15 秒）
      </p>
    </div>
  );
}

/** 結果：全幅輪播圖 + 圓點指示 + 每張回饋 + 詳細 bottom sheet。 */
function ResultsScreen({
  response,
  battleImage,
  onReset,
}: {
  response: RecommendResponse;
  battleImage: string | null;
  onReset: () => void;
}) {
  const [index, setIndex] = useState(0);
  const [detail, setDetail] = useState<ResultItem | null>(null);
  const results = response.results;

  const onScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    setIndex(Math.round(el.scrollLeft / el.clientWidth));
  };

  if (results.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-4 px-8 text-center">
        <SearchX className="size-10 text-muted" strokeWidth={1.5} />
        <p className="text-sm">這次沒找到夠合適的梗圖</p>
        <p className="text-xs text-muted">
          {response.intent.sensitive
            ? "偵測到敏感情境，已保守處理"
            : "換一張圖，或多給一點上下文再試"}
        </p>
        <button
          onClick={onReset}
          className="mt-2 rounded-full bg-amber px-6 py-2.5 text-sm font-semibold text-ink active:opacity-80"
        >
          再試一次
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col">
      {response.intent.sensitive && (
        <p className="mx-4 mb-1 flex items-center justify-center gap-1.5 rounded-full bg-amber-soft px-3 py-1.5 text-center text-xs text-amber">
          <AlertTriangle className="size-3.5" /> 偵測到敏感情境，回應已降級為安撫
        </p>
      )}

      {battleImage && (
        <div className="mx-4 mb-1 flex items-center gap-2 rounded-2xl border border-line bg-panel p-2">
          <img
            src={battleImage}
            alt="對方丟的梗圖"
            className="h-12 w-12 rounded-lg object-cover"
          />
          <span className="text-xs text-muted">
            對方出這張 —— <span className="text-fg">往下滑挑一張回敬</span>
          </span>
        </div>
      )}

      <div
        onScroll={onScroll}
        className="flex flex-1 snap-x snap-mandatory overflow-x-auto scroll-smooth"
        style={{ scrollbarWidth: "none" }}
      >
        {results.map((item) => (
          <Slide
            key={item.meme_id}
            item={item}
            queryId={response.query_id}
            onDetail={() => setDetail(item)}
          />
        ))}
      </div>

      <div className="flex items-center justify-center gap-1.5 py-3">
        {results.map((item, i) => (
          <span
            key={item.meme_id}
            className={`h-1.5 rounded-full transition-all ${
              i === index ? "w-5 bg-amber" : "w-1.5 bg-line"
            }`}
          />
        ))}
      </div>

      <div className="px-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
        <button
          onClick={onReset}
          className="flex w-full items-center justify-center gap-2 rounded-full border border-line py-3 text-sm text-muted active:bg-panel"
        >
          <RotateCcw className="size-4" /> 換一張
        </button>
      </div>

      {detail && (
        <DetailSheet
          item={detail}
          intentSummary={response.intent.summary}
          onClose={() => setDetail(null)}
        />
      )}
    </div>
  );
}

function Slide({
  item,
  queryId,
  onDetail,
}: {
  item: ResultItem;
  queryId: string;
  onDetail: () => void;
}) {
  const [sent, setSent] = useState<"up" | "down" | null>(null);

  const rate = async (rating: "up" | "down") => {
    setSent(rating);
    try {
      await sendFeedback({ query_id: queryId, meme_id: item.meme_id, rank: item.rank, rating });
    } catch {
      /* 靜默：手機端回饋失敗不打擾使用者 */
    }
  };

  return (
    <section className="flex w-full shrink-0 snap-center flex-col px-4">
      <div className="relative flex flex-1 items-center justify-center rounded-3xl border border-line bg-ink">
        <MemeImage
          src={item.image_url}
          href={item.image_url}
          alt={`推薦梗圖第 ${item.rank} 名`}
          className="max-h-[52vh] w-full rounded-3xl object-contain"
        />
        <span className="absolute left-3 top-3 rounded-full bg-ink/80 px-2.5 py-0.5 font-mono text-xs text-amber">
          #{item.rank}
        </span>
        <span className="absolute right-3 top-3 rounded-full border border-amber/70 bg-ink/70 px-2.5 py-0.5 text-xs text-amber">
          {item.matched_strategy}
        </span>
      </div>

      <div className="mt-3 flex items-center gap-2">
        <button
          onClick={() => rate("up")}
          disabled={sent !== null}
          className={`flex flex-1 items-center justify-center rounded-full border py-3 active:scale-95 ${
            sent === "up" ? "border-signal bg-signal/15 text-signal" : "border-line text-fg"
          }`}
          aria-label="這張讚"
        >
          <ThumbsUp className="size-5" strokeWidth={sent === "up" ? 2.4 : 1.75} />
        </button>
        <button
          onClick={() => rate("down")}
          disabled={sent !== null}
          className={`flex flex-1 items-center justify-center rounded-full border py-3 active:scale-95 ${
            sent === "down" ? "border-danger bg-danger/15 text-danger" : "border-line text-fg"
          }`}
          aria-label="這張不行"
        >
          <ThumbsDown className="size-5" strokeWidth={sent === "down" ? 2.4 : 1.75} />
        </button>
        <button
          onClick={onDetail}
          className="rounded-full border border-line px-5 py-3 text-sm text-muted active:bg-panel"
        >
          詳細
        </button>
      </div>
    </section>
  );
}

/** 詳細數據 bottom sheet：分數拆解、命中理由、意圖摘要。 */
function DetailSheet({
  item,
  intentSummary,
  onClose,
}: {
  item: ResultItem;
  intentSummary: string;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-20 flex items-end bg-ink/70" onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full rounded-t-3xl border-t border-line bg-panel px-5
                   pb-[max(1.5rem,env(safe-area-inset-bottom))] pt-3"
      >
        <div className="mx-auto mb-4 h-1 w-10 rounded-full bg-line" />

        <div className="mb-3 flex items-center gap-2">
          <span className="rounded-full border border-amber px-2.5 py-0.5 text-xs text-amber">
            {item.matched_strategy}
          </span>
          {item.matched_tags.slice(0, 3).map((tag) => (
            <span key={tag} className="rounded-full bg-raised px-2 py-0.5 text-xs text-muted">
              {tag}
            </span>
          ))}
        </div>

        <p className="text-sm leading-relaxed">{item.reason}</p>

        {intentSummary && (
          <p className="mt-2 text-xs text-muted">系統判讀的情境：{intentSummary}</p>
        )}

        <div className="mt-4 space-y-2">
          <ScoreBar label="相似度" value={item.scores.vector} />
          <ScoreBar label="重排分" value={item.scores.rerank} />
          <ScoreBar label="最終分" value={item.scores.final} accent />
        </div>

        <button
          onClick={onClose}
          className="mt-5 w-full rounded-full bg-amber py-3 text-sm font-semibold text-ink active:opacity-80"
        >
          關閉
        </button>
      </div>
    </div>
  );
}

function ScoreBar({ label, value, accent }: { label: string; value: number; accent?: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <span className="w-14 shrink-0 text-xs text-muted">{label}</span>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-raised">
        <div
          className={`h-full rounded-full ${accent ? "bg-amber" : "bg-amber/50"}`}
          style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
        />
      </div>
      <span className="w-9 text-right font-mono text-xs">{value.toFixed(2)}</span>
    </div>
  );
}
