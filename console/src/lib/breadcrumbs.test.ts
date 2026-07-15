import { beforeEach, describe, expect, it } from "vitest";
import { clearBreadcrumbs, getBreadcrumbs, logBreadcrumb } from "./breadcrumbs";

beforeEach(() => clearBreadcrumbs());

describe("breadcrumbs", () => {
  it("記錄動作與 metadata", () => {
    logBreadcrumb("nav", "home→settings");
    logBreadcrumb("api", "POST /tasks 500", { status: 500 });
    const crumbs = getBreadcrumbs();
    expect(crumbs).toHaveLength(2);
    expect(crumbs[0]).toMatchObject({ type: "nav", msg: "home→settings" });
    expect(crumbs[1].data).toEqual({ status: 500 });
    expect(typeof crumbs[0].t).toBe("number");
  });

  it("超過上限只保留最近 80 筆", () => {
    for (let i = 0; i < 200; i++) logBreadcrumb("action", `step ${i}`);
    const crumbs = getBreadcrumbs();
    expect(crumbs).toHaveLength(80);
    expect(crumbs[0].msg).toBe("step 120"); // 最舊的被丟掉
    expect(crumbs[79].msg).toBe("step 199");
  });

  it("getBreadcrumbs 回傳副本，外部改動不影響內部", () => {
    logBreadcrumb("action", "a");
    const copy = getBreadcrumbs();
    copy.push({ t: 0, type: "x", msg: "y" });
    expect(getBreadcrumbs()).toHaveLength(1);
  });
});
