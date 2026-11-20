// Presentation only: registration, hashing and signature verification stay on the backend.
(() => {
  const panel = document.querySelector("#syncidPanel");
  const id = panel.querySelector("#syncidValue");
  const result = panel.querySelector("#syncidResult");
  const copy = panel.querySelector("#syncidCopy");
  const download = panel.querySelector("#syncidDownload");
  const notice = panel.querySelector("#syncidNotice");
  const actionButtons = [panel.querySelector("#syncidRegister"), panel.querySelector("#syncidVerify")];
  let signedRecord = null;
  let noticeTimer;

  const syncButton = document.createElement("button");
  syncButton.className = "ghost small";
  syncButton.textContent = "Sync ID";
  syncButton.onclick = () => panel.showModal();
  document.querySelector('[aria-label="Investigation tools"]').append(syncButton);

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function notify(message, failed = false) {
    clearTimeout(noticeTimer);
    notice.className = "syncid-notice " + (failed ? "syncid-failure" : "syncid-success");
    notice.textContent = message;
    noticeTimer = setTimeout(() => { notice.textContent = ""; }, 4000);
  }

  function statusCard(label, value, tone, explanation) {
    const card = element("section", "syncid-status syncid-" + tone);
    const heading = element("h4", "syncid-status-heading");
    heading.append(element("span", "syncid-dot"), document.createTextNode(label));
    card.append(heading, element("strong", "syncid-status-value", value));
    if (explanation) card.append(element("p", "syncid-explanation", explanation));
    return card;
  }

  const readable = value => String(value).replaceAll("_", " ").toUpperCase();
  const present = value => value !== undefined && value !== null && value !== "";

  function render(body) {
    result.replaceChildren();
    const statuses = element("div", "syncid-status-grid");
    if (body.hash_match === true) {
      statuses.append(statusCard("SHA-256", "MATCH", "success", "The submitted file matches the registered original byte-for-byte."));
    } else if (body.hash_match === false) {
      statuses.append(statusCard("SHA-256", "MISMATCH", "failure", "The submitted file differs from the registered original."));
    } else if ("hash_match" in body) {
      statuses.append(statusCard("SHA-256", "NOT CHECKED", "neutral"));
    }
    if (present(body.signature_status)) {
      const valid = body.signature_status === "valid";
      const invalid = body.signature_status === "invalid";
      statuses.append(statusCard("Signature", readable(body.signature_status), valid ? "success" : invalid ? "failure" : "neutral",
        valid ? "The signed registration record has a valid cryptographic signature." : invalid ? "The signed registration record failed cryptographic signature verification." : "Signature verification has not completed."));
    }
    if (present(body.issuer_trust)) {
      const unknown = body.issuer_trust === "not_established";
      statuses.append(statusCard("Issuer trust", readable(body.issuer_trust), unknown ? "warning" : "neutral",
        unknown ? (body.signature_status === "valid" ? "The signature is valid, but the issuer's real-world identity has not been independently trusted." : "The issuer's real-world identity has not been independently trusted.") : "A pinned key does not independently establish content origin."));
    }
    if (present(body.origin_status)) {
      statuses.append(statusCard("Origin", readable(body.origin_status), "neutral",
        body.origin_status === "not_established" ? "Registration does not independently prove the original creator or creation time." : undefined));
    }
    if (statuses.childElementCount) result.append(element("h3", "syncid-section-title", "Verification results"), statuses);
    if (body.message) result.append(element("p", "syncid-service-note", body.message));
    const payload = body.record?.payload || {};
    const rows = [
      ["Sync ID", body.sync_id, true],
      ["Registration timestamp", payload.registered_at, true],
      ["Registry issuer", body.record?.protected?.issuer],
      ["SHA-256 fingerprint", payload.sha256 ?? body.registered_sha256, true],
      ["File size", typeof payload.size_bytes === "number" ? `${payload.size_bytes.toLocaleString()} bytes` : undefined],
      ["Media type", payload.media_type],
      ["Registration status", body.registration_status ? readable(body.registration_status) : undefined],
      ["Submitted SHA-256", body.calculated_sha256, true]
    ].filter(([, value]) => present(value));
    if (rows.length) {
      result.append(element("h3", "syncid-section-title", "Registration information"));
      const info = element("dl", "syncid-info");
      rows.forEach(([label, value, mono]) => {
        const row = element("div", "syncid-info-row");
        row.append(element("dt", "", label), element("dd", mono ? "mono" : "", value));
        info.append(row);
      });
      result.append(info);
    }
    if (body.record) {
      const details = element("details", "syncid-record");
      details.append(element("summary", "", "Signed identity record"), element("pre", "", JSON.stringify(body.record, null, 2)));
      result.append(details);
    }
  }

  id.addEventListener("input", () => { copy.disabled = !id.value.trim(); });
  copy.onclick = async () => {
    const value = id.value.trim();
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      notify("Sync ID copied to clipboard.");
    } catch {
      id.focus(); id.select();
      notify("Clipboard access failed. The ID is selected; press Ctrl+C or Command+C to copy.", true);
    }
  };

  download.onclick = () => {
    if (!signedRecord) return;
    // Export the returned envelope unchanged, including its signature.
    const blob = new Blob([JSON.stringify(signedRecord, null, 2) + "\n"], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = element("a");
    const safeId = String(signedRecord.payload?.sync_id || "identity").replace(/[^a-zA-Z0-9-]/g, "_");
    link.href = url;
    link.download = `syncid-${safeId}.json`;
    document.body.append(link);
    link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  async function syncAction(action) {
    const file = panel.querySelector("#syncidFile").files[0];
    if (!file) { notify("Choose a content file first.", true); return; }
    if (file.size > 25 * 1024 * 1024) { notify("The prototype limit is 25 MiB.", true); return; }
    if (action === "verify" && !id.value.trim()) { notify("Enter the registered Sync ID.", true); return; }
    signedRecord = null; download.disabled = true;
    clearTimeout(noticeTimer); notice.textContent = "";
    actionButtons.forEach(button => { button.disabled = true; });
    result.setAttribute("aria-busy", "true");
    result.replaceChildren(element("p", "syncid-service-note", action === "register" ? "Registering and signing…" : "Checking bytes and signed record…"));
    try {
      const fd = new FormData(); fd.append("file", file);
      if (action === "verify") fd.append("sync_id", id.value.trim());
      const response = await fetch(`/api/syncid/${action}`, { method: "POST", body: fd });
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Request rejected");
      if (body.available && body.sync_id) id.value = body.sync_id;
      copy.disabled = !id.value.trim();
      signedRecord = body.record || null;
      download.disabled = !signedRecord;
      render(body);
    } catch (error) {
      result.replaceChildren(element("p", "syncid-service-note syncid-failure", "Sync ID request failed: " + error.message));
    } finally {
      actionButtons.forEach(button => { button.disabled = false; });
      result.setAttribute("aria-busy", "false");
    }
  }
  actionButtons[0].onclick = () => syncAction("register");
  actionButtons[1].onclick = () => syncAction("verify");
})();
