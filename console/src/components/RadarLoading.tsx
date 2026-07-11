import { useEffect, useState } from "react";

/** 管線階段燈號的預估切換點（秒），對齊 docs/04 §5 實測 */
const STAGES: Array<{ label: string; startsAt: number }> = [
  { label: "意圖分析", startsAt: 0 },
  { label: "向量檢索", startsAt: 7.5 },
  { label: "重排序", startsAt: 8 },
];

export default function RadarLoading() {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const started = performance.now();
    const timer = setInterval(() => setElapsed((performance.now() - started) / 1000), 100);
    return () => clearInterval(timer);
  }, []);

  const activeIndex = STAGES.filter((s) => elapsed >= s.startsAt).length - 1;

  return (
    <div className="flex flex-col items-center gap-5 py-10" role="status" aria-live="polite">
      <div className="radar h-40 w-40">
        <span className="radar-blip" style={{ left: "62%", top: "30%" }} />
        <span className="radar-blip" style={{ left: "30%", top: "58%", animationDelay: "0.9s" }} />
        <span className="radar-blip" style={{ left: "52%", top: "70%", animationDelay: "1.6s" }} />
      </div>
      <div className="flex items-center gap-4 font-mono text-xs">
        {STAGES.map((stage, i) => (
          <span key={stage.label} className="flex items-center gap-1.5">
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                i < activeIndex
                  ? "bg-signal"
                  : i === activeIndex
                    ? "animate-pulse bg-amber"
                    : "bg-line"
              }`}
            />
            <span className={i <= activeIndex ? "text-fg" : "text-muted"}>{stage.label}</span>
          </span>
        ))}
        <span className="text-muted">{elapsed.toFixed(1)}s</span>
      </div>
      <p className="text-xs text-muted">正在掃描梗圖庫……（暖機後約 11–13 秒）</p>
    </div>
  );
}
