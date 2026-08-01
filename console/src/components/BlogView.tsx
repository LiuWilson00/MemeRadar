import { useCallback, useEffect, useState } from "react";
import {
  editBlogPost,
  fetchBlogAdmin,
  generateBlogPost,
  imageUrl,
  setBlogStatus,
} from "../lib/api";
import type { BlogPostAdmin } from "../types";

/** 後台：每日一梗審核。
 *
 * 這一頁存在的理由就是 PoC 的結論——調研模型查不到出處時會編，所以低信心的文章
 * 一律進草稿等人看過。審核者要判斷的不是文筆，是**這段話有沒有來源撐著**，
 * 因此 verdict / confidence / sources / unverified_claims 全部攤在同一個畫面上。
 */

const STATUS_LABEL: Record<string, string> = {
  draft: "草稿",
  published: "已發布",
  rejected: "退稿",
};

function Badge({ post }: { post: BlogPostAdmin }) {
  const conf = post.confidence ?? 0;
  const tone =
    post.verdict === "identified" && conf >= 0.6
      ? "text-ok"
      : post.verdict === "unknown"
        ? "text-danger"
        : "text-amber";
  return (
    <span className={`font-mono text-xs ${tone}`}>
      {post.verdict ?? "?"} · {conf.toFixed(2)}
    </span>
  );
}

function PostCard({ post, onChanged }: { post: BlogPostAdmin; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [title, setTitle] = useState(post.title);
  const [html, setHtml] = useState(post.article_html);
  const dirty = title !== post.title || html !== post.article_html;

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="rounded-lg border border-line bg-panel p-4">
      <div className="mb-3 flex items-start gap-3">
        <img
          src={imageUrl(`/memes/${post.meme_id}/image`)}
          alt=""
          className="h-20 w-20 shrink-0 rounded object-cover"
        />
        <div className="min-w-0 flex-1">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="mb-1 w-full rounded border border-line bg-raised px-2 py-1 text-sm"
          />
          <p className="text-xs text-muted">
            {post.featured_on} · {STATUS_LABEL[post.status] ?? post.status} · <Badge post={post} />
          </p>
          <p className="text-xs text-muted">{post.model_version}</p>
        </div>
      </div>

      {post.origin?.work && (
        <p className="mb-2 text-sm">
          <span className="text-muted">出處：</span>
          {post.origin.work} {post.origin.year}
        </p>
      )}
      {post.caption_note && (
        <p className="mb-2 text-sm">
          <span className="text-muted">圖上文字：</span>
          {post.caption_note}
        </p>
      )}

      {post.sources && post.sources.length > 0 ? (
        <div className="mb-2 text-xs">
          <p className="text-muted">來源（點開驗證，這是審核的重點）</p>
          <ul className="list-inside list-disc">
            {post.sources.map((s) => (
              <li key={s.url}>
                <a href={s.url} target="_blank" rel="noreferrer noopener"
                   className="text-amber underline">{s.title || s.url}</a>
                <span className="text-muted"> — {s.supports}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="mb-2 text-xs text-danger">⚠ 沒有任何來源</p>
      )}

      {post.unverified_claims && post.unverified_claims.length > 0 && (
        <div className="mb-2 text-xs">
          <p className="text-muted">模型自陳查不到證據的部分（不該出現在正文）</p>
          <ul className="list-inside list-disc text-amber">
            {post.unverified_claims.map((u) => <li key={u}>{u}</li>)}
          </ul>
        </div>
      )}

      <textarea
        value={html}
        onChange={(e) => setHtml(e.target.value)}
        rows={8}
        className="mb-3 w-full rounded border border-line bg-raised px-2 py-1 font-mono text-xs"
      />

      <div className="flex flex-wrap gap-2">
        {dirty && (
          <button
            disabled={busy}
            onClick={() => act(() => editBlogPost(post.post_id, { title, article_html: html }))}
            className="rounded bg-amber px-3 py-1 text-sm font-semibold text-ink disabled:opacity-40"
          >
            儲存修訂
          </button>
        )}
        {post.status !== "published" && (
          <button
            disabled={busy || dirty}
            title={dirty ? "先儲存修訂再發布" : ""}
            onClick={() => act(() => setBlogStatus(post.post_id, "published"))}
            className="rounded border border-line px-3 py-1 text-sm disabled:opacity-40"
          >
            發布
          </button>
        )}
        {post.status !== "draft" && (
          <button
            disabled={busy}
            onClick={() => act(() => setBlogStatus(post.post_id, "draft"))}
            className="rounded border border-line px-3 py-1 text-sm disabled:opacity-40"
          >
            退回草稿
          </button>
        )}
        {post.status !== "rejected" && (
          <button
            disabled={busy}
            onClick={() => act(() => setBlogStatus(post.post_id, "rejected"))}
            className="rounded border border-line px-3 py-1 text-sm text-danger disabled:opacity-40"
          >
            退稿
          </button>
        )}
      </div>
    </li>
  );
}

export default function BlogView() {
  const [posts, setPosts] = useState<BlogPostAdmin[] | null>(null);
  const [filter, setFilter] = useState<string>("");
  const [msg, setMsg] = useState("");

  const load = useCallback(() => {
    fetchBlogAdmin(filter || undefined)
      .then(setPosts)
      .catch(() => setPosts([]));
  }, [filter]);

  useEffect(() => load(), [load]);

  const generate = async () => {
    setMsg("產文中（調研要跑 30～60 秒）…");
    try {
      await generateBlogPost();
      setMsg("完成");
      load();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "產文失敗");
    }
  };

  return (
    <div className="p-4">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold">每日一梗</h2>
        <select
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="rounded border border-line bg-raised px-2 py-1 text-sm"
        >
          <option value="">全部</option>
          <option value="draft">草稿</option>
          <option value="published">已發布</option>
          <option value="rejected">退稿</option>
        </select>
        <button
          onClick={generate}
          className="rounded border border-line px-3 py-1 text-sm"
        >
          產出今天的文章
        </button>
        {msg && <span className="text-xs text-muted">{msg}</span>}
      </div>
      {posts === null ? (
        <p className="text-sm text-muted">載入中…</p>
      ) : posts.length === 0 ? (
        <p className="text-sm text-muted">沒有文章。</p>
      ) : (
        <ul className="space-y-4">
          {posts.map((p) => (
            <PostCard key={p.post_id} post={p} onChanged={load} />
          ))}
        </ul>
      )}
    </div>
  );
}
