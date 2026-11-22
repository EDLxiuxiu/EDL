"use strict";
const statusPanel = document.querySelector("[data-run-status]");
if (statusPanel) {
  let seenActive = false;
  const poll = async () => {
    try {
      const response = await fetch(statusPanel.dataset.runStatus, {cache: "no-store"});
      if (!response.ok) throw new Error("读取运行状态失败");
      const status = await response.json();
      const message = document.getElementById("job-message");
      message.textContent = status.error || status.message;
      statusPanel.querySelectorAll("button").forEach(button => button.disabled = status.active);
      if (status.completed_batch !== Number(statusPanel.dataset.batch) || (seenActive && !status.active)) {
        location.reload(); return;
      }
      seenActive ||= status.active;
      setTimeout(poll, status.active ? 1500 : 6000);
    } catch (error) {
      document.getElementById("job-message").textContent = "暂时无法读取进度，请刷新页面检查已提交批次。";
      setTimeout(poll, 6000);
    }
  };
  poll();
}
