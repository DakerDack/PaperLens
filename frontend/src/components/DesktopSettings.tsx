import { useEffect, useState } from "react";
import * as api from "../api";
import type { ModelMode } from "../types";

export function DesktopSettings() {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<ModelMode>("live");
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function read() {
    try {
      const status = await api.getDesktopSettings();
      setMode(status.mode);
      setConfigured(status.key_configured);
      setLoaded(true);
    } catch {
      setConfigured(null);
      setLoaded(false);
      setError("无法读取设置，Key 状态未知。请重新读取设置。");
    }
  }

  useEffect(() => {
    if (!open) return;
    setBusy(true);
    void read().finally(() => setBusy(false));
  }, [open]);

  async function save() {
    setBusy(true);
    setMessage("");
    setError("");
    try {
      await api.saveDesktopSettings({ mode, ...(key === "" ? {} : { api_key: key }) });
      setMessage("设置已保存，重启后生效。当前运行实例继续使用原配置。");
      await read();
    } catch {
      setError("保存设置失败，请检查输入或稍后重试。原运行配置保持不变。");
    } finally {
      setKey("");
      setBusy(false);
    }
  }

  async function clear() {
    setBusy(true);
    setMessage("");
    setError("");
    setKey("");
    try {
      await api.clearDesktopKey();
      setConfigured(false);
      setMessage("已清除保存的 Key，重启后生效；当前运行实例仍可能使用已加载的 Key。");
    } catch {
      setError("清除 Key 失败，请重试。已保存的 Key 未确认清除。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="desktop-settings" aria-label="桌面模型设置">
      <button type="button" aria-expanded={open} aria-controls="desktop-settings-panel"
        disabled={busy} onClick={() => { setOpen(!open); setKey(""); }}>模型设置</button>
      {open && <div id="desktop-settings-panel" className="desktop-settings-panel">
        <div>
          <h2>模型设置</h2>
          <p>使用自己的 Hy3 API Key。设置保存后，请关闭并重新打开 PaperLens。</p>
        </div>
        <label>运行模式<select aria-label="运行模式" value={mode} disabled={busy || !loaded}
          onChange={(event) => setMode(event.target.value as ModelMode)}>
          <option value="live">Live · 使用自己的 Key</option>
          <option value="mock">Mock · 离线演示数据</option>
        </select></label>
        <p aria-live="polite">{configured === null ? "Key 状态未知" : configured ? "已配置 Key" : "未配置 Key"}</p>
        <label>Hy3 API Key<input aria-label="Hy3 API Key" type="password" autoComplete="new-password"
          spellCheck={false} value={key} disabled={busy || !loaded}
          onChange={(event) => setKey(event.target.value)} /></label>
        <p>留空保留已保存的 Key；清除请使用下方独立按钮。保存不会调用模型验证 Key。</p>
        <div className="desktop-settings-actions">
          <button className="primary-button" type="button" disabled={busy || !loaded} onClick={() => void save()}>保存设置</button>
          <button type="button" disabled={busy} onClick={() => void clear()}>清除 Key</button>
          <button type="button" disabled={busy} onClick={() => {
            setBusy(true); setError(""); void read().finally(() => setBusy(false));
          }}>重新读取设置</button>
        </div>
        {busy && <p role="status">正在处理设置…</p>}
        {message && <p role="status">{message}</p>}
        {error && <p className="desktop-settings-error" role="alert">{error}</p>}
      </div>}
    </section>
  );
}
