import { useEffect, useState } from "react";

import { ApiClientError, getHealth } from "./api";
import type { HealthResponse } from "./types";


type LoadState = "loading" | "ready" | "unavailable";


export function App() {
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [message, setMessage] = useState("正在连接本地服务");

  useEffect(() => {
    const controller = new AbortController();

    getHealth(controller.signal)
      .then((response) => {
        setHealth(response);
        setLoadState("ready");
        setMessage("本地服务已连接");
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setLoadState("unavailable");
        setMessage(
          error instanceof ApiClientError ? error.message : "无法连接本地服务",
        );
      });

    return () => controller.abort();
  }, []);

  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <h1>PaperLens</h1>
          <p>可信学术解读工作台</p>
        </div>
        <span className="mode-badge">Mock</span>
      </header>

      <section className="status-panel" aria-live="polite">
        <span className={`status-dot status-dot--${loadState}`} aria-hidden="true" />
        <div>
          <strong>{message}</strong>
          {health ? <p>API {health.version}</p> : null}
        </div>
      </section>
    </main>
  );
}

