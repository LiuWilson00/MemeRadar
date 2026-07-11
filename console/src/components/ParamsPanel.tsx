import { DEFAULT_FILTERS, DEFAULT_PARAMS } from "../lib/api";
import type { Filters, Meta, Params } from "../types";

interface Props {
  meta: Meta | null;
  filters: Filters;
  params: Params;
  onFilters: (f: Filters) => void;
  onParams: (p: Params) => void;
  onRerun: () => void;
  canRerun: boolean;
}

function Slider(props: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  hint?: string;
}) {
  return (
    <label className="block">
      <span className="flex items-baseline justify-between text-xs">
        <span className="text-muted">{props.label}</span>
        <span className="font-mono text-amber">{props.value}</span>
      </span>
      <input
        type="range"
        className="w-full accent-(--color-amber)"
        min={props.min}
        max={props.max}
        step={props.step}
        value={props.value}
        onChange={(e) => props.onChange(Number(e.target.value))}
      />
      {props.hint && <span className="text-[10px] text-muted">{props.hint}</span>}
    </label>
  );
}

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

export default function ParamsPanel({
  meta,
  filters,
  params,
  onFilters,
  onParams,
  onRerun,
  canRerun,
}: Props) {
  return (
    <aside className="flex h-full flex-col gap-4 text-sm" aria-label="參數面板">
      <div>
        <h3 className="mb-1.5 font-mono text-xs tracking-widest text-muted">梗圖包</h3>
        <div className="flex flex-wrap gap-1.5">
          {meta === null && <span className="text-xs text-muted">載入中…</span>}
          {meta?.franchises.length === 0 && (
            <span className="text-xs text-muted">庫內尚無已標註梗圖</span>
          )}
          {meta?.franchises.map((f) => (
            <button
              key={f.name}
              onClick={() => onFilters({ ...filters, franchises: toggle(filters.franchises, f.name) })}
              className={`rounded-full border px-2 py-0.5 text-xs ${
                filters.franchises.includes(f.name)
                  ? "border-amber bg-amber-soft text-amber"
                  : "border-line text-muted hover:text-fg"
              }`}
            >
              {f.name} <span className="font-mono">{f.count}</span>
            </button>
          ))}
        </div>
        <p className="mt-1 text-[10px] text-muted">未選 = 不限梗圖包</p>
      </div>

      <div>
        <h3 className="mb-1.5 font-mono text-xs tracking-widest text-muted">分類</h3>
        <div className="flex flex-wrap gap-1.5">
          {meta?.categories.map((c) => (
            <button
              key={c}
              onClick={() => onFilters({ ...filters, categories: toggle(filters.categories, c) })}
              className={`rounded-full border px-2 py-0.5 text-xs ${
                filters.categories.includes(c)
                  ? "border-amber bg-amber-soft text-amber"
                  : "border-line text-muted hover:text-fg"
              }`}
            >
              {c}
            </button>
          ))}
        </div>
      </div>

      <label className="flex items-center justify-between">
        <span className="text-muted">排除 NSFW</span>
        <input
          type="checkbox"
          className="h-4 w-4 accent-(--color-amber)"
          checked={filters.exclude_nsfw}
          onChange={(e) => onFilters({ ...filters, exclude_nsfw: e.target.checked })}
        />
      </label>

      <div className="space-y-3 border-t border-line pt-3">
        <Slider label="回傳張數 top_n" value={params.top_n} min={3} max={5} step={1}
          onChange={(v) => onParams({ ...params, top_n: v })} />
        <Slider label="候選池 candidate_k" value={params.candidate_k} min={10} max={200} step={10}
          onChange={(v) => onParams({ ...params, candidate_k: v })} />
        <Slider label="相似度下限 min_similarity" value={params.min_similarity} min={0} max={1} step={0.05}
          onChange={(v) => onParams({ ...params, min_similarity: v })}
          hint="BGE-M3 分佈偏高，過高會空手" />
        <Slider label="多樣性 diversity" value={params.diversity} min={0} max={1} step={0.1}
          onChange={(v) => onParams({ ...params, diversity: v })}
          hint="0 = 純相關性；1 = 最大多樣性" />
        <Slider label="熱度加成 hotness_weight" value={params.hotness_weight} min={0} max={0.5} step={0.05}
          onChange={(v) => onParams({ ...params, hotness_weight: v })} />
      </div>

      <div className="mt-auto space-y-2 border-t border-line pt-3">
        <button
          className="w-full rounded border border-line py-1.5 text-muted hover:text-fg"
          onClick={() => {
            onFilters({ ...DEFAULT_FILTERS });
            onParams({ ...DEFAULT_PARAMS });
          }}
        >
          重設為預設值
        </button>
        <button
          disabled={!canRerun}
          onClick={onRerun}
          className="w-full rounded border border-amber py-1.5 text-amber hover:bg-amber-soft
                     disabled:cursor-not-allowed disabled:opacity-40"
        >
          以目前參數重跑
        </button>
      </div>
    </aside>
  );
}
