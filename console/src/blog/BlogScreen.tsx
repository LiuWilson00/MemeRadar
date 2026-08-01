import { useEffect, useState } from "react";
import { fetchBlogList, fetchBlogPost, imageUrl } from "../lib/api";
import { navigate } from "../lib/router";
import type { BlogPost, BlogSummary } from "../types";

/** 每日一梗專欄：列表（/blog）與單篇（/blog/{slug}）。
 *
 * 刻意做成獨立閱讀頁而不是塞進 MobileApp 的分頁——這是「讀文章」的情境，
 * 跟「查梗圖回嘴」是兩種心態，共用底部導覽只會互相干擾。
 *
 * article_html 來自我方後端（模型產出 → 存 DB），不是使用者輸入，故以
 * dangerouslySetInnerHTML 呈現。若日後開放讀者投稿，這裡必須改成消毒後再渲染。
 */

function Meta({ post }: { post: BlogPost }) {
  const o = post.origin;
  const hasOrigin = o && (o.work || o.year);
  return (
    <div className="mb-6 rounded-lg border border-line bg-panel p-4 text-sm">
      {hasOrigin ? (
        <p className="mb-2">
          <span className="text-muted">出處：</span>
          <strong>{o!.work || "—"}</strong>
          {o!.year && <span className="text-muted">（{o!.year}）</span>}
          {o!.scene && <span className="text-muted"> · {o!.scene}</span>}
        </p>
      ) : (
        <p className="mb-2 text-muted">出處：查無可靠來源</p>
      )}
      {post.caption_note && (
        <p className="mb-2">
          <span className="text-muted">圖上文字：</span>
          {post.caption_note}
        </p>
      )}
      {post.sources && post.sources.length > 0 && (
        <div>
          <p className="mb-1 text-muted">參考來源</p>
          <ul className="list-inside list-disc space-y-1">
            {post.sources.map((s) => (
              <li key={s.url}>
                <a
                  href={s.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="text-amber underline"
                >
                  {s.title || s.url}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function PostView({ slug }: { slug: string }) {
  const [post, setPost] = useState<BlogPost | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    fetchBlogPost(slug)
      .then(setPost)
      .catch(() => setErr("找不到這篇文章，可能還沒發布。"));
  }, [slug]);

  if (err) return <p className="p-6 text-sm text-muted">{err}</p>;
  if (!post) return <p className="p-6 text-sm text-muted">載入中…</p>;

  return (
    <article className="mx-auto max-w-2xl px-4 py-6">
      <button onClick={() => navigate("/blog")} className="mb-4 text-sm text-muted">
        ← 回專欄列表
      </button>
      <h1 className="mb-1 text-2xl font-semibold">{post.title}</h1>
      <p className="mb-5 text-xs text-muted">{post.featured_on}</p>
      <img
        src={imageUrl(`/memes/${post.meme_id}/image`)}
        alt={post.title}
        className="mb-6 w-full rounded-lg"
      />
      <Meta post={post} />
      <div
        className="space-y-3 leading-relaxed"
        dangerouslySetInnerHTML={{ __html: post.article_html }}
      />
    </article>
  );
}

function ListView() {
  const [posts, setPosts] = useState<BlogSummary[] | null>(null);

  useEffect(() => {
    fetchBlogList().then(setPosts).catch(() => setPosts([]));
  }, []);

  if (!posts) return <p className="p-6 text-sm text-muted">載入中…</p>;
  if (posts.length === 0)
    return <p className="p-6 text-sm text-muted">還沒有文章，第一篇很快就來。</p>;

  return (
    <div className="mx-auto max-w-2xl px-4 py-6">
      <h1 className="mb-1 text-2xl font-semibold">每日一梗</h1>
      <p className="mb-6 text-sm text-muted">一天一張梗圖，講清楚它的來龍去脈。</p>
      <ul className="space-y-3">
        {posts.map((p) => (
          <li key={p.slug}>
            <button
              onClick={() => navigate(`/blog/${p.slug}`)}
              className="flex w-full gap-3 rounded-lg border border-line bg-panel p-3 text-left"
            >
              <img
                src={imageUrl(`/memes/${p.meme_id}/image`)}
                alt=""
                className="h-16 w-16 shrink-0 rounded object-cover"
              />
              <span className="min-w-0">
                <span className="block truncate font-medium">{p.title}</span>
                <span className="block text-xs text-muted">
                  {p.featured_on}
                  {p.origin?.work ? ` · ${p.origin.work}` : ""}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function BlogScreen({ slug }: { slug: string | null }) {
  return slug ? <PostView slug={slug} /> : <ListView />;
}
