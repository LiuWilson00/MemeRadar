import { Check } from "lucide-react";
import { useState } from "react";
import type { UserSettings } from "../lib/settings";
import type { Meta } from "../types";
import Chip, { toggle } from "./Chip";

/** 設定頁：使用者偏好（存 localStorage，套用到每次推薦）。 */
export default function SettingsScreen({
  settings,
  meta,
  onChange,
}: {
  settings: UserSettings;
  meta: Meta | null;
  onChange: (next: UserSettings) => void;
}) {
  const [cleared, setCleared] = useState(false);
  return (
    <div className="flex-1 space-y-6 overflow-y-auto px-5 py-4">
      <section>
        <h2 className="mb-2 text-sm font-semibold">內容過濾</h2>
        <label className="flex items-center justify-between rounded-2xl border border-line bg-panel px-4 py-3">
          <span className="text-sm">排除成人 / 不宜內容</span>
          <button
            role="switch"
            aria-checked={settings.excludeNsfw}
            onClick={() => onChange({ ...settings, excludeNsfw: !settings.excludeNsfw })}
            className={`relative h-6 w-11 rounded-full transition-colors ${
              settings.excludeNsfw ? "bg-amber" : "bg-line"
            }`}
          >
            <span
              className={`absolute top-0.5 size-5 rounded-full bg-ink transition-all ${
                settings.excludeNsfw ? "left-[22px]" : "left-0.5"
              }`}
            />
          </button>
        </label>
      </section>

      <section>
        <h2 className="mb-1 text-sm font-semibold">偏好梗圖包</h2>
        <p className="mb-2 text-xs text-muted">選了就只從這些梗圖包推薦；留空＝不限。</p>
        <div className="flex flex-wrap gap-2">
          {meta?.franchises.length ? (
            meta.franchises.map((f) => (
              <Chip
                key={f.name}
                label={`${f.name}（${f.count}）`}
                active={settings.franchises.includes(f.name)}
                onToggle={() => onChange({ ...settings, franchises: toggle(settings.franchises, f.name) })}
              />
            ))
          ) : (
            <span className="text-xs text-muted">載入中…</span>
          )}
        </div>
      </section>

      <section>
        <h2 className="mb-1 text-sm font-semibold">偏好分類</h2>
        <p className="mb-2 text-xs text-muted">留空＝不限。</p>
        <div className="flex flex-wrap gap-2">
          {meta?.categories.map((c) => (
            <Chip
              key={c}
              label={c}
              active={settings.categories.includes(c)}
              onToggle={() => onChange({ ...settings, categories: toggle(settings.categories, c) })}
            />
          ))}
        </div>
      </section>

      <p className="flex items-center gap-1.5 pt-2 text-xs text-muted">
        <Check className="size-3.5 text-signal" /> 偏好會自動存在這支手機，下次打開沿用。
      </p>

      <section className="border-t border-line pt-4">
        <h2 className="mb-1 text-sm font-semibold">隱私</h2>
        <p className="text-xs text-muted">
          本機保存一個<span className="text-fg">匿名代碼</span>（無任何個資），只為了改善推薦——
          讓系統分辨同一支手機的多次使用。可隨時清除，清除後視為新裝置。
        </p>
        <div className="mt-2 flex items-center gap-3">
          <button
            onClick={() => {
              try {
                localStorage.removeItem("memeradar.clientId");
              } catch {
                /* localStorage 不可用時略過 */
              }
              setCleared(true);
            }}
            className="rounded-full border border-line px-4 py-1.5 text-xs text-muted active:bg-panel"
          >
            清除匿名代碼
          </button>
          {cleared && <span className="text-xs text-signal">已清除，下次使用視為新裝置</span>}
        </div>
      </section>
    </div>
  );
}
