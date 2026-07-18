document.addEventListener("DOMContentLoaded", () => {
  const requestJson = async (url, options = {}) => {
    const response = await fetch(url, {
      ...options,
      headers: { Accept: "application/json", ...(options.headers || {}) },
    });
    const payload = await response.json();
    if (!response.ok || payload.ok === false) {
      const error = new Error(payload?.error?.message || "请求失败");
      error.payload = payload;
      throw error;
    }
    return payload;
  };

  const updateSelectionCount = (payload) => {
    const count = payload?.data?.count;
    const badge = document.querySelector("#selection-count");
    if (badge instanceof HTMLElement && typeof count === "number") badge.textContent = String(count);
    const drawerBadge = document.querySelector("#drawer-selection-count");
    if (drawerBadge instanceof HTMLElement && typeof count === "number") drawerBadge.textContent = String(count);
  };

  const sendSelection = async (url, options = {}) => {
    const payload = await requestJson(url, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    updateSelectionCount(payload);
    return payload;
  };

  const doctorButton = document.querySelector("#doctor");
  const doctorOutput = document.querySelector("#doctor-result");
  if (doctorButton instanceof HTMLButtonElement && doctorOutput instanceof HTMLElement) {
    doctorButton.addEventListener("click", async () => {
      doctorButton.disabled = true;
      doctorOutput.textContent = "检查中…";
      try {
        const payload = await requestJson(doctorButton.dataset.doctorUrl || "/api/v1/doctor");
        doctorOutput.textContent = JSON.stringify(payload.data, null, 2);
      } catch (error) {
        doctorOutput.textContent = `检查失败：${error instanceof Error ? error.message : String(error)}`;
      } finally {
        doctorButton.disabled = false;
      }
    });
  }

  const importForm = document.querySelector("#import-form");
  const importButton = document.querySelector("#import-button");
  const importOutput = document.querySelector("#import-result");
  const sourceRoot = document.querySelector("#source-root");
  if (
    importForm instanceof HTMLFormElement &&
    importButton instanceof HTMLButtonElement &&
    importOutput instanceof HTMLElement &&
    sourceRoot instanceof HTMLInputElement
  ) {
    importForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      importButton.disabled = true;
      importOutput.textContent = "扫描并导入中…";
      try {
        const payload = await requestJson(importForm.dataset.importUrl || "/api/v1/imports", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ root: sourceRoot.value.trim() }),
        });
        const data = payload.data || {};
        const imported = Array.isArray(data.imported) ? data.imported : [];
        const created = imported.filter((item) => item && item.created).length;
        const failed = Array.isArray(data.failed) ? data.failed : [];
        importOutput.textContent = `扫描完成：发现 ${data.discovered || 0} 个文件，新增 ${created} 个版本，失败 ${failed.length} 个。`;
        if (imported.length > 0) {
          const link = document.createElement("a");
          link.href = "/library";
          link.className = "text-link setup-library-link";
          link.textContent = "打开页面库 →";
          importOutput.append(document.createTextNode(" "), link);
        }
      } catch (error) {
        importOutput.textContent = `导入失败：${error instanceof Error ? error.message : String(error)}`;
      } finally {
        importButton.disabled = false;
      }
    });
  }

  const uploadForm = document.querySelector("#upload-form");
  const uploadButton = document.querySelector("#upload-button");
  const sourceFiles = document.querySelector("#source-files");
  if (
    uploadForm instanceof HTMLFormElement &&
    uploadButton instanceof HTMLButtonElement &&
    sourceFiles instanceof HTMLInputElement &&
    importOutput instanceof HTMLElement
  ) {
    uploadForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!sourceFiles.files || sourceFiles.files.length === 0) {
        importOutput.textContent = "请先选择至少一个 .pptx 文件。";
        return;
      }
      const maxFileBytes = Number(uploadForm.dataset.maxFileBytes || 0);
      const oversizedFile = [...sourceFiles.files].find(
        (file) => maxFileBytes > 0 && file.size > maxFileBytes
      );
      if (oversizedFile) {
        const maxFileGiB = maxFileBytes / (1024 ** 3);
        importOutput.textContent = `导入失败：${oversizedFile.name} 超过单个文件 ${maxFileGiB} GiB 的大小限制。`;
        return;
      }
      uploadButton.disabled = true;
      importOutput.textContent = `正在上传 ${sourceFiles.files.length} 个文件并导入…`;
      try {
        const formData = new FormData();
        [...sourceFiles.files].forEach((file) => formData.append("files", file, file.name));
        const payload = await requestJson(uploadForm.dataset.uploadUrl || "/api/v1/imports/files", {
          method: "POST",
          body: formData,
        });
        const data = payload.data || {};
        const imported = Array.isArray(data.imported) ? data.imported : [];
        const created = imported.filter((item) => item && item.created).length;
        const failed = Array.isArray(data.failed) ? data.failed : [];
        importOutput.textContent = `导入完成：新增 ${created} 个版本，失败 ${failed.length} 个。`;
        if (created > 0) {
          const link = document.createElement("a");
          link.href = "/library";
          link.className = "text-link setup-library-link";
          link.textContent = "打开页面库 →";
          importOutput.append(document.createTextNode(" "), link);
        }
      } catch (error) {
        importOutput.textContent = `导入失败：${error instanceof Error ? error.message : String(error)}`;
      } finally {
        uploadButton.disabled = false;
      }
    });
  }

  document.querySelectorAll(".add-slide").forEach((element) => {
    if (!(element instanceof HTMLButtonElement)) return;
    if (element.dataset.selected === "true") return;
    element.addEventListener("click", async () => {
      element.disabled = true;
      try {
        await sendSelection("/api/v1/selection/items", {
          method: "POST",
          body: JSON.stringify({ slide_id: element.dataset.slideId }),
        });
        element.textContent = "已在选片单";
        element.dataset.selected = "true";
        element.classList.add("is-added");
        element.closest(".slide-card")?.classList.add("is-selected");
      } catch (error) {
        element.textContent = `失败：${error instanceof Error ? error.message : String(error)}`;
        element.disabled = false;
      }
    });
  });

  const drawer = document.querySelector("#selection-drawer");
  const drawerToggle = drawer?.querySelector("[data-drawer-toggle]");
  if (drawer instanceof HTMLElement && drawerToggle instanceof HTMLButtonElement) {
    drawerToggle.addEventListener("click", () => {
      const collapsed = drawer.classList.toggle("is-collapsed");
      drawerToggle.setAttribute("aria-expanded", String(!collapsed));
      drawerToggle.setAttribute("aria-label", collapsed ? "展开选片单" : "收起选片单");
      drawerToggle.textContent = collapsed ? "展开" : "收起";
    });
  }

  document.querySelectorAll(".remove-slide").forEach((element) => {
    if (!(element instanceof HTMLButtonElement)) return;
    element.addEventListener("click", async () => {
      const row = element.closest(".selection-row");
      element.disabled = true;
      try {
        await sendSelection(`/api/v1/selection/items/${encodeURIComponent(element.dataset.slideId || "")}`, {
          method: "DELETE",
        });
        row?.remove();
        window.location.reload();
      } catch (error) {
        element.textContent = `失败：${error instanceof Error ? error.message : String(error)}`;
        element.disabled = false;
      }
    });
  });

  const list = document.querySelector("#selection-list");
  const refreshIndexes = () => {
    list?.querySelectorAll(".selection-row").forEach((row, index) => {
      const target = row.querySelector(".selection-index");
      if (target) target.textContent = String(index + 1);
    });
  };
  const persistOrder = async () => {
    if (!(list instanceof HTMLElement)) return;
    const ids = [...list.querySelectorAll(".selection-row")]
      .map((row) => row.dataset.slideId)
      .filter(Boolean);
    await sendSelection("/api/v1/selection/reorder", {
      method: "POST",
      body: JSON.stringify({ slide_ids: ids }),
    });
    refreshIndexes();
  };

  if (list instanceof HTMLElement) {
    let dragged = null;
    list.querySelectorAll(".selection-row").forEach((row) => {
      if (!(row instanceof HTMLElement)) return;
      row.addEventListener("dragstart", () => {
        dragged = row;
        row.classList.add("dragging");
      });
      row.addEventListener("dragend", () => {
        dragged = null;
        row.classList.remove("dragging");
      });
      row.addEventListener("dragover", (event) => {
        event.preventDefault();
        if (!dragged || dragged === row) return;
        const box = row.getBoundingClientRect();
        list.insertBefore(dragged, event.clientY < box.top + box.height / 2 ? row : row.nextSibling);
        refreshIndexes();
      });
    });
    list.addEventListener("drop", async (event) => {
      event.preventDefault();
      const status = document.querySelector("#selection-status");
      try {
        await persistOrder();
        if (status) status.textContent = `${list.querySelectorAll(".selection-row").length} 页已选`;
      } catch (error) {
        if (status) status.textContent = `排序失败：${error instanceof Error ? error.message : String(error)}`;
        window.location.reload();
      }
    });
  }

  document.querySelectorAll(".move-slide").forEach((element) => {
    if (!(element instanceof HTMLButtonElement)) return;
    element.addEventListener("click", async () => {
      if (!(list instanceof HTMLElement)) return;
      const row = element.closest(".selection-row");
      if (!(row instanceof HTMLElement)) return;
      const sibling = element.dataset.direction === "up" ? row.previousElementSibling : row.nextElementSibling;
      if (!(sibling instanceof HTMLElement) || !sibling.classList.contains("selection-row")) return;
      if (element.dataset.direction === "up") list.insertBefore(row, sibling);
      else list.insertBefore(sibling, row);
      refreshIndexes();
      element.disabled = true;
      try {
        await persistOrder();
      } catch (error) {
        const status = document.querySelector("#selection-status");
        if (status) status.textContent = `排序失败：${error instanceof Error ? error.message : String(error)}`;
        window.location.reload();
      } finally {
        element.disabled = false;
      }
    });
  });

  const exportButton = document.querySelector("#export-selection");
  if (exportButton instanceof HTMLButtonElement) {
    exportButton.addEventListener("click", async () => {
      exportButton.disabled = true;
      const status = document.querySelector("#selection-status");
      if (status) status.textContent = "导出中…";
      try {
        const payload = await sendSelection("/api/v1/exports", { method: "POST", body: "{}" });
        const data = payload.data || {};
        if (status) {
          status.textContent = `已导出 ${data.page_count || 0} 页。`;
          if (data.download_url) {
            const link = document.createElement("a");
            link.className = "download-link";
            link.href = data.download_url;
            link.download = "";
            link.textContent = "下载 PPTX";
            status.append(document.createTextNode(" "), link);
          }
        }
      } catch (error) {
        if (status) status.textContent = `导出失败：${error instanceof Error ? error.message : String(error)}`;
      } finally {
        exportButton.disabled = false;
      }
    });
  }
});
