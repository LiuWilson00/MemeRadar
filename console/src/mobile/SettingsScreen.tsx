import { GoogleLogin } from "@react-oauth/google";
import { Check, ImagePlus, LogOut, User as UserIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { googleLogin, setNickname } from "../lib/api";
import {
  clearSession,
  GOOGLE_CLIENT_ID,
  saveSession,
  updateStoredUser,
  useCurrentUser,
} from "../lib/auth";
import { getDeviceNickname } from "../lib/nickname";
import type { UserSettings } from "../lib/settings";
import type { Meta } from "../types";
import Chip, { toggle } from "./Chip";
import ContributeModal from "./ContributeModal";

/** 設定頁：帳號（Google 登入）＋使用者偏好（存 localStorage，套用到每次推薦）。 */
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
      <AccountSection />

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

/** 帳號區：未登入顯示 Google 登入按鈕；已登入顯示頭像／名稱＋登出。
 * 未設定 Client ID（本地未配置）時整區隱藏。 */
function AccountSection() {
  const user = useCurrentUser();
  const [err, setErr] = useState<string | null>(null);
  const [contributing, setContributing] = useState(false);
  const [nick, setNick] = useState("");
  const [nickSaved, setNickSaved] = useState(false);

  useEffect(() => {
    if (user) setNick(user.nickname ?? getDeviceNickname());
  }, [user]);

  const saveNick = async () => {
    const name = nick.trim();
    if (!name) return;
    setNickSaved(false);
    try {
      await setNickname(name);
      updateStoredUser({ nickname: name });
      setNickSaved(true);
    } catch {
      /* 忽略；下次再試 */
    }
  };

  if (!GOOGLE_CLIENT_ID) return null;

  if (user) {
    return (
      <section>
        <h2 className="mb-2 text-sm font-semibold">帳號</h2>
        <div className="flex items-center gap-3 rounded-2xl border border-line bg-panel px-4 py-3">
          {user.picture ? (
            <img src={user.picture} alt="" className="size-9 rounded-full" />
          ) : (
            <div className="grid size-9 place-items-center rounded-full bg-amber-soft text-amber">
              <UserIcon className="size-5" />
            </div>
          )}
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm text-fg">{user.name || "已登入"}</p>
            <p className="truncate text-xs text-muted">{user.email}</p>
          </div>
          <button
            onClick={() => clearSession()}
            className="flex items-center gap-1 rounded-full border border-line px-3 py-1.5 text-xs text-muted active:bg-raised"
          >
            <LogOut className="size-3.5" /> 登出
          </button>
        </div>

        <div className="mt-2 rounded-2xl border border-line bg-panel px-4 py-3">
          <label className="mb-1.5 block text-xs text-muted">留言顯示暱稱</label>
          <div className="flex gap-2">
            <input
              value={nick}
              maxLength={24}
              onChange={(e) => {
                setNick(e.target.value);
                setNickSaved(false);
              }}
              className="min-w-0 flex-1 rounded-xl border border-line bg-ink px-3 py-2 text-sm outline-none focus:border-amber"
            />
            <button
              onClick={saveNick}
              disabled={!nick.trim()}
              className="rounded-full bg-amber px-5 text-sm font-semibold text-ink active:opacity-80 disabled:opacity-40"
            >
              存
            </button>
          </div>
          {nickSaved && <p className="mt-1.5 text-xs text-signal">已更新，留言會用這個名字顯示</p>}
        </div>

        <button
          onClick={() => setContributing(true)}
          className="mt-2 flex w-full items-center gap-3 rounded-2xl border border-line bg-panel px-4 py-3 text-left active:bg-raised"
        >
          <ImagePlus className="size-5 shrink-0 text-amber" strokeWidth={1.75} />
          <span className="min-w-0 flex-1">
            <span className="block text-sm text-fg">貢獻梗圖</span>
            <span className="block text-xs text-muted">上傳你的梗圖到大家的共用圖庫</span>
          </span>
        </button>

        {contributing && <ContributeModal onClose={() => setContributing(false)} />}
      </section>
    );
  }

  return (
    <section>
      <h2 className="mb-1 text-sm font-semibold">登入</h2>
      <p className="mb-3 text-xs text-muted">
        用 Google 登入即可<span className="text-fg">無限使用</span>，還能貢獻梗圖到大家的共用圖庫。
      </p>
      <GoogleLogin
        theme="filled_black"
        shape="pill"
        text="signin_with"
        onSuccess={async (cr) => {
          setErr(null);
          if (!cr.credential) {
            setErr("登入失敗，請再試一次");
            return;
          }
          try {
            const { token, user } = await googleLogin(cr.credential);
            saveSession(token, user);
          } catch (e) {
            setErr(e instanceof Error ? e.message : "登入失敗，請稍後再試");
          }
        }}
        onError={() => setErr("Google 登入被中斷")}
      />
      {err && <p className="mt-2 text-xs text-danger">{err}</p>}
    </section>
  );
}
