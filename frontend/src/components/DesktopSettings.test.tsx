import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { DesktopSettings } from "./DesktopSettings";

const api = vi.hoisted(() => ({ getDesktopSettings: vi.fn(), saveDesktopSettings: vi.fn(), clearDesktopKey: vi.fn() }));
vi.mock("../api", () => api);
beforeEach(() => {
  Object.values(api).forEach(mock => mock.mockReset());
  api.getDesktopSettings.mockResolvedValue({ mode: "live", key_configured: true });
  api.saveDesktopSettings.mockResolvedValue({ restart_required: true });
  api.clearDesktopKey.mockResolvedValue({ key_configured: false, restart_required: true });
});
afterEach(cleanup);
async function open() {
  render(<DesktopSettings />);
  fireEvent.click(screen.getByRole("button", { name: "模型设置" }));
  await screen.findByText("已配置 Key");
}
it("saves a password then clears the field and requests restart", async () => {
  await open();
  const input=screen.getByLabelText("Hy3 API Key") as HTMLInputElement;
  expect(input.type).toBe("password");
  expect(input.value).toBe("");
  fireEvent.change(input,{target:{value:"synthetic-only"}});
  fireEvent.click(screen.getByRole("button",{name:"保存设置"}));
  await screen.findByText(/设置已保存，重启后生效/);
  expect(input.value).toBe("");
  expect(api.saveDesktopSettings).toHaveBeenCalledWith({mode:"live",api_key:"synthetic-only"});
  expect(window.document.body.textContent).not.toContain("synthetic-only");
});
it("omits an empty key and never implicitly deletes", async () => {
  await open();
  fireEvent.change(screen.getByLabelText("运行模式"),{target:{value:"mock"}});
  fireEvent.click(screen.getByRole("button",{name:"保存设置"}));
  await waitFor(()=>expect(api.saveDesktopSettings).toHaveBeenCalledWith({mode:"mock"}));
  expect(api.clearDesktopKey).not.toHaveBeenCalled();
});
it("clears explicitly and permits repeated clearing", async () => {
  await open();
  api.getDesktopSettings.mockResolvedValue({mode:"live",key_configured:false});
  fireEvent.click(screen.getByRole("button",{name:"清除 Key"}));
  await screen.findByText(/已清除保存的 Key，重启后生效/);
  await screen.findByText("未配置 Key");
  fireEvent.click(screen.getByRole("button",{name:"清除 Key"}));
  await waitFor(()=>expect(api.clearDesktopKey).toHaveBeenCalledTimes(2));
});
it("retains configured status after delete failure and hides raw errors", async () => {
  await open();
  api.clearDesktopKey.mockRejectedValue(new Error("synthetic-sensitive-error"));
  fireEvent.click(screen.getByRole("button",{name:"清除 Key"}));
  await screen.findByRole("alert");
  expect(screen.getByText("已配置 Key")).toBeTruthy();
  expect(window.document.body.textContent).not.toContain("synthetic-sensitive-error");
  expect(screen.queryByText(/已清除保存的 Key/)).toBeNull();
});
it("shows unknown state on read failure and allows retry", async () => {
  api.getDesktopSettings.mockRejectedValueOnce(new Error("synthetic-private"));
  render(<DesktopSettings />);
  fireEvent.click(screen.getByRole("button",{name:"模型设置"}));
  await screen.findByText("Key 状态未知");
  expect(screen.queryByText("未配置 Key")).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:"重新读取设置"}));
  await screen.findByText("已配置 Key");
});
it("locks operations while saving and clears password even on failure", async () => {
  await open();
  let reject!: (reason: unknown)=>void;
  api.saveDesktopSettings.mockReturnValue(new Promise((_resolve,fail)=>{reject=fail;}));
  const input=screen.getByLabelText("Hy3 API Key") as HTMLInputElement;
  fireEvent.change(input,{target:{value:"synthetic-only"}});
  fireEvent.click(screen.getByRole("button",{name:"保存设置"}));
  expect((screen.getByRole("button",{name:"清除 Key"}) as HTMLButtonElement).disabled).toBe(true);
  reject(new Error("synthetic-only"));
  await screen.findByRole("alert");
  expect(input.value).toBe("");
});

it("reports unknown status when refresh fails after a successful save", async () => {
  await open();
  api.getDesktopSettings.mockRejectedValue(new Error("synthetic-private"));
  fireEvent.click(screen.getByRole("button",{name:"保存设置"}));
  await screen.findByText(/设置已保存，重启后生效/);
  await screen.findByText("Key 状态未知");
  expect(screen.queryByText("未配置 Key")).toBeNull();
});
it("discards an unsaved password when the panel closes", async () => {
  await open();
  fireEvent.change(screen.getByLabelText("Hy3 API Key"),{target:{value:"synthetic-only"}});
  fireEvent.click(screen.getByRole("button",{name:"模型设置"}));
  fireEvent.click(screen.getByRole("button",{name:"模型设置"}));
  await screen.findByText("已配置 Key");
  expect((screen.getByLabelText("Hy3 API Key") as HTMLInputElement).value).toBe("");
});
