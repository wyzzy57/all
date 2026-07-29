import { readFileSync } from "node:fs";

import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";

import AsyncState from "@/components/common/AsyncState.vue";

const stylesSource = readFileSync("src/styles.css", "utf8");
const wrappers: Array<ReturnType<typeof mount>> = [];

function mountState(props: {
  state: "loading" | "empty" | "denied" | "error" | "terminal";
  title?: string;
  description?: string;
  retryLabel?: string;
  testId?: string;
  focusOnAttention?: boolean;
}) {
  const wrapper = mount(AsyncState, { props, attachTo: document.body });
  wrappers.push(wrapper);
  return wrapper;
}

describe("AsyncState", () => {
  afterEach(() => {
    wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  });

  it("announces loading as a polite busy status", () => {
    const wrapper = mountState({
      state: "loading",
      title: "正在加载工作台统计...",
      description: "请稍候",
      testId: "shared-loading",
    });

    const region = wrapper.get("[data-testid='shared-loading']");
    expect(region.text()).toContain("正在加载工作台统计...");
    expect(region.text()).toContain("请稍候");
    expect(region.attributes()).toMatchObject({
      role: "status",
      "aria-live": "polite",
      "aria-busy": "true",
      "aria-atomic": "true",
    });
    expect(region.classes()).toContain("async-state--loading");
    expect(wrapper.get(".async-state__icon").attributes("aria-hidden")).toBe("true");
  });

  it("renders an empty status without a retry action", () => {
    const wrapper = mountState({ state: "empty", title: "暂无审计记录" });

    expect(wrapper.get("[role='status']").attributes("aria-live")).toBe("polite");
    expect(wrapper.get("[role='status']").classes()).toContain("async-state--empty");
    expect(wrapper.find("button").exists()).toBe(false);
  });

  it("announces denied access and never presents it as retryable", async () => {
    const outside = document.createElement("button");
    document.body.append(outside);
    outside.focus();
    const wrapper = mountState({ state: "denied", title: "无权访问平台统计" });
    await wrapper.vm.$nextTick();

    const region = wrapper.get("[role='alert']");
    expect(region.attributes()).toMatchObject({ "aria-live": "assertive", tabindex: "-1" });
    expect(region.classes()).toContain("async-state--denied");
    expect(document.activeElement).toBe(outside);
    expect(wrapper.find("button").exists()).toBe(false);
    outside.remove();
  });

  it("emits retry from a focusable native button for retryable errors", async () => {
    const outside = document.createElement("button");
    document.body.append(outside);
    outside.focus();
    const wrapper = mountState({ state: "error", title: "统计加载失败", retryLabel: "重新加载" });
    await wrapper.vm.$nextTick();

    expect(document.activeElement).toBe(outside);
    const retry = wrapper.get("button");
    expect(retry.attributes("type")).toBe("button");
    expect(retry.text()).toContain("重新加载");
    retry.element.focus();
    expect(document.activeElement).toBe(retry.element);
    await retry.trigger("click");
    expect(wrapper.emitted("retry")).toHaveLength(1);
    outside.remove();
  });

  it("announces a terminal error without stealing focus or showing retry", async () => {
    const outside = document.createElement("button");
    document.body.append(outside);
    outside.focus();
    const wrapper = mountState({ state: "terminal", title: "该任务无法继续" });
    await wrapper.vm.$nextTick();

    const region = wrapper.get("[role='alert']");
    expect(region.classes()).toContain("async-state--terminal");
    expect(document.activeElement).toBe(outside);
    expect(wrapper.find("button").exists()).toBe(false);
    outside.remove();
  });

  it("moves focus only when focusOnAttention is explicitly enabled", async () => {
    const wrapper = mountState({ state: "loading", title: "正在加载", focusOnAttention: true });
    const outside = document.createElement("button");
    document.body.append(outside);
    outside.focus();

    await wrapper.setProps({ state: "error", title: "请求失败" });
    await wrapper.vm.$nextTick();
    expect(document.activeElement).toBe(wrapper.get("[role='alert']").element);
    outside.remove();
  });

  it("reserves stable space and disables loading motion when requested", () => {
    expect(stylesSource).toContain("--visiox-async-state-min-height: 180px");
    expect(stylesSource).toMatch(/\.async-state\s*\{[^}]*min-block-size:\s*var\(--visiox-async-state-min-height\)/s);
    expect(stylesSource).toMatch(/\.async-state__retry\s*\{[^}]*min-height:\s*44px/s);
    expect(stylesSource).toMatch(/@media \(prefers-reduced-motion: reduce\)[\s\S]*\.async-state__icon/s);
  });
});
