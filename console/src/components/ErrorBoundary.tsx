import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}
interface State {
  error: Error | null;
}

/** 全域錯誤邊界：任一 render 例外不再讓整頁白屏，改顯示友善畫面 + 重新整理。 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 記到 console 供除錯（尚未接遠端錯誤回報）
    console.error("[MemeRadar] render error:", error, info.componentStack);
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex min-h-[100dvh] flex-col items-center justify-center gap-5 bg-ink px-8 text-center text-fg">
        <span className="radar h-14 w-14 opacity-60" aria-hidden />
        <div>
          <p className="text-base font-semibold">出了點狀況</p>
          <p className="mx-auto mt-1.5 max-w-[16rem] text-sm leading-relaxed text-muted">
            頁面遇到未預期的錯誤，重新整理通常就好了。
          </p>
        </div>
        <button
          onClick={() => window.location.reload()}
          className="rounded-full bg-amber px-6 py-2.5 text-sm font-semibold text-ink active:opacity-80"
        >
          重新整理
        </button>
      </div>
    );
  }
}
